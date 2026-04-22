"""Parsers and GeoJSON helpers."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Tuple, Optional, Any

import pandas as pd


def parse_inp_coordinates(inp_path: Path) -> Tuple[List[Dict[str, Any]], str]:
    """Parse SWMM INP [COORDINATES] section.

    Returns
    - nodes: list of {id, x, y}
    - crs_hint: best-effort hint about coordinate type
    """
    text = inp_path.read_text(encoding='ISO-8859-1', errors='replace').splitlines()

    coords_start = None
    coords_end = None
    for i, line in enumerate(text):
        s = line.strip().upper()
        if s.startswith('[COORDINATES]'):
            coords_start = i + 1
            continue
        if coords_start is not None and s.startswith('[') and i > coords_start:
            coords_end = i
            break
    if coords_start is None:
        raise ValueError('No [COORDINATES] section found in INP file.')
    coords_end = coords_end or len(text)

    nodes: List[Dict[str, Any]] = []
    for line in text[coords_start:coords_end]:
        line = line.strip()
        if not line or line.startswith(';'):
            continue
        parts = line.split()
        if len(parts) < 3:
            continue
        node_id = parts[0]
        try:
            x = float(parts[1])
            y = float(parts[2])
        except ValueError:
            continue
        nodes.append({'id': str(node_id), 'x': x, 'y': y})

    crs_hint = _guess_crs_hint(nodes)
    return nodes, crs_hint


def parse_hec_geometry(geom_path: Path) -> Dict[str, Any]:
    """Parse a HEC geometry-like file.

    Supports two common text styles:
    1) HEC-RAS geometry (.g01, .g02, ...) containing 'Junct Name=' and 'Reach XY=' blocks.
    2) HEC-HMS basin model (.basin/.geo/.txt) containing element blocks with 'Canvas X'/'Canvas Y'
       and reach connections with 'Upstream'/'Downstream' or 'From'/'To'.

    Output dict keys:
    - raw_text
    - junctions: [{id, x, y, type}]
    - pipes: [{id, from_id, to_id, coords:[[x,y],...] }]
    - parser_used: 'ras' | 'hms' | 'unknown'
    - crs_hint
    - is_planar
    """
    raw = geom_path.read_text(errors='replace')

    # Heuristics
    if 'Junct Name=' in raw or 'River Reach=' in raw or 'Reach XY=' in raw:
        parsed = _parse_ras_geometry(raw)
        parsed['parser_used'] = 'ras'
    elif re.search(r'\bCanvas\s*X\b', raw, flags=re.IGNORECASE) and re.search(r'\bCanvas\s*Y\b', raw, flags=re.IGNORECASE):
        parsed = _parse_hms_basin(raw)
        parsed['parser_used'] = 'hms'
    else:
        parsed = {'junctions': [], 'pipes': []}
        parsed['parser_used'] = 'unknown'

    parsed['raw_text'] = raw
    parsed['crs_hint'] = _guess_crs_hint(parsed.get('junctions', []))
    # For Leaflet: treat as planar unless clearly lat/lon
    parsed['is_planar'] = parsed['crs_hint'] != 'geographic'
    return parsed


def _parse_ras_geometry(raw: str) -> Dict[str, Any]:
    """Best-effort parser for HEC-RAS geometry files."""
    junctions: List[Dict[str, Any]] = []
    pipes: List[Dict[str, Any]] = []

    lines = raw.splitlines()

    # Junction blocks
    # Example patterns vary; handle a few:
    #   Junct Name=J1
    #   Junct X Y= 123.4 567.8
    # sometimes: 'Junct X=' 'Junct Y='
    i = 0
    while i < len(lines):
        line = lines[i]
        if 'Junct Name=' in line:
            name = line.split('Junct Name=', 1)[1].strip()
            x = y = None
            j = i + 1
            while j < min(i + 25, len(lines)):
                l2 = lines[j]
                if 'Junct X Y=' in l2:
                    tail = l2.split('Junct X Y=', 1)[1].strip()
                    parts = tail.split()
                    if len(parts) >= 2:
                        try:
                            x = float(parts[0]); y = float(parts[1])
                        except ValueError:
                            pass
                    break
                # Some files use: Junct X= ..  Junct Y= ..
                if 'Junct X=' in l2:
                    try:
                        x = float(l2.split('Junct X=', 1)[1].strip().split()[0])
                    except Exception:
                        pass
                if 'Junct Y=' in l2:
                    try:
                        y = float(l2.split('Junct Y=', 1)[1].strip().split()[0])
                    except Exception:
                        pass
                if l2.strip().startswith('Junct Name='):
                    break
                j += 1
            if x is not None and y is not None:
                junctions.append({'id': name, 'x': x, 'y': y, 'type': 'junction'})
            i = j
        else:
            i += 1

    # Reach polylines
    # Pattern:
    #   River Reach=River,Reach
    #   Reach XY= 5
    #   123 456
    #   124 457
    # ...
    i = 0
    current_reach_name = None
    while i < len(lines):
        line = lines[i]
        if line.startswith('River Reach='):
            current_reach_name = line.split('River Reach=', 1)[1].strip()
        if 'Reach XY=' in line:
            # point count
            try:
                npts = int(line.split('Reach XY=', 1)[1].strip().split()[0])
            except Exception:
                npts = 0
            coords = []
            for k in range(1, npts + 1):
                if i + k >= len(lines):
                    break
                parts = lines[i + k].strip().split()
                if len(parts) >= 2:
                    try:
                        x = float(parts[0]); y = float(parts[1])
                        coords.append([x, y])
                    except ValueError:
                        pass
            if coords:
                rid = current_reach_name or f'reach_{len(pipes)+1}'
                # Try infer endpoints by nearest junctions
                from_id, to_id = _infer_endpoints(coords, junctions)
                pipes.append({'id': rid, 'from_id': from_id, 'to_id': to_id, 'coords': coords, 'type': 'reach'})
            i += max(npts, 1)
        else:
            i += 1

    return {'junctions': junctions, 'pipes': pipes}


def _parse_hms_basin(raw: str) -> Dict[str, Any]:
    """Best-effort parser for HEC-HMS basin/geometry text that includes Canvas X/Y."""
    # HMS basin files are key/value style with element blocks. We'll parse blocks that start with '<Type>: <Name>'
    # and read Canvas X/Y from subsequent indented lines.

    junctions: List[Dict[str, Any]] = []
    pipes: List[Dict[str, Any]] = []

    lines = raw.splitlines()

    # Collect element coordinates
    elem_xy: Dict[str, Tuple[float, float, str]] = {}

    header_re = re.compile(r'^(Subbasin|Junction|Reservoir|Sink|Source|Reach|Diversion|T Junction)\s*:\s*(.+)$', re.IGNORECASE)

    i = 0
    while i < len(lines):
        m = header_re.match(lines[i].strip())
        if not m:
            i += 1
            continue
        etype = m.group(1).strip()
        name = m.group(2).strip()

        canvas_x = canvas_y = None
        upstream = downstream = from_id = to_id = None

        j = i + 1
        while j < len(lines):
            s = lines[j].strip()
            if header_re.match(s):
                break
            # Key parsing
            if re.match(r'^Canvas\s*X\s*:', s, flags=re.IGNORECASE):
                try:
                    canvas_x = float(s.split(':', 1)[1].strip())
                except Exception:
                    pass
            if re.match(r'^Canvas\s*Y\s*:', s, flags=re.IGNORECASE):
                try:
                    canvas_y = float(s.split(':', 1)[1].strip())
                except Exception:
                    pass

            # Common link descriptors
            if re.match(r'^(Upstream|From)\s*:', s, flags=re.IGNORECASE):
                from_id = s.split(':', 1)[1].strip()
            if re.match(r'^(Downstream|To)\s*:', s, flags=re.IGNORECASE):
                to_id = s.split(':', 1)[1].strip()

            j += 1

        if canvas_x is not None and canvas_y is not None:
            elem_xy[name] = (canvas_x, canvas_y, etype.lower())
            # Treat junction-like nodes as points
            if etype.lower() in ('junction', 'source', 'sink', 'reservoir', 'diversion', 't junction'):
                junctions.append({'id': name, 'x': canvas_x, 'y': canvas_y, 'type': etype.lower()})
            elif etype.lower() == 'subbasin':
                junctions.append({'id': name, 'x': canvas_x, 'y': canvas_y, 'type': 'subbasin'})

        # Create simple reach line if this is a reach and connections exist
        if etype.lower() == 'reach' and from_id and to_id:
            # We'll resolve coords later after we've read all elements
            pipes.append({'id': name, 'from_id': from_id, 'to_id': to_id, 'coords': [], 'type': 'reach'})

        i = j

    # Resolve reach coords as straight lines between endpoints
    for p in pipes:
        if p.get('coords'):
            continue
        a = elem_xy.get(p['from_id'])
        b = elem_xy.get(p['to_id'])
        if a and b:
            p['coords'] = [[a[0], a[1]], [b[0], b[1]]]

    return {'junctions': junctions, 'pipes': pipes}


def _infer_endpoints(coords: List[List[float]], junctions: List[Dict[str, Any]]) -> Tuple[Optional[str], Optional[str]]:
    """Infer reach endpoints by nearest junctions to first/last polyline vertex."""
    if not junctions or not coords:
        return None, None

    def nearest(pt):
        x0, y0 = pt
        best = None
        best_d2 = None
        for j in junctions:
            dx = j['x'] - x0
            dy = j['y'] - y0
            d2 = dx*dx + dy*dy
            if best_d2 is None or d2 < best_d2:
                best_d2 = d2
                best = j['id']
        return best

    return nearest(coords[0]), nearest(coords[-1])


def _guess_crs_hint(points: List[Dict[str, Any]]) -> str:
    """Best-effort CRS hint.

    - If points look like lon/lat (x in [-180, 180], y in [-90, 90]) => 'geographic'
    - Else => 'planar'
    """
    if not points:
        return 'unknown'

    xs = [p.get('x') for p in points if isinstance(p.get('x'), (int, float))]
    ys = [p.get('y') for p in points if isinstance(p.get('y'), (int, float))]
    if not xs or not ys:
        return 'unknown'

    if all(-180 <= x <= 180 for x in xs) and all(-90 <= y <= 90 for y in ys):
        return 'geographic'
    return 'planar'


def build_mapping_lines(
    df_map: pd.DataFrame,
    ssa_col: str,
    hms_col: str,
    inp_nodes: List[Dict[str, Any]],
    geom_junctions: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """Build lines between geometry junctions (HMS side) and INP nodes (SSA side).

    Returns: (lines, summary)
    line dict: {id, hms_id, ssa_id, coords:[[x,y],[x,y]]}
    """
    # Normalize id lookups
    inp_lookup = {str(n['id']): n for n in inp_nodes}
    geom_lookup = {str(j['id']): j for j in geom_junctions}

    total_rows = 0
    created = 0
    missing_hms = 0
    missing_ssa = 0

    lines: List[Dict[str, Any]] = []

    for _, row in df_map.iterrows():
        if ssa_col not in row or hms_col not in row:
            continue
        ssa_id = str(row[ssa_col]) if pd.notna(row[ssa_col]) else None
        hms_id = str(row[hms_col]) if pd.notna(row[hms_col]) else None
        if not ssa_id or not hms_id:
            continue
        total_rows += 1

        gj = geom_lookup.get(hms_id)
        sn = inp_lookup.get(ssa_id)
        if not gj:
            missing_hms += 1
            continue
        if not sn:
            missing_ssa += 1
            continue
        coords = [[gj['x'], gj['y']], [sn['x'], sn['y']]]
        lines.append({'id': f"{hms_id}->{ssa_id}", 'hms_id': hms_id, 'ssa_id': ssa_id, 'coords': coords, 'type': 'mapping'})
        created += 1

    summary = {
        'total_rows': total_rows,
        'lines_created': created,
        'missing_hms': missing_hms,
        'missing_ssa': missing_ssa,
    }
    return lines, summary


def geojson_featurecollection(features: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {'type': 'FeatureCollection', 'features': features}

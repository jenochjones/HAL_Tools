"""Flask app: DSS -> INP timeseries + HEC geometry visualization in Leaflet.

Key features
- Upload HEC-DSS (.dss), mapping CSV (.csv), SWMM INP (.inp), and a HEC geometry file (e.g., HEC-HMS .basin or HEC-RAS .g*).
- Select DSS Run and mapping columns (HMS element name -> SSA/SWMM Node ID).
- Generate an updated INP with [INFLOWS] and [TIMESERIES].
- Parse HEC geometry (junctions/links) and INP coordinates, then display them on an interactive Leaflet map.
- Download updated INP and GeoJSON outputs.

Notes
- pydsstools depends on HEC-DSS libraries. If unavailable in your runtime, the app will still run for mapping, but DSS conversion will be disabled.
"""

from __future__ import annotations

import os
import re
import io
import uuid
import json
import shutil
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple, Optional, Any

from flask import Flask, render_template, request, redirect, url_for, send_from_directory, flash
from werkzeug.utils import secure_filename

import pandas as pd

from parsers import (
    parse_inp_coordinates,
    parse_hec_geometry,
    build_mapping_lines,
    geojson_featurecollection,
)

# Optional DSS support
try:
    import numpy as np
    from pydsstools.heclib.dss import HecDss
    DSS_AVAILABLE = True
except Exception:
    DSS_AVAILABLE = False


BASE_DIR = Path(__file__).resolve().parent
INSTANCE_DIR = BASE_DIR / 'instance'
UPLOAD_ROOT = INSTANCE_DIR / 'uploads'
OUTPUT_ROOT = INSTANCE_DIR / 'outputs'

ALLOWED_EXTENSIONS = {
    'dss', 'csv', 'inp',
    # geometry possibilities
    'basin', 'geo', 'g01', 'g02', 'g03', 'g04', 'g05', 'g06', 'g07', 'g08', 'g09',
    'g10', 'g11', 'g12', 'g13', 'g14', 'g15', 'g16', 'g17', 'g18', 'g19', 'g20',
    'txt'
}


def allowed_file(filename: str) -> bool:
    if not filename or '.' not in filename:
        return False
    ext = filename.rsplit('.', 1)[1].lower()
    return ext in ALLOWED_EXTENSIONS


@dataclass
class TokenState:
    token: str
    upload_dir: Path
    output_dir: Path
    files: Dict[str, Path]


# In-memory state for simplicity. In production, store in a DB or cache.
STATE: Dict[str, TokenState] = {}


def create_app() -> Flask:
    app = Flask(__name__)
    app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'dev-secret-change-me')
    UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)

    @app.get('/')
    def index():
        return render_template('index.html', dss_available=DSS_AVAILABLE)

    @app.post('/analyze')
    def analyze():
        """Save uploads and return a page with selectable options (CSV columns + DSS runs)."""
        token = uuid.uuid4().hex
        upload_dir = UPLOAD_ROOT / token
        output_dir = OUTPUT_ROOT / token
        upload_dir.mkdir(parents=True, exist_ok=True)
        output_dir.mkdir(parents=True, exist_ok=True)

        saved: Dict[str, Path] = {}

        # Expected keys
        for key in ['dss_file', 'csv_file', 'inp_file', 'geom_file']:
            f = request.files.get(key)
            if not f or not f.filename:
                continue
            if not allowed_file(f.filename):
                flash(f"File type not allowed: {f.filename}", 'error')
                continue
            name = secure_filename(f.filename)
            path = upload_dir / name
            f.save(path)
            saved[key] = path

        if 'geom_file' not in saved:
            flash('Geometry file is required.', 'error')
            return redirect(url_for('index'))

        # Parse CSV headers if present
        csv_columns: List[str] = []
        if 'csv_file' in saved:
            try:
                df_head = pd.read_csv(saved['csv_file'], nrows=5)
                csv_columns = df_head.columns.tolist()
            except Exception as e:
                flash(f"Failed to read CSV: {e}", 'error')

        # Parse DSS run names if present & supported
        run_names: List[str] = []
        dss_error: Optional[str] = None
        if 'dss_file' in saved:
            if not DSS_AVAILABLE:
                dss_error = 'pydsstools/HEC-DSS libraries not available in this runtime. DSS conversion is disabled.'
            else:
                try:
                    fid = HecDss.Open(str(saved['dss_file']))
                    pathname_dict = fid.getPathnameDict()
                    pathname_list = pathname_dict.get('TS', [])
                    # Extract HMS run names: RUN:xxx in pathname
                    runs = set()
                    for path in pathname_list:
                        m = re.search(r'RUN:([^/]+)', path)
                        if m:
                            runs.add(m.group(1))
                    run_names = sorted(runs)
                    fid.close()
                except Exception as e:
                    dss_error = f"Failed to inspect DSS file: {e}"

        # Keep state
        STATE[token] = TokenState(token=token, upload_dir=upload_dir, output_dir=output_dir, files=saved)

        return render_template(
            'analyze.html',
            token=token,
            csv_columns=csv_columns,
            run_names=run_names,
            dss_available=DSS_AVAILABLE,
            dss_error=dss_error,
            has_csv=('csv_file' in saved),
            has_dss=('dss_file' in saved),
            has_inp=('inp_file' in saved),
        )

    @app.post('/process')
    def process():
        """Run conversion and build map data."""
        token = request.form.get('token', '').strip()
        if not token or token not in STATE:
            flash('Session expired or invalid token. Please upload again.', 'error')
            return redirect(url_for('index'))

        st = STATE[token]
        files = st.files

        # Read geometry
        geom_path = files.get('geom_file')
        try:
            geom_parsed = parse_hec_geometry(geom_path)
        except Exception as e:
            flash(f"Failed to parse geometry file: {e}", 'error')
            geom_parsed = {
                'raw_text': geom_path.read_text(errors='replace') if geom_path else '',
                'junctions': [],
                'pipes': [],
                'crs_hint': 'unknown',
                'is_planar': True,
                'parser_used': 'error'
            }

        # Parse INP coordinates (optional)
        inp_nodes = []
        inp_crs_hint = None
        if 'inp_file' in files:
            try:
                inp_nodes, inp_crs_hint = parse_inp_coordinates(files['inp_file'])
            except Exception as e:
                flash(f"Failed to parse INP coordinates: {e}", 'error')

        # Build mapping lines if CSV + INP nodes exist
        mapping_lines = []
        mapping_summary = {'total_rows': 0, 'lines_created': 0, 'missing_hms': 0, 'missing_ssa': 0}
        ssa_col = request.form.get('ssa_col') or ''
        hms_col = request.form.get('hms_col') or ''

        if 'csv_file' in files and inp_nodes and (ssa_col and hms_col):
            try:
                df_map = pd.read_csv(files['csv_file'])
                mapping_lines, mapping_summary = build_mapping_lines(
                    df_map=df_map,
                    ssa_col=ssa_col,
                    hms_col=hms_col,
                    inp_nodes=inp_nodes,
                    geom_junctions=geom_parsed['junctions']
                )
            except Exception as e:
                flash(f"Failed to build mapping lines from CSV: {e}", 'error')

        # DSS -> INP conversion (optional)
        updated_inp_path = None
        conversion_log = []
        if request.form.get('do_conversion') == '1':
            if not DSS_AVAILABLE:
                conversion_log.append('DSS conversion requested but DSS libraries are unavailable.')
            else:
                if not all(k in files for k in ['dss_file', 'csv_file', 'inp_file']):
                    conversion_log.append('DSS conversion requested but DSS/CSV/INP were not all uploaded.')
                else:
                    try:
                        selected_run = request.form.get('run_name') or ''
                        updated_inp_path, conversion_log = run_dss_to_inp(
                            dss_path=files['dss_file'],
                            csv_path=files['csv_file'],
                            inp_path=files['inp_file'],
                            ssa_col=ssa_col,
                            hms_col=hms_col,
                            selected_run=selected_run,
                            output_dir=st.output_dir,
                        )
                    except Exception as e:
                        conversion_log.append('Conversion failed: ' + str(e))
                        conversion_log.append(traceback.format_exc())

        # Create GeoJSON outputs
        # Decide whether we're in planar space (CRS.Simple) or geographic
        # If either source suggests planar, treat as planar.
        is_planar = bool(geom_parsed.get('is_planar', True))
        # If INP coordinates look geographic, and geometry does too, we can use OSM.
        # In practice, keep planar if geometry is planar.

        junction_fc = geojson_featurecollection(
            features=[
                {
                    'type': 'Feature',
                    'geometry': {'type': 'Point', 'coordinates': [j['x'], j['y']]},
                    'properties': {k: v for k, v in j.items() if k not in ('x', 'y')}
                }
                for j in geom_parsed.get('junctions', [])
            ]
        )

        pipes_fc = geojson_featurecollection(
            features=[
                {
                    'type': 'Feature',
                    'geometry': {'type': 'LineString', 'coordinates': p['coords']},
                    'properties': {k: v for k, v in p.items() if k != 'coords'}
                }
                for p in geom_parsed.get('pipes', []) if p.get('coords')
            ]
        )

        inp_fc = geojson_featurecollection(
            features=[
                {
                    'type': 'Feature',
                    'geometry': {'type': 'Point', 'coordinates': [n['x'], n['y']]},
                    'properties': {'id': n['id'], 'source': 'INP'}
                }
                for n in inp_nodes
            ]
        )

        maplines_fc = geojson_featurecollection(
            features=[
                {
                    'type': 'Feature',
                    'geometry': {'type': 'LineString', 'coordinates': ln['coords']},
                    'properties': {k: v for k, v in ln.items() if k != 'coords'}
                }
                for ln in mapping_lines
            ]
        )

        # Save outputs
        (st.output_dir).mkdir(parents=True, exist_ok=True)
        (st.output_dir / 'junctions.geojson').write_text(json.dumps(junction_fc), encoding='utf-8')
        (st.output_dir / 'pipes.geojson').write_text(json.dumps(pipes_fc), encoding='utf-8')
        (st.output_dir / 'inp_nodes.geojson').write_text(json.dumps(inp_fc), encoding='utf-8')
        (st.output_dir / 'mapping_lines.geojson').write_text(json.dumps(maplines_fc), encoding='utf-8')

        # Geometry raw content
        raw_geom_text = geom_parsed.get('raw_text', '')

        return render_template(
            'results.html',
            token=token,
            geom_parser=geom_parsed.get('parser_used', 'unknown'),
            geom_crs_hint=geom_parsed.get('crs_hint', 'unknown'),
            inp_crs_hint=inp_crs_hint,
            is_planar=is_planar,
            junctions_geojson=json.dumps(junction_fc),
            pipes_geojson=json.dumps(pipes_fc),
            inp_geojson=json.dumps(inp_fc),
            maplines_geojson=json.dumps(maplines_fc),
            raw_geom_text=raw_geom_text,
            mapping_summary=mapping_summary,
            updated_inp_filename=(updated_inp_path.name if updated_inp_path else None),
            conversion_log=conversion_log,
        )

    @app.get('/download/<token>/<path:filename>')
    def download(token: str, filename: str):
        if token not in STATE:
            flash('Invalid token.', 'error')
            return redirect(url_for('index'))
        st = STATE[token]
        # Only allow downloads from output directory
        return send_from_directory(st.output_dir, filename, as_attachment=True)

    @app.get('/cleanup/<token>')
    def cleanup(token: str):
        """Optional cleanup for a token."""
        st = STATE.pop(token, None)
        if st:
            shutil.rmtree(st.upload_dir, ignore_errors=True)
            shutil.rmtree(st.output_dir, ignore_errors=True)
            flash('Cleaned up temporary files.', 'info')
        return redirect(url_for('index'))

    return app


def run_dss_to_inp(
    dss_path: Path,
    csv_path: Path,
    inp_path: Path,
    ssa_col: str,
    hms_col: str,
    selected_run: str,
    output_dir: Path,
) -> Tuple[Path, List[str]]:
    """Replicates the original conversion logic, writing an updated INP to output_dir."""
    log: List[str] = []
    if not DSS_AVAILABLE:
        raise RuntimeError('DSS libraries not available.')

    fid = HecDss.Open(str(dss_path))
    mapping = pd.read_csv(csv_path)

    pathname_dict = fid.getPathnameDict()
    pathname_list = pathname_dict.get('TS', [])

    # Determine run
    run_names = sorted(set(
        m.group(1)
        for p in pathname_list
        for m in [re.search(r'RUN:([^/]+)', p)]
        if m
    ))

    if not run_names:
        fid.close()
        raise RuntimeError('No RUN:... tokens were found in the DSS TS pathnames.')

    model = selected_run if selected_run else (run_names[0] if len(run_names) == 1 else run_names[0])
    if selected_run and selected_run not in run_names:
        log.append(f"Selected run '{selected_run}' was not found; using '{model}'.")

    unique_ssa_ids = mapping[ssa_col].dropna().astype(str).unique()

    df_list = []
    missed_mapping = []

    for ssa_id in unique_ssa_ids:
        hms_ids = mapping.loc[mapping[ssa_col].astype(str) == str(ssa_id), hms_col].dropna().astype(str)
        composite_list = []

        for hms_id in hms_ids:
            # Match FLOW time series for this hms_id and run
            pathnames = [
                s for s in pathname_list
                if f'/{hms_id}/' in s and '/FLOW/' in s and model in s
            ]
            for pathname in pathnames:
                ts = fid.read_ts(pathname)
                times = np.array(ts.pytimes)
                values = ts.values
                # Filter nodata
                valid_mask = ~ts.nodata
                valid_times = times[valid_mask]
                valid_values = values[valid_mask]
                df = pd.DataFrame({'Time': pd.to_datetime(valid_times), hms_id: valid_values}).set_index('Time')
                composite_list.append(df)

        if composite_list:
            composite_df = pd.concat(composite_list, axis=1, join='outer')
            summed_df = composite_df.sum(axis=1).to_frame(name=f'{ssa_id}_ts')
            df_list.append(summed_df)
        else:
            missed_mapping.append(str(ssa_id))

    if not df_list:
        fid.close()
        raise RuntimeError('No time series were extracted based on the mapping and DSS contents.')

    combined_df = pd.concat(df_list, axis=1, join='outer')
    combined_df.index = pd.to_datetime(combined_df.index)
    fid.close()

    updated_text = replace_timeseries_in_inp_text(inp_path, combined_df, unique_ssa_ids)

    updated_name = inp_path.stem + '_Updated.inp'
    updated_path = output_dir / updated_name
    updated_path.write_text(updated_text, encoding='ISO-8859-1')

    log.append(f"Wrote updated INP: {updated_name}")
    if missed_mapping:
        log.append('No time series found for SSA IDs: ' + ', '.join(missed_mapping))

    return updated_path, log


def format_timeseries(df: pd.DataFrame) -> str:
    timeseries_str = "[TIMESERIES]\n"
    timeseries_str += ";;Name             Date       Time       Value     \n"
    timeseries_str += ";;-------------------------------------------------\n"
    for name, series in df.items():
        timeseries_str += f"\n;;Timeseries for {name} from HMS\n"
        for timestamp, value in series.items():
            if pd.isna(value):
                continue
            date = timestamp.strftime('%m/%d/%Y')
            time = timestamp.strftime('%H:%M')
            timeseries_str += f"{name:<18} {date} {time}      {value:<10.5f}\n"
    timeseries_str += "\n"
    return timeseries_str


def format_inflows(ssa_ids) -> str:
    inflow_str = "[INFLOWS]\n"
    inflow_str += ";;                                                 Param    Units    Scale    Baseline Baseline\n"
    inflow_str += ";;Node           Parameter        Time Series      Type     Factor   Factor   Value    Pattern\n"
    inflow_str += ";;-------------- ---------------- ---------------- -------- -------- -------- -------- --------\n"
    for ssa_id in ssa_ids:
        inflow_str += f"{str(ssa_id):<17}FLOW             {str(ssa_id)}_ts\n"
    inflow_str += "\n"
    return inflow_str


def replace_timeseries_in_inp_text(inp_path: Path, df: pd.DataFrame, ssa_ids) -> str:
    inp_content = inp_path.read_text(encoding='ISO-8859-1').splitlines(keepends=True)

    timeseries_found = any(line.strip().startswith('[TIMESERIES]') for line in inp_content)
    inflows_found = any(line.strip().startswith('[INFLOWS]') for line in inp_content)

    # Identify sections
    in_section = False
    start_1_index = end_1_index = start_2_index = end_2_index = None

    for i, line in enumerate(inp_content):
        if line.strip().startswith('[INFLOWS]') or line.strip().startswith('[TIMESERIES]'):
            if in_section:
                end_1_index = i
            if start_1_index is None:
                start_1_index = i
            else:
                start_2_index = i
            in_section = True
        elif line.strip().startswith('[') and in_section:
            if end_1_index is None:
                end_1_index = i
            else:
                end_2_index = i
            in_section = False

    inflow_section = format_inflows(ssa_ids)
    timeseries_section = format_timeseries(df)

    if not timeseries_found or not inflows_found:
        start_2_index = end_1_index
        end_2_index = end_1_index

    if not timeseries_found and not inflows_found:
        updated_content = inp_content + ['\n'] + [inflow_section] + [timeseries_section]
    else:
        # Guard None indices
        start_1_index = start_1_index or 0
        end_1_index = end_1_index or start_1_index
        start_2_index = start_2_index or end_1_index
        end_2_index = end_2_index or start_2_index
        updated_content = inp_content[:start_1_index] + [inflow_section] + inp_content[end_1_index:start_2_index] + [timeseries_section] + inp_content[end_2_index:]

    return ''.join(updated_content)


if __name__ == '__main__':
    app = create_app()
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)), debug=True)

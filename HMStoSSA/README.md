# HEC Geometry Viewer (Flask + Leaflet)

This app converts your original Tkinter/Matplotlib script into a web-based workflow.

## What it does

1. Upload a **HEC geometry file** (required)
   - Supports best-effort parsing of:
     - **HEC-RAS** geometry files (`.g01`, `.g02`, ...) containing `Junct Name=` and `Reach XY=` blocks.
     - **HEC-HMS** basin/geometry text files (`.basin`, `.geo`, `.txt`) containing `Canvas X:` and `Canvas Y:`.
2. Optionally upload:
   - **SWMM INP** (`.inp`) → parses `[COORDINATES]` and plots nodes.
   - **Mapping CSV** (`.csv`) → draws lines between geometry junction IDs and INP node IDs.
   - **HEC-DSS** (`.dss`) → enables DSS→INP conversion (requires `pydsstools` + HEC-DSS libs).

## Run locally

```bash
python -m venv .venv
# activate venv
pip install -r requirements.txt
python app.py
```

Then open: http://127.0.0.1:5000

## Notes about coordinates

- If coordinates look like **lon/lat**, the map uses OpenStreetMap tiles.
- Otherwise, the map uses **Leaflet CRS.Simple** and draws everything in planar space.

If your files are in a projected CRS (State Plane, UTM, etc.) and you want a basemap, you can add `proj4leaflet` + an EPSG definition, but that requires knowing the CRS.

## Where to adjust parsing

Parsing lives in `parsers.py`. If your geometry file uses a different structure, you can update the regex patterns or add a new parser.

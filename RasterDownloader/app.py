
from flask import Flask, render_template, request, jsonify
from werkzeug.utils import secure_filename
from werkzeug.exceptions import RequestEntityTooLarge
import os
import tempfile
import shutil
import json

import geopandas as gpd

# --- Create the app ONCE ---
app = Flask(__name__)

# Limit upload size to 20 MB (adjust as needed)
app.config['MAX_CONTENT_LENGTH'] = 20 * 1024 * 1024  # 20 MB


# -------- Helpers --------
def _same_stem(*names: str) -> bool:
    """Return True if all filenames share the same basename (case-insensitive)."""
    stems = [os.path.splitext(n)[0].lower() for n in names]
    return len(set(stems)) == 1

def _has_ext(filename: str, ext: str) -> bool:
    """Case-insensitive check for a given extension (without leading dot)."""
    return filename.lower().endswith(f'.{ext}')


# -------- Error handlers --------
@app.errorhandler(RequestEntityTooLarge)
def handle_file_too_large(e):
    return 'File too large. Max size is 20 MB.', 413


# -------- Routes --------
@app.route('/')
def index():
    # Default center and zoom; tweak as desired
    return render_template('index.html', center_lat=39.5, center_lng=-98.35, zoom=4)


@app.post('/upload_shapefile_parts')
def upload_shapefile_parts():
    """
    Accepts four parts of a shapefile: .shp, .shx, .dbf, .prj
    Validates shared basename, reads with GeoPandas, reprojects to EPSG:4326,
    and returns GeoJSON.
    """
    req = request.files
    missing = [k for k in ('shp', 'shx', 'dbf', 'prj') if k not in req or req[k].filename == '']
    if missing:
        return f"Missing required file(s): {', '.join(missing)}", 400

    shp_f = req['shp']
    shx_f = req['shx']
    dbf_f = req['dbf']
    prj_f = req['prj']

    # Validate extensions
    if not _has_ext(shp_f.filename, 'shp'):
        return 'Expected a .shp file for field "shp"', 400
    if not _has_ext(shx_f.filename, 'shx'):
        return 'Expected a .shx file for field "shx"', 400
    if not _has_ext(dbf_f.filename, 'dbf'):
        return 'Expected a .dbf file for field "dbf"', 400
    if not _has_ext(prj_f.filename, 'prj'):
        return 'Expected a .prj file for field "prj"', 400

    # Ensure same basename
    if not _same_stem(shp_f.filename, shx_f.filename, dbf_f.filename, prj_f.filename):
        return 'All four files must share the same basename (e.g., parcels.shp/shx/dbf/prj).', 400

    # Normalize filenames to a unified, safe stem (from the .shp name)
    safe_stem = os.path.splitext(secure_filename(shp_f.filename))[0]
    temp_dir = tempfile.mkdtemp(prefix='shp_parts_')

    warnings = []
    try:
        # Save all four using the same stem
        shp_path = os.path.join(temp_dir, f'{safe_stem}.shp')
        shx_path = os.path.join(temp_dir, f'{safe_stem}.shx')
        dbf_path = os.path.join(temp_dir, f'{safe_stem}.dbf')
        prj_path = os.path.join(temp_dir, f'{safe_stem}.prj')

        shp_f.save(shp_path)
        shx_f.save(shx_path)
        dbf_f.save(dbf_path)
        prj_f.save(prj_path)

        # Read with GeoPandas (Fiona will find .shx/.dbf/.prj by stem)
        try:
            gdf = gpd.read_file(shp_path)
        except Exception as read_err:
            return f'Failed to read shapefile: {read_err}', 400

        # Reproject to WGS84 for Leaflet
        if gdf.crs is None:
            # We have a .prj, but sometimes CRS parsing fails—warn, but continue.
            warnings.append('CRS not detected; proceeding as-is. Data may not align with basemap.')
        else:
            try:
                gdf = gdf.to_crs(epsg=4326)
            except Exception as crs_err:
                warnings.append(f'Failed to reproject to EPSG:4326: {crs_err}. Using original coordinates.')

        geojson_obj = json.loads(gdf.to_json())

        # Return single layer payload for consistency
        return jsonify({
            'layer': {'name': safe_stem, 'geojson': geojson_obj},
            'warnings': warnings
        })

    finally:
        # Cleanup temp directory regardless of success/failure
        try:
            shutil.rmtree(temp_dir)
        except Exception:
            pass



#    url = 'https://services1.arcgis.com/99lidPhWCzftIe9K/ArcGIS/rest/services/LiDAR_Extents/FeatureServer/0'
#    tile_index_url = 'https://mapserv.utah.gov/arcgis/rest/services/Raster/MapServer/'
    

# --- Dev server ---
if __name__ == '__main__':
    # For local development only; use a proper WSGI server in production
    app.run(host='0.0.0.0', port=5001, debug=True)

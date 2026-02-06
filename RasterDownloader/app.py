import geopandas as gpd

import os
import re
import json
import uuid
import shutil
import zipfile
import tempfile
from glob import glob
from urllib.parse import urljoin

import requests
from flask import Flask, request, render_template, jsonify, send_file
from werkzeug.utils import secure_filename
from werkzeug.exceptions import RequestEntityTooLarge

import rasterio
from rasterio.merge import merge as rio_merge
from rasterio.mask import mask as rio_mask
from rasterio.warp import calculate_default_transform, reproject, Resampling
from rasterio.io import MemoryFile

from shapely.geometry import shape, mapping
from shapely.ops import unary_union
from pyproj import CRS

# --- Create the app ONCE ---
app = Flask(__name__)

# Limit upload size to 20 MB (adjust as needed)
app.config['MAX_CONTENT_LENGTH'] = 20 * 1024 * 1024  # 20 MB



# ---- Config (same services as your template) ----
LIDAR_EXTENTS_FS0 = "https://services1.arcgis.com/99lidPhWCzftIe9K/ArcGIS/rest/services/LiDAR_Extents/FeatureServer/0"
TILE_INDEX_MAPSERVER = "https://mapserv.utah.gov/arcgis/rest/services/Raster/MapServer/"

# Where to build temp jobs (use a persistent path if you want to keep results)
BASE_WORK_DIR = os.environ.get("LIDAR_WORKDIR", tempfile.gettempdir())


# ----------------------------
# Helpers (template-inspired)
# ----------------------------
def safe_name(text: str) -> str:
    """Filesystem-safe name."""
    text = str(text or "").strip()
    text = re.sub(r"[^A-Za-z0-9_\-]+", "_", text)
    return text[:120] or "dataset"


def remove_all_files(folderpath: str) -> None:
    """Remove all files in a folder (not recursive)."""
    if os.path.isdir(folderpath):
        for f in glob(os.path.join(folderpath, "*")):
            if os.path.isfile(f):
                os.remove(f)


def download_and_extract_zip(url: str, save_folder: str) -> None:
    """
    Download a zip from url -> save_folder, extract contents, remove zip.
    """
    os.makedirs(save_folder, exist_ok=True)
    file_name = os.path.basename(url)
    zip_path = os.path.join(save_folder, file_name)

    try:
        with requests.get(url, stream=True, timeout=120) as r:
            r.raise_for_status()
            with open(zip_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        f.write(chunk)

        with zipfile.ZipFile(zip_path, "r") as z:
            z.extractall(save_folder)

    finally:
        if os.path.exists(zip_path):
            os.remove(zip_path)


def get_layer_id(mapserv_url: str, layer_name: str) -> int | None:
    """
    Find a layer id by layer name from an ArcGIS MapServer root endpoint.
    """
    meta_url = f"{mapserv_url}?f=json"
    resp = requests.get(meta_url, timeout=60)
    if resp.status_code != 200:
        return None

    data = resp.json()
    for lyr in data.get("layers", []):
        if lyr.get("name") == layer_name:
            return lyr.get("id")
    return None


def parse_geojson_geometry(uploaded_geojson) -> tuple[list[dict], CRS]:
    """
    Convert GeoJSON to (list_of_geojson_geom_dicts, input_crs).

    - Supports FeatureCollection, Feature, geometry dict, or list of features.
    - CRS handling:
      * If GeoJSON has a "crs" object, attempt to parse it
      * Otherwise default to EPSG:4326
    """
    # Default assumption
    input_crs = CRS.from_epsg(4326)

    # Try to read CRS from GeoJSON (non-standard in modern GeoJSON but sometimes present)
    if isinstance(uploaded_geojson, dict):
        crs_obj = uploaded_geojson.get("crs")
        if crs_obj:
            try:
                # Common patterns:
                # {"type":"name","properties":{"name":"EPSG:26912"}}
                name = crs_obj.get("properties", {}).get("name")
                if name:
                    input_crs = CRS.from_user_input(name)
            except Exception:
                pass

    # Normalize to features
    geoms = []
    if isinstance(uploaded_geojson, dict) and uploaded_geojson.get("type") == "FeatureCollection":
        feats = uploaded_geojson.get("features", [])
        geoms = [f.get("geometry") for f in feats if f.get("geometry")]
    elif isinstance(uploaded_geojson, dict) and uploaded_geojson.get("type") == "Feature":
        if uploaded_geojson.get("geometry"):
            geoms = [uploaded_geojson["geometry"]]
    elif isinstance(uploaded_geojson, dict) and uploaded_geojson.get("type") in ("Polygon", "MultiPolygon"):
        geoms = [uploaded_geojson]
    elif isinstance(uploaded_geojson, list):
        # list of features/geometries
        for item in uploaded_geojson:
            if isinstance(item, dict) and item.get("type") == "Feature" and item.get("geometry"):
                geoms.append(item["geometry"])
            elif isinstance(item, dict) and item.get("type") in ("Polygon", "MultiPolygon"):
                geoms.append(item)
    else:
        geoms = []

    if not geoms:
        raise ValueError("No polygon geometry found in uploaded GeoJSON.")

    # Combine into one mask geometry (union)
    shapely_geoms = [shape(g) for g in geoms]
    unioned = unary_union(shapely_geoms)
    if unioned.is_empty:
        raise ValueError("Uploaded GeoJSON geometry is empty.")

    return [mapping(unioned)], input_crs


def get_bbox_from_geom(geom_geojson_list: list[dict]) -> dict:
    """
    Returns bbox dict suitable for ArcGIS 'esriGeometryEnvelope' query.
    """
    geom = shape(geom_geojson_list[0])
    minx, miny, maxx, maxy = geom.bounds
    return {
        "xmin": minx,
        "ymin": miny,
        "xmax": maxx,
        "ymax": maxy
    }


def get_dataset_ext(datasets: list[str], url: str) -> dict:
    """
    Query LiDAR_Extents FeatureServer/0 for Tile_Index -> File_Extension mapping.
    """
    query_url = f"{url}/query"
    where = " OR ".join([f"Tile_Index = '{d}'" for d in datasets])

    params = {
        "outFields": "Tile_Index,File_Extension",
        "returnGeometry": "false",
        "f": "json",
        "where": where
    }
    resp = requests.get(query_url, params=params, timeout=60)
    resp.raise_for_status()

    data = resp.json()
    features = data.get("features", [])
    return {
        f["attributes"]["Tile_Index"]: f["attributes"]["File_Extension"]
        for f in features
        if f.get("attributes")
    }


def get_intersecting_tiles(geom_geojson_list: list[dict], tile_index_url: str, tile_group: str, in_wkid: int = 4326):
    """
    Query the tile index layer for tiles intersecting the geometry bbox.
    Returns list of tuples: (PATH, TILE, EXT).
    """
    layer_id = get_layer_id(tile_index_url, tile_group)
    if layer_id is None:
        return []

    query_url = urljoin(tile_index_url, f"{layer_id}/query")

    bbox = get_bbox_from_geom(geom_geojson_list)
    bbox["spatialReference"] = {"wkid": in_wkid}

    params = {
        "f": "json",
        "where": "1=1",
        "outFields": "PATH,TILE,EXT",
        "geometry": json.dumps(bbox),
        "geometryType": "esriGeometryEnvelope",
        "spatialRel": "esriSpatialRelIntersects",
        "returnGeometry": "false",
        "inSR": in_wkid
    }

    resp = requests.get(query_url, params=params, timeout=60)
    if resp.status_code != 200:
        return []

    data = resp.json()
    tiles = []
    for feat in data.get("features", []):
        attrs = feat.get("attributes") or feat.get("properties")
        if attrs and all(k in attrs for k in ("PATH", "TILE", "EXT")):
            tiles.append((attrs["PATH"], attrs["TILE"], attrs["EXT"]))
    return tiles


def mosaic_rasters_to_array(raster_paths: list[str]):
    """
    Mosaics rasters and returns (mosaic_array, mosaic_transform, mosaic_crs, mosaic_meta)
    """
    srcs = [rasterio.open(p) for p in raster_paths]
    try:
        mosaic, transform = rio_merge(srcs)
        meta = srcs[0].meta.copy()
        meta.update({
            "height": mosaic.shape[1],
            "width": mosaic.shape[2],
            "transform": transform,
            "count": mosaic.shape[0]
        })
        return mosaic, transform, srcs[0].crs, meta
    finally:
        for s in srcs:
            s.close()


def clip_array_with_geojson(mosaic_array, mosaic_transform, mosaic_meta, mosaic_crs, mask_geoms):
    """
    Clip mosaic by polygon mask using rasterio.mask.
    Returns (clipped_array, clipped_transform, clipped_meta)
    """
    # Write mosaic to an in-memory dataset to use rasterio.mask cleanly
    mem_meta = mosaic_meta.copy()
    mem_meta.update({"driver": "GTiff", "crs": mosaic_crs})

    with MemoryFile() as memfile:
        with memfile.open(**mem_meta) as ds:
            ds.write(mosaic_array)
            out_img, out_transform = rio_mask(ds, mask_geoms, crop=True, nodata=ds.nodata)

            out_meta = ds.meta.copy()
            out_meta.update({
                "height": out_img.shape[1],
                "width": out_img.shape[2],
                "transform": out_transform
            })
            return out_img, out_transform, out_meta


def reproject_to_crs(src_array, src_meta, dst_crs_str: str, resampling=Resampling.bilinear):
    """
    Reproject array+meta to dst_crs, return (dst_array, dst_meta).
    """
    src_crs = src_meta["crs"]
    dst_crs = CRS.from_user_input(dst_crs_str)

    transform, width, height = calculate_default_transform(
        src_crs, dst_crs, src_meta["width"], src_meta["height"], *rasterio.transform.array_bounds(
            src_meta["height"], src_meta["width"], src_meta["transform"]
        )
    )

    dst_meta = src_meta.copy()
    dst_meta.update({
        "crs": dst_crs,
        "transform": transform,
        "width": width,
        "height": height
    })

    # Allocate destination
    count = src_meta.get("count", 1)
    dtype = src_meta.get("dtype", "float32")

    import numpy as np
    dst_array = np.zeros((count, height, width), dtype=dtype)

    for band in range(1, count + 1):
        reproject(
            source=src_array[band - 1],
            destination=dst_array[band - 1],
            src_transform=src_meta["transform"],
            src_crs=src_crs,
            dst_transform=transform,
            dst_crs=dst_crs,
            resampling=resampling
        )

    return dst_array, dst_meta


def write_geotiff(out_path: str, array, meta):
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    meta2 = meta.copy()
    meta2.update({"driver": "GTiff"})
    with rasterio.open(out_path, "w", **meta2) as dst:
        dst.write(array)


def zip_outputs(output_paths: list[str], zip_path: str) -> str:
    os.makedirs(os.path.dirname(zip_path), exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for p in output_paths:
            arcname = os.path.basename(p)
            z.write(p, arcname=arcname)
    return zip_path


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

# ----------------------------
# Updated Endpoint
# ----------------------------
@app.post("/download_lidar")
def download_lidar():
    """
    Receives JSON body with:
      [uploaded_shapefile_geojson, ranked_dataset_ids, output_crs, stitch_enabled]

    Implements: tile query -> download/extract -> mosaic -> clip -> reproject -> optional stitch.
    Returns a ZIP containing resulting GeoTIFF(s).
    """
    data = request.get_json(silent=True) or {}
    arr = data.get("data")

    if not isinstance(arr, list) or len(arr) != 4:
        return jsonify({
            "status": "error",
            "message": "Expected JSON {data: [geojson, datasets, output_crs, stitch]}"
        }), 400

    uploaded_geojson, ranked_datasets, output_crs, stitch = arr

    # ---- validation (same spirit as your stub) ----
    if not uploaded_geojson or not isinstance(uploaded_geojson, (dict, list)):
        return jsonify({"status": "error", "message": "Invalid or missing uploaded GeoJSON."}), 400

    if not isinstance(ranked_datasets, list) or len(ranked_datasets) == 0:
        return jsonify({"status": "error", "message": "No datasets provided."}), 400

    if not isinstance(output_crs, str) or not output_crs.upper().startswith("EPSG:"):
        return jsonify({"status": "error", "message": "Output CRS must look like EPSG:####."}), 400

    try:
        mask_geoms, input_crs = parse_geojson_geometry(uploaded_geojson)
    except Exception as e:
        return jsonify({"status": "error", "message": f"GeoJSON parse error: {e}"}), 400

    # ---- job workspace ----
    job_id = str(uuid.uuid4())
    job_dir = os.path.join(BASE_WORK_DIR, f"lidar_job_{job_id}")
    downloads_dir = os.path.join(job_dir, "downloads")
    outputs_dir = os.path.join(job_dir, "outputs")
    os.makedirs(downloads_dir, exist_ok=True)
    os.makedirs(outputs_dir, exist_ok=True)

    try:
        # Get file extensions for datasets (like your template)
        ext_map = get_dataset_ext(ranked_datasets, LIDAR_EXTENTS_FS0)

        produced = []   # per-dataset output tifs

        for dataset in ranked_datasets:
            ds_name = safe_name(dataset)
            ds_download_dir = os.path.join(downloads_dir, ds_name)
            os.makedirs(ds_download_dir, exist_ok=True)

            file_ext = ext_map.get(dataset)
            if not file_ext:
                # If the service doesn't return an ext, try common fallback
                file_ext = ".tif"

            # 1) Tile intersection query
            tiles = get_intersecting_tiles(mask_geoms, TILE_INDEX_MAPSERVER, dataset, in_wkid=4326)
            if not tiles:
                return jsonify({
                    "status": "error",
                    "message": f"No intersecting tiles found for dataset '{dataset}'."
                }), 404

            # 2) Download/extract each tile zip
            for path, tile, ext in tiles:
                # In your template: ftp_url = os.path.join(PATH, f'{TILE}{EXT}')
                tile_url = os.path.join(path, f"{tile}{ext}")
                download_and_extract_zip(tile_url, ds_download_dir)

            # 3) Find extracted rasters
            raster_paths = glob(os.path.join(ds_download_dir, f"*{file_ext}"))
            if not raster_paths:
                # also try tif if ext mismatched
                raster_paths = glob(os.path.join(ds_download_dir, "*.tif")) + glob(os.path.join(ds_download_dir, "*.tiff"))

            if not raster_paths:
                return jsonify({
                    "status": "error",
                    "message": f"No rasters found after download for dataset '{dataset}'."
                }), 500

            # 4) Mosaic
            mosaic_arr, mosaic_transform, mosaic_crs, mosaic_meta = mosaic_rasters_to_array(raster_paths)

            # 5) Clip to polygon mask
            clipped_arr, clipped_transform, clipped_meta = clip_array_with_geojson(
                mosaic_arr, mosaic_transform, mosaic_meta, mosaic_crs, mask_geoms
            )
            clipped_meta.update({"crs": mosaic_crs, "transform": clipped_transform})

            # 6) Reproject to requested output CRS
            reproj_arr, reproj_meta = reproject_to_crs(clipped_arr, clipped_meta, output_crs)

            # 7) Write dataset output GeoTIFF
            out_tif = os.path.join(outputs_dir, f"{ds_name}.tif")
            write_geotiff(out_tif, reproj_arr, reproj_meta)
            produced.append(out_tif)

        # Optional stitch across datasets
        final_outputs = produced
        if bool(stitch) and len(produced) > 1:
            stitched_arr, stitched_transform, stitched_crs, stitched_meta = mosaic_rasters_to_array(produced)
            stitched_meta.update({"crs": stitched_crs, "transform": stitched_transform})
            stitched_path = os.path.join(outputs_dir, "Stitched_DEMs.tif")
            write_geotiff(stitched_path, stitched_arr, stitched_meta)
            final_outputs = [stitched_path]

        # Zip and return
        zip_path = os.path.join(job_dir, "lidar_outputs.zip")
        zip_outputs(final_outputs, zip_path)

        return send_file(
            zip_path,
            mimetype="application/zip",
            as_attachment=True,
            download_name=f"lidar_{job_id}.zip"
        )

    except requests.HTTPError as e:
        return jsonify({"status": "error", "message": f"HTTP error: {e}"}), 502
    except Exception as e:
        return jsonify({"status": "error", "message": f"Processing error: {e}"}), 500

    finally:
        # Optional: cleanup job dir after returning
        # If you want results to persist for later downloads, remove this block.
        try:
            shutil.rmtree(job_dir, ignore_errors=True)
        except Exception:
            pass


# --- Dev server ---
if __name__ == '__main__':
    # For local development only; use a proper WSGI server in production
    app.run(host='0.0.0.0', port=5001, debug=True)

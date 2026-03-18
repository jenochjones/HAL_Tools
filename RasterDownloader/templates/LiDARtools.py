import rasterio
import numpy as np
from shapely.geometry import Point
import geopandas as gpd
from scipy.ndimage import gaussian_gradient_magnitude, laplace
from scipy.spatial import Delaunay
import ezdxf

# =========================
# 1. LOAD DEM
# =========================
def load_dem(dem_path):
    with rasterio.open(dem_path) as src:
        dem = src.read(1)
        transform = src.transform
        nodata = src.nodata
        crs = src.crs
    return dem, transform, nodata, crs


# =========================
# 2. TERRAIN METRICS
# =========================
def compute_terrain_metrics(dem, nodata):
    dem = dem.astype(float)
    dem[dem == nodata] = np.nan

    # Slope proxy (gradient magnitude)
    slope = gaussian_gradient_magnitude(dem, sigma=1)

    # Curvature (detects ridges/valleys)
    curvature = laplace(dem)

    return slope, curvature


# =========================
# 3. ADAPTIVE THINNING
# =========================
def adaptive_thinning(dem, slope, curvature, transform, nodata,
                      slope_weight=0.7,
                      curvature_weight=0.3,
                      keep_percent=0.2,
                      base_grid=5):

    rows, cols = np.where(~np.isnan(dem))

    # Normalize metrics
    slope_norm = (slope - np.nanmin(slope)) / (np.nanmax(slope) - np.nanmin(slope))
    curvature_norm = (np.abs(curvature) - np.nanmin(np.abs(curvature))) / (
        np.nanmax(np.abs(curvature)) - np.nanmin(np.abs(curvature))
    )

    # Combined importance score
    importance = slope_weight * slope_norm + curvature_weight * curvature_norm

    threshold = np.nanpercentile(importance, 100 * (1 - keep_percent))

    points = []
    elevations = []

    for row, col in zip(rows, cols):
        score = importance[row, col]

        if score >= threshold:
            keep = True
        else:
            # Grid thinning for low-importance areas
            keep = (row % base_grid == 0) and (col % base_grid == 0)

        if keep:
            x, y = rasterio.transform.xy(transform, row, col)
            z = dem[row, col]

            points.append((x, y))
            elevations.append(z)

    return np.array(points), np.array(elevations)


# =========================
# 4. BUILD TIN
# =========================
def build_tin(points):
    tri = Delaunay(points)
    return tri


# =========================
# 5. EXPORT DXF (3DFACE)
# =========================
def export_dxf_tin(points, elevations, tri, output_path):
    doc = ezdxf.new()
    msp = doc.modelspace()

    for simplex in tri.simplices:
        pts = []
        for idx in simplex:
            x, y = points[idx]
            z = elevations[idx]
            pts.append((x, y, z))

        # DXF 3DFACE (triangle)
        msp.add_3dface(pts)

    doc.saveas(output_path)


# =========================
# 6. OPTIONAL: EXPORT POINTS
# =========================
def export_points_gpkg(points, elevations, crs, output_path):
    gdf = gpd.GeoDataFrame(
        {"elevation": elevations},
        geometry=[Point(x, y) for x, y in points],
        crs=crs
    )
    gdf.to_file(output_path, driver="GPKG")


# =========================
# MAIN PIPELINE
# =========================
def process_dem_to_tin(
    dem_path,
    output_dxf,
    output_points=None,
    keep_percent=0.2,
    base_grid=5
):
    print("Loading DEM...")
    dem, transform, nodata, crs = load_dem(dem_path)

    print("Computing terrain metrics...")
    slope, curvature = compute_terrain_metrics(dem, nodata)

    print("Adaptive thinning...")
    points, elevations = adaptive_thinning(
        dem,
        slope,
        curvature,
        transform,
        nodata,
        keep_percent=keep_percent,
        base_grid=base_grid
    )

    print(f"Points retained: {len(points)}")

    print("Building TIN...")
    tri = build_tin(points)

    print("Exporting DXF...")
    export_dxf_tin(points, elevations, tri, output_dxf)

    if output_points:
        print("Exporting points...")
        export_points_gpkg(points, elevations, crs, output_points)

    print("Done.")


# =========================
# RUN
# =========================
if __name__ == "__main__":
    process_dem_to_tin(
        dem_path="dem.tif",
        output_dxf="terrain_tin.dxf",
        output_points="terrain_points.gpkg",
        keep_percent=0.25,   # adjust density
        base_grid=6          # thinning aggressiveness
    )
import requests
import geopandas as gpd
import zipfile
import os
import rasterio
from rasterio.mask import mask
from rasterio.merge import merge
from rasterio.warp import calculate_default_transform, reproject, Resampling


def reproject_raster(masked_filepath, crs, output_dataset, units):
    # Open the input raster file
    with rasterio.open(masked_filepath) as src:       
        # Reproject to the target CRS
        transform, width, height = calculate_default_transform(
            src.crs, {'init': crs}, src.width, src.height, *src.bounds)
       
        kwargs = src.meta.copy()
        kwargs.update({
            'crs': crs,
            'transform': transform,
            'width': width,
            'height': height
        })
        
        # Create the output raster file
        with rasterio.open(output_dataset, 'w', **kwargs) as dst:
            for i in range(1, src.count + 1):
                reproject(
                    source=rasterio.band(src, i),
                    destination=rasterio.band(dst, i),
                    src_transform=src.transform,
                    src_crs=src.crs,
                    dst_transform=transform,
                    dst_crs={'init': crs},
                    resampling=Resampling.nearest)
    
    print(f'Reprojected raster and saved to {output_dataset}')
    

def mask_raster(mask_gdf, mosaic_filepath, masked_filepath):
    try:
        # Open the raster file
        with rasterio.open(mosaic_filepath) as src:
            # Reproject mask_gdf to match the CRS of the raster
            mask_gdf = mask_gdf.to_crs(src.crs)
            
            # Mask the raster with the polygon geometries
            masked_image, masked_transform = mask(src, mask_gdf.geometry, crop=True)
            masked_meta = src.meta.copy()

        # Update metadata for the masked raster
        masked_meta.update({
            'height': masked_image.shape[1],
            'width': masked_image.shape[2],
            'transform': masked_transform
        })

        # Save the masked raster to a new file
        with rasterio.open(masked_filepath, 'w', **masked_meta) as dst:
            dst.write(masked_image)
            
        if os.path.exists(mosaic_filepath):
            os.remove(mosaic_filepath)

        return True
    
    except Exception as e:
        print(f"Error masking raster: {e}")
        return False


def mosaic_rasters(downloads_folder, mosaic_filepath):
    raster_extensions = ['.tif', '.tiff', '.asc', '.bil', '.bsq', '.bip', '.jpg', '.jpeg', '.png', '.gif', '.img', '.vrt']
    img_files = [os.path.join(downloads_folder, f) for f in os.listdir(downloads_folder) if os.path.splitext(f)[1].lower() in raster_extensions]
    
    if len(img_files) == 0:
        print("No files found in the downloads folder.")
        exit()
    
    print(f"Found {len(img_files)} files in the folder {downloads_folder}.")
    
    # Open each raster file
    src_files_to_mosaic = []
    for file in img_files:
        src = rasterio.open(file)
        src_files_to_mosaic.append(src)
    
    # Merge rasters
    mosaic, out_trans = merge(src_files_to_mosaic)
    
    # Copy the metadata of the first raster
    out_meta = src.meta.copy()
    
    # Update the metadata
    out_meta.update({"driver": "ENVI",
                     "height": mosaic.shape[1],
                     "width": mosaic.shape[2],
                     "transform": out_trans})
    
    with rasterio.open(mosaic_filepath, 'w', **out_meta) as dst:
        dst.write(mosaic)  # Write the data to the first band

    return True


def download_raster_image(url, save_filepath):
    
    file_name = os.path.basename(url)
    zip_path = os.path.join(save_filepath, file_name)
        
    try:
        # Send a GET request to the URL
        response = requests.get(url, stream=True)
        response.raise_for_status()  # Check if the request was successful
        if response.status_code == 200:
            print(f'Downloaded {url}')
    
        # Open the save_path in write-binary mode and write the content of the response to it
        with open(zip_path, 'wb') as file:
            for chunk in response.iter_content(chunk_size=8192):
                file.write(chunk)
    
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(save_filepath)
            
        os.remove(zip_path)
            
    except requests.exceptions.RequestException as e:
        print(f"Failed to download file: {e}")
 


def get_layer_id(mapserv_url, layer_name):
    mapserv_url = f'{mapserv_url}?f=json'
    # Send the GET request to the API
    response = requests.get(mapserv_url)

    # Check if the request was successful
    if response.status_code == 200:
        # Get the JSON data from the response
        data = response.json()

        # Extract the layers and their IDs
        layers = data.get('layers', [])
        
        for layer in layers:
            if layer['name'] == layer_name:
                layer_id = layer['id']
        
        return layer_id
    else:
        print(f"Error: Unable to retrieve data. HTTP Status Code: {response.status_code}")
    


def get_intersecting_tiles(mask_gdf, tile_index_url, tile_group):    
    layer_id = get_layer_id(tile_index_url, tile_group)
    
    tile_index_url = os.path.join(tile_index_url, str(layer_id), 'query')

    # Define the parameters for the query
    params = {
        "f": "geojson",  # Specify the response format as GeoJSON
        "where": "1=1",  # Select all features
        "outFields": "*",  # Retrieve all fields
        "geometryType": "esriGeometryPolygon",
        "spatialRel": "esriSpatialRelIntersects",
        "returnGeometry": "true",
        "maxRecordCount": 1000
    }

    # Send the GET request to the API
    response = requests.get(tile_index_url, params=params)

    # Check if the request was successful
    if response.status_code == 200:
        # Get the GeoJSON data from the response
        tile_json = response.json()
        tile_index = gpd.GeoDataFrame.from_features(tile_json)
        tile_index.set_crs(epsg=tile_json['crs']['properties']['name'].replace('EPSG:',''), inplace=True)
    else:
        print(f"Error: Unable to retrieve data. HTTP Status Code: {response.status_code}")
    
    # Ensure both GeoDataFrames use the same coordinate reference system (CRS)
    if mask_gdf.crs != tile_index.crs:
        mask_gdf = mask_gdf.to_crs(tile_index.crs)

    intersecting_tiles = []
    
    # Iterate over each feature in the mask
    for mask_feature in mask_gdf.itertuples():
        mask_geometry = mask_feature.geometry
        
        # Check intersection with each feature in tile_index
        for tile_feature in tile_index.itertuples():
            tile_geometry = tile_feature.geometry
            
            if mask_geometry.intersects(tile_geometry):
                intersecting_tiles.append((tile_feature.PATH, tile_feature.TILE, tile_feature.EXT))
    
    return intersecting_tiles


# Function to fetch features overlapping with a given shapefile
def get_products(gdf_feature_class, arcgis_url, dem_type):
    # Convert geometry to JSON format
    bounds = gdf_feature_class.bounds.iloc[0]
    minx = bounds['minx']
    miny = bounds['miny']
    maxx = bounds['maxx']
    maxy = bounds['maxy']

    # Construct the URL for querying overlapping features
    query_url = f"{arcgis_url}/query"

    # Parameters for the query
    params = {
        'geometryType': 'esriGeometryEnvelope',  # Changed to envelope for bounds
        'inSR': gdf_feature_class.crs.to_epsg(),  # Assuming CRS has EPSG code
        'spatialRel': 'esriSpatialRelIntersects',
        'geometry': f'{minx},{miny},{maxx},{maxy}',
        'outFields': 'FTP_Path,Tile_Index,Product',
        'returnGeometry': False,
        'f': 'json'  # Response format
    }

    # Make the request
    response = requests.get(query_url, params=params)
    response.raise_for_status()

    data = response.json()
    features = data.get('features', [])
    list_of_paths = [feature['attributes']['Tile_Index'] for feature in features if feature['attributes']['Product'] == dem_type]

    return list_of_paths

# Example usage
if __name__ == "__main__":
    
    # URL of the ArcGIS REST service
    url = 'https://services1.arcgis.com/99lidPhWCzftIe9K/ArcGIS/rest/services/LiDAR_Extents/FeatureServer/0'
    tile_index_url = 'https://mapserv.utah.gov/arcgis/rest/services/Raster/MapServer/'

    # Parameters
    feature = '/Users/jonjones/Sites/GIS/Mask.shp'
    save_filetype = '.img'
    output_folder = '/Users/jonjones/Sites/GIS/Final'
    downloads_folder = '/Users/jonjones/Sites/GIS/Downloads'
    units = 'Feet'
    dem_type = 'Bare Earth DEM'
    sr = 'EPSG: 3566'
    
    gdb_path = ''
    
    if save_filetype =='ESRI Grid':
        save_filetype = ''
    
    
    if '.shp' in feature:
        # Load shapefile using geopandas
        mask_gdf = gpd.read_file(feature)
    else:
        mask_gdf = gpd.read_file(f'{gdb_path}', layer=feature)
    
    # Fetch overlapping features
    products = get_products(mask_gdf, url, dem_type)
    
    print(f'{len(products)} datasets found.')
    
    for dataset in products:
        print(f'Downloading {dataset}...')
        output_filename = dataset
        
        final_filepath = os.path.join(output_folder, f'{output_filename}{save_filetype}')
        mosaic_filepath = os.path.join(output_folder, f'{output_filename}_mosaic{save_filetype}')
        masked_filepath = os.path.join(output_folder, f'{output_filename}_masked{save_filetype}')
        
        tile_list = get_intersecting_tiles(mask_gdf, tile_index_url, dataset)
        
        if not os.path.exists(os.path.join(downloads_folder, dataset)):
            os.makedirs(os.path.join(downloads_folder, dataset))
        
        for tile in tile_list:
            ftp_url = os.path.join(tile[0], f'{tile[1]}{tile[2]}')
            download_raster_image(ftp_url, os.path.join(downloads_folder, dataset))
        
        mosaiced = mosaic_rasters(os.path.join(downloads_folder, dataset), mosaic_filepath)
        masked = mask_raster(mask_gdf, mosaic_filepath, masked_filepath)
        reproject_raster(masked_filepath, sr, final_filepath, units)

        print(f"Masked raster saved to: {final_filepath}")

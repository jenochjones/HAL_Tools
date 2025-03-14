import requests
import geopandas as gpd
import zipfile
import os
import rasterio
import tkinter as tk
from rasterio.mask import mask
from rasterio.merge import merge
from rasterio.warp import calculate_default_transform, reproject, Resampling
from tkinter import filedialog, ttk



# Function to fetch features overlapping with a given shapefile
def get_products(shp_path):
    
    gdf_feature_class = gpd.read_file(shp_path)
    
    # Convert geometry to JSON format
    bounds = gdf_feature_class.bounds.iloc[0]
    minx = bounds['minx']
    miny = bounds['miny']
    maxx = bounds['maxx']
    maxy = bounds['maxy']

    # Construct the URL for querying overlapping features
    arcgis_url = 'https://services1.arcgis.com/99lidPhWCzftIe9K/ArcGIS/rest/services/LiDAR_Extents/FeatureServer/0'
    
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
    list_of_paths = [feature['attributes']['Tile_Index'] for feature in features]
    return list_of_paths


def get_gis_parameters():
    def browse_file(entry, filetypes):
        filepath = filedialog.askopenfilename(filetypes=filetypes)
        if filepath:
            entry.delete(0, tk.END)
            entry.insert(0, filepath)
            if entry == feature_entry:
                update_dem_types(filepath)

    def browse_folder(entry):
        folderpath = filedialog.askdirectory()
        if folderpath:
            entry.delete(0, tk.END)
            entry.insert(0, folderpath)

    def update_dem_types(shapefile_path):
        if shapefile_path:
            dem_options = get_products(shapefile_path)
            dem_type_listbox.delete(0, tk.END)
            for option in dem_options:
                dem_type_listbox.insert(tk.END, option)

    def submit():
        nonlocal parameters
        selected_indices = dem_type_listbox.curselection()
        selected_dem_types = [dem_type_listbox.get(i) for i in selected_indices]

        parameters = {
            'feature': feature_var.get(),
            'save_filetype': save_filetype_var.get(),
            'output_folder': output_folder_var.get(),
            'downloads_folder': downloads_folder_var.get(),
            'units': units_var.get(),
            'products': selected_dem_types,
            'sr': sr_var.get()
        }
        root.quit()

    root = tk.Tk()
    root.title("GIS Parameter Selection")
    root.geometry("500x500")
    root.configure(bg='lightgray')

    frame = ttk.Frame(root, padding=10)
    frame.pack(fill=tk.BOTH, expand=True)

    def create_input_row(label_text, text_variable=None, browse_command=None, values=None, default_value=None):
        row = ttk.Frame(frame)
        row.pack(fill=tk.X, pady=2)
        ttk.Label(row, text=label_text, width=20).pack(side=tk.LEFT)
        if values:
            entry = ttk.Combobox(row, textvariable=text_variable, values=values)
            entry.current(0)
        else:
            entry = ttk.Entry(row, textvariable=text_variable, width=40)
            if default_value is not None:
                text_variable.set(default_value)
        entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        if browse_command:
            ttk.Button(row, text="Browse", command=lambda e=entry: browse_command(e)).pack(side=tk.RIGHT)
        return entry
    
    feature_var = tk.StringVar()
    feature_entry = create_input_row("Feature (Shapefile):", feature_var, lambda e: browse_file(e, [("Shapefiles", "*.shp")]))
    
    save_filetype_var = tk.StringVar()
    create_input_row("Save Filetype:", save_filetype_var, values=['.tif', '.jpg', '.png'])
    
    output_folder_var = tk.StringVar()
    create_input_row("Save Folder:", output_folder_var, browse_folder)
    
    downloads_folder_var = tk.StringVar()
    create_input_row("Downloads Folder:", downloads_folder_var, browse_folder)
    
    units_var = tk.StringVar()
    create_input_row("Units:", units_var, values=['Feet', 'Meters'])
    
    sr_var = tk.StringVar()
    create_input_row("Coordinate System (EPSG Number):", sr_var, default_value="3566")


    ttk.Label(frame, text="Products:").pack(anchor=tk.W)
    dem_type_listbox = tk.Listbox(frame, selectmode=tk.MULTIPLE, height=5, exportselection=0)
    dem_type_listbox.pack(fill=tk.X)

    ttk.Button(frame, text="Submit", command=submit).pack(pady=10)

    parameters = None

    root.mainloop()
    root.destroy()

    return parameters


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
    
    tile_index_url = f'{tile_index_url}{str(layer_id)}/query'

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
        exit()
    
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



# Example usage
if __name__ == "__main__":
    
    # URL of the ArcGIS REST service
    url = 'https://services1.arcgis.com/99lidPhWCzftIe9K/ArcGIS/rest/services/LiDAR_Extents/FeatureServer/0'
    tile_index_url = 'https://mapserv.utah.gov/arcgis/rest/services/Raster/MapServer/'
    
    # Example usage:
    params = get_gis_parameters()
    print(params)
    
    # Parameters
    feature = params['feature']
    save_filetype = params['save_filetype']
    output_folder = params['output_folder']
    downloads_folder = params['downloads_folder']
    units = params['units']
    products = params['products']
    sr = f"EPSG:{params['sr']}"
    
    for dataset in products:
        print(f'Downloading {dataset}...')
        output_filename = dataset
        
        mask_gdf = gpd.read_file(feature)
        
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
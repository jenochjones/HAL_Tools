# -*- coding: utf-8 -*-
"""
Created on Mon Jul  8 08:30:52 2024

@author: ejones
"""

import arcpy
import requests
import zipfile
import os
import json
from glob import glob
from urllib.parse import urljoin

def remove_all_files(filepath):
    if os.path.exists(filepath):
        all_files = glob(os.path.basename(filepath) + '*')
        for file in all_files:
            os.remove(file)

def download_raster_image(url, save_filepath):
    file_name = os.path.basename(url)
    zip_path = os.path.join(save_filepath, file_name)
    try:
        response = requests.get(url, stream=True)
        response.raise_for_status()
        if response.status_code == 200:
            arcpy.AddMessage(f'Downloaded {url}')
        with open(zip_path, 'wb') as file:
            for chunk in response.iter_content(chunk_size=8192):
                file.write(chunk)
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(save_filepath)
        os.remove(zip_path)
    except requests.exceptions.RequestException as e:
        arcpy.AddError(f"Failed to download file: {e}")

def get_layer_id(mapserv_url, layer_name):
    mapserv_url = f'{mapserv_url}?f=json'
    response = requests.get(mapserv_url)
    if response.status_code == 200:
        data = response.json()
        layers = data.get('layers', [])
        for layer in layers:
            if layer['name'] == layer_name:
                return layer['id']
    else:
        arcpy.AddError(f"Error: Unable to retrieve data. HTTP Status Code: {response.status_code}")

def get_intersecting_tiles(mask_fc, tile_index_url, tile_group, download_folder):
    layer_id = get_layer_id(tile_index_url, tile_group)
    tile_index_url = urljoin(tile_index_url, f'{str(layer_id)}/query')
    params = {
        "f": "geojson",
        "where": "1=1",
        "outFields": "*",
        "geometryType": "esriGeometryPolygon",
        "spatialRel": "esriSpatialRelIntersects",
        "returnGeometry": "true",
        "maxRecordCount": 1000
    }
    response = requests.get(tile_index_url, params=params)
    if response.status_code == 200:
        tile_json = response.json()
        with open(os.path.join(download_folder, 'output.geojson'), 'w') as f:
            json.dump(tile_json, f)
        arcpy.conversion.JSONToFeatures(os.path.join(download_folder, 'output.geojson'), os.path.join(arcpy.env.workspace, 'tile_index'))
        os.remove(os.path.join(download_folder, 'output.geojson'))
    else:
        arcpy.AddError(f"Error: Unable to retrieve data. HTTP Status Code: {response.status_code}")
    
    intersecting_tiles = []
    arcpy.analysis.Intersect([os.path.join(arcpy.env.workspace, 'tile_index'), mask_fc], os.path.join(arcpy.env.workspace, 'tile_index_masked'))
    with arcpy.da.SearchCursor(os.path.join(arcpy.env.workspace, 'tile_index_masked'), ['PATH', 'TILE', 'EXT']) as cursor:
        for row in cursor:
            intersecting_tiles.append((row[0], row[1], row[2]))
            arcpy.AddMessage(row[1])
    return intersecting_tiles

def get_products(feature_class, arcgis_url, dem_type):
    extent = arcpy.Describe(feature_class).extent
    bounds = [extent.XMin, extent.YMin, extent.XMax, extent.YMax]
    query_url = f"{arcgis_url}/query"
    params = {
        'geometryType': 'esriGeometryEnvelope',
        'inSR': arcpy.Describe(feature_class).spatialReference.factoryCode,
        'spatialRel': 'esriSpatialRelIntersects',
        'geometry': f'{bounds[0]},{bounds[1]},{bounds[2]},{bounds[3]}',
        'outFields': 'Tile_Index,Product,File_Extension',
        'returnGeometry': False,
        'f': 'json'
    }
    response = requests.get(query_url, params=params)
    response.raise_for_status()
    data = response.json()
    features = data.get('features', [])
    list_of_paths = [(feature['attributes']['Tile_Index'], feature['attributes']['File_Extension']) for feature in features if feature['attributes']['Product'] == dem_type]
    arcpy.AddMessage(list_of_paths)
    return list_of_paths

if __name__ == "__main__":
    url = 'https://services1.arcgis.com/99lidPhWCzftIe9K/ArcGIS/rest/services/LiDAR_Extents/FeatureServer/0'
    tile_index_url = 'https://mapserv.utah.gov/arcgis/rest/services/Raster/MapServer/'
    arcpy.AddMessage('started')
    feature = arcpy.GetParameterAsText(0)
    output_folder = arcpy.GetParameterAsText(1)
    save_filetype = arcpy.GetParameterAsText(2)
    downloads_folder = arcpy.GetParameterAsText(3)
    units = arcpy.GetParameterAsText(4)
    dem_type = arcpy.GetParameterAsText(5)
    sr = arcpy.GetParameter(6)
    arcpy.AddMessage(sr)
    arcpy.CheckOutExtension("Spatial")
    if save_filetype == 'ESRI Grid':
        save_filetype = ''
    if not os.path.isfile(feature):
        workspace = arcpy.env.workspace
        mask_fc = os.path.join(workspace, feature)
    else:
        mask_fc = feature
    products = get_products(mask_fc, url, dem_type)
    if len(products) <= 0:
        arcpy.AddError("No products found")
        exit
    arcpy.AddMessage(f'{len(products)} datasets found.')
    for dataset in products:
        arcpy.AddMessage(f'Downloading {dataset}...')
        output_filename = dataset[0]
        filename_extension = dataset[1]
        final_filepath = os.path.join(output_folder, f'{output_filename}{save_filetype}')
        mosaic_filepath = os.path.join(arcpy.env.workspace, f'{output_filename}_mosaic')
        masked_filepath = os.path.join(arcpy.env.workspace, f'{output_filename}_masked')
        unit_filepath = os.path.join(arcpy.env.workspace, f'{output_filename}_unit')
        tile_list = get_intersecting_tiles(mask_fc, tile_index_url, dataset[0], downloads_folder)
        arcpy.AddMessage(tile_list)
        if len(tile_list) <= 0:
            arcpy.AddError("No tiles found")
        
        if not os.path.exists(os.path.join(downloads_folder, dataset[0])):
            os.makedirs(os.path.join(downloads_folder, dataset[0]))
        
        for tile in tile_list:
            ftp_url = os.path.join(tile[0], f'{tile[1]}{tile[2]}')
            download_raster_image(ftp_url, os.path.join(downloads_folder, dataset[0]))
        arcpy.AddMessage('Getting img rasters...')
        
        img_rasters = glob(os.path.join(downloads_folder, dataset[0], f'*{filename_extension}'))
        if len(img_rasters) <= 0:
            arcpy.AddError("No rasters found")
            continue
        else:
            arcpy.management.MosaicToNewRaster(
                img_rasters,
                os.path.dirname(mosaic_filepath),
                os.path.basename(mosaic_filepath),
                coordinate_system_for_the_raster=sr,
                pixel_type='32_BIT_FLOAT',
                cellsize=None,
                number_of_bands=1,
                mosaic_method='MEAN',
                mosaic_colormap_mode='FIRST'
            )
            out_extract_by_mask = arcpy.sa.ExtractByMask(mosaic_filepath, mask_fc)
            out_extract_by_mask.save(masked_filepath)
            if units == 'Feet':
                unit_raster = arcpy.sa.Times(masked_filepath, 3.28084)
                unit_raster.save(unit_filepath)
            else:
                unit_raster = arcpy.sa.Times(masked_filepath, 1)
                unit_raster.save(unit_filepath)
            
            arcpy.management.CopyRaster(unit_filepath, final_filepath)
            arcpy.management.Delete(mosaic_filepath)
            arcpy.management.Delete(masked_filepath)
            arcpy.management.Delete(unit_filepath)
            
            aprx = arcpy.mp.ArcGISProject("CURRENT")
            map = aprx.activeMap
            map.addDataFromPath(final_filepath)
            arcpy.AddMessage(f'Final raster saved to: {final_filepath}')
    arcpy.CheckInExtension("Spatial")

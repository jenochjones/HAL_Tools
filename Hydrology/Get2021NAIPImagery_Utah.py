# -*- coding: utf-8 -*-
"""
Created on Thu Dec  5 22:00:16 2024

@author: ejones
"""

import geopandas as gpd
import os
import glob
import requests
import zipfile
import rasterio

from osgeo import gdal
from rasterio.merge import merge
from rasterio.mask import mask
from rasterio.crs import CRS
from shapely.geometry import mapping

import tkinter as tk
from tkinter import filedialog
from tkinter import messagebox

# Create a Tkinter root window
root = tk.Tk()
root.withdraw()  # Hide the root window

# Function to get the shapefile path
def get_shapefile_path():
    file_path = filedialog.askopenfilename(
        title="Select a Shapefile",
        filetypes=[("Shapefiles", "*.shp")],
    )
    if not file_path:
        messagebox.showerror("Error", "No shapefile selected.")
        return None
    return file_path

# Function to get the save folder path
def get_save_folder_path():
    folder_path = filedialog.askdirectory(
        title="Select a Save Folder"
    )
    if not folder_path:
        messagebox.showerror("Error", "No folder selected.")
        return None
    return folder_path


def mask_tif_with_shapefile(tif_path, shapefile_path):
    """
    Reprojects a shapefile to match the CRS of a TIFF file, masks the TIFF with the shapefile's polygons, 
    and saves the masked TIFF.

    Parameters:
        tif_path (str): Path to the input TIFF file.
        shapefile_path (str): Path to the shapefile.
        output_tif_path (str): Path to save the masked TIFF file.

    Returns:
        None
    """
    
    base, ext = os.path.splitext(tif_path)
    output_tif_path = f'{base}_Masked{ext}'
    
    # Open the TIFF file to get its CRS
    with rasterio.open(tif_path) as src:
        tif_crs = src.crs
        tif_meta = src.meta

    # Read the shapefile
    shapefile = gpd.read_file(shapefile_path)

    # Reproject the shapefile to match the TIFF CRS
    if shapefile.crs != tif_crs:
        shapefile = shapefile.to_crs(tif_crs)

    # Extract the geometries from the shapefile
    shapes = [mapping(geom) for geom in shapefile.geometry]

    # Open the TIFF file again to apply the mask
    with rasterio.open(tif_path) as src:
        masked_image, masked_transform = mask(src, shapes, crop=True)

    # Update the metadata for the output file
    tif_meta.update({
        "driver": "GTiff",
        "height": masked_image.shape[1],
        "width": masked_image.shape[2],
        "transform": masked_transform
    })

    # Save the masked TIFF
    with rasterio.open(output_tif_path, "w", **tif_meta) as dest:
        dest.write(masked_image)

# Example usage
# mask_tif_with_shapefile("path_to_input.tif", "path_to_input.shp", "path_to_output.tif")



def add_band_to_tif(input_files, shp_filepath, save_folder):
    """
    Adds the second TIFF image as a new band to the first TIFF image.
    Saves the resulting image and deletes the original input files.

    Args:
        input_files (list): List of two file paths to the input TIFF files.
        output_file (str): File path for the output TIFF file.
    """
    
    tif_filename = os.path.basename(shp_filepath).replace('.shp', '.tif')
    output_file = os.path.join(save_folder, tif_filename)
    
    if len(input_files) != 2:
        raise ValueError("Provide exactly two TIFF file paths in the input list.")

    # Read the first and second TIFF files
    with rasterio.open(input_files[0]) as src1, rasterio.open(input_files[1]) as src2:
        # Ensure both TIFF files have the same shape and transform
        if src1.width != src2.width or src1.height != src2.height or src1.transform != src2.transform:
            raise ValueError("Input TIFF files must have the same spatial resolution, extent, and projection.")

        # Read the data from both files
        data1 = src1.read()
        data2 = src2.read(1)  # Read the first band of the second file

        # Stack the bands (add the new band to the existing bands)
        combined_data = list(data1) + [data2]

        # Create a new TIFF with an updated number of bands
        new_meta = src1.meta.copy()
        new_meta.update(count=len(combined_data))

        # Write the new TIFF file
        with rasterio.open(output_file, 'w', **new_meta) as dst:
            for i, band in enumerate(combined_data, start=1):
                dst.write(band, i)

    # Remove the original files
    for file in input_files:
        os.remove(file)

    print(f"New file created: {output_file}")
    return output_file


def remove_files_with_same_name(filepath):
    # Extract the directory and base name without the extension
    directory = os.path.dirname(filepath)
    base_name = os.path.splitext(os.path.basename(filepath))[0]

    # Construct the pattern to match all files with the same name
    pattern = os.path.join(directory, f"{base_name}.*")

    # Find and remove all matching files
    for file in glob.glob(pattern):
        try:
            os.remove(file)
            print(f"Deleted file: {file}")
        except OSError as e:
            print(f"Error deleting file {file}: {e}")



def merge_tifs(tif_filepaths, save_folder, shp_filepath, ids):
    """
    Combines georeferenced .tif files into one file, saves it, and removes individual tif files.

    Parameters:
    tif_filepaths (list of str): List of file paths to the .tif files to be combined.
    output_filepath (str): Path where the combined .tif file will be saved.

    Returns:
    str: Path to the merged output file.
    """
    ext = 'NIR' if ids == 'TILE_B4' else 'RGB'
    tif_filename = os.path.basename(shp_filepath).replace('.shp', f'_{ext}.tif')
    output_filepath = os.path.join(save_folder, tif_filename)
    
    if not tif_filepaths:
        raise ValueError("The list of input file paths is empty.")
    
    # Use gdal.Warp to merge the .tif files
    print("Merging TIFF files...")
    vrt = gdal.BuildVRT("temp.vrt", tif_filepaths)  # Create a virtual dataset
    merged = gdal.Translate(output_filepath, vrt)  # Convert virtual dataset to .tif
    vrt = None  # Clean up the VRT in memory
    merged = None  # Clean up the merged dataset

    # Remove individual TIFF files
    print("Deleting individual TIFF files...")
    for filepath in tif_filepaths:
        remove_files_with_same_name(filepath)

    # Optionally, clean up the temporary VRT file
    if os.path.exists("temp.vrt"):
        os.remove("temp.vrt")

    print(f"Merged file saved at: {output_filepath}")
    return output_filepath


def find_intersecting_features(shapefile1, shapefile2, id_field):
    """
    Load two shapefiles, find the polygons in the second shapefile that
    intersect with the first shapefile's polygon, and return the IDs of the intersected features.

    Args:
        shapefile1 (str): Path to the first shapefile (containing a single polygon feature).
        shapefile2 (str): Path to the second shapefile (containing multiple polygon features).
        id_field (str): Name of the field to extract ID values from intersected features.

    Returns:
        list: A list of IDs from the intersecting features in the second shapefile.
    """
    # Load the shapefiles
    gdf1 = gpd.read_file(shapefile1)
    gdf2 = gpd.read_file(shapefile2)

    # Ensure both GeoDataFrames are using the same CRS
    if gdf1.crs != gdf2.crs:
        gdf2 = gdf2.to_crs(gdf1.crs)

    # Check if the first shapefile contains only one feature
    if len(gdf1) != 1:
        raise ValueError("The first shapefile must contain exactly one polygon feature.")

    # Extract the single polygon from the first shapefile
    polygon1 = gdf1.iloc[0].geometry

    # Find intersecting features in the second shapefile
    intersecting_features = gdf2[gdf2.geometry.intersects(polygon1)]

    # Extract and return the IDs from the intersecting features
    return intersecting_features[id_field].tolist()


# Function to download the file
def download_file(tif_id, dest_path):
    url = f'https://storage.googleapis.com/state-of-utah-sgid-downloads/aerial-photography/naip/naip2021/{tif_id}.zip'
    save_file = f'{dest_path}/{tif_id}.zip'
    print("Starting download...")
    response = requests.get(url, stream=True)
    response.raise_for_status()  # Raise an error for bad responses
    with open(save_file, "wb") as file:
        for chunk in response.iter_content(chunk_size=8192):
            file.write(chunk)
    print(f"Download complete: {dest_path}")
    return save_file

# Function to extract the ZIP file
def extract_zip(zip_path, extract_to):
    print("Extracting files...")
    with zipfile.ZipFile(zip_path, "r") as zip_ref:
        zip_ref.extractall(extract_to)
    print(f"Files extracted to: {extract_to}")
    
    # Remove the ZIP file after extraction
    try:
        os.remove(zip_path)
        print(f"ZIP file '{zip_path}' has been removed.")
    except OSError as e:
        print(f"Error while deleting ZIP file '{zip_path}': {e}")
    


# Get shapefile and save folder paths from the user
shapefile1_path = get_shapefile_path()
save_folder = get_save_folder_path()

shapefile2_path = 'Data/NAIP2021_TileIndex.shp'

# ID field in the second shapefile
id_field_names = ['TILE_RGB', 'TILE_B4']

file_paths = []

for ids in id_field_names:
    # Call the function
    group_paths = []
    intersected_ids = find_intersecting_features(shapefile1_path, shapefile2_path, ids)
    for tif_id in intersected_ids:
        save_file = download_file(tif_id, save_folder)
        extract_zip(save_file, save_folder)
        group_paths.append(save_file.replace('.zip', '.tif'))
        
    merged_tif_path = merge_tifs(group_paths, save_folder, shapefile1_path, ids)
    file_paths.append(merged_tif_path)
    
comb_tif_path = add_band_to_tif(file_paths, shapefile1_path, save_folder)
mask_tif_with_shapefile(comb_tif_path, shapefile1_path)


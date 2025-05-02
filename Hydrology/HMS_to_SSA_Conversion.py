import pandas as pd
import numpy as np
import tkinter as tk
from tkinter import filedialog, ttk
import geopandas as gpd
from shapely.geometry import LineString
import os
import re
from pydsstools.heclib.dss import HecDss


def select_run_value(unique_run_values):
    # Initialize the Tkinter root window
    root = tk.Tk()
    root.withdraw()  # Hide the root window

    # Create a new top-level dialog window for selection
    dialog = tk.Toplevel(root)
    dialog.title("Select HMS Run")

    # Variable to store selected value
    selected_value = tk.StringVar()
    selected_value.set(unique_run_values[0])  # Set the default selection to the first item

    # Label and dropdown (ComboBox) for selecting HMS Run
    tk.Label(dialog, text="Select the HMS Run:").pack(pady=5)
    dropdown = ttk.Combobox(dialog, values=unique_run_values, textvariable=selected_value)
    dropdown.pack(pady=5)
    
    # Confirm button
    def on_confirm():
        dialog.destroy()
    
    tk.Button(dialog, text="OK", command=on_confirm).pack(pady=10)

    # Run the dialog and wait for it to close
    root.wait_window(dialog)

    return selected_value.get()


# Open file dialog to select files
def get_file_path(file_type, ext):
    root = tk.Tk()
    root.withdraw()  # Hide the main window
    file_path = filedialog.askopenfilename(title=file_type, filetypes=[(f"{file_type} files", ext)])
    return file_path

# Formatting functions
def format_timeseries(df):
    timeseries_str = "[TIMESERIES]\n"
    timeseries_str += ";;Name             Date       Time       Value     \n"
    timeseries_str += ";;-------------------------------------------------\n"
    for name, series in df.items():
        timeseries_str += f"\n;;Timeseries for {name} from HMS\n"
        for timestamp, value in series.items():
            date = timestamp.strftime('%m/%d/%Y')
            time = timestamp.strftime('%H:%M')
            timeseries_str += f"{name:<18} {date} {time}      {value:<10.5f}\n"
    timeseries_str += "\n"
    return timeseries_str

def format_inflows(ssa_ids):
    inflow_str = "[INFLOWS]\n"
    inflow_str += ";;                                                 Param    Units    Scale    Baseline Baseline\n"
    inflow_str += ";;Node           Parameter        Time Series      Type     Factor   Factor   Value    Pattern\n"
    inflow_str += ";;-------------- ---------------- ---------------- -------- -------- -------- -------- --------\n"
    for ssa_id in ssa_ids:
        inflow_str += f"{ssa_id:<17}FLOW             {ssa_id}_ts\n"
    inflow_str += "\n"
    return inflow_str

def replace_timeseries_in_inp(inp_file_path, df, ssa_ids):
    with open(inp_file_path, 'r', encoding='ISO-8859-1') as file:
        inp_content = file.readlines()

    timeseries_found = False
    inflows_found = False
    
    for i, line in enumerate(inp_content):
        if line.strip().startswith("[TIMESERIES]"):
            timeseries_found = True
        elif line.strip().startswith("[INFLOWS]"):
            inflows_found = True
    
    
    in_section = False
    start_1_index, end_1_index, start_2_index, end_2_index = None, None, None, None
    for i, line in enumerate(inp_content):
        if line.strip().startswith("[INFLOWS]") or line.strip().startswith("[TIMESERIES]"):
            if in_section:
                end_1_index = i
            if start_1_index is None:
                start_1_index = i
            else:
                start_2_index = i
            in_section = True
        elif line.strip().startswith("[") and in_section:
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
        updated_content = inp_content + ["\n"] + [inflow_section] + [timeseries_section]
    else:
        updated_content = inp_content[:start_1_index] + [inflow_section] + inp_content[end_1_index:start_2_index] + [timeseries_section] + inp_content[end_2_index:]

    output_inp_file = os.path.splitext(inp_file_path)[0] + "_Updated.inp"
    with open(output_inp_file, 'w', encoding='ISO-8859-1') as file:
        file.writelines(updated_content)
    print(f"Timeseries data successfully updated in the INP file: {output_inp_file}")
    
    return updated_content


def create_shapefile(shp_input_filepath, inp_string, mapping_dict):
    start = None
    end = None

    for i, line in enumerate(inp_string):
        if line.strip().startswith("[COORDINATES]"):
            start = i + 1
        elif line.strip().startswith("[") and start is not None:
            end = i
            break

    if start is not None and end is None:
        end = len(inp_string)

    coords = inp_string[start:end] if start is not None else []
    
    data = []
    for line in coords:
        line = line.strip()
        if not line or line.startswith(";"):
            continue
        parts = line.split()
        if len(parts) >= 3:
            node, x_coord, y_coord = parts[:3]
            data.append([node, float(x_coord), float(y_coord)])

    # Create DataFrame
    coords_df = pd.DataFrame(data, columns=["Node", "X-Coord", "Y-Coord"])
    
    gdf = gpd.read_file(shp_input_filepath)
    crs = gdf.crs
    
    line_data = {
        'HMS ID': [],
        'SSA Manhole-ID': [],
        'geometry': []
    }
    
    for index, mapping in mapping_dict.iterrows():
        
        shp_id = mapping['HMS Name']
        coord_id = mapping['SSA Manhole-ID']
        print(shp_id)
        watershed = gdf[gdf['name'] == shp_id]
        watershed_centroid = watershed.geometry.centroid
        first_point = (watershed_centroid.iloc[0].x, watershed_centroid.iloc[0].y)
        second_point = (coords_df[coords_df['Node'] == coord_id]['X-Coord'].iloc[0], coords_df[coords_df['Node'] == coord_id]['Y-Coord'].iloc[0])
        line_data['HMS ID'].append(shp_id)
        line_data['SSA Manhole-ID'].append(coord_id)
        line_data['geometry'].append(LineString([first_point, second_point]))
        
    line_gdf = gpd.GeoDataFrame(line_data, crs=crs)
    shp_out_path = filedialog.asksaveasfilename(defaultextension=".shp", filetypes=[("Shapefile", "*.shp")])
    line_gdf.to_file(shp_out_path, driver='ESRI Shapefile')
    print('Shapefile Created')



def start_conversion():
    # Prompt user for model input
    root = tk.Tk()
    root.withdraw()
    
    # Prompt user for files
    dss_file = get_file_path("Select DSS File", "*.dss")
    if not dss_file:  # Check if the user canceled the dialog
        print("DSS file selection canceled. Exiting.")
        return
    
    csv_file = get_file_path("Select CSV File", "*.csv")
    if not csv_file:  # Check if the user canceled the dialog
        print("CSV file selection canceled. Exiting.")
        return

    inp_file = get_file_path("Select INP File", "*.inp")
    if not inp_file:  # Check if the user canceled the dialog
        print("INP file selection canceled. Exiting.")
        return
    
    shp_in_file = get_file_path("Select Watershed Shapefile (optional)", "*.shp")
    
    # Read files
    fid = HecDss.Open(dss_file)
    mapping = pd.read_csv(csv_file)
    pathname_dict = fid.getPathnameDict()
    pathname_list = pathname_dict['TS']
    
    unique_run_values = set()

    # Iterate over each path in the list
    for path in pathname_list:
        # Use regular expression to find the pattern after "RUN:"
        match = re.search(r'RUN:([^/]+)', path)
        if match:
            # Add the extracted value to the set
            unique_run_values.add(match.group(1))
    
    # Convert set to a sorted list (optional)
    unique_run_values = sorted(unique_run_values)
    
    if len(unique_run_values) == 1:
        model = unique_run_values[0]
    else:
        model = select_run_value(unique_run_values)
    
    # If the user cancels or doesn't provide input, stop execution
    if not model:
        fid.close()
        print("Model input required. Exiting.")
        return
    
    # Create a list to store individual dataframes
    df_list = []
    missed_mapping = []
    
    unique_ssa_ids = mapping['SSA Manhole-ID'].unique()
    
    for ssa_id in unique_ssa_ids:
        hms_ids = mapping[mapping['SSA Manhole-ID'] == ssa_id]['HMS Name']
        
        composite_list = []
        
        for hms_id in hms_ids:
            # Filter pathnames for the specific HMS ID and model with /FLOW/
            pathnames = [s for s in pathname_list if f'/{hms_id}/' in s and '/FLOW/' in s and model in s]
            
            for pathname in pathnames:
                ts = fid.read_ts(pathname)
                times = np.array(ts.pytimes)
                values = ts.values
                valid_times = times[~ts.nodata]
                valid_values = values[~ts.nodata]
                df = pd.DataFrame({'Time': pd.to_datetime(valid_times), hms_id: valid_values})
                df.set_index('Time', inplace=True)
                composite_list.append(df)
        
        if len(composite_list) > 0:
            composite_df = pd.concat(composite_list, axis=1, join='outer')
            summed_df = composite_df.sum(axis=1).to_frame(name=f'{ssa_id}_ts')
            df_list.append(summed_df)
        else:
            missed_mapping.append(ssa_id)
    
    combined_df = pd.concat(df_list, axis=1, join='outer')
    combined_df.index = pd.to_datetime(combined_df.index)
    fid.close()
    
    #combined_df.plot()
    # Replace timeseries in INP file
    inp_str = replace_timeseries_in_inp(inp_file, combined_df, unique_ssa_ids)
    
    if shp_in_file:
        create_shapefile(shp_in_file, inp_str, mapping)
    
    if len(missed_mapping) > 0:
        print(f'No timeseries were found for the following basins:\n{chr(10).join(missed_mapping)}')


if __name__ == "__main__":
    start_conversion()

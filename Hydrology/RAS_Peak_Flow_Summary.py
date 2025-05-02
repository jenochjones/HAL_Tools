# -*- coding: utf-8 -*-
"""
Created on Tue Dec  3 10:45:00 2024

@author: ejones
"""
import os
import re
import pandas as pd
import numpy as np
import tkinter as tk
from tkinter import filedialog
from tkinter import messagebox
from pydsstools.heclib.dss import HecDss

import pandas as pd
import plotly.graph_objects as go


def plot_interactive_line(df, output_file):

    if not isinstance(df.index, pd.DatetimeIndex):
        raise ValueError("The DataFrame index must be a DatetimeIndex.")

    # Create a Plotly figure
    fig = go.Figure()

    # Add a line trace for each column
    for col in df.columns:
        fig.add_trace(go.Scatter(x=df.index, y=df[col], mode='lines', name=col))

    # Update layout for better visualization
    fig.update_layout(
        title="Interactive Line Plot",
        xaxis_title="Time",
        yaxis_title="Flow",
        template="plotly_white",
        legend_title="Legend"
    )

    # Save the plot as an HTML file
    fig.write_html(output_file)
    print(f"Plot saved to {output_file}")
    

# Open file dialog to select files
def get_file_path():
    root = tk.Tk()
    root.withdraw()  # Hide the main window
    file_path = filedialog.askopenfilename(title="Select RAS output file", filetypes=[("files", "*")])
    return file_path


def prompt_yes_no(prompt_title, prompt_message):
    # Create a hidden root window
    root = tk.Tk()
    root.withdraw()  # Hide the main tkinter window

    # Display a Yes/No dialog box
    result = messagebox.askyesno(prompt_title, prompt_message)

    # Close the tkinter root window
    root.destroy()

    return result


def split_into_groups(filename):
    groups = []
    with open(filename, 'r') as file:
        current_group = []
        for line in file:
            line = line.strip()  # Remove any leading/trailing whitespace
            if line.startswith("Boundary Location="):
                if current_group:
                    groups.append(current_group)  # Save the previous group
                current_group = [line]  # Start a new group
            else:
                if current_group:  # Add to the current group if it exists
                    current_group.append(line)
        if current_group:  # Append the last group if it exists
            groups.append(current_group)
    return groups


def summarize_flows():
    error_log = ''
    path_log = ''
    file_path = get_file_path()
    
    if not file_path:
        print("No file selected. Exiting.")
        return
    
    outflows = prompt_yes_no("Include Outflows", "Do you want to include negative flow values?")

    # Initialize the DataFrame
    columns = ['ID', 'Use DSS', 'DSS Filepath', 'Timeseries Path', 'Q Min', 'Hydrograph', 'Interval']
    df = pd.DataFrame(columns=columns)

    groups = split_into_groups(file_path)
    
    for group in groups:
        
        boundary_id = None
        use_dss = None
        dss_filepath = None
        dss_path = None
        q_min = None
        interval = None
        hydrograph = ""
        in_hydrograph = False
        
        for line in group:
            if in_hydrograph:
                if "=" in line:
                    in_hydrograph = False
                else:
                    hydrograph += line
            
            if line.startswith("Boundary Location="):
                after_equal = line.split("=", 1)[1]
                boundary_id = " ".join(after_equal.replace(",", "").split())
            elif line.startswith("Use DSS="):
                use_dss = line.split("=", 1)[1]
            elif line.startswith("DSS File="):
                dss_filepath = line.split("=", 1)[1]
            elif line.startswith("DSS Path="):
                dss_path = line.split("=", 1)[1]
            elif line.startswith("Interval="):
                interval = line.split("=", 1)[1]
            elif line.startswith("Flow Hydrograph QMin="):
                q_min = line.split("=", 1)[1]
            elif line.startswith("Lateral Inflow Hydrograph="):
                in_hydrograph = True

        new_row = {
            'ID': boundary_id,
            'Use DSS': use_dss,
            'DSS Filepath': dss_filepath,
            'Timeseries Path': dss_path,
            'Q Min': q_min,
            'Hydrograph': list(map(float, re.findall(r'-?\d*\.\d+|-?\d+', hydrograph))),
            'Interval': interval
        }

        # Use pd.concat to add the new row to the DataFrame
        df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)

    unique_ids = df['ID'].unique()
    
    df_list = []
    no_dss_df = pd.DataFrame(columns=['ID', 'Q Min', 'Hydrograph', 'Interval'])
    
    for unique_id in unique_ids:
        unique_df = df[df['ID'] == unique_id]
        
        composite_list = []
        
        for index, row in unique_df.iterrows():
            dss_file = row['DSS Filepath']
            pathname = row['Timeseries Path']
            
            if dss_file and pathname:
                path_parts = pathname.split('/')
                path_parts[4] = ''#path_parts[4].split('-')[0]
                pathname = '/'.join(path_parts)
                
                full_dss_path = os.path.abspath(os.path.join(os.path.dirname(file_path), dss_file))

                if os.path.exists(full_dss_path):
                    try:
                        fid = HecDss.Open(full_dss_path)
                        ts = fid.read_ts(pathname)
                        fid.close()
                        path_log += f'{full_dss_path}: {pathname}: '
                        if ts is None or ts.pytimes is None or ts.values is None:
                            error_log += f'Time series data is None for {pathname} in {full_dss_path}\n'
                            path_log += 'NO\n'
                        else:
                            path_log += 'YES\n'
                            times = np.array(ts.pytimes)
                            values = ts.values
                            
                            # Check for empty arrays
                            if len(times) == 0 or len(values) == 0:
                                error_log += f'Time series data is empty for pathname: {pathname}\n'
                            else:
                                valid_times = times[~ts.nodata]
                                valid_values = values[~ts.nodata]
                                
                                ts_df = pd.DataFrame({'Time': pd.to_datetime(valid_times), unique_id: valid_values})
                                ts_df.set_index('Time', inplace=True)
                                #print(f'{unique_id}: {valid_values.max()}')
                                composite_list.append(ts_df)
                                #ts_df.plot()
                    except Exception as e:
                        error_log += f'Error processing DSS file {full_dss_path} with pathname {pathname}: {e}\n'
            else:
                no_dss_row = {
                    'ID': unique_id,
                    'Q Min': row['Q Min'],
                    'Hydrograph': row['Hydrograph'],
                    'Interval': row['Interval']
                }
                no_dss_df = pd.concat([no_dss_df, pd.DataFrame([no_dss_row])], ignore_index=True)

        if len(composite_list) > 0:
            composite_df = pd.concat(composite_list, axis=1, join='outer').astype('float')
            
            if not outflows:
                composite_df = composite_df.clip(lower=0)
                
            summed_df = composite_df.sum(axis=1).to_frame(name=f'{unique_id}')
            df_list.append(summed_df)
    
    
    combined_df = pd.concat(df_list, axis=1, join='outer')
    combined_df.index = pd.to_datetime(combined_df.index)
    
    for index, row in no_dss_df.iterrows():
        if (row['Q Min'] is not None) or (len(row['Hydrograph']) > 0):
            if row['Q Min'] is not None:
                interpolated_series = pd.Series(row['Q Min'], index=combined_df.index).astype(float)
            elif len(row['Hydrograph']) > 0:
                interval_str = row['Interval'].replace('MINUTE', 'T').replace('HOUR', 'H').replace('DAY', 'D').replace('WEEK', 'W')
                value_index = pd.date_range(start=combined_df.index[0], periods=len(row['Hydrograph']), freq=interval_str)
                value_series = pd.Series(row['Hydrograph'], index=value_index).astype(float)
                interpolated_series = value_series.reindex(combined_df.index).interpolate(method="time")
            
            if not outflows:
                interpolated_series = interpolated_series.clip(lower=0)
                
            if row['ID'] in combined_df.columns:
                combined_df[row['ID']] += interpolated_series
            else:
                combined_df[row['ID']] = interpolated_series
    
    pf_df = pd.DataFrame({
        'Max Value': combined_df.max(),
        'Time': combined_df.idxmax()
    }).reset_index()
    
    # Rename the columns
    pf_df.columns = ['ID', 'Peak Flow', 'Peak Flow Time']
    
    output_file = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV files", "*.csv")])
    
    if not output_file:
        print("No file selected. Exiting.")
        return
    
    pf_df.to_csv(output_file)
    
    save_graph = prompt_yes_no("Timeseries Graph?", "Would you like to save the timeseries to an html graph?")
    
    if save_graph:
        output_graph = filedialog.asksaveasfilename(defaultextension=".html", filetypes=[("HTML files", "*.html")])
        plot_interactive_line(combined_df, output_graph)
        
    print(error_log)
    
    return
    

if __name__ == "__main__":
    summarize_flows()

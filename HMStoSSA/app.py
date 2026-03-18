from flask import Flask, request, jsonify, send_file
import pandas as pd
import numpy as np
import geopandas as gpd
from shapely.geometry import LineString
import os
import re
import tempfile
from pydsstools.heclib.dss import HecDss

app = Flask(__name__)

# -----------------------------
# Formatting Functions
# -----------------------------
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


# -----------------------------
# INP Update
# -----------------------------
def replace_timeseries_in_inp(inp_file_path, df, ssa_ids):
    with open(inp_file_path, 'r', encoding='ISO-8859-1') as file:
        inp_content = file.readlines()

    inflow_section = format_inflows(ssa_ids)
    timeseries_section = format_timeseries(df)

    updated_content = inp_content + ["\n"] + [inflow_section] + [timeseries_section]

    output_path = os.path.splitext(inp_file_path)[0] + "_Updated.inp"

    with open(output_path, 'w', encoding='ISO-8859-1') as file:
        file.writelines(updated_content)

    return output_path, updated_content


# -----------------------------
# Shapefile Creation
# -----------------------------
def create_shapefile(shp_input_filepath, inp_string, mapping_df, output_dir):
    start, end = None, None

    for i, line in enumerate(inp_string):
        if line.strip().startswith("[COORDINATES]"):
            start = i + 1
        elif line.strip().startswith("[") and start is not None:
            end = i
            break

    if start is not None and end is None:
        end = len(inp_string)

    coords = inp_string[start:end] if start else []

    data = []
    for line in coords:
        if not line.strip() or line.startswith(";"):
            continue
        parts = line.split()
        if len(parts) >= 3:
            node, x, y = parts[:3]
            data.append([node, float(x), float(y)])

    coords_df = pd.DataFrame(data, columns=["Node", "X", "Y"])

    gdf = gpd.read_file(shp_input_filepath)
    crs = gdf.crs

    line_data = {'HMS ID': [], 'SSA Manhole-ID': [], 'geometry': []}

    for _, row in mapping_df.iterrows():
        shp_id = row['HMS Name']
        coord_id = row['SSA Manhole-ID']

        watershed = gdf[gdf['name'] == shp_id]
        centroid = watershed.geometry.centroid.iloc[0]

        node = coords_df[coords_df['Node'] == coord_id].iloc[0]

        line = LineString([
            (centroid.x, centroid.y),
            (node['X'], node['Y'])
        ])

        line_data['HMS ID'].append(shp_id)
        line_data['SSA Manhole-ID'].append(coord_id)
        line_data['geometry'].append(line)

    line_gdf = gpd.GeoDataFrame(line_data, crs=crs)

    output_path = os.path.join(output_dir, "output.shp")
    line_gdf.to_file(output_path)

    return output_path


# -----------------------------
# Main Processing Endpoint
# -----------------------------
@app.route('/process', methods=['POST'])
def process_files():
    try:
        dss_file = request.files['dss']
        csv_file = request.files['csv']
        inp_file = request.files['inp']
        shp_file = request.files.get('shp')  # optional
        selected_run = request.form.get('run_name')

        with tempfile.TemporaryDirectory() as tmpdir:
            dss_path = os.path.join(tmpdir, dss_file.filename)
            csv_path = os.path.join(tmpdir, csv_file.filename)
            inp_path = os.path.join(tmpdir, inp_file.filename)

            dss_file.save(dss_path)
            csv_file.save(csv_path)
            inp_file.save(inp_path)

            shp_path = None
            if shp_file:
                shp_path = os.path.join(tmpdir, shp_file.filename)
                shp_file.save(shp_path)

            # Load DSS
            fid = HecDss.Open(dss_path)
            mapping = pd.read_csv(csv_path)

            pathname_list = fid.getPathnameDict()['TS']

            # If run not provided, return options
            if not selected_run:
                runs = sorted({
                    re.search(r'RUN:([^/]+)', p).group(1)
                    for p in pathname_list if 'RUN:' in p
                })
                fid.close()
                return jsonify({"available_runs": runs})

            df_list = []
            unique_ssa_ids = mapping['SSA Manhole-ID'].unique()

            for ssa_id in unique_ssa_ids:
                hms_ids = mapping[mapping['SSA Manhole-ID'] == ssa_id]['HMS Name']

                composite_list = []

                for hms_id in hms_ids:
                    paths = [
                        p for p in pathname_list
                        if f'/{hms_id}/' in p and '/FLOW/' in p and selected_run in p
                    ]

                    for p in paths:
                        ts = fid.read_ts(p)
                        times = np.array(ts.pytimes)
                        values = ts.values

                        mask = ~ts.nodata
                        df = pd.DataFrame({
                            'Time': pd.to_datetime(times[mask]),
                            hms_id: values[mask]
                        }).set_index('Time')

                        composite_list.append(df)

                if composite_list:
                    combined = pd.concat(composite_list, axis=1)
                    summed = combined.sum(axis=1).to_frame(name=f'{ssa_id}_ts')
                    df_list.append(summed)

            final_df = pd.concat(df_list, axis=1)
            fid.close()

            # Update INP
            updated_inp_path, inp_str = replace_timeseries_in_inp(
                inp_path, final_df, unique_ssa_ids
            )

            result = {
                "message": "Processing complete",
                "inp_file": updated_inp_path
            }

            # Optional shapefile
            if shp_path:
                shp_output = create_shapefile(shp_path, inp_str, mapping, tmpdir)
                result["shapefile"] = shp_output

            return jsonify(result)

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# -----------------------------
# Download Endpoint
# -----------------------------
@app.route('/download')
def download_file():
    path = request.args.get('path')
    return send_file(path, as_attachment=True)


if __name__ == '__main__':
    app.run(debug=True)
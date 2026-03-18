import { } from './helpers.js';

// -----------------------------
// Map Setup
// -----------------------------
const map = L.map('map', { zoomControl: false }).setView(MAP_CENTER, MAP_ZOOM);

map.createPane('lidarPane'); map.getPane('lidarPane').style.zIndex = 400;
map.createPane('uploadPane'); map.getPane('uploadPane').style.zIndex = 650;
map.createPane('drawPane'); map.getPane('drawPane').style.zIndex = 700;

L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
  maxZoom: 19
}).addTo(map);

L.control.zoom({ position: 'topright' }).addTo(map);

// -----------------------------
// File Inputs
// -----------------------------
const dssInput = document.getElementById('dssFile');
const csvInput = document.getElementById('csvFile');
const inpInput = document.getElementById('inpFile');
const hmsGeomInput = document.getElementById('hmsGeomFile');

const runBtn = document.getElementById('runBtn');
const downloadBtn = document.getElementById('downloadBtn');

// -----------------------------
// Layers
// -----------------------------
const basinLayer = L.layerGroup().addTo(map);
const flowLayer = L.layerGroup().addTo(map);
const junctionLayer = L.layerGroup().addTo(map);

// -----------------------------
// Parse SWMM INP
// -----------------------------
function parseINP(text) {
  const lines = text.split('\n');

  let coords = {};
  let junctions = [];

  let inCoords = false;

  lines.forEach(line => {
    line = line.trim();

    if (line.startsWith('[COORDINATES]')) {
      inCoords = true;
      return;
    }

    if (line.startsWith('[') && !line.startsWith('[COORDINATES]')) {
      inCoords = false;
    }

    if (inCoords && line && !line.startsWith(';')) {
      const parts = line.split(/\s+/);
      if (parts.length >= 3) {
        coords[parts[0]] = [
          parseFloat(parts[2]),
          parseFloat(parts[1])
        ];
      }
    }
  });

  Object.keys(coords).forEach(id => {
    junctions.push({
      id,
      latlng: coords[id]
    });
  });

  return junctions;
}

// -----------------------------
// Parse HMS Geometry (basic)
// -----------------------------
function parseHMS(text) {
  const lines = text.split('\n');

  let basins = [];
  let reaches = [];

  let current = null;

  lines.forEach(line => {
    line = line.trim();

    if (line.startsWith('Subbasin:')) {
      current = { type: 'basin', name: line.split(':')[1].trim(), coords: [] };
      basins.push(current);
    }

    if (line.startsWith('Reach:')) {
      current = { type: 'reach', name: line.split(':')[1].trim(), coords: [] };
      reaches.push(current);
    }

    if (line.startsWith('     ') && current) {
      const parts = line.trim().split(',');
      if (parts.length === 2) {
        current.coords.push([
          parseFloat(parts[1]),
          parseFloat(parts[0])
        ]);
      }
    }
  });

  return { basins, reaches };
}

// -----------------------------
// Draw Functions
// -----------------------------
function drawJunctions(junctions) {
  junctionLayer.clearLayers();

  junctions.forEach(j => {
    L.circleMarker(j.latlng, {
      radius: 4
    }).bindPopup(j.id).addTo(junctionLayer);
  });
}

function drawBasins(basins) {
  basinLayer.clearLayers();

  basins.forEach(b => {
    if (b.coords.length > 2) {
      L.polygon(b.coords, {
        weight: 1
      }).bindPopup(b.name).addTo(basinLayer);
    }
  });
}

function drawReaches(reaches) {
  flowLayer.clearLayers();

  reaches.forEach(r => {
    if (r.coords.length > 1) {
      L.polyline(r.coords, {
        weight: 2
      }).bindPopup(r.name).addTo(flowLayer);
    }
  });
}

// -----------------------------
// File Readers
// -----------------------------
inpInput.addEventListener('change', async (e) => {
  const text = await e.target.files[0].text();
  const junctions = parseINP(text);
  drawJunctions(junctions);
});

hmsGeomInput.addEventListener('change', async (e) => {
  const text = await e.target.files[0].text();
  const { basins, reaches } = parseHMS(text);

  drawBasins(basins);
  drawReaches(reaches);
});

// -----------------------------
// Backend Call
// -----------------------------
let lastOutputPath = null;

runBtn.addEventListener('click', async () => {
  const formData = new FormData();

  formData.append('dss', dssInput.files[0]);
  formData.append('csv', csvInput.files[0]);
  formData.append('inp', inpInput.files[0]);

  // Step 1: Get available runs
  let response = await fetch('/process', {
    method: 'POST',
    body: formData
  });

  let data = await response.json();

  let selectedRun = null;

  if (data.available_runs) {
    selectedRun = prompt(
      "Select Run:\n" + data.available_runs.join('\n'),
      data.available_runs[0]
    );

    formData.append('run_name', selectedRun);

    response = await fetch('/process', {
      method: 'POST',
      body: formData
    });

    data = await response.json();
  }

  if (data.error) {
    alert(data.error);
    return;
  }

  lastOutputPath = data.inp_file;

  alert("Processing complete!");
});

// -----------------------------
// Download
// -----------------------------
downloadBtn.addEventListener('click', () => {
  if (!lastOutputPath) {
    alert("No file yet.");
    return;
  }

  window.location.href = `/download?path=${encodeURIComponent(lastOutputPath)}`;
});
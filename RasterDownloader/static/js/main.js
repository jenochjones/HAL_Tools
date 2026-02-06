import { renderLidarLayer, filterLidarByUploadedAOI } from './helpers.js';

const DEFAULT_STYLE = {
  color: '#0066cc',
  weight: 1.5,
  opacity: 1,
  fillColor: '#66b2ff',
  fillOpacity: 0.2
};

const HIGHLIGHT_STYLE = {
  color: '#ff8800',
  weight: 4,
  opacity: 1,
  fillColor: '#ffd08a',
  fillOpacity: 0.55
};

let lidarAllGeojson = null;

// Shared state between modules
const state = {
  featureIndex: new Map(),
  lidarLayer: null
};

let currentlyHighlighted = new Set();

function clearHighlight() {
  if (currentlyHighlighted.size === 0) return;

  for (const lyr of currentlyHighlighted) {
    lyr.setStyle?.(DEFAULT_STYLE);
  }

  currentlyHighlighted.clear();
}

function highlightSelected() {
  const datasets = document.getElementsByClassName("dataset selected");
  const highlightedIds = Array.from(datasets).map(d => String(d.dataset.id));

  if (highlightedIds.length === 0) {
    clearHighlight();
    return;
  }

  clearHighlight();

  let combinedBounds = null;
  let lastLayer = null;

  for (const hid of highlightedIds) {
    const lyr = state.featureIndex.get(hid);
    if (!lyr) continue;

    lyr.setStyle?.(HIGHLIGHT_STYLE);
    lyr.bringToFront?.();

    currentlyHighlighted.add(lyr);
    lastLayer = lyr;

    const b = lyr.getBounds?.();
    if (b?.isValid?.() && b.isValid()) {
      combinedBounds = combinedBounds ? combinedBounds.extend(b) : b;
    }
  }

  if (combinedBounds?.isValid?.() && combinedBounds.isValid()) {
    map.fitBounds(combinedBounds, { padding: [30, 30], maxZoom: 14 });
    return;
  }

  const ll = lastLayer?.getLatLng?.();
  if (ll) map.setView(ll, Math.max(map.getZoom(), 12));
}

// Initialize map
const map = L.map('map', { zoomControl: false }).setView(MAP_CENTER, MAP_ZOOM);
// --- Panes (control draw order) ---
map.createPane('lidarPane');
map.getPane('lidarPane').style.zIndex = 400;

map.createPane('uploadPane');
map.getPane('uploadPane').style.zIndex = 650; // always on top

L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
  maxZoom: 19,
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
}).addTo(map);

// Load LiDAR extents
const base = 'https://services1.arcgis.com/99lidPhWCzftIe9K/ArcGIS/rest/services/LiDAR_Extents/FeatureServer/0/query';
const params = new URLSearchParams({
  where: '1=1',
  outFields: '*',
  returnGeometry: 'true',
  f: 'geojson'
});
const url = `${base}?${params.toString()}`;

fetch(url)
  .then(resp => {
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    return resp.json();
  })
  .then(geojson => {
    lidarAllGeojson = geojson;
    renderLidarLayer(lidarAllGeojson, map, state);
  })
  .catch(err => {
    console.error('Failed to load GeoJSON:', err);
    alert('Could not load GeoJSON from the service (check CORS/format/token).');
  });

L.control.zoom({ position: 'topright' }).addTo(map);

// Shapefile upload group
const serverUploadedGroup = window.serverUploadedGroup || L.layerGroup().addTo(map);

function styleFor(feature) {
  const geom = feature?.geometry?.type || '';
  if (geom.includes('Line')) return { color: '#2563eb', weight: 3 };
  if (geom.includes('Polygon')) return { color: '#ef4444', weight: 2, fillColor: '#fca5a5', fillOpacity: 0.35 };
  return { color: '#10b981' };
}

const btnLoadShp4 = document.getElementById('btn-load-shp-4');
const shp4Input = document.getElementById('shp-4-input');

btnLoadShp4?.addEventListener('click', () => shp4Input?.click());

shp4Input?.addEventListener('change', async (e) => {
  const files = Array.from(e.target.files || []);
  if (files.length === 0) return;

  try {
    const parts = { shp: null, shx: null, dbf: null, prj: null };
    for (const f of files) {
      const ext = f.name.split('.').pop().toLowerCase();
      if (ext in parts && !parts[ext]) parts[ext] = f;
    }

    if (!parts.shp || !parts.shx || !parts.dbf || !parts.prj) {
      alert('Please select exactly one each: .shp, .shx, .dbf, and .prj (same basename).');
      e.target.value = '';
      return;
    }

    const stem = (name) => name.replace(/\.[^.]+$/, '').toLowerCase();
    const s = stem(parts.shp.name);
    if (![parts.shx, parts.dbf, parts.prj].every(f => stem(f.name) === s)) {
      alert('All four files must share the same name (e.g., parcels.shp/shx/dbf/prj).');
      e.target.value = '';
      return;
    }

    const form = new FormData();
    form.append('shp', parts.shp, parts.shp.name);
    form.append('shx', parts.shx, parts.shx.name);
    form.append('dbf', parts.dbf, parts.dbf.name);
    form.append('prj', parts.prj, parts.prj.name);

    const res = await fetch('/upload_shapefile_parts', { method: 'POST', body: form });
    if (!res.ok) throw new Error(await res.text() || `Upload failed: ${res.status}`);

    const payload = await res.json();
    const { layer, warnings } = payload || {};

    if (warnings?.length) console.warn('Upload warnings:', warnings);
    if (!layer?.geojson) {
      alert('No features returned from server.');
      e.target.value = '';
      return;
    }

    serverUploadedGroup.clearLayers();

    const layerObj = L.geoJSON(layer.geojson, {
      pane: 'uploadPane',
      style: styleFor,
      pointToLayer: (feature, latlng) =>
        L.circleMarker(latlng, { radius: 6, ...styleFor(feature) }),
      onEachFeature: (feature, lyr) => {
        const props = feature?.properties || {};
        const rows = Object.entries(props)
          .slice(0, 20)
          .map(([k, v]) => `<tr><th style="text-align:left;padding-right:8px;">${k}</th><td>${v}</td></tr>`)
          .join('');
        if (rows) lyr.bindPopup(`<div style="max-height:180px;overflow:auto;"><table>${rows}</table></div>`);
      }
    }).addTo(serverUploadedGroup);

    // Filter LiDAR by uploaded AOI
    filterLidarByUploadedAOI(layer.geojson, lidarAllGeojson, map, state);

    const b = layerObj.getBounds?.();
    if (b?.isValid?.() && b.isValid()) {
      map.fitBounds(b, { padding: [20, 20] });
    }
  } catch (err) {
    console.error(err);
    alert(`Upload failed: ${err.message}`);
  } finally {
    e.target.value = '';
  }
});

document.getElementById('stitch-toggle')?.addEventListener('click', (e) => {
  e.currentTarget.classList.toggle('selected');
});

// Dataset selection => highlight
window.addEventListener("dataset:selected", () => {
  highlightSelected();
});

window.addEventListener('resize', () => map.invalidateSize());
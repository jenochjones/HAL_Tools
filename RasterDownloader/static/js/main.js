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
let uploadedShapefileGeoJSON = null; // set after shapefile upload

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
    uploadedShapefileGeoJSON = layer.geojson;
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

// ------------------------------
// Download LiDAR: compile request payload, optionally rank datasets, send to server
// ------------------------------

function normalizeEpsg(input) {
  const raw = (input || '').trim();
  if (!raw) return null;
  const upper = raw.toUpperCase();
  return upper.startsWith('EPSG:') ? upper : `EPSG:${upper.replace(/[^0-9]/g, '') || upper}`;
}

function getSelectedDatasetObjects() {
  const selected = document.querySelectorAll('#dataset-list .dataset.selected');
  return Array.from(selected).map((el) => ({
    id: String(el.dataset.id),
    label: (el.dataset.label || el.textContent || '').trim() || String(el.dataset.id)
  }));
}

function isStitchSelected() {
  return document.getElementById('stitch-toggle')?.classList.contains('selected') === true;
}

function buildRankListItems(listEl, datasetObjs) {
  listEl.innerHTML = '';
  datasetObjs.forEach((ds) => {
    const li = document.createElement('li');
    li.className = 'rank-item';
    li.draggable = true;
    li.dataset.id = ds.id;

    const handle = document.createElement('span');
    handle.className = 'rank-handle';
    handle.textContent = '⠿';
    handle.title = 'Drag to reorder';

    const label = document.createElement('span');
    label.className = 'rank-label';
    label.textContent = ds.label;

    const controls = document.createElement('span');
    controls.className = 'rank-controls';

    const up = document.createElement('button');
    up.type = 'button';
    up.className = 'rank-arrow';
    up.textContent = '↑';
    up.title = 'Move up';
    up.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      const prev = li.previousElementSibling;
      if (prev) listEl.insertBefore(li, prev);
    });

    const down = document.createElement('button');
    down.type = 'button';
    down.className = 'rank-arrow';
    down.textContent = '↓';
    down.title = 'Move down';
    down.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      const next = li.nextElementSibling;
      if (next) listEl.insertBefore(next, li);
    });

    controls.appendChild(up);
    controls.appendChild(down);

    li.appendChild(handle);
    li.appendChild(label);
    li.appendChild(controls);
    listEl.appendChild(li);
  });

  // drag and drop ordering
  let dragging = null;
  listEl.querySelectorAll('li').forEach((li) => {
    li.addEventListener('dragstart', (e) => {
      dragging = li;
      li.classList.add('dragging');
      e.dataTransfer.effectAllowed = 'move';
      e.dataTransfer.setData('text/html', li.innerHTML);
    });
    li.addEventListener('dragend', () => {
      dragging = null;
      li.classList.remove('dragging');
    });
    li.addEventListener('dragover', (e) => {
      e.preventDefault();
      e.dataTransfer.dropEffect = 'move';
      if (!dragging || dragging === li) return;
      const rect = li.getBoundingClientRect();
      const before = (e.clientY - rect.top) < rect.height / 2;
      if (before) listEl.insertBefore(dragging, li);
      else listEl.insertBefore(dragging, li.nextSibling);
    });
    li.addEventListener('drop', (e) => {
      e.preventDefault();
      e.stopPropagation();
    });
  });
}

function openRankingDialog(datasetObjs) {
  const dialog = document.getElementById('rank-dialog');
  const listEl = document.getElementById('rank-list');
  const cancelBtn = document.getElementById('rank-cancel');
  const form = document.getElementById('rank-form');

  // If <dialog> isn't supported, just return current order
  if (!dialog || typeof dialog.showModal !== 'function') {
    return Promise.resolve(datasetObjs.map(d => d.id));
  }

  buildRankListItems(listEl, datasetObjs);

  return new Promise((resolve, reject) => {
    const cleanup = () => {
      cancelBtn?.removeEventListener('click', onCancel);
      dialog.removeEventListener('cancel', onCancel);
      form.removeEventListener('submit', onSubmit);
    };

    const onCancel = () => {
      cleanup();
      dialog.close('cancel');
      reject(new Error('Ranking cancelled'));
    };

    const onSubmit = (e) => {
      e.preventDefault();
      const ranked = Array.from(listEl.querySelectorAll('li')).map(li => li.dataset.id);
      cleanup();
      dialog.close('confirm');
      resolve(ranked);
    };

    cancelBtn?.addEventListener('click', onCancel);
    dialog.addEventListener('cancel', onCancel);
    form.addEventListener('submit', onSubmit);

    dialog.showModal();
  });
}

async function sendDownloadRequest(payloadArray) {
  const res = await fetch('/download_lidar', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'same-origin',
    body: JSON.stringify({ data: payloadArray })
  });
  if (!res.ok) {
    const txt = await res.text().catch(() => '');
    throw new Error(txt || `Request failed (${res.status})`);
  }
  return res.json().catch(() => ({}));
}

document.getElementById('btn-download-lidar')?.addEventListener('click', async () => {
  try {
    if (!uploadedShapefileGeoJSON) {
      alert('Please load a shapefile AOI first.');
      return;
    }

    const selected = getSelectedDatasetObjects();
    if (selected.length === 0) {
      alert('Please select at least one dataset.');
      return;
    }

    const outCrs = normalizeEpsg(document.getElementById('out-crs')?.value);
    if (!outCrs || outCrs === 'EPSG:') {
      alert('Please enter an Output CRS (EPSG code).');
      return;
    }

    const stitch = isStitchSelected();

    // Rank if multiple datasets
    let rankedIds = selected.map(d => d.id);
    if (selected.length > 1) {
      rankedIds = await openRankingDialog(selected);
    }

    // Build array as requested: [uploaded shapefile geojson, selected datasets (ranked), output CRS, stitch toggle]
    const payload = [uploadedShapefileGeoJSON, rankedIds, outCrs, stitch];

    const btn = document.getElementById('btn-download-lidar');
    if (btn) {
      btn.disabled = true;
      btn.textContent = 'Processing…';
    }

    const result = await sendDownloadRequest(payload);
    console.log('Download request accepted:', result);

    // Backend may respond with a job id or a download URL
    if (result?.download_url) {
      window.location.href = result.download_url;
    } else {
      alert(result?.message || 'Request submitted. Server is processing.');
    }
  } catch (err) {
    console.error(err);
    alert(err?.message || 'Download request failed.');
  } finally {
    const btn = document.getElementById('btn-download-lidar');
    if (btn) {
      btn.disabled = false;
      btn.textContent = 'Download LiDAR';
    }
  }
});

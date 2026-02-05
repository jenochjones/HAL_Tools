import addDataset from './helpers.js';

// Initialize map
const map = L.map('map', {
  zoomControl: false, // we'll place it custom
}).setView(MAP_CENTER, MAP_ZOOM);

// Add a tile layer (OpenStreetMap)
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
  maxZoom: 19,
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
}).addTo(map);



// Build a GeoJSON query URL. Adjust fields/where/geometry if needed.
const base = 'https://services1.arcgis.com/99lidPhWCzftIe9K/ArcGIS/rest/services/LiDAR_Extents/FeatureServer/0/query';
const params = new URLSearchParams({
  where: '1=1',
  outFields: '*',
  returnGeometry: 'true',
  f: 'geojson' // <-- ask for GeoJSON directly
});
const url = `${base}?${params.toString()}`;

fetch(url)
  .then(resp => {
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    return resp.json();
  })
  .then(geojson => {
    const layer = L.geoJSON(geojson, {
      style: {
        color: '#0066cc',
        weight: 1.5,
        opacity: 1,
        fillColor: '#66b2ff',
        fillOpacity: 0.2
      },
      onEachFeature: (feature, lyr) => {
        const p = feature.properties || {};
        const category = p.Category || 'Not Listed';
        const description = p.Description || 'Not Listed';
        const hacc = p.Horizontal_Accuracy || 'Not Listed';
        const vacc = p.Vertical_Accuracy || 'Not Listed';
        const year = p.Year_Collected || 'Not Listed';
        const metadata = p.FTP_Path + p.METADATA || 'Not Listed';
        
        lyr.bindPopup(`
            <div style="min-width:200px; width:auto;">
              <strong>${category}</strong><br/>
              ${description ? `Description: ${description}<br/>` : ''}
              ${year ? `Year: ${year}<br/>` : ''}
              ${hacc ? `Horizontal Accuracy: ${hacc}<br/>` : ''}
              ${vacc ? `Vertical Accuracy: ${vacc}<br/>` : ''}
              ${metadata ? `<a href="${metadata}" target="_blank">Metadata</a>` : ''}
            </div>
          `);
        
        addDataset(category, p.OBJECTID)
      }
    }).addTo(map);

  })
  .catch(err => {
    console.error('Failed to load GeoJSON:', err);
    alert('Could not load GeoJSON from the service (check CORS/format/token).');
  });

// Add zoom control to top-right for a clean look
L.control.zoom({ position: 'topright' }).addTo(map);

// Actions
const btnLoadShp = document.getElementById('btn-load-shp');

// A layer group to hold server-uploaded data (if you don't have one already)
const serverUploadedGroup = window.serverUploadedGroup || L.layerGroup().addTo(map);
function styleFor(feature) {
  const geom = (feature?.geometry?.type) || '';
  if (geom.includes('Line')) return { color: '#2563eb', weight: 3 };
  if (geom.includes('Polygon')) return { color: '#ef4444', weight: 2, fillColor: '#fca5a5', fillOpacity: 0.35 };
  return { color: '#10b981' }; // points
}

// --- Four-part Shapefile upload wiring ---
const btnLoadShp4 = document.getElementById('btn-load-shp-4');
const shp4Input = document.getElementById('shp-4-input');

btnLoadShp4?.addEventListener('click', () => shp4Input?.click());

shp4Input?.addEventListener('change', async (e) => {
  const files = Array.from(e.target.files || []);
  if (files.length === 0) return;

  try {
    // Categorize by extension
    const parts = { shp: null, shx: null, dbf: null, prj: null };
    for (const f of files) {
      const ext = f.name.split('.').pop().toLowerCase();
      if (ext in parts && !parts[ext]) parts[ext] = f;
    }

    // Validate presence of all 4
    if (!parts.shp || !parts.shx || !parts.dbf || !parts.prj) {
      alert('Please select exactly one each: .shp, .shx, .dbf, and .prj (same basename).');
      e.target.value = '';
      return;
    }

    // Validate same basename (case-insensitive)
    const stem = (name) => name.replace(/\.[^.]+$/, '').toLowerCase();
    const s = stem(parts.shp.name);
    if (![parts.shx, parts.dbf, parts.prj].every(f => stem(f.name) === s)) {
      alert('All four files must share the same name (e.g., parcels.shp/shx/dbf/prj).');
      e.target.value = '';
      return;
    }

    // Build FormData (use specific field names)
    const form = new FormData();
    form.append('shp', parts.shp, parts.shp.name);
    form.append('shx', parts.shx, parts.shx.name);
    form.append('dbf', parts.dbf, parts.dbf.name);
    form.append('prj', parts.prj, parts.prj.name);

    // POST to Flask
    const res = await fetch('/upload_shapefile_parts', { method: 'POST', body: form });
    if (!res.ok) {
      const text = await res.text();
      throw new Error(text || `Upload failed: ${res.status}`);
    }
    const payload = await res.json();
    const { layer, warnings } = payload || {};

    if (warnings?.length) console.warn('Upload warnings:', warnings);
    if (!layer?.geojson) {
      alert('No features returned from server.');
      e.target.value = '';
      return;
    }

    // Clear previous (optional)
    serverUploadedGroup.clearLayers();

    const layerObj = L.geoJSON(layer.geojson, {
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

    const b = layerObj.getBounds?.();
    if (b?.isValid?.() && b.isValid()) {
      map.fitBounds(b, { padding: [20, 20] });
    } else {
      alert('Uploaded features added, but bounds could not be determined.');
    }
  } catch (err) {
    console.error(err);
    alert(`Upload failed: ${err.message}`);
  } finally {
    // Allow same-file reselect
    e.target.value = '';
  }
});


// Resize map if container size changes (safety)
window.addEventListener('resize', () => { map.invalidateSize(); });

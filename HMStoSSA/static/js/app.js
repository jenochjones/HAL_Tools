(function () {
  const el = document.getElementById('map');
  if (!el || !window.__MAPDATA__) return;

  const data = window.__MAPDATA__;

  // Helper for CRS.Simple: Leaflet expects [lat, lng]. We'll map (x,y)->(y,x).
  function xyToLatLng(x, y) {
    return [y, x];
  }

  // Create map
  const mapOptions = {};
  if (data.isPlanar) {
    mapOptions.crs = L.CRS.Simple;
    mapOptions.minZoom = -5;
  }

  const map = L.map('map', mapOptions);

  if (!data.isPlanar) {
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19,
      attribution: '&copy; OpenStreetMap contributors'
    }).addTo(map);
  }

  function addPointLayer(fc, opts) {
    return L.geoJSON(fc, {
      pointToLayer: function (feature, latlng) {
        return L.circleMarker(latlng, opts.markerStyle);
      },
      onEachFeature: function (feature, layer) {
        if (opts.popup) {
          const p = feature.properties || {};
          layer.bindPopup(opts.popup(p));
        }
      }
    });
  }

  function addLineLayer(fc, opts) {
    return L.geoJSON(fc, {
      style: opts.style,
      onEachFeature: function (feature, layer) {
        if (opts.popup) {
          const p = feature.properties || {};
          layer.bindPopup(opts.popup(p));
        }
      }
    });
  }

  // Convert coordinates for planar mode (x,y) to (lat,lng)=(y,x)
  function convertFeatureCollection(fc) {
    if (!data.isPlanar) return fc;
    const out = JSON.parse(JSON.stringify(fc));
    out.features.forEach(f => {
      if (!f.geometry) return;
      if (f.geometry.type === 'Point') {
        const [x, y] = f.geometry.coordinates;
        f.geometry.coordinates = [y, x];
      }
      if (f.geometry.type === 'LineString') {
        f.geometry.coordinates = f.geometry.coordinates.map(([x, y]) => [y, x]);
      }
    });
    return out;
  }

  const junctions = convertFeatureCollection(data.junctions);
  const pipes = convertFeatureCollection(data.pipes);
  const inp = convertFeatureCollection(data.inp);
  const mapping = convertFeatureCollection(data.mapping);

  const junctionLayer = addPointLayer(junctions, {
    markerStyle: {radius: 5, color: '#b00020', fillColor: '#ff5a66', fillOpacity: 0.9, weight: 2},
    popup: p => `<b>${p.id || 'junction'}</b><br>type: ${p.type || ''}`
  }).addTo(map);

  const pipeLayer = addLineLayer(pipes, {
    style: {color: '#2f6fed', weight: 3, opacity: 0.8},
    popup: p => `<b>${p.id || 'pipe'}</b><br>from: ${p.from_id || ''}<br>to: ${p.to_id || ''}`
  }).addTo(map);

  const inpLayer = addPointLayer(inp, {
    markerStyle: {radius: 4, color: '#066a2d', fillColor: '#2ecc71', fillOpacity: 0.9, weight: 2},
    popup: p => `<b>${p.id || 'node'}</b><br>source: ${p.source || 'INP'}`
  }).addTo(map);

  const mappingLayer = addLineLayer(mapping, {
    style: {color: '#ff8c00', weight: 2, opacity: 0.9, dashArray: '6 4'},
    popup: p => `<b>${p.id || 'mapping'}</b><br>HMS: ${p.hms_id || ''}<br>SSA: ${p.ssa_id || ''}`
  }).addTo(map);

  const overlays = {
    'Geometry junctions': junctionLayer,
    'Pipes / reaches': pipeLayer,
    'INP nodes': inpLayer,
    'Mapping lines': mappingLayer
  };

  L.control.layers(null, overlays, {collapsed: false}).addTo(map);

  // Fit bounds
  const group = L.featureGroup([junctionLayer, pipeLayer, inpLayer, mappingLayer]);
  try {
    const b = group.getBounds();
    if (b.isValid()) map.fitBounds(b.pad(0.1));
    else map.setView([0, 0], data.isPlanar ? 0 : 2);
  } catch (e) {
    map.setView([0, 0], data.isPlanar ? 0 : 2);
  }
})();

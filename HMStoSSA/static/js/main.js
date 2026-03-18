import {  } from './helpers.js';

// Initialize map
const map = L.map('map', { zoomControl: false }).setView(MAP_CENTER, MAP_ZOOM);

// Panes
map.createPane('lidarPane'); map.getPane('lidarPane').style.zIndex = 400;
map.createPane('uploadPane'); map.getPane('uploadPane').style.zIndex = 650;

map.createPane('drawPane');
map.getPane('drawPane').style.zIndex = 700; // above upload

L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
  maxZoom: 19,
  attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
}).addTo(map);

L.control.zoom({ position: 'topright' }).addTo(map);


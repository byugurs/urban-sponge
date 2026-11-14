/* =========================================================================
   UrbanSponge - Frontend logic
   Map interaction, polygon drawing (Leaflet.draw) and API connection.
   Area is computed in m² via Turf.js (turf.area).
   ========================================================================= */

const PILOT = { lat: 37.8874, lng: 41.1322, zoom: 14 };
const API_BASE = ""; // Served from the same origin; if different, set to "http://127.0.0.1:8000".

// Zone risk level -> fill color (red/orange heat map)
// Note: keys match the Turkish risk_level values returned by the backend.
const RISK_COLORS = {
  Kritik: "#ef4444",
  Yüksek: "#f97316",
  Orta: "#f59e0b",
  Düşük: "#10b981",
};

// Backend risk level -> English display label
const RISK_LABELS = {
  Kritik: "Critical",
  Yüksek: "High",
  Orta: "Medium",
  Düşük: "Low",
};

let map;
let drawnItems;          // Layer group holding the drawn polygons
let currentPolygon = null;
let currentAreaM2 = 0;
let selectedSponge = "yagmur_bahcesi";
let rainfallMm = 60;
let activeBaseLayer = "dark";

// Base map layers: dark (default) + satellite view
const baseLayers = {
  dark: L.tileLayer(
    "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png?key=cb1_589l_1_03073975243975ff03a693b5",
    {
      attribution:
        '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; <a href="https://carto.com/attributions">CARTO</a>',
      subdomains: "abcd",
      maxZoom: 20,
    }
  ),
  satellite: L.tileLayer(
    "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    {
      attribution:
        "Tiles &copy; Esri &mdash; Source: Esri, Maxar, Earthstar Geographics",
      maxZoom: 19,
    }
  ),
};

/* ------------------------------------------------------------------ */
/* Initialization                                                     */
/* ------------------------------------------------------------------ */
document.addEventListener("DOMContentLoaded", () => {
  initMap();
  initControls();
  loadRiskZones();
});

function initMap() {
  map = L.map("map", { zoomControl: false }).setView(
    [PILOT.lat, PILOT.lng],
    PILOT.zoom
  );

  // Default dark layer
  baseLayers.dark.addTo(map);

  // Satellite / dark layer toggle button (bottom left)
  map.addControl(new LayerToggleControl());
  L.control.zoom({ position: "bottomright" }).addTo(map);

  drawnItems = new L.FeatureGroup().addTo(map);

  // Trigger the simulation when drawing is completed
  map.on(L.Draw.Event.CREATED, (e) => {
    drawnItems.clearLayers();
    drawnItems.addLayer(e.layer);
    currentPolygon = e.layer;
    handleNewPolygon(e.layer);
  });
}

/* ------------------------------------------------------------------ */
/* Map layer toggle control (bottom left)                             */
/* ------------------------------------------------------------------ */
const LayerToggleControl = L.Control.extend({
  options: { position: "bottomleft" },

  onAdd() {
    const container = L.DomUtil.create("div", "layer-toggle glass");
    container.innerHTML = `
      <span class="layer-toggle-label">Map Layer</span>
      <div class="layer-toggle-buttons">
        <button class="layer-btn is-active" data-layer="dark">Dark</button>
        <button class="layer-btn" data-layer="satellite">Satellite</button>
      </div>`;

    // Stop map interactions from propagating through the control
    L.DomEvent.disableClickPropagation(container);
    L.DomEvent.disableScrollPropagation(container);

    container.querySelectorAll(".layer-btn").forEach((btn) => {
      btn.addEventListener("click", () => switchBaseLayer(btn.dataset.layer, container));
    });

    return container;
  },
});

function switchBaseLayer(name, container) {
  if (name === activeBaseLayer || !baseLayers[name]) return;

  map.removeLayer(baseLayers[activeBaseLayer]);
  baseLayers[name].addTo(map);
  activeBaseLayer = name;

  container.querySelectorAll(".layer-btn").forEach((btn) => {
    btn.classList.toggle("is-active", btn.dataset.layer === name);
  });
}

function initControls() {
  // Rainfall slider (20 mm/h downpour - 120 mm/h extreme event)
  const slider = document.getElementById("rainfallSlider");
  const sliderValue = document.getElementById("rainfallValue");
  slider.addEventListener("input", () => {
    rainfallMm = Number(slider.value);
    sliderValue.textContent = `${rainfallMm} mm/h`;
    if (currentPolygon) simulate(currentAreaM2);
  });

  // Sponge type cards
  document.querySelectorAll(".sponge-card").forEach((card) => {
    card.addEventListener("click", () => {
      document
        .querySelectorAll(".sponge-card")
        .forEach((c) => c.classList.remove("is-active"));
      card.classList.add("is-active");
      selectedSponge = card.dataset.type;
      if (currentPolygon) simulate(currentAreaM2);
    });
  });

  // Drawing buttons
  document.getElementById("btnDraw").addEventListener("click", startDrawing);
  document.getElementById("btnClear").addEventListener("click", clearDrawing);

  // Methodology modal
  const modal = document.getElementById("methodologyModal");
  document
    .getElementById("btnMethodology")
    .addEventListener("click", () => modal.classList.remove("hidden"));
  document
    .getElementById("btnCloseModal")
    .addEventListener("click", () => modal.classList.add("hidden"));
  modal.addEventListener("click", (e) => {
    if (e.target === modal) modal.classList.add("hidden");
  });
}

/* ------------------------------------------------------------------ */
/* NASA risk layer                                                    */
/* ------------------------------------------------------------------ */
async function loadRiskZones() {
  try {
    const res = await fetch(`${API_BASE}/api/risk-zones`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    renderRiskZones(data);
    setApiStatus(true, "NASA data loaded");
  } catch (err) {
    console.error("Failed to load risk zones:", err);
    setApiStatus(false, "No API connection");
  }
}

function renderRiskZones(geojson) {
  if (!geojson || !geojson.features) return;

  const layer = L.geoJSON(geojson, {
    style: (feature) => {
      const color = RISK_COLORS[feature.properties.risk_level] || "#f59e0b";
      return {
        color,
        weight: 1.5,
        fillColor: color,
        fillOpacity: 0.42,
      };
    },
    onEachFeature: (feature, lyr) => {
      lyr.on("click", () => showZonePopup(feature, lyr));
    },
  });

  layer.addTo(map);
}

function showZonePopup(feature, lyr) {
  const p = feature.properties;
  const riskLabel = RISK_LABELS[p.risk_level] || p.risk_level;
  const html = `
    <div class="zone-popup">
      <div class="zone-popup-title">${p.name}</div>
      <table>
        <tr><td>Risk Level</td><td>${riskLabel} (${p.risk_score}/100)</td></tr>
        <tr><td>Impervious Ratio (Landsat)</td><td>%${p.impervious_ratio}</td></tr>
        <tr><td>DEM Slope</td><td>${p.slope_deg}°</td></tr>
        <tr><td>Mean Elevation</td><td>${p.elevation_m} m</td></tr>
        <tr><td>NDVI</td><td>${p.ndvi}</td></tr>
        <tr><td>District</td><td>${p.district}</td></tr>
      </table>
      <div style="margin-top:8px;color:#94a3b8;font-size:11.5px;">${p.description}</div>
    </div>`;
  lyr.bindPopup(html, { maxWidth: 300 }).openPopup();
}

/* ------------------------------------------------------------------ */
/* Drawing                                                            */
/* ------------------------------------------------------------------ */
function startDrawing() {
  new L.Draw.Polygon(map, {
    allowIntersection: false,
    showArea: true,
    shapeOptions: {
      color: "#00f0ff",
      weight: 2,
      fillColor: "#00f0ff",
      fillOpacity: 0.22,
    },
  }).enable();
}

function clearDrawing() {
  drawnItems.clearLayers();
  currentPolygon = null;
  currentAreaM2 = 0;
  document.getElementById("results").classList.add("hidden");
  document.getElementById("resultPlaceholder").classList.remove("hidden");
}

/* ------------------------------------------------------------------ */
/* Simulation                                                         */
/* ------------------------------------------------------------------ */
function handleNewPolygon(layer) {
  const latlngs = layer.getLatLngs()[0].map((ll) => [ll.lat, ll.lng]);

  // Turf.js requires a closed ring and [lng, lat] order
  const ring = latlngs.map(([lat, lng]) => [lng, lat]);
  ring.push(ring[0]);
  currentAreaM2 = turf.area(turf.polygon([ring]));

  simulate(currentAreaM2, latlngs);
}

async function simulate(areaM2, polygon) {
  if (!areaM2 && areaM2 !== 0) return;

  const payload = {
    area_m2: Number(areaM2.toFixed(2)),
    rainfall_mm: rainfallMm,
    sponge_type: selectedSponge,
    polygon: polygon || null,
  };

  try {
    const res = await fetch(`${API_BASE}/api/simulate-sponge`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    const result = await res.json();
    renderResults(result);
    setApiStatus(true, "NASA data loaded");
  } catch (err) {
    console.error("Simulation failed:", err);
    setApiStatus(false, "Simulation error");
  }
}

/* ------------------------------------------------------------------ */
/* Live impact cards                                                  */
/* ------------------------------------------------------------------ */
function renderResults(r) {
  document.getElementById("resultPlaceholder").classList.add("hidden");
  document.getElementById("results").classList.remove("hidden");

  // Animated counters
  animateValue(document.getElementById("statRetained"), r.retained_water_m3, 0);
  animateValue(document.getElementById("statReduction"), r.flood_reduction_pct, 1);
  animateValue(document.getElementById("statHouseholds"), r.households_protected, 0);

  // Comparison bar (before vs. after)
  const before = r.existing.runoff_volume_m3;
  const after = r.intervention.runoff_volume_m3;
  const max = Math.max(before, after, 1);
  document.getElementById("barBefore").style.height = `${(before / max) * 100}%`;
  document.getElementById("barAfter").style.height = `${(after / max) * 100}%`;
  document.getElementById("barBeforeValue").textContent = `${fmt(before, 1)} m³`;
  document.getElementById("barAfterValue").textContent = `${fmt(after, 1)} m³`;

  // Details
  document.getElementById("detArea").textContent = `${fmt(r.area_m2, 0)} m²`;
  document.getElementById("detRain").textContent = `${fmt(r.rainfall_mm, 0)} mm/h · ${r.sponge_label}`;
  document.getElementById("detBeforeCN").textContent = `CN ${r.existing.cn} (${r.existing.label})`;
  document.getElementById("detAfterCN").textContent = `CN ${r.intervention.cn}`;
  document.getElementById("detInfiltrated").textContent = `${fmt(r.infiltrated_water_m3, 1)} m³`;
  document.getElementById("detCost").textContent = `${fmt(r.estimated_cost_tl, 0)} ₺`;
}

/* Animated counter */
function animateValue(el, endValue, decimals = 0) {
  const startValue = 0;
  const duration = 850;
  const startTime = performance.now();

  function frame(now) {
    const progress = Math.min((now - startTime) / duration, 1);
    const eased = 1 - Math.pow(1 - progress, 3);
    const value = startValue + (endValue - startValue) * eased;
    el.textContent = fmt(value, decimals);
    if (progress < 1) requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);
}

/* en-US number formatting */
function fmt(value, decimals = 0) {
  return Number(value).toLocaleString("en-US", {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
}

/* ------------------------------------------------------------------ */
/* API status indicator                                               */
/* ------------------------------------------------------------------ */
function setApiStatus(online, text) {
  const el = document.getElementById("apiStatus");
  el.classList.remove("online", "offline");
  el.classList.add(online ? "online" : "offline");
  el.innerHTML = `<span class="dot"></span> ${text}`;
}

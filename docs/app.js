const RISK_COLORS = { Low: "#22c55e", Moderate: "#eab308", High: "#f97316", Critical: "#ef4444" };
const FEATURED_NAMES = ["Pallikaranai Marsh", "Chembarambakkam Lake"];

const map = L.map("map", { zoomControl: true }).setView([12.95, 80.05], 10);
L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
  attribution: "&copy; OpenStreetMap contributors",
  maxZoom: 19,
}).addTo(map);

map.createPane("recentPane");
map.getPane("recentPane").style.zIndex = 450;

let lakesLayer = null;
let lakesData = null;
let summaryData = null;
let bounds = null;
let overlays = {};
let lossChart = null;
let demoTimer = null;
let demoStage = 0;

const STAGES = [
  { name: "Satellite Data", caption: "Sentinel-2 SR Harmonized imagery (10m) + Dynamic World + JRC Global Surface Water, pulled directly via the Earth Engine Python API for the Chennai-Kancheepuram-Chengalpattu belt." },
  { name: "Image Processing", caption: "Per-scene cloud masking (SCL) and MNDWI computation isolate water pixels in every clear observation across each season." },
  { name: "Geospatial Analysis", caption: "Baseline (2019-20) vs. recent post-monsoon water extent are compared pixel-by-pixel to map exactly where water was lost or gained." },
  { name: "Indicator / Model", caption: "Lost water co-located with new built-up is classified as encroachment; a weighted Encroachment Risk Index (0-100) is computed per lake." },
  { name: "Interactive Map", caption: "Every lake polygon, colored by risk class, is explorable here — toggle layers, swipe before/after, or click a lake for details." },
  { name: "Decision / Recommendation", caption: "Rule-based recommendations rank lakes by urgency, from routine monitoring to immediate boundary enforcement." },
];

function setStage(i) {
  document.querySelectorAll(".stepper .step").forEach((el) => el.classList.remove("active"));
  const el = document.querySelector('.stepper .step[data-stage="' + i + '"]');
  if (el) el.classList.add("active");
  const cap = document.getElementById("demoCaption");
  cap.style.display = "block";
  cap.textContent = "Stage " + (i + 1) + " — " + STAGES[i].name + ": " + STAGES[i].caption;
}

function applyDemoStage(i) {
  setStage(i);
  const set = (id, on) => { document.getElementById(id).checked = on; toggleLayer(id, on); };
  if (i === 0) {
    set("lyr-baseline", false); set("lyr-recent", false); set("lyr-lost", false); set("lyr-builtup", false); set("lyr-lakes", false);
  } else if (i === 1) {
    set("lyr-baseline", true); set("lyr-recent", false); set("lyr-lost", false); set("lyr-builtup", false); set("lyr-lakes", false);
  } else if (i === 2) {
    set("lyr-baseline", true); set("lyr-recent", true); set("lyr-lost", true); set("lyr-builtup", false); set("lyr-lakes", false);
  } else if (i === 3) {
    set("lyr-baseline", false); set("lyr-recent", false); set("lyr-lost", false); set("lyr-builtup", true); set("lyr-lakes", true);
  } else if (i === 4) {
    set("lyr-baseline", false); set("lyr-recent", false); set("lyr-lost", false); set("lyr-builtup", false); set("lyr-lakes", true);
  } else if (i === 5) {
    set("lyr-baseline", false); set("lyr-recent", false); set("lyr-lost", false); set("lyr-builtup", false); set("lyr-lakes", true);
    if (lakesData && lakesData.features.length) {
      const candidates = lakesData.features.filter((f) => f.properties.baseline_ha >= REPORT_MIN_HA);
      const top = [...candidates].sort((a, b) => b.properties.risk_score - a.properties.risk_score)[0];
      if (top) {
        showDetail(top.properties, top);
        map.fitBounds(L.geoJSON(top).getBounds(), { maxZoom: 13 });
      }
    }
  }
}

document.getElementById("demoBtn").addEventListener("click", () => {
  const btn = document.getElementById("demoBtn");
  if (demoTimer) {
    clearInterval(demoTimer);
    demoTimer = null;
    btn.textContent = "▶ Demo Mode";
    document.getElementById("demoCaption").style.display = "none";
    document.querySelectorAll(".stepper .step").forEach((el) => el.classList.remove("active"));
    return;
  }
  btn.textContent = "■ Stop Demo";
  demoStage = 0;
  applyDemoStage(demoStage);
  demoTimer = setInterval(() => {
    demoStage = (demoStage + 1) % STAGES.length;
    applyDemoStage(demoStage);
  }, 4500);
});

function fmt(n, d) { return (n === undefined || n === null) ? "-" : Number(n).toFixed(d === undefined ? 1 : d); }

function loadKpis(summary) {
  const kpis = [
    ["Baseline water", fmt(summary.baseline_ha, 0) + " ha"],
    ["Recent water", fmt(summary.recent_ha, 0) + " ha"],
    ["Water lost", fmt(summary.lost_ha, 0) + " ha"],
    ["% loss", fmt(summary.pct_loss, 1) + "%"],
    ["Encroached", fmt(summary.encroached_ha, 0) + " ha"],
    ["Drying/siltation", fmt(summary.drying_ha, 0) + " ha"],
    ["CHIRPS rain ratio", fmt(summary.chirps_ratio, 2) + "x"],
    ["JRC agreement", fmt(summary.jrc_agreement_pct, 0) + "%"],
  ];
  const el = document.getElementById("kpis");
  el.innerHTML = kpis.map(([lbl, val]) => (
    '<div class="kpi"><div class="val">' + val + '</div><div class="lbl">' + lbl + "</div></div>"
  )).join("");
}

function styleLake(feature) {
  const cls = feature.properties.risk_class || "Low";
  return { color: RISK_COLORS[cls] || "#888", weight: 1.5, fillColor: RISK_COLORS[cls] || "#888", fillOpacity: 0.45 };
}

function showDetail(props, feature) {
  document.getElementById("detailEmpty").style.display = "none";
  const body = document.getElementById("detailBody");
  body.style.display = "block";
  const rows = [
    ["Baseline area", fmt(props.baseline_ha, 2) + " ha"],
    ["Recent area", fmt(props.recent_ha, 2) + " ha"],
    ["Area lost", fmt(props.lost_ha, 2) + " ha"],
    ["% lost", fmt(props.pct_lost, 1) + "%"],
    ["Encroached area", fmt(props.encroached_ha, 2) + " ha"],
    ["Drying/siltation", fmt(props.drying_ha, 2) + " ha"],
    ["New built-up in 250m buffer", fmt(props.new_builtup_pct_buffer, 1) + "%"],
    ["Built-up density (buffer)", fmt(props.built_density_buffer_pct, 1) + "%"],
    ["Distance to built-up", fmt(props.dist_to_built_m, 0) + " m"],
  ];
  const fragNote = props.fragment_count > 1
    ? '<div class="frag-note">Merged from ' + props.fragment_count + " connected water bodies (marsh/reservoir fragmentation at 30m)</div>"
    : "";
  body.innerHTML =
    "<h3 style=\"margin:0 0 4px\">" + (props.name || "Unnamed waterbody") + "</h3>" +
    '<span class="badge ' + props.risk_class + '">' + props.risk_class + " · " + fmt(props.risk_score, 0) + "</span>" +
    fragNote +
    rows.map(([l, v]) => '<div class="stat-row"><span>' + l + "</span><span>" + v + "</span></div>").join("") +
    '<div class="recommendation-box">' + props.recommendation + "</div>";
}

const REPORT_MIN_HA = 10; // priority list/chart exclude sub-10ha ponds (noisy at 10m S2 resolution)

function loadFeatured(features) {
  const el = document.getElementById("featured");
  const cards = FEATURED_NAMES.map((name) => {
    const f = features.find((x) => x.properties.name === name);
    if (!f) return '<div class="featured-card"><span class="name">' + name + "</span><br>not detected in this run</div>";
    const p = f.properties;
    const fragNote = p.fragment_count > 1 ? p.fragment_count + " connected water bodies merged" : "";
    return (
      '<div class="featured-card" data-id="' + p.id + '"><span class="name">' + name + "</span> " +
      '<span class="badge ' + p.risk_class + '">' + p.risk_class + "</span><br>" +
      fmt(p.baseline_ha, 1) + "ha -> " + fmt(p.recent_ha, 1) + "ha (" + fmt(p.pct_lost, 1) + "% lost)" +
      (fragNote ? '<div class="frag-note">' + fragNote + "</div>" : "")
    );
  });
  el.innerHTML = cards.join("");
  el.querySelectorAll(".featured-card[data-id]").forEach((card) => {
    card.addEventListener("click", () => {
      const f = features.find((x) => x.properties.id === card.dataset.id);
      if (f) {
        showDetail(f.properties, f);
        map.fitBounds(L.geoJSON(f).getBounds(), { maxZoom: 13 });
      }
    });
  });
}

function loadTop10(features) {
  const top = [...features]
    .filter((f) => f.properties.baseline_ha >= REPORT_MIN_HA)
    .sort((a, b) => b.properties.risk_score - a.properties.risk_score)
    .slice(0, 10);
  const tbody = document.querySelector("#top10 tbody");
  tbody.innerHTML = top.map((f) => (
    "<tr data-id=\"" + f.properties.id + "\"><td>" + (f.properties.name || "Unnamed") + "</td><td>" + fmt(f.properties.pct_lost, 0) +
    '%</td><td><span class="badge ' + f.properties.risk_class + '">' + f.properties.risk_class + "</span></td></tr>"
  )).join("");
  tbody.querySelectorAll("tr").forEach((tr, i) => {
    tr.addEventListener("click", () => {
      const f = top[i];
      showDetail(f.properties, f);
      map.fitBounds(L.geoJSON(f).getBounds(), { maxZoom: 14 });
    });
  });
  return top;
}

function loadChart(features) {
  const top = [...features]
    .filter((f) => f.properties.baseline_ha >= REPORT_MIN_HA)
    .sort((a, b) => b.properties.lost_ha - a.properties.lost_ha)
    .slice(0, 10);
  const ctx = document.getElementById("lossChart");
  if (lossChart) lossChart.destroy();
  lossChart = new Chart(ctx, {
    type: "bar",
    data: {
      labels: top.map((f) => f.properties.name || "Unnamed"),
      datasets: [{
        label: "Area lost (ha)",
        data: top.map((f) => f.properties.lost_ha),
        backgroundColor: top.map((f) => RISK_COLORS[f.properties.risk_class] || "#888"),
      }],
    },
    options: {
      indexAxis: "y",
      plugins: { legend: { display: false } },
      scales: {
        x: { ticks: { color: "#93a2c0" }, grid: { color: "#27324f" } },
        y: { ticks: { color: "#93a2c0", font: { size: 9 } }, grid: { display: false } },
      },
    },
  });
}

function toggleLayer(checkboxId, forceState) {
  const map_ = {
    "lyr-baseline": "baseline", "lyr-recent": "recent", "lyr-lost": "lost",
    "lyr-builtup": "builtup", "lyr-lakes": "lakes",
  };
  const key = map_[checkboxId];
  const on = forceState !== undefined ? forceState : document.getElementById(checkboxId).checked;
  const layer = overlays[key];
  if (!layer) return;
  if (on) { if (!map.hasLayer(layer)) layer.addTo(map); }
  else { if (map.hasLayer(layer)) map.removeLayer(layer); }
}

["lyr-baseline", "lyr-recent", "lyr-lost", "lyr-builtup", "lyr-lakes"].forEach((id) => {
  document.getElementById(id).addEventListener("change", () => toggleLayer(id));
});

document.getElementById("swipeSlider").addEventListener("input", (e) => {
  const pct = e.target.value;
  const pane = map.getPane("recentPane");
  if (pane) pane.style.clipPath = "inset(0 0 0 " + pct + "%)";
});

async function init() {
  try {
    [summaryData, bounds, lakesData] = await Promise.all([
      fetch("data/summary.json").then((r) => r.json()),
      fetch("data/bounds.json").then((r) => r.json()),
      fetch("data/lakes_risk.geojson").then((r) => r.json()),
    ]);
  } catch (e) {
    document.getElementById("kpis").innerHTML = '<div class="kpi"><div class="lbl">Data not generated yet. Run the pipeline export step.</div></div>';
    console.error("Failed to load data", e);
    return;
  }

  loadKpis(summaryData);

  const b = [[bounds.south, bounds.west], [bounds.north, bounds.east]];
  map.fitBounds(b);

  overlays.baseline = L.imageOverlay("data/baseline_water.png", b, { opacity: 0.85 });
  overlays.recent = L.imageOverlay("data/recent_water.png", b, { opacity: 0.85, pane: "recentPane" });
  overlays.lost = L.imageOverlay("data/water_lost.png", b, { opacity: 0.9 });
  overlays.builtup = L.imageOverlay("data/new_builtup.png", b, { opacity: 0.75 });
  overlays.lakes = L.geoJSON(lakesData, {
    style: styleLake,
    onEachFeature: (feature, layer) => {
      layer.on("click", () => showDetail(feature.properties, feature));
      layer.bindTooltip(feature.properties.name || "Unnamed waterbody");
    },
  });

  toggleLayer("lyr-baseline", true);
  toggleLayer("lyr-recent", false);
  toggleLayer("lyr-lost", false);
  toggleLayer("lyr-builtup", false);
  toggleLayer("lyr-lakes", true);

  loadFeatured(lakesData.features);
  loadTop10(lakesData.features);
  loadChart(lakesData.features);
}

init();

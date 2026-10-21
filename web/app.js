// Harbour Twin viewer. Everything it shows is produced by the `twin` pipeline into data/<aoi>/.
const AOI = new URLSearchParams(location.search).get("aoi") || "vancouver";
const DATA = `data/${AOI}/`;

const $ = (id) => document.getElementById(id);
const getJSON = (f) => fetch(DATA + f).then((r) => (r.ok ? r.json() : Promise.reject(new Error(f))));
const getI16 = (f) => fetch(DATA + f).then((r) => r.arrayBuffer()).then((b) => new Int16Array(b));
const fmt = (v, d = 2) => (v == null || Number.isNaN(v) ? "–" : Number(v).toFixed(d));

// ---------------------------------------------------------------- terrain
// Two int16 decimetre grids of WGS 84 ellipsoidal heights (core 5 m, context 20 m),
// sampled into Cesium heightmap tiles on demand. No Cesium ion token needed.
function makeSampler(grids, seaLevel) {
  const order = [grids.core, grids.context];
  return (lon, lat) => {
    for (const g of order) {
      if (lon < g.west || lon > g.east || lat < g.south || lat > g.north) continue;
      const fx = ((lon - g.west) / (g.east - g.west)) * (g.width - 1);
      const fy = ((g.north - lat) / (g.north - g.south)) * (g.height - 1);
      const x0 = Math.floor(fx), y0 = Math.floor(fy);
      const x1 = Math.min(x0 + 1, g.width - 1), y1 = Math.min(y0 + 1, g.height - 1);
      const tx = fx - x0, ty = fy - y0, d = g.data, w = g.width;
      const top = d[y0 * w + x0] * (1 - tx) + d[y0 * w + x1] * tx;
      const bot = d[y1 * w + x0] * (1 - tx) + d[y1 * w + x1] * tx;
      return (top * (1 - ty) + bot * ty) * g.scale;
    }
    return seaLevel;
  };
}

function terrainProvider(sample) {
  const N = 33;
  const provider = new Cesium.CustomHeightmapTerrainProvider({
    width: N,
    height: N,
    callback(x, y, level) {
      const r = provider.tilingScheme.tileXYToRectangle(x, y, level);
      const out = new Float32Array(N * N);
      const toDeg = Cesium.Math.toDegrees;
      const w = toDeg(r.west), e = toDeg(r.east), n = toDeg(r.north), s = toDeg(r.south);
      for (let j = 0; j < N; j++) {
        const lat = n - ((n - s) * j) / (N - 1);
        for (let i = 0; i < N; i++) out[j * N + i] = sample(w + ((e - w) * i) / (N - 1), lat);
      }
      return out;
    },
  });
  return provider;
}

// ---------------------------------------------------------------- building styles
const HEIGHT_RAMP = [
  [150, "#f4f1de"], [100, "#f2cc8f"], [60, "#e07a5f"], [30, "#b5838d"], [15, "#6d6875"], [0, "#4a4e69"],
];
const SOURCE_COLORS = { lidar: "#dfe7ee", overture: "#f2b134", floors: "#b084f5", default: "#7a828c" };

function buildingStyle(mode, level) {
  if (mode === "height")
    return new Cesium.Cesium3DTileStyle({
      color: { conditions: HEIGHT_RAMP.map(([h, c]) => [`\${height} >= ${h}`, `color('${c}')`]) },
    });
  if (mode === "source")
    return new Cesium.Cesium3DTileStyle({
      color: {
        conditions: [
          ...Object.entries(SOURCE_COLORS).map(([k, c]) => [`\${height_source} === '${k}'`, `color('${c}')`]),
          ["true", "color('#7a828c')"],
        ],
      },
    });
  if (mode === "flag")
    return new Cesium.Cesium3DTileStyle({
      color: { conditions: [["${height_flag} === true", "color('#ff6b5b')"], ["true", "color('#cfd8e0', 0.55)"]] },
    });
  // flood: reached by the sea at this still-water level
  return new Cesium.Cesium3DTileStyle({
    color: {
      conditions: [
        [`\${flood_level_cgvd2013} <= ${level.toFixed(2)}`, "color('#ff5a4a')"],
        [`\${flood_level_cgvd2013} <= ${(level + 1).toFixed(2)}`, "color('#ffb347')"],
        ["true", "color('#cfd8e0', 0.75)"],
      ],
    },
  });
}

function legendHTML(mode) {
  if (mode === "height") {
    const stops = [...HEIGHT_RAMP].reverse();
    return `<div class="ramp" style="background:linear-gradient(90deg,${stops.map((s) => s[1]).join(",")})"></div>
      <div class="ramp-labels">${stops.map((s) => `<span>${s[0]}</span>`).join("")}<span>m</span></div>`;
  }
  if (mode === "source")
    return Object.entries(SOURCE_COLORS).map(([k, c]) => `<span><i style="background:${c}"></i>${k}</span>`).join("");
  if (mode === "flag")
    return `<span><i style="background:#ff6b5b"></i>lidar height disagrees with Overture / floors</span>`;
  return `<span><i style="background:#ff5a4a"></i>reached at this level</span><span><i style="background:#ffb347"></i>within +1 m</span>`;
}

// ---------------------------------------------------------------- flood overlay
function floodCanvas(meta, fill, ground, level) {
  const { width: w, height: h } = meta;
  const canvas = document.createElement("canvas");
  canvas.width = w;
  canvas.height = h;
  const ctx = canvas.getContext("2d");
  const img = ctx.createImageData(w, h);
  const L = level * 10; // grids are decimetres
  let flooded = 0;
  for (let i = 0; i < w * h; i++) {
    if (fill[i] <= L && ground[i] > 3) { // land (above +0.3 m) that the sea reaches
      const depth = Math.min(1, (L - ground[i]) / 20); // full colour at 2 m depth
      const p = i * 4;
      img.data[p] = 40; img.data[p + 1] = 120 + 60 * (1 - depth); img.data[p + 2] = 255;
      img.data[p + 3] = 110 + 120 * depth;
      flooded++;
    }
  }
  ctx.putImageData(img, 0, 0);
  return { canvas, flooded };
}

// ---------------------------------------------------------------- weather
const WMO = { 0: "Clear", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast", 45: "Fog", 48: "Rime fog",
  51: "Light drizzle", 53: "Drizzle", 55: "Heavy drizzle", 61: "Light rain", 63: "Rain", 65: "Heavy rain",
  71: "Light snow", 73: "Snow", 75: "Heavy snow", 80: "Showers", 81: "Showers", 82: "Violent showers", 95: "Thunderstorm" };
const compass = (d) => ["N", "NE", "E", "SE", "S", "SW", "W", "NW"][Math.round(d / 45) % 8];

async function weather(lon, lat) {
  try {
    const u = `https://api.open-meteo.com/v1/forecast?latitude=${lat}&longitude=${lon}` +
      "&current=temperature_2m,wind_speed_10m,wind_direction_10m,weather_code,precipitation&wind_speed_unit=kn";
    const c = (await (await fetch(u)).json()).current;
    $("wx").innerHTML = `${WMO[c.weather_code] ?? "—"} · ${fmt(c.temperature_2m, 0)} °C · wind ${compass(c.wind_direction_10m)} ${fmt(c.wind_speed_10m, 0)} kn` +
      (c.precipitation ? ` · ${c.precipitation} mm` : "") + `<br><small class="hint">Open-Meteo, ${c.time} UTC</small>`;
  } catch {
    $("wx").textContent = "Weather unavailable";
  }
}

// ---------------------------------------------------------------- info panel
function showInfo(title, rows, note) {
  $("info-body").innerHTML = `<h3>${title}</h3><table>${rows
    .map(([k, v]) => `<tr><td>${k}</td><td>${v}</td></tr>`).join("")}</table>${note ? `<p class="note">${note}</p>` : ""}`;
  $("info").hidden = false;
}

// ---------------------------------------------------------------- main
async function main() {
  const [terrainMeta, s2, tide, summary, qc] = await Promise.all([
    getJSON("terrain.json"), getJSON("sentinel2.json"), getJSON("tide.json").catch(() => null),
    getJSON("buildings_summary.json"), getJSON("qc_report.json").catch(() => null),
  ]);
  const g = terrainMeta.grids;
  const [core, context, fill, ground] = await Promise.all([
    getI16(g.core.file), getI16(g.context.file), getI16("flood_level_core.bin"), getI16("ground_cgvd2013_core.bin"),
  ]);
  g.core.data = core;
  g.context.data = context;
  const geoid = terrainMeta.geoid_cgvd2013_to_wgs84; // CGVD2013 -> ellipsoid at the AOI

  const viewer = new Cesium.Viewer("globe", {
    baseLayer: new Cesium.ImageryLayer(new Cesium.UrlTemplateImageryProvider({
      url: "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png", subdomains: "abcd",
      credit: "© OpenStreetMap contributors © CARTO", maximumLevel: 19,
    })),
    terrainProvider: terrainProvider(makeSampler(g, terrainMeta.geoid_egm2008_to_wgs84)),
    baseLayerPicker: false, geocoder: false, sceneModePicker: false, navigationHelpButton: false,
    infoBox: false, selectionIndicator: false, fullscreenButton: false,
  });
  const scene = viewer.scene;
  scene.globe.depthTestAgainstTerrain = true;
  scene.globe.enableLighting = false;
  scene.highDynamicRange = false;

  // Sentinel-2 true colour, draped on the terrain
  const s2Layer = viewer.imageryLayers.addImageryProvider(await Cesium.SingleTileImageryProvider.fromUrl(
    DATA + "sentinel2.jpg", { rectangle: Cesium.Rectangle.fromDegrees(s2.west, s2.south, s2.east, s2.north),
      credit: "Contains modified Copernicus Sentinel-2 data" }));
  const scene0 = s2.scenes?.[0];
  if (scene0) $("s2-date").textContent = scene0.datetime.slice(0, 10);

  // Live weather radar (ECCC MSC GeoMet WMS)
  let radar = null;
  $("lyr-radar").onchange = (e) => {
    if (e.target.checked) {
      radar = viewer.imageryLayers.addImageryProvider(new Cesium.WebMapServiceImageryProvider({
        url: "https://geo.weather.gc.ca/geomet", layers: "RADAR_1KM_RRAI",
        parameters: { transparent: "true", format: "image/png" }, credit: "ECCC MSC GeoMet",
      }));
      radar.alpha = 0.75;
    } else if (radar) {
      viewer.imageryLayers.remove(radar);
      radar = null;
    }
  };
  $("lyr-s2").onchange = (e) => (s2Layer.show = e.target.checked);

  // Buildings: 3D Tiles 1.1 with EXT_structural_metadata
  const tileset = await Cesium.Cesium3DTileset.fromUrl(DATA + "buildings/tileset.json", { maximumScreenSpaceError: 6 });
  scene.primitives.add(tileset);
  $("lyr-buildings").onchange = (e) => (tileset.show = e.target.checked);

  // AIS replay (CZML); the viewer clock follows the capture window
  let ais = null;
  try {
    ais = await Cesium.CzmlDataSource.load(DATA + "ais.czml");
    await viewer.dataSources.add(ais);
    viewer.clock.shouldAnimate = true;
    viewer.timeline.zoomTo(viewer.clock.startTime, viewer.clock.stopTime);
    const cats = {};
    for (const e of ais.entities.values) {
      const c = e.properties?.category?.getValue() ?? "unknown";
      cats[c] = cats[c] || { n: 0, color: e.point.color.getValue(Cesium.JulianDate.now()).toCssColorString() };
      cats[c].n++;
    }
    $("ais-legend").innerHTML = Object.entries(cats).sort((a, b) => b[1].n - a[1].n)
      .map(([k, v]) => `<span><i style="background:${v.color}"></i>${k} ${v.n}</span>`).join("");
    const start = Cesium.JulianDate.toDate(viewer.clock.startTime), stop = Cesium.JulianDate.toDate(viewer.clock.stopTime);
    $("ais-note").textContent = `${ais.entities.values.length} vessels recorded live from aisstream.io, ` +
      `${start.toISOString().slice(0, 16).replace("T", " ")}–${stop.toISOString().slice(11, 16)} UTC. Click a vessel for details.`;
  } catch (err) {
    $("ais-note").textContent = "No AIS capture for this AOI yet.";
  }
  $("lyr-ais").onchange = (e) => ais && (ais.show = e.target.checked);

  // ---------------- flood planner
  let mode = "height";
  let level = parseFloat($("level").value);
  let floodLayer = null, pending = null;
  const rect = Cesium.Rectangle.fromDegrees(g.core.west, g.core.south, g.core.east, g.core.north);
  const cellArea = ((g.core.east - g.core.west) / g.core.width) * 111320 * Math.cos(Cesium.Math.toRadians((g.core.north + g.core.south) / 2)) *
    ((g.core.north - g.core.south) / g.core.height) * 111320;
  const levels = summary.flood_levels_sorted;
  const countAtOrBelow = (L) => { let lo = 0, hi = levels.length; while (lo < hi) { const m = (lo + hi) >> 1; levels[m] <= L ? (lo = m + 1) : (hi = m); } return lo; };

  async function redrawFlood() {
    const { canvas, flooded } = floodCanvas(g.core, fill, ground, level);
    const provider = await Cesium.SingleTileImageryProvider.fromUrl(canvas.toDataURL(), { rectangle: rect });
    const layer = viewer.imageryLayers.addImageryProvider(provider);
    if (floodLayer) viewer.imageryLayers.remove(floodLayer);
    floodLayer = layer;
    $("st-area").innerHTML = `${fmt((flooded * cellArea) / 1e4, 1)} <small>ha</small>`;
  }
  function setLevel(L, fromTide = false) {
    level = L;
    $("level").value = L;
    $("level-out").textContent = `${L.toFixed(2)} m`;
    $("st-bldg").innerHTML = `${countAtOrBelow(L).toLocaleString()} <small>of ${summary.buildings.toLocaleString()}</small>`;
    if (mode === "flood") tileset.style = buildingStyle(mode, level);
    clearTimeout(pending);
    pending = setTimeout(redrawFlood, fromTide ? 0 : 120);
  }
  $("level").oninput = (e) => { $("follow-tide").checked = false; setLevel(parseFloat(e.target.value)); };

  if (tide) {
    const lv = tide.levels;
    const presets = [
      ["Mean water level", lv.MWL], ["Mean higher high water", lv.MHHW], ["Highest astronomical tide", lv.HAT],
      [`Record high ${lv.HRWL?.date ?? ""}`, lv.HRWL],
    ].filter(([, v]) => v);
    if (lv.HRWL) {
      presets.push(["Record + 1 m sea-level rise", { cgvd2013_m: lv.HRWL.cgvd2013_m + 1 }]);
      presets.push(["Record + 2 m sea-level rise", { cgvd2013_m: lv.HRWL.cgvd2013_m + 2 }]);
    }
    $("presets").innerHTML = presets.map(([name, v]) =>
      `<button data-l="${v.cgvd2013_m}">${name}<span>${v.cgvd2013_m.toFixed(2)} m</span></button>`).join("");
    $("presets").onclick = (e) => {
      const b = e.target.closest("button");
      if (b) { $("follow-tide").checked = false; setLevel(parseFloat(b.dataset.l)); }
    };
    const series = tide.series.map(([t, v]) => [Date.parse(t), v]);
    let lastT = 0;
    viewer.clock.onTick.addEventListener((clock) => {
      if (!$("follow-tide").checked || !series.length) return;
      const t = Cesium.JulianDate.toDate(clock.currentTime).getTime();
      if (Math.abs(t - lastT) < 60_000) return; // update each simulated minute
      lastT = t;
      let i = series.findIndex(([ts]) => ts >= t);
      if (i <= 0) i = i === 0 ? 1 : series.length - 1;
      const [t0, v0] = series[i - 1], [t1, v1] = series[i];
      setLevel(v0 + ((v1 - v0) * Math.min(1, Math.max(0, (t - t0) / (t1 - t0)))), true);
    });
  }

  // ---------------- building colour mode
  $("style-mode").onclick = (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    mode = b.dataset.mode;
    for (const x of $("style-mode").children) x.classList.toggle("on", x === b);
    tileset.style = buildingStyle(mode, level);
    $("legend").innerHTML = legendHTML(mode);
  };
  tileset.style = buildingStyle(mode, level);
  $("legend").innerHTML = legendHTML(mode);
  setLevel(level);

  // ---------------- picking
  viewer.screenSpaceEventHandler.setInputAction((click) => {
    const f = scene.pick(click.position);
    if (f instanceof Cesium.Cesium3DTileFeature) {
      const p = (k) => f.getProperty(k);
      const fl = p("flood_level_cgvd2013");
      showInfo(p("name") || "Building", [
        ["Height", `${fmt(p("height"), 1)} m`],
        ["Height source", p("height_source")],
        ["Ground (CGVD2013)", `${fmt(p("ground_cgvd2013"), 2)} m`],
        ["Roof (CGVD2013)", `${fmt(p("roof_cgvd2013"), 1)} m`],
        ["Sea reaches it at", Number.isFinite(fl) ? `${fmt(fl, 2)} m` : "–"],
        ["Class", p("class") || "–"],
        ["Overture id", `<small>${(p("id") || "").slice(0, 18)}…</small>`],
      ], p("height_flag") ? "QC flag: lidar height disagrees with the Overture / floor-count height. Queued for review." : "");
    } else if (Cesium.defined(f) && f.id instanceof Cesium.Entity) {
      const e = f.id;
      const pos = e.position?.getValue(viewer.clock.currentTime);
      const c = pos && Cesium.Cartographic.fromCartesian(pos);
      $("info-body").innerHTML = `<h3>${e.name}</h3>${e.description?.getValue(viewer.clock.currentTime) ?? ""}` +
        (c ? `<p class="hint">${fmt(Cesium.Math.toDegrees(c.latitude), 4)}°, ${fmt(Cesium.Math.toDegrees(c.longitude), 4)}°</p>` : "");
      $("info").hidden = false;
    } else {
      $("info").hidden = true;
    }
  }, Cesium.ScreenSpaceEventType.LEFT_CLICK);
  $("info-close").onclick = () => ($("info").hidden = true);

  // ---------------- data quality panel
  const qcRows = [];
  const bySrc = summary.by_height_source;
  qcRows.push(["Lidar-measured heights", `${fmt((100 * bySrc.lidar) / summary.buildings, 1)}<small>%</small>`]);
  qcRows.push(["Heights flagged", `${summary.height_flags}`]);
  if (qc) {
    const c = qc.comparisons;
    const og = c["Copernicus GLO-30 vs lidar DTM, open ground"]?.result.by_slope["0-5 deg"];
    const mr = c["NRCan MRDEM-30 DTM vs lidar DTM"]?.result.by_slope["0-5 deg"];
    if (og) qcRows.push(["GLO-30 bias, flat open", `${fmt(og.bias_m)} <small>m</small>`]);
    if (mr) qcRows.push(["MRDEM LE90, flat", `${fmt(mr.le90_m)} <small>m</small>`]);
    const err = Object.entries(qc.datum_audit).find(([k]) => k.includes("error of the PROJ default"));
    if (err) qcRows.push(["PROJ default datum error", `${fmt(err[1])} <small>m</small>`]);
    qcRows.push(["Geoid N (EGM2008)", `${fmt(terrainMeta.geoid_egm2008_to_wgs84)} <small>m</small>`]);
  }
  $("qc").innerHTML = qcRows.map(([k, v]) => `<div><dt>${k}</dt><dd>${v}</dd></div>`).join("");
  $("qc-link").href = DATA + "QC_REPORT.md";
  $("about-link").onclick = (e) => { e.preventDefault(); $("about").showModal(); };

  weather((g.core.west + g.core.east) / 2, (g.core.south + g.core.north) / 2);

  // Opening view: from False Creek looking north over downtown to the North Shore mountains
  viewer.camera.setView({
    destination: Cesium.Cartesian3.fromDegrees(-123.128, 49.262, 900),
    orientation: { heading: Cesium.Math.toRadians(12), pitch: Cesium.Math.toRadians(-22), roll: 0 },
  });
  window.twin = { viewer, tileset, ais, setLevel }; // handy for debugging in the console
}

main().catch((err) => {
  console.error(err);
  document.body.insertAdjacentHTML("beforeend", `<p style="position:fixed;top:40%;width:100%;text-align:center">Failed to load: ${err.message}</p>`);
});

import { createLidarView } from "./lidar.js";
import { lidarDecoder } from "./lidar_stream.js";
import { createHud } from "./hud.js";
import { createOps, kmh } from "./ops.js";
import { createPalette } from "./palette.js";
import { createKpis, createRadio } from "./radio.js";
import { createDialog, createShell, isTyping, SPLITS, VIEWS } from "./shell.js";
import { applyTheme, onTheme, palette, renderChrome, setStatusSource, storedTheme, THEMES } from "./ui.js";
import { readMeta, videoFeed } from "./video.js";

const COLOURS = ["#9aa5e6", "#d9b98a", "#c98f86", "#9cbfa6"];
const feeds = document.getElementById("feeds");
const map = document.getElementById("map");
const ctx = map.getContext("2d");
const truckList = document.getElementById("trucks");
const barCamera = document.getElementById("bar-camera");
const stopButton = document.getElementById("stop-all");
const captionHint = document.getElementById("caption-hint");
const SITE_COLOURS = { ore: "#7fa38f", crusher: "#b0736a", dump: "#8f86a8", parking: "#b59a5e", fuel: "#b5835e" };
const lidarView = createLidarView(document.getElementById("lidar"));
const hud = createHud({ focusOnClick: false });

let state = { sim_connected: false, bounds: [-100, -100, 100, 100], trucks: [], obstacles: [], paths: [], pedestrians: [], cameras: [] };
let network = { roads: [], sites: [], junctions: [] };
let worldSeq = null;
let preview = null;
let selected = null;
let PX = 1;
const fit = { s: 1, ox: 0, oy: 0 };
const tiles = new Map();
const decoder = new TextDecoder();
let stopLidar = null;

const shell = createShell({ onView: (v) => { layoutOps(); renderCaption(); resizeMap(); if (v === "judge") ops.renderQr(); }, onCamera: (name) => { hud.setDetail(name); renderCaption(); renderTruckList(); } });
const kpis = createKpis();
const radio = createRadio({ onEvent: (ev) => ops.addLine(ev), onState: (msg) => kpis.update(msg) });
const ops = createOps({ radio, kpis });
setStatusSource(ops.status);
fetch("/radio/data/worlds/quarry/world.json").then((r) => (r.ok ? r.json() : null)).then((w) => {
  const cap = w?.trucks?.[0]?.capacity_t ?? w?.design?.design_truck?.payload_t;
  if (cap) kpis.capacity = cap;
}).catch(() => {});
const help = createDialog(document.getElementById("help"));
const commandPalette = createPalette({ root: document.getElementById("palette"), commands });
const schedulePalette = createPalette({ root: document.getElementById("schedule"), commands: schedule, recents: false });
setInterval(() => schedulePalette.refresh(), 500);

function wsUrl(path) {
  return `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}${path}`;
}

function connect(path, onMessage, binary = false) {
  let delay = 500;
  let ws;
  let stopped = false;
  const open = () => {
    ws = new WebSocket(wsUrl(path));
    if (binary) ws.binaryType = "arraybuffer";
    ws.onopen = () => { delay = 500; };
    ws.onmessage = onMessage;
    ws.onclose = () => {
      if (stopped) return;
      setTimeout(open, delay);
      delay = Math.min(delay * 2, 5000);
    };
  };
  open();
  return () => { stopped = true; ws.close(); };
}

function truckColour(id) {
  const i = state.trucks.findIndex((t) => t.id === id);
  return COLOURS[(i < 0 ? 0 : i) % COLOURS.length];
}

function cameraOrder() {
  return [...tiles.keys()].sort((a, b) => (a === "overview" ? 1 : b === "overview" ? -1 : a.localeCompare(b)));
}

function truckCams() {
  return state.trucks.map((t) => t.id).filter((id) => tiles.has(id));
}

const SEAM = 0.5;
const opsSlots = new Set();

function place(el, [x, y, w, h]) {
  el.style.left = `calc(${x}% + ${x > 0 ? SEAM : 0}px)`;
  el.style.top = `calc(${y}% + ${y > 0 ? SEAM : 0}px)`;
  el.style.width = `calc(${w}% - ${(x > 0 ? SEAM : 0) + (x + w < 100 ? SEAM : 0)}px)`;
  el.style.height = `calc(${h}% - ${(y > 0 ? SEAM : 0) + (y + h < 100 ? SEAM : 0)}px)`;
  el.style.right = el.style.bottom = "auto";
}

function unplace(el) {
  for (const k of ["left", "top", "width", "height", "right", "bottom"]) el.style[k] = "";
}

function opsRects() {
  const trucks = truckCams();
  const primary = trucks.includes(shell.camera) ? shell.camera : trucks[0];
  const others = trucks.filter((id) => id !== primary);
  const overview = tiles.has("overview");
  const W = innerWidth, H = innerHeight;
  const r = new Map();
  if (W <= 720) {
    const S = others.length ? Math.min(20, ((100 / others.length) * W * 9) / 16 / H) : 0;
    const P = 48 - S / 2;
    if (primary) r.set(primary, [0, 0, 100, P]);
    others.forEach((id, i) => r.set(id, [(i * 100) / others.length, P, 100 / others.length, S]));
    if (overview) r.set("overview", [0, P + S, 50, 100 - P - S]);
    r.set("map", overview ? [50, P + S, 50, 100 - P - S] : [0, P + S, 100, 100 - P - S]);
  } else {
    const L = primary ? 60 : 0;
    const S = others.length ? Math.min(30, ((L / others.length) * W * 9) / 16 / H) : 0;
    if (primary) r.set(primary, [0, 0, L, 100 - S]);
    others.forEach((id, i) => r.set(id, [(i * L) / others.length, 100 - S, L / others.length, S]));
    if (overview) r.set("overview", [L, 0, 100 - L, 50]);
    r.set("map", overview ? [L, 50, 100 - L, 50] : [L, 0, 100 - L, 100]);
  }
  return { r, primary };
}

function layoutOps() {
  const on = shell.view === "ops";
  const mapSurface = document.getElementById("surface-map");
  if (!on) {
    for (const name of opsSlots) {
      const t = tiles.get(name);
      if (t) { unplace(t.el); t.el.classList.remove("slot", "primary", "thumb"); }
    }
    opsSlots.clear();
    mapSurface.style.width = mapSurface.style.height = "";
    return;
  }
  const { r, primary } = opsRects();
  if (primary && shell.camera !== primary) shell.setCamera(primary, tiles);
  for (const [name, t] of tiles) {
    const rect = r.get(name);
    t.el.classList.toggle("slot", !!rect);
    t.el.classList.toggle("primary", name === primary);
    t.el.classList.toggle("thumb", !!rect && name !== primary && name !== "overview");
    if (rect) { place(t.el, rect); opsSlots.add(name); } else unplace(t.el);
  }
  place(mapSurface, r.get("map"));
}

addEventListener("resize", () => { if (shell.view === "ops") layoutOps(); });

function tileFor(name) {
  let tile = tiles.get(name);
  if (tile) return tile;
  const el = document.createElement("div");
  el.className = "feed";
  el.dataset.camera = name;
  el.innerHTML = `<canvas></canvas><div class="feed-wait">no signal</div><span class="feed-name"></span>`;
  el.querySelector(".feed-name").textContent = name;
  el.addEventListener("click", () => { if (el.classList.contains("thumb")) showCamera(name); });
  el.querySelector("canvas").setAttribute("aria-label", `${name} camera`);
  feeds.append(el);
  tile = { el, canvas: el.querySelector("canvas"), frames: 0, fps: 0, busy: false };
  tiles.set(name, tile);
  hud.attach(name, el, tile.canvas);
  tile.onFrame = (meta) => hud.frame(name, meta);
  document.getElementById("feeds-empty").hidden = true;
  const active = shell.camera && tiles.has(shell.camera) ? shell.camera : name;
  if (!feeds.querySelector(".feed.active") || active === name) shell.setCamera(active, tiles);
  if (shell.view === "ops") layoutOps();
  return tile;
}

async function onVideo(ev) {
  const buf = new Uint8Array(ev.data);
  const n = buf[0];
  const name = decoder.decode(buf.subarray(1, 1 + n));
  const tile = tileFor(name);
  if (tile.busy) return;
  tile.busy = true;
  try {
    const { meta, end } = readMeta(buf, 1 + n);
    const bmp = await createImageBitmap(new Blob([buf.subarray(end)], { type: "image/jpeg" }));
    if (tile.canvas.width !== bmp.width) tile.canvas.width = bmp.width;
    if (tile.canvas.height !== bmp.height) tile.canvas.height = bmp.height;
    tile.canvas.getContext("2d").drawImage(bmp, 0, 0);
    bmp.close();
    tile.frames++;
    tile.el.classList.add("live");
    tile.onFrame(meta);
  } finally {
    tile.busy = false;
  }
}

setInterval(() => {
  for (const tile of tiles.values()) {
    tile.fps = tile.frames;
    tile.el.classList.toggle("stale", tile.frames === 0);
    tile.frames = 0;
  }
  renderCaption();
}, 1000);

function resizeMap() {
  const dpr = Math.min(devicePixelRatio || 1, 2);
  const w = Math.max(1, Math.round(map.clientWidth * dpr));
  const h = Math.max(1, Math.round(map.clientHeight * dpr));
  if (map.width !== w || map.height !== h) { map.width = w; map.height = h; }
  const [xmin, zmin, xmax, zmax] = state.bounds;
  const W = xmax - xmin, H = zmax - zmin;
  const pts = [...network.sites.map((p) => [p.x, p.z]), ...network.roads.flatMap((r) => r.points.map((p) => [p.x, p.z]))];
  const box = pts.length
    ? pts.reduce((b, [x, z]) => [Math.min(b[0], x), Math.min(b[1], z), Math.max(b[2], x), Math.max(b[3], z)], [Infinity, Infinity, -Infinity, -Infinity])
    : [xmin, zmin, xmax, zmax];
  const pad = 1.1;
  const sContent = Math.min(w / ((box[2] - box[0]) * pad || W), h / ((box[3] - box[1]) * pad || H));
  fit.s = Math.min(Math.max(w / W, h / H), sContent);
  const place = (size, span, centre, lo) => {
    const o = size / 2 - (centre - lo) * fit.s;
    return span * fit.s >= size ? Math.min(0, Math.max(size - span * fit.s, o)) : (size - span * fit.s) / 2;
  };
  fit.ox = place(w, W, (box[0] + box[2]) / 2, xmin);
  fit.oy = place(h, H, zmax - (box[1] + box[3]) / 2, 0);
  PX = Math.min(w, h) / 600;
  drawMap();
}

new ResizeObserver(resizeMap).observe(map);

function toCanvas(x, z) {
  const [xmin, , , zmax] = state.bounds;
  return [fit.ox + (x - xmin) * fit.s, fit.oy + (zmax - z) * fit.s];
}

function toWorld(px, py) {
  const [xmin, , , zmax] = state.bounds;
  return [xmin + (px - fit.ox) / fit.s, zmax - (py - fit.oy) / fit.s];
}

function metresToPx(m) {
  return m * fit.s;
}

async function loadWorld() {
  const w = await (await fetch("/api/world")).json();
  network = { roads: w.roads, sites: w.sites, junctions: w.junctions };
  preview = null;
  document.documentElement.classList.remove("imagery");
  const aligned = w.preview_bounds && w.preview_bounds.every((v, i) => Math.abs(v - w.bounds[i]) < 0.01);
  if (w.preview && aligned && w.roads.length) {
    const img = new Image();
    img.onload = () => { preview = img; document.documentElement.classList.add("imagery"); drawMap(); };
    img.src = `/api/world/preview?seq=${worldSeq}`;
  }
  resizeMap();
  fillSiteSelect();
}

function polyline(points) {
  ctx.beginPath();
  points.forEach((p, i) => { const [x, y] = toCanvas(p[0], p[1]); i ? ctx.lineTo(x, y) : ctx.moveTo(x, y); });
}

function drawNetwork() {
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  ctx.strokeStyle = palette.road;
  for (const r of network.roads) {
    ctx.lineWidth = Math.max(metresToPx(r.width_m), 2 * PX);
    polyline(r.points.map((p) => [p.x, p.z]));
    ctx.stroke();
  }
  ctx.fillStyle = palette.junction;
  for (const j of network.junctions) {
    const [cx, cy] = toCanvas(j.x, j.z);
    ctx.beginPath(); ctx.arc(cx, cy, 2 * PX, 0, Math.PI * 2); ctx.fill();
  }
  if (preview) return;
  ctx.font = `500 ${11 * PX}px Inter, system-ui, sans-serif`;
  ctx.textAlign = "center";
  network.sites.forEach((s, i) => {
    const [cx, cy] = toCanvas(s.x, s.z);
    ctx.fillStyle = SITE_COLOURS[s.type] || palette.inkMuted;
    ctx.fillRect(cx - 3.5 * PX, cy - 3.5 * PX, 7 * PX, 7 * PX);
    const dy = (i % 2 ? 18 : -11) * PX;
    ctx.lineWidth = 4 * PX;
    ctx.strokeStyle = palette.halo;
    ctx.strokeText(s.name, cx, cy + dy);
    ctx.fillStyle = palette.inkMuted;
    ctx.fillText(s.name, cx, cy + dy);
  });
}

function drawMap() {
  ctx.clearRect(0, 0, map.width, map.height);
  const [x0, y0] = toCanvas(state.bounds[0], state.bounds[3]);
  const [x1, y1] = toCanvas(state.bounds[2], state.bounds[1]);
  if (preview) {
    ctx.globalAlpha = palette.previewAlpha;
    ctx.drawImage(preview, x0, y0, x1 - x0, y1 - y0);
    ctx.globalAlpha = 1;
  } else {
    ctx.strokeStyle = palette.grid;
    ctx.lineWidth = 1;
    for (let i = 0; i <= 10; i++) {
      const px = x0 + ((x1 - x0) * i) / 10, py = y0 + ((y1 - y0) * i) / 10;
      ctx.beginPath(); ctx.moveTo(px, y0); ctx.lineTo(px, y1); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(x0, py); ctx.lineTo(x1, py); ctx.stroke();
    }
  }
  drawNetwork();
  ctx.fillStyle = palette.obstacle;
  for (const o of state.obstacles) {
    const [cx, cy] = toCanvas(o.x, o.z);
    ctx.beginPath(); ctx.arc(cx, cy, Math.max(metresToPx(o.r), 1.2 * PX), 0, Math.PI * 2); ctx.fill();
  }
  ctx.strokeStyle = palette.path;
  ctx.lineWidth = PX;
  ctx.setLineDash([3 * PX, 4 * PX]);
  for (const path of state.paths) {
    if (path.length < 2) continue;
    ctx.beginPath();
    path.forEach((p, i) => { const [x, y] = toCanvas(p.x, p.z); i ? ctx.lineTo(x, y) : ctx.moveTo(x, y); });
    if (path.length > 2) ctx.closePath();
    ctx.stroke();
  }
  ctx.setLineDash([]);
  ctx.fillStyle = palette.pedestrian;
  for (const p of state.pedestrians) {
    const [cx, cy] = toCanvas(p.x, p.z);
    ctx.beginPath(); ctx.arc(cx, cy, 2.5 * PX, 0, Math.PI * 2); ctx.fill();
  }
  const hz = ops.hazard;
  if (hz) {
    const [hx, hy] = toCanvas(hz.ped.x, hz.ped.z);
    ctx.strokeStyle = palette.pedestrian;
    ctx.lineWidth = PX;
    ctx.globalAlpha = 0.9;
    ctx.beginPath(); ctx.arc(hx, hy, metresToPx(ops.thresholds.CRIT_M), 0, Math.PI * 2); ctx.stroke();
    ctx.globalAlpha = 0.45;
    ctx.beginPath(); ctx.arc(hx, hy, metresToPx(ops.thresholds.WARN_M), 0, Math.PI * 2); ctx.stroke();
    ctx.globalAlpha = 1;
  }
  if (radio.online) {
    for (const e of radio.state.excavators || []) {
      const [ex, ey] = toCanvas(e.x, e.z);
      ctx.save();
      ctx.translate(ex, ey);
      ctx.rotate(Math.PI / 4);
      ctx.strokeStyle = palette.ink;
      ctx.fillStyle = palette.ink;
      ctx.lineWidth = 1.2 * PX;
      const r = 4 * PX;
      if (e.state === "digging") ctx.fillRect(-r, -r, 2 * r, 2 * r);
      else ctx.strokeRect(-r, -r, 2 * r, 2 * r);
      ctx.restore();
    }
  }
  for (const t of state.trucks) {
    const colour = truckColour(t.id);
    const [cx, cy] = toCanvas(t.x, t.z);
    if (t.goal) {
      const [gx, gy] = toCanvas(t.goal[0], t.goal[1]);
      ctx.strokeStyle = colour;
      ctx.lineWidth = 1.5 * PX;
      if (t.route && t.route.length) {
        polyline([[t.x, t.z], ...t.route]);
        ctx.stroke();
      } else {
        ctx.setLineDash([4 * PX, 4 * PX]);
        ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(gx, gy); ctx.stroke();
        ctx.setLineDash([]);
      }
      ctx.beginPath(); ctx.arc(gx, gy, 6 * PX, 0, Math.PI * 2); ctx.stroke();
    }
    const cands = t.candidates || [];
    const scores = t.candidate_scores || [];
    if (cands.length) {
      ctx.save();
      ctx.strokeStyle = colour;
      ctx.lineWidth = 1 * PX;
      ctx.globalAlpha = 0.18;
      cands.forEach((c, i) => { if (scores[i] != null && i !== t.choice) { polyline([[t.x, t.z], ...c]); ctx.stroke(); } });
      const chosen = cands[t.choice];
      if (chosen) {
        ctx.globalAlpha = 0.9;
        ctx.lineWidth = 4 * PX;
        polyline([[t.x, t.z], ...chosen]);
        ctx.stroke();
      }
      ctx.restore();
    }
    ctx.save();
    ctx.translate(cx, cy);
    ctx.rotate((t.yaw * Math.PI) / 180);
    ctx.scale(PX, PX);
    ctx.fillStyle = colour;
    ctx.strokeStyle = t.id === selected ? palette.selected : palette.outline;
    ctx.lineWidth = 1.5;
    ctx.beginPath(); ctx.moveTo(0, -9); ctx.lineTo(6, 7); ctx.lineTo(-6, 7); ctx.closePath();
    ctx.fill(); ctx.stroke();
    ctx.restore();
  }
}

const onLidar = lidarDecoder((points) => lidarView.update(points));

function select(id) {
  if (id === selected) return;
  selected = id;
  if (stopLidar) stopLidar();
  lidarView.clear();
  stopLidar = connect(`/ws/lidar?truck=${encodeURIComponent(id)}`, onLidar, true);
  renderTruckList();
  renderCaption();
  drawMap();
}

function sendGoal(id, body) {
  return fetch(`/api/trucks/${encodeURIComponent(id)}/goal`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

function stopTruck(id) {
  return fetch(`/api/trucks/${encodeURIComponent(id)}/goal`, { method: "DELETE" });
}

async function stopAll() {
  const [rel] = await Promise.allSettled([radio.release(), ...state.trucks.map((t) => stopTruck(t.id))]);
  ops.notice(rel.status === "fulfilled" ? "all stopped · trucks and excavators holding" : "trucks stopped · radio unreachable");
}

let armedAt = 0;
let armTimer = 0;
function armedStop() {
  clearTimeout(armTimer);
  if (Date.now() - armedAt < 2000) {
    armedAt = 0;
    stopButton.classList.remove("armed");
    stopButton.textContent = "stop";
    stopAll();
  } else {
    armedAt = Date.now();
    stopButton.classList.add("armed");
    stopButton.textContent = "stop all?";
    armTimer = setTimeout(() => { stopButton.classList.remove("armed"); stopButton.textContent = "stop"; }, 2000);
  }
}

stopButton.addEventListener("click", armedStop);

async function sendRadio(text) {
  try {
    await radio.command(text);
  } catch {
    ops.notice("radio unreachable");
  }
}

function fillSiteSelect() {
  commandPalette.refresh();
}

const truckItems = new Map();

function renderTruckList() {
  for (const t of state.trucks) {
    let li = truckItems.get(t.id);
    if (!li) {
      li = document.createElement("li");
      li.innerHTML = `<span class="id"></span><span class="st"></span><span class="sp"></span><span class="go"></span>`;
      li.dataset.truck = t.id;
      li.querySelector(".id").textContent = t.id;
      truckList.append(li);
      truckItems.set(t.id, li);
    }
    li.classList.toggle("selected", t.id === selected);
    li.classList.toggle("viewed", t.id === shell.camera);
    li.querySelector(".st").textContent = t.status;
    li.querySelector(".sp").textContent = kmh(t.speed);
    const task = ops.truckLine(t).split(" · ")[0];
    const goal = task !== t.status ? task : t.goal_site ? `to ${siteName(t.goal_site)}` : t.goal ? `to ${t.goal[0].toFixed(0)}, ${t.goal[1].toFixed(0)}` : "";
    const ctl = t.id === selected && (t.throttle || t.steer) ? `  thr ${(t.throttle ?? 0).toFixed(2)}  str ${(t.steer ?? 0) >= 0 ? "+" : ""}${(t.steer ?? 0).toFixed(2)}` : "";
    li.querySelector(".go").textContent = goal + ctl;
  }
}

function siteName(id) {
  const s = network.sites.find((n) => n.id === id);
  return s ? s.name : id;
}

const caption = document.querySelector(".chrome-bottom");

function renderCaption() {
  const view = shell.view;
  const cam = shell.camera;
  const fps = tiles.get(cam)?.fps;
  ops.renderKpis();
  const onVideo = view === "video" || view === "ops" || (view === "split" && shell.split.includes("video"));
  caption.toggleAttribute("data-only", onVideo && state.trucks.some((t) => t.id === cam));
  barCamera.textContent = onVideo ? cam || "" : view === "lidar" ? selected || "" : "";
  captionHint.textContent = onVideo && cam && fps !== undefined ? `${fps} fps` : "";
}

function onState(ev) {
  state = JSON.parse(ev.data);
  if (state.world_seq !== worldSeq) {
    worldSeq = state.world_seq;
    loadWorld();
  }
  if (!selected && state.trucks.length) select(state.trucks[0].id);
  ops.safety(state);
  ops.renderRoster(state, siteName);
  renderTruckList();
  drawMap();
  renderChrome(state, { tileFor, tiles, truckColour, selected });
  hud.setState(state);
}

map.addEventListener("click", (e) => {
  if (!selected) return;
  const r = map.getBoundingClientRect();
  const [x, z] = toWorld(((e.clientX - r.left) / r.width) * map.width, ((e.clientY - r.top) / r.height) * map.height);
  sendGoal(selected, { x, z });
});

function showCamera(name) {
  if (!tiles.has(name)) return;
  shell.setCamera(name, tiles);
  if (shell.view === "ops") { if (name === "overview") shell.setView("video"); else layoutOps(); return; }
  if (!(shell.view === "video" || (shell.view === "split" && shell.split.includes("video")))) shell.setView("video");
}

function focusTruck(id) {
  select(id);
  if (tiles.has(id)) showCamera(id);
}

const VIEW_KEYS = { ops: "O", video: "V", map: "M", lidar: "L", fleet: "F", radio: "R", judge: "J", split: "S" };
const VIEW_BY_KEY = Object.fromEntries(Object.entries(VIEW_KEYS).map(([v, k]) => [k.toLowerCase(), v]));
const SPLIT_LABEL = (s) => s.split(" ").join(" · ");
const URLISH = /^(?:radio url|url)\s+(\S+)$/i;
const PINISH = /^(?:pin|access code)\s+(\S+)$/i;

function commands(query = "") {
  const out = [];
  for (const v of VIEWS) out.push({ id: `view:${v}`, group: "view", title: v[0].toUpperCase() + v.slice(1), keywords: "tab view show go", detail: shell.view === v ? "current" : "", keys: [VIEW_KEYS[v]], run: () => shell.setView(v) });
  cameraOrder().forEach((name, i) => out.push({ id: `camera:${name}`, group: "camera", title: `View ${name}`, keywords: "camera cam feed video", detail: shell.camera === name ? "current" : "", keys: i < 9 ? [String(i + 1)] : null, run: () => showCamera(name) }));
  if (state.trucks.length) out.push({ id: "stop:all", group: "fleet", title: "Stop all", keywords: "everything trucks excavators halt release emergency estop", detail: "trucks · excavators · radio jobs", keys: ["⇧", "esc"], confirm: "Press ↵ again to stop everything", run: stopAll });
  for (const t of state.trucks) {
    out.push({ id: `focus:${t.id}`, group: "fleet", title: `Focus ${t.id}`, keywords: "select follow truck", detail: t.status, run: () => focusTruck(t.id) });
    out.push({ id: `stop:${t.id}`, group: "fleet", title: `Stop ${t.id}`, keywords: "halt cancel goal truck", detail: t.goal_site ? siteName(t.goal_site) : "", run: () => stopTruck(t.id) });
  }
  for (const t of state.trucks) {
    for (const s of network.sites) {
      out.push({ id: `send:${t.id}:${s.id}`, group: "send", title: `Send ${t.id} to ${s.name}`, keywords: `${s.type} ${s.id} goto dispatch route drive`, detail: s.type, searchOnly: true, run: () => { select(t.id); sendGoal(t.id, { site: s.id }); } });
    }
  }
  for (const s of SPLITS) out.push({ id: `split:${s}`, group: "layout", title: `Split ${SPLIT_LABEL(s)}`, keywords: "custom layout tiles", detail: shell.view === "split" && shell.split === s ? "current" : "", run: () => shell.setSplit(s) });
  out.push({ id: "radio:audio", group: "radio", title: ops.audio ? "Mute radio replies" : "Play radio replies", keywords: "audio sound voice tts", run: () => ops.setAudio(!ops.audio) });
  out.push({ id: "ops:reset", group: "radio", title: "Reset KPIs", keywords: "loads tonnes cycle idle counters", run: () => { kpis.reset(); renderCaption(); ops.notice("kpis reset"); } });
  if (ops.judge.url) out.push({ id: "radio:aerial", group: "radio", title: "Open aerial view", keywords: "3d view scene", run: () => window.open(`${ops.judge.url.replace(/\/$/, "")}/view`, "_blank", "noopener") });
  const url = query.match(URLISH);
  out.push(url
    ? { id: "judge:url", group: "radio", title: `Set radio URL to ${url[1]}`, keywords: query, run: () => ops.setJudge({ url: url[1] }) }
    : { id: "judge:url", group: "radio", title: "Set radio URL…", keywords: "qr judge phone public link", searchOnly: true, run: () => setTimeout(() => commandPalette.open("radio url "), 160) });
  const pin = query.match(PINISH);
  out.push(pin
    ? { id: "judge:pin", group: "radio", title: `Set access code to ${pin[1]}`, keywords: query, run: () => ops.setJudge({ pin: pin[1] }) }
    : { id: "judge:pin", group: "radio", title: "Set access code…", keywords: "pin judge", searchOnly: true, run: () => setTimeout(() => commandPalette.open("pin "), 160) });
  const theme = storedTheme();
  for (const th of THEMES) out.push({ id: `theme:${th}`, group: "appearance", title: `Theme ${th}`, keywords: "appearance colour mode", detail: theme === th ? "current" : "", run: () => applyTheme(th) });
  out.push({ id: "help:keys", group: "help", title: "Keyboard shortcuts", keywords: "help keys", keys: ["?"], run: () => help.open() });
  if (query && !url && !pin) out.push({ id: "radio:send", group: "radio", title: `Send to radio: “${query}”`, always: true, run: () => sendRadio(query) });
  return out;
}

const PHASE_LABEL = { idle: "idle", to_site: "en route", to_load: "to load", loading: "loading", to_dump: "hauling", dumping: "dumping", returning: "returning" };

function eta(m, speed) {
  if (m == null || !(speed > 0.5)) return "";
  const s = Math.round(m / speed);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

function scheduleTruck(id) {
  schedulePalette.close(true);
  select(id);
  if (shell.view !== "ops") shell.setView("ops");
  if (tiles.has(id)) shell.setCamera(id, tiles);
  layoutOps();
}

function schedule() {
  const pad = (v, n) => String(v ?? "").padEnd(n);
  const lpad = (v, n) => String(v ?? "").padStart(n);
  const out = state.trucks.map((t) => {
    const r = radio.online ? radio.trucks.get(t.id) : null;
    const phase = r?.phase && r.phase !== "idle" ? PHASE_LABEL[r.phase] || r.phase : t.status;
    const dest = r?.target || (t.goal_site ? siteName(t.goal_site) : "");
    const metres = r?.remaining_m ?? null;
    const load = r?.load ?? t.load;
    const detail = [pad(dest, 16), lpad(metres != null ? `${metres} m` : "", 7), lpad(eta(metres, t.speed), 6), lpad(kmh(t.speed), 8), lpad(load != null ? `${load} t` : "", 5)].join("  ");
    return { id: `sched:${t.id}`, group: "trucks", title: `${t.id} · ${phase}`, keywords: `${dest} ${r?.task || ""}`, detail, run: () => scheduleTruck(t.id) };
  });
  for (const e of radio.online ? radio.state.excavators || [] : []) {
    out.push({ id: `sched:${e.id}`, group: "excavators", title: `${e.id} · ${e.state || ""}`, keywords: e.face || "", detail: pad(e.face_name || "", 16), run: () => {} });
  }
  return out;
}

const SHORTCUTS = [
  ["Command palette", ["/"]],
  ["Command palette", ["⌘", "K"]],
  ["Schedule", ["\\"]],
  ["Ops · video · map · lidar", ["O", "V", "M", "L"]],
  ["Fleet · radio · judge · split", ["F", "R", "J", "S"]],
  ["Stop everything (twice)", ["⇧", "esc"]],
  ["Camera", ["1", "–", "9"]],
  ["Shortcuts", ["?"]],
  ["Close", ["esc"]],
];

document.querySelector("#help .help-list").append(...SHORTCUTS.flatMap(([label, keys]) => {
  const dt = document.createElement("dt");
  dt.textContent = label;
  const dd = document.createElement("dd");
  for (const k of keys) {
    const kbd = document.createElement("kbd");
    kbd.textContent = k;
    dd.append(kbd);
  }
  return [dt, dd];
}));

document.getElementById("palette-open").addEventListener("click", () => commandPalette.open());

addEventListener("keydown", (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
    e.preventDefault();
    if (help.isOpen()) help.close();
    schedulePalette.close();
    commandPalette.toggle();
    return;
  }
  if (e.key === "Escape" && e.shiftKey) { e.preventDefault(); armedStop(); return; }
  if (commandPalette.isOpen() || schedulePalette.isOpen() || help.isOpen() || isTyping(e.target) || e.metaKey || e.ctrlKey || e.altKey) return;
  const key = e.key.toLowerCase();
  if (e.key === "/") { e.preventDefault(); commandPalette.open(); }
  else if (e.key === "\\") { e.preventDefault(); schedulePalette.open(); }
  else if (e.key === "?") { e.preventDefault(); help.open(); }
  else if (VIEW_BY_KEY[key] && !e.shiftKey) shell.setView(VIEW_BY_KEY[key]);
  else if (/^[1-9]$/.test(e.key)) { const name = cameraOrder()[Number(e.key) - 1]; if (name) showCamera(name); }
});

onTheme(() => { drawMap(); commandPalette.refresh(); });

videoFeed(tileFor, onVideo).then((v) => connect(v.path, v.onMessage, true));
connect("/ws/state", onState);

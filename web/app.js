import { createLidarView } from "./lidar.js";
import { lidarDecoder } from "./lidar_stream.js";
import { createHud } from "./hud.js";
import { createPalette } from "./palette.js";
import { createDialog, createShell, isTyping, SPLITS, VIEWS } from "./shell.js";
import { applyTheme, onTheme, palette, renderChrome, storedTheme, THEMES } from "./ui.js";
import { readMeta, videoFeed } from "./video.js";

const COLOURS = ["#9aa5e6", "#d9b98a", "#c98f86", "#9cbfa6"];
const feeds = document.getElementById("feeds");
const map = document.getElementById("map");
const ctx = map.getContext("2d");
const truckList = document.getElementById("trucks");
const captionTitle = document.getElementById("caption-title");
const captionHint = document.getElementById("caption-hint");
const SITE_COLOURS = { ore: "#7fa38f", crusher: "#b0736a", dump: "#8f86a8", parking: "#b59a5e", fuel: "#b5835e" };
const RADIO_URL = document.querySelector('meta[name="radio-url"]')?.content || "";
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

const shell = createShell({ onView: () => { renderCaption(); resizeMap(); }, onCamera: () => renderCaption() });
const help = createDialog(document.getElementById("help"));
const commandPalette = createPalette({ root: document.getElementById("palette"), commands });

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
  return [...tiles.keys()].sort((a, b) => (a === "overview" ? -1 : b === "overview" ? 1 : a.localeCompare(b)));
}

function tileFor(name) {
  let tile = tiles.get(name);
  if (tile) return tile;
  const el = document.createElement("div");
  el.className = "feed";
  el.dataset.camera = name;
  el.innerHTML = `<canvas></canvas><div class="feed-wait">waiting for frames</div>`;
  el.querySelector("canvas").setAttribute("aria-label", `${name} camera`);
  feeds.append(el);
  tile = { el, canvas: el.querySelector("canvas"), frames: 0, fps: 0, busy: false };
  tiles.set(name, tile);
  hud.attach(name, el, tile.canvas);
  tile.onFrame = (meta) => hud.frame(name, meta);
  document.getElementById("feeds-empty").hidden = true;
  const active = shell.camera && tiles.has(shell.camera) ? shell.camera : tiles.has("overview") ? "overview" : name;
  if (!feeds.querySelector(".feed.active") || active === name) shell.setCamera(active, tiles);
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
    ctx.globalAlpha = 0.72;
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

function stopAll() {
  return Promise.all(state.trucks.map((t) => stopTruck(t.id)));
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
      li.querySelector(".id").textContent = t.id;
      truckList.append(li);
      truckItems.set(t.id, li);
    }
    li.classList.toggle("selected", t.id === selected);
    li.querySelector(".st").textContent = t.status;
    li.querySelector(".sp").textContent = `${t.speed.toFixed(1)} m/s`;
    const goal = t.goal_site ? `→ ${siteName(t.goal_site)}` : t.goal ? `→ ${t.goal[0].toFixed(0)}, ${t.goal[1].toFixed(0)}` : "";
    const ctl = t.id === selected && (t.throttle || t.steer) ? `  thr ${(t.throttle ?? 0).toFixed(2)}  str ${(t.steer ?? 0) >= 0 ? "+" : ""}${(t.steer ?? 0).toFixed(2)}` : "";
    li.querySelector(".go").textContent = goal + ctl;
  }
}

function siteName(id) {
  const s = network.sites.find((n) => n.id === id);
  return s ? s.name : id;
}

function renderCaption() {
  const view = shell.view;
  const cam = shell.camera;
  const fps = tiles.get(cam)?.fps;
  if (view === "lidar") {
    captionTitle.textContent = selected ? `lidar · ${selected}` : "lidar";
    captionHint.textContent = "drag to orbit";
  } else if (view === "map") {
    captionTitle.textContent = "map";
    captionHint.textContent = selected ? `click to send ${selected}` : "";
  } else {
    captionTitle.textContent = cam || "";
    captionHint.textContent = cam && fps !== undefined ? `${fps} fps` : "";
  }
}

function onState(ev) {
  state = JSON.parse(ev.data);
  if (state.world_seq !== worldSeq) {
    worldSeq = state.world_seq;
    loadWorld();
  }
  if (!selected && state.trucks.length) select(state.trucks[0].id);
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
  if (!(shell.view === "video" || (shell.view === "split" && shell.split.includes("video")))) shell.setView("video");
}

function focusTruck(id) {
  select(id);
  if (tiles.has(id)) showCamera(id);
}

const VIEW_KEYS = { video: "V", lidar: "L", map: "M", split: "S" };
const SPLIT_LABEL = (s) => s.split(" ").join(" · ");

function commands() {
  const out = [];
  for (const v of VIEWS) out.push({ id: `view:${v}`, group: "view", title: v[0].toUpperCase() + v.slice(1), keywords: "tab view show go", detail: shell.view === v ? "current" : "", keys: [VIEW_KEYS[v]], run: () => shell.setView(v) });
  cameraOrder().forEach((name, i) => out.push({ id: `camera:${name}`, group: "camera", title: `View ${name}`, keywords: "camera cam feed video", detail: shell.camera === name ? "current" : "", keys: i < 9 ? [String(i + 1)] : null, run: () => showCamera(name) }));
  for (const t of state.trucks) {
    out.push({ id: `focus:${t.id}`, group: "fleet", title: `Focus ${t.id}`, keywords: "select follow truck", detail: t.status, run: () => focusTruck(t.id) });
    out.push({ id: `stop:${t.id}`, group: "fleet", title: `Stop ${t.id}`, keywords: "halt cancel goal truck", detail: t.goal_site ? siteName(t.goal_site) : "", run: () => stopTruck(t.id) });
  }
  if (state.trucks.length) out.push({ id: "stop:all", group: "fleet", title: "Stop all trucks", keywords: "halt cancel everything fleet emergency", run: stopAll });
  for (const t of state.trucks) {
    for (const s of network.sites) {
      out.push({ id: `send:${t.id}:${s.id}`, group: "send", title: `Send ${t.id} to ${s.name}`, keywords: `${s.type} ${s.id} goto dispatch route drive`, detail: s.type, searchOnly: true, run: () => { select(t.id); sendGoal(t.id, { site: s.id }); } });
    }
  }
  for (const s of SPLITS) out.push({ id: `split:${s}`, group: "layout", title: `Split ${SPLIT_LABEL(s)}`, keywords: "custom layout tiles", detail: shell.view === "split" && shell.split === s ? "current" : "", run: () => shell.setSplit(s) });
  const theme = storedTheme();
  for (const th of THEMES) out.push({ id: `theme:${th}`, group: "appearance", title: `Theme ${th}`, keywords: "appearance colour mode", detail: theme === th ? "current" : "", run: () => applyTheme(th) });
  out.push({ id: "help:keys", group: "help", title: "Keyboard shortcuts", keywords: "help keys", keys: ["?"], run: () => help.open() });
  if (RADIO_URL) out.push({ id: "help:radio", group: "help", title: "Open radio", keywords: "voice talk", run: () => window.open(RADIO_URL, "_blank", "noopener") });
  return out;
}

const SHORTCUTS = [
  ["Command palette", ["/"]],
  ["Command palette", ["⌘", "K"]],
  ["Video · lidar · map · split", ["V", "L", "M", "S"]],
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
    commandPalette.toggle();
    return;
  }
  if (commandPalette.isOpen() || help.isOpen() || isTyping(e.target) || e.metaKey || e.ctrlKey || e.altKey) return;
  const key = e.key.toLowerCase();
  if (e.key === "/") { e.preventDefault(); commandPalette.open(); }
  else if (e.key === "?") { e.preventDefault(); help.open(); }
  else if (key === "v") shell.setView("video");
  else if (key === "l") shell.setView("lidar");
  else if (key === "m") shell.setView("map");
  else if (key === "s") shell.setView("split");
  else if (/^[1-9]$/.test(e.key)) { const name = cameraOrder()[Number(e.key) - 1]; if (name) showCamera(name); }
});

onTheme(() => { drawMap(); commandPalette.refresh(); });

videoFeed(tileFor, onVideo).then((v) => connect(v.path, v.onMessage, true));
connect("/ws/state", onState);

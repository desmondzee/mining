import { createLidarView } from "./lidar.js";
import { lidarDecoder } from "./lidar_stream.js";
import { videoFeed } from "./video.js";

const COLOURS = ["#f0b428", "#3ca0f0", "#e65050", "#78dc78"];
const feeds = document.getElementById("feeds");
const map = document.getElementById("map");
const ctx = map.getContext("2d");
const truckList = document.getElementById("trucks");
const simStatus = document.getElementById("sim-status");
const summary = document.getElementById("summary");
const PEDESTRIAN_COLOUR = "#ff7814";
const lidarView = createLidarView(document.getElementById("lidar"));

let state = { sim_connected: false, bounds: [-100, -100, 100, 100], trucks: [], obstacles: [], paths: [], pedestrians: [] };
let selected = null;
const tiles = new Map();
const decoder = new TextDecoder();
let stopLidar = null;

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

function tileFor(name) {
  let tile = tiles.get(name);
  if (tile) return tile;
  const el = document.createElement("div");
  el.className = "feed";
  el.innerHTML = `<canvas></canvas><div class="label"><span></span><span class="fps"></span></div>`;
  el.querySelector(".label span").textContent = name;
  const names = [...tiles.keys(), name].sort((a, b) => (a === "overview" ? -1 : b === "overview" ? 1 : a.localeCompare(b)));
  const before = tiles.get(names[names.indexOf(name) + 1]);
  feeds.insertBefore(el, before ? before.el : null);
  tile = { el, canvas: el.querySelector("canvas"), fpsEl: el.querySelector(".fps"), frames: 0, busy: false };
  tiles.set(name, tile);
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
    const bmp = await createImageBitmap(new Blob([buf.subarray(1 + n)], { type: "image/jpeg" }));
    if (tile.canvas.width !== bmp.width) tile.canvas.width = bmp.width;
    if (tile.canvas.height !== bmp.height) tile.canvas.height = bmp.height;
    tile.canvas.getContext("2d").drawImage(bmp, 0, 0);
    bmp.close();
    tile.frames++;
  } finally {
    tile.busy = false;
  }
}

setInterval(() => {
  for (const tile of tiles.values()) {
    tile.fpsEl.textContent = `${tile.frames} fps`;
    tile.frames = 0;
  }
}, 1000);

function toCanvas(x, z) {
  const [xmin, zmin, xmax, zmax] = state.bounds;
  return [((x - xmin) / (xmax - xmin)) * map.width, (1 - (z - zmin) / (zmax - zmin)) * map.height];
}

function toWorld(px, py) {
  const [xmin, zmin, xmax, zmax] = state.bounds;
  return [xmin + (px / map.width) * (xmax - xmin), zmin + (1 - py / map.height) * (zmax - zmin)];
}

function metresToPx(m) {
  return (m / (state.bounds[2] - state.bounds[0])) * map.width;
}

function drawMap() {
  ctx.clearRect(0, 0, map.width, map.height);
  ctx.strokeStyle = "#3a342c";
  ctx.lineWidth = 1;
  for (let i = 1; i < 10; i++) {
    const p = (i / 10) * map.width;
    ctx.beginPath(); ctx.moveTo(p, 0); ctx.lineTo(p, map.height); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(0, p); ctx.lineTo(map.width, p); ctx.stroke();
  }
  ctx.fillStyle = "#6e6e73";
  for (const o of state.obstacles) {
    const [cx, cy] = toCanvas(o.x, o.z);
    ctx.beginPath(); ctx.arc(cx, cy, Math.max(metresToPx(o.r), 1.5), 0, Math.PI * 2); ctx.fill();
  }
  ctx.strokeStyle = "rgba(255, 120, 20, 0.45)";
  ctx.setLineDash([3, 3]);
  for (const path of state.paths) {
    if (path.length < 2) continue;
    ctx.beginPath();
    path.forEach((p, i) => { const [x, y] = toCanvas(p.x, p.z); i ? ctx.lineTo(x, y) : ctx.moveTo(x, y); });
    if (path.length > 2) ctx.closePath();
    ctx.stroke();
  }
  ctx.setLineDash([]);
  ctx.fillStyle = PEDESTRIAN_COLOUR;
  for (const p of state.pedestrians) {
    const [cx, cy] = toCanvas(p.x, p.z);
    ctx.beginPath(); ctx.arc(cx, cy, 3.5, 0, Math.PI * 2); ctx.fill();
  }
  for (const t of state.trucks) {
    const colour = truckColour(t.id);
    const [cx, cy] = toCanvas(t.x, t.z);
    if (t.goal) {
      const [gx, gy] = toCanvas(t.goal[0], t.goal[1]);
      ctx.strokeStyle = colour;
      ctx.setLineDash([4, 4]);
      ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(gx, gy); ctx.stroke();
      ctx.setLineDash([]);
      ctx.beginPath(); ctx.arc(gx, gy, 6, 0, Math.PI * 2); ctx.stroke();
    }
    ctx.save();
    ctx.translate(cx, cy);
    ctx.rotate((t.yaw * Math.PI) / 180);
    ctx.fillStyle = colour;
    ctx.strokeStyle = t.id === selected ? "#fff" : "#000";
    ctx.lineWidth = 2;
    ctx.beginPath(); ctx.moveTo(0, -10); ctx.lineTo(7, 8); ctx.lineTo(-7, 8); ctx.closePath();
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
  drawMap();
}

const truckItems = new Map();

function renderTruckList() {
  for (const t of state.trucks) {
    let li = truckItems.get(t.id);
    if (!li) {
      li = document.createElement("li");
      li.innerHTML = `<span class="swatch"></span><span><b></b> <span class="meta"></span></span><button>stop</button>`;
      li.querySelector("b").textContent = t.id;
      li.onclick = () => select(t.id);
      li.querySelector("button").onclick = (e) => {
        e.stopPropagation();
        fetch(`/api/trucks/${encodeURIComponent(t.id)}/goal`, { method: "DELETE" });
      };
      truckList.append(li);
      truckItems.set(t.id, li);
    }
    li.className = t.id === selected ? "selected" : "";
    li.querySelector(".swatch").style.background = truckColour(t.id);
    li.querySelector(".meta").textContent = `${t.status} · ${t.speed.toFixed(1)} m/s`;
  }
}

function onState(ev) {
  state = JSON.parse(ev.data);
  if (!selected && state.trucks.length) select(state.trucks[0].id);
  simStatus.textContent = state.sim_connected ? "sim online" : "sim offline";
  simStatus.className = `pill ${state.sim_connected ? "on" : "off"}`;
  const model = state.truck_model ? ` · ${state.truck_model}` : "";
  summary.textContent = `${state.trucks.length} trucks · ${state.pedestrians.length} pedestrians · ${state.obstacles.length} rocks${model}`;
  renderTruckList();
  drawMap();
}

map.addEventListener("click", (e) => {
  if (!selected) return;
  const r = map.getBoundingClientRect();
  const [x, z] = toWorld(((e.clientX - r.left) / r.width) * map.width, ((e.clientY - r.top) / r.height) * map.height);
  fetch(`/api/trucks/${encodeURIComponent(selected)}/goal`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ x, z }),
  });
});

videoFeed(tileFor, onVideo).then((v) => connect(v.path, v.onMessage, true));
connect("/ws/state", onState);

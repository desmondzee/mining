import { createLidarView } from "./lidar.js";

const COLOURS = ["#f0b428", "#3ca0f0", "#e65050", "#78dc78"];
const feeds = document.getElementById("feeds");
const map = document.getElementById("map");
const ctx = map.getContext("2d");
const truckList = document.getElementById("trucks");
const simStatus = document.getElementById("sim-status");
const summary = document.getElementById("summary");
const PEDESTRIAN_COLOUR = "#ff7814";
const SITE_COLOURS = { ore: "#4cc3a0", crusher: "#e5534b", dump: "#b36ae2", parking: "#f0b428", fuel: "#f08c28" };
const PX = map.width / 400;
const lidarView = createLidarView(document.getElementById("lidar"));

let state = { sim_connected: false, bounds: [-100, -100, 100, 100], trucks: [], obstacles: [], paths: [], pedestrians: [] };
let network = { roads: [], sites: [], junctions: [] };
let worldSeq = null;
let preview = null;
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

async function loadWorld() {
  const w = await (await fetch("/api/world")).json();
  network = { roads: w.roads, sites: w.sites, junctions: w.junctions };
  preview = null;
  const aligned = w.preview_bounds && w.preview_bounds.every((v, i) => Math.abs(v - w.bounds[i]) < 0.01);
  if (w.preview && aligned && w.roads.length) {
    const img = new Image();
    img.onload = () => { preview = img; drawMap(); };
    img.src = `/api/world/preview?seq=${worldSeq}`;
  }
  for (const li of truckItems.values()) fillSiteSelect(li.querySelector("select"));
  drawMap();
}

function polyline(points) {
  ctx.beginPath();
  points.forEach((p, i) => { const [x, y] = toCanvas(p[0], p[1]); i ? ctx.lineTo(x, y) : ctx.moveTo(x, y); });
}

function drawNetwork() {
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  ctx.strokeStyle = preview ? "rgba(235, 225, 205, 0.28)" : "rgba(190, 175, 150, 0.35)";
  for (const r of network.roads) {
    ctx.lineWidth = Math.max(metresToPx(r.width_m), 2 * PX);
    polyline(r.points.map((p) => [p.x, p.z]));
    ctx.stroke();
  }
  ctx.fillStyle = "#d8d2c4";
  for (const j of network.junctions) {
    const [cx, cy] = toCanvas(j.x, j.z);
    ctx.beginPath(); ctx.arc(cx, cy, 3 * PX, 0, Math.PI * 2); ctx.fill();
  }
  ctx.font = `${11 * PX}px system-ui, sans-serif`;
  ctx.textAlign = "center";
  network.sites.forEach((s, i) => {
    const [cx, cy] = toCanvas(s.x, s.z);
    ctx.fillStyle = SITE_COLOURS[s.type] || "#ccc";
    ctx.strokeStyle = "#000";
    ctx.lineWidth = 1.5 * PX;
    ctx.beginPath(); ctx.rect(cx - 5 * PX, cy - 5 * PX, 10 * PX, 10 * PX); ctx.fill(); ctx.stroke();
    const dy = (i % 2 ? 18 : -10) * PX;
    ctx.lineWidth = 3 * PX;
    ctx.strokeText(s.name, cx, cy + dy);
    ctx.fillText(s.name, cx, cy + dy);
  });
}

function drawMap() {
  ctx.clearRect(0, 0, map.width, map.height);
  if (preview) {
    ctx.globalAlpha = 0.6;
    ctx.drawImage(preview, 0, 0, map.width, map.height);
    ctx.globalAlpha = 1;
  } else {
    ctx.strokeStyle = "#3a342c";
    ctx.lineWidth = PX;
    for (let i = 1; i < 10; i++) {
      const p = (i / 10) * map.width;
      ctx.beginPath(); ctx.moveTo(p, 0); ctx.lineTo(p, map.height); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(0, p); ctx.lineTo(map.width, p); ctx.stroke();
    }
  }
  drawNetwork();
  ctx.fillStyle = "#6e6e73";
  for (const o of state.obstacles) {
    const [cx, cy] = toCanvas(o.x, o.z);
    ctx.beginPath(); ctx.arc(cx, cy, Math.max(metresToPx(o.r), 1.5 * PX), 0, Math.PI * 2); ctx.fill();
  }
  ctx.strokeStyle = "rgba(255, 120, 20, 0.6)";
  ctx.lineWidth = PX;
  ctx.setLineDash([3 * PX, 3 * PX]);
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
    ctx.beginPath(); ctx.arc(cx, cy, 3.5 * PX, 0, Math.PI * 2); ctx.fill();
  }
  for (const t of state.trucks) {
    const colour = truckColour(t.id);
    const [cx, cy] = toCanvas(t.x, t.z);
    if (t.goal) {
      const [gx, gy] = toCanvas(t.goal[0], t.goal[1]);
      ctx.strokeStyle = colour;
      ctx.lineWidth = 2 * PX;
      if (t.route && t.route.length) {
        polyline([[t.x, t.z], ...t.route]);
        ctx.stroke();
      } else {
        ctx.setLineDash([4 * PX, 4 * PX]);
        ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(gx, gy); ctx.stroke();
        ctx.setLineDash([]);
      }
      ctx.beginPath(); ctx.arc(gx, gy, 7 * PX, 0, Math.PI * 2); ctx.stroke();
    }
    ctx.save();
    ctx.translate(cx, cy);
    ctx.rotate((t.yaw * Math.PI) / 180);
    ctx.fillStyle = colour;
    ctx.strokeStyle = t.id === selected ? "#fff" : "#000";
    ctx.lineWidth = 2 * PX;
    ctx.scale(PX, PX);
    ctx.beginPath(); ctx.moveTo(0, -10); ctx.lineTo(7, 8); ctx.lineTo(-7, 8); ctx.closePath();
    ctx.fill(); ctx.stroke();
    ctx.restore();
  }
}

function onLidar(ev) {
  const n = new Uint8Array(ev.data, 0, 1)[0];
  const cm = new Int16Array(ev.data.slice(1 + n + 12));
  lidarView.update(Float32Array.from(cm, (v) => v / 100));
}

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

function sendGoal(id, body) {
  fetch(`/api/trucks/${encodeURIComponent(id)}/goal`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

function fillSiteSelect(sel) {
  sel.replaceChildren(new Option("send to…", ""), ...network.sites.map((s) => new Option(s.name, s.id)));
  sel.hidden = !network.sites.length;
}

function renderTruckList() {
  for (const t of state.trucks) {
    let li = truckItems.get(t.id);
    if (!li) {
      li = document.createElement("li");
      li.innerHTML = `<span class="swatch"></span><span><b></b> <span class="meta"></span></span><select></select><button>stop</button>`;
      li.querySelector("b").textContent = t.id;
      li.onclick = () => select(t.id);
      const sel = li.querySelector("select");
      fillSiteSelect(sel);
      sel.onclick = (e) => e.stopPropagation();
      sel.onchange = () => {
        if (sel.value) sendGoal(t.id, { site: sel.value });
        sel.value = "";
      };
      li.querySelector("button").onclick = (e) => {
        e.stopPropagation();
        fetch(`/api/trucks/${encodeURIComponent(t.id)}/goal`, { method: "DELETE" });
      };
      truckList.append(li);
      truckItems.set(t.id, li);
    }
    li.className = t.id === selected ? "selected" : "";
    li.querySelector(".swatch").style.background = truckColour(t.id);
    const dest = t.goal_site ? ` · → ${siteName(t.goal_site)}` : "";
    li.querySelector(".meta").textContent = `${t.status} · ${t.speed.toFixed(1)} m/s${dest}`;
  }
}

function siteName(id) {
  const s = network.sites.find((n) => n.id === id);
  return s ? s.name : id;
}

function onState(ev) {
  state = JSON.parse(ev.data);
  if (state.world_seq !== worldSeq) {
    worldSeq = state.world_seq;
    loadWorld();
  }
  if (!selected && state.trucks.length) select(state.trucks[0].id);
  simStatus.textContent = state.sim_connected ? "sim online" : "sim offline";
  simStatus.className = `pill ${state.sim_connected ? "on" : "off"}`;
  const model = state.truck_model ? ` · ${state.truck_model}` : "";
  summary.textContent = `${state.trucks.length} trucks · ${state.pedestrians.length} pedestrians · ${state.obstacles.length} obstacles${model}`;
  renderTruckList();
  drawMap();
}

map.addEventListener("click", (e) => {
  if (!selected) return;
  const r = map.getBoundingClientRect();
  const [x, z] = toWorld(((e.clientX - r.left) / r.width) * map.width, ((e.clientY - r.top) / r.height) * map.height);
  sendGoal(selected, { x, z });
});

connect("/ws/video", onVideo, true);
connect("/ws/state", onState);

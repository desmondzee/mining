import { createLidarView } from "./lidar.js";
import { lidarDecoder } from "./lidar_stream.js";
import { palette, renderChrome } from "./ui.js";
import { videoFeed } from "./video.js";

const COLOURS = ["#4fc3f7", "#ffb74d", "#ef5350", "#81c784"];
const feeds = document.getElementById("feeds");
const map = document.getElementById("map");
const ctx = map.getContext("2d");
const truckList = document.getElementById("trucks");
const simStatus = document.getElementById("sim-status");
const summary = document.getElementById("summary");
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
  el.className = name === "overview" ? "feed featured" : "feed";
  el.innerHTML = `<canvas></canvas><div class="feed-wait">Waiting for frames</div><div class="feed-bar top"><span class="chip name"><i></i><span></span></span><span class="spacer"></span><span class="chip status"></span></div><div class="feed-bar bottom"><span class="chip speed"></span><span class="chip fps"></span></div>`;
  el.querySelector(".name span").textContent = name;
  el.querySelector("canvas").setAttribute("aria-label", `${name} camera`);
  const names = [...tiles.keys(), name].sort((a, b) => (a === "overview" ? -1 : b === "overview" ? 1 : a.localeCompare(b)));
  const before = tiles.get(names[names.indexOf(name) + 1]);
  feeds.insertBefore(el, before ? before.el : null);
  tile = { el, canvas: el.querySelector("canvas"), fpsEl: el.querySelector(".fps"), statusEl: el.querySelector(".status"), speedEl: el.querySelector(".speed"), frames: 0, busy: false };
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
    tile.el.classList.add("live");
  } finally {
    tile.busy = false;
  }
}

setInterval(() => {
  for (const tile of tiles.values()) {
    tile.fpsEl.textContent = `${tile.frames} fps`;
    tile.el.classList.toggle("stale", tile.frames === 0);
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
  ctx.strokeStyle = palette.grid;
  ctx.lineWidth = 1;
  for (let i = 1; i < 10; i++) {
    const p = (i / 10) * map.width;
    ctx.beginPath(); ctx.moveTo(p, 0); ctx.lineTo(p, map.height); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(0, p); ctx.lineTo(map.width, p); ctx.stroke();
  }
  ctx.fillStyle = palette.obstacle;
  for (const o of state.obstacles) {
    const [cx, cy] = toCanvas(o.x, o.z);
    ctx.beginPath(); ctx.arc(cx, cy, Math.max(metresToPx(o.r), 1.5), 0, Math.PI * 2); ctx.fill();
  }
  ctx.strokeStyle = palette.path;
  ctx.setLineDash([3, 3]);
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
    ctx.strokeStyle = t.id === selected ? palette.selected : palette.outline;
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

function setBar(bar, value, centered) {
  const v = Math.max(-1, Math.min(1, value));
  const fill = bar.querySelector(".bar-fill");
  fill.style.left = centered ? `${50 + Math.min(v, 0) * 50}%` : "0%";
  fill.style.width = `${Math.abs(v) * (centered ? 50 : 100)}%`;
  bar.querySelector(".bar-value").textContent = `${v >= 0 && centered ? "+" : ""}${v.toFixed(2)}`;
}

function renderTruckList() {
  for (const t of state.trucks) {
    let li = truckItems.get(t.id);
    if (!li) {
      li = document.createElement("li");
      li.innerHTML = `
        <button type="button" class="truck-head"><span class="swatch"></span><span class="truck-id"></span><span class="chip status"></span><span class="truck-speed num"><span class="speed"></span><small>m/s</small></span></button>
        <div class="bars">
          <div class="bar throttle"><span>Thr</span><span class="bar-track"><span class="bar-fill"></span></span><span class="bar-value num"></span></div>
          <div class="bar steer centered"><span>Str</span><span class="bar-track"><span class="bar-fill"></span></span><span class="bar-value num"></span></div>
        </div>
        <div class="truck-foot">
          <span class="truck-goal"><span>Goal</span><span class="goal num"></span><span class="goal-site"></span></span>
          <span class="truck-actions">
            <span class="send-group" hidden><select class="btn send-site" aria-label="Destination site"><option value="">Send to…</option></select><button type="button" class="btn send">Send</button></span>
            <button type="button" class="btn danger stop">Stop</button>
          </span>
        </div>`;
      li.querySelector(".truck-id").textContent = t.id;
      li.querySelector(".stop").setAttribute("aria-label", `Stop ${t.id}`);
      li.onclick = () => select(t.id);
      li.querySelector(".stop").onclick = (e) => {
        e.stopPropagation();
        fetch(`/api/trucks/${encodeURIComponent(t.id)}/goal`, { method: "DELETE" });
      };
      truckList.append(li);
      truckItems.set(t.id, li);
    }
    li.className = t.id === selected ? "truck selected" : "truck";
    li.querySelector(".truck-head").setAttribute("aria-pressed", String(t.id === selected));
    li.querySelector(".swatch").style.background = truckColour(t.id);
    const status = li.querySelector(".status");
    status.textContent = t.status;
    status.className = `chip status status-${t.status}`;
    li.querySelector(".speed").textContent = t.speed.toFixed(1);
    setBar(li.querySelector(".throttle"), t.throttle ?? 0, false);
    setBar(li.querySelector(".steer"), t.steer ?? 0, true);
    li.querySelector(".goal").textContent = t.goal ? `${t.goal[0].toFixed(0)}, ${t.goal[1].toFixed(0)}` : "—";
    li.querySelector(".goal-site").textContent = t.goal_site || "";
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
  renderChrome(state, { tileFor, tiles, truckColour, selected });
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

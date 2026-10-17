import { NEAR, PathBuffer, Ribbon, buildRibbon, cameraModel, interpolateActors, nearestOnPolyline, predictPath, project, routePath, straightPath, toCamera } from "./hud_math.js";

const MAX_LEN = 80;
const HALF_W = 2.65;
const TRUCK_SIZE = [5.3, 4.5, 9.35];
const PERSON_SIZE = [0.6, 1.8, 0.6];
const CAM_HEIGHT = 3.6;
const FRONT = 4.7;
const STOP_CLEAR = 8;
const WARN_CLEAR = 20;
const OBJECT_RANGE = 90;
const ROCK_RANGE = 50;
const DETECTION_TTL_MS = 1000;
const STATE_BUFFER = 30;
const UNIT_KEY = "quarry-hud-unit";
const MAX_PIXELS = 2.6e6;
const DETAIL_WIDTH = 960;
const HOLD_LEN = 14;
const ENGAGED = new Set(["driving", "avoiding", "holding", "hold", "blocked", "yielding"]);
const CAUTION = new Set(["avoiding", "holding", "hold", "blocked", "yielding"]);
const HOLD = new Set(["holding", "hold", "blocked"]);
const FORWARD_DEG = 60;
const KIND_LABEL = { person: "Pedestrian", truck: "Haul truck", obstacle: "Obstacle", equipment: "Equipment" };

const C = {
  blue: [40, 128, 255],
  blueHi: [150, 200, 255],
  amber: [255, 176, 64],
  amberHi: [255, 214, 150],
  red: [255, 84, 84],
  redHi: [255, 170, 170],
  grey: [205, 210, 218],
  white: [238, 242, 246],
};

const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)");
const rgba = (c, a) => `rgba(${c[0]},${c[1]},${c[2]},${a.toFixed(3)})`;

const WHEEL_SVG = `<svg class="hud-wheel" viewBox="0 0 24 24" aria-hidden="true"><g fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="2.4"/><path d="M3.4 10.2c3.2-.9 5.6-1 8.6-1s5.4.1 8.6 1M12 14.4V21"/></g></svg>`;

function storedUnit() {
  try { return localStorage.getItem(UNIT_KEY) === "ms" ? "ms" : "kmh"; } catch { return "kmh"; }
}

function formatDistance(m) {
  if (!Number.isFinite(m)) return "";
  return m >= 1000 ? `${(m / 1000).toFixed(1)} km` : `${Math.max(0, Math.round(m))} m`;
}

function hull(pts, n, out) {
  const idx = [];
  for (let i = 0; i < n; i++) idx.push(i);
  idx.sort((a, b) => pts[a * 2] - pts[b * 2] || pts[a * 2 + 1] - pts[b * 2 + 1]);
  const cross = (o, a, b) => (pts[a * 2] - pts[o * 2]) * (pts[b * 2 + 1] - pts[o * 2 + 1]) - (pts[a * 2 + 1] - pts[o * 2 + 1]) * (pts[b * 2] - pts[o * 2]);
  out.length = 0;
  for (const pass of [idx, idx.slice().reverse()]) {
    const start = out.length;
    for (const i of pass) {
      while (out.length >= start + 2 && cross(out[out.length - 2], out[out.length - 1], i) <= 0) out.pop();
      out.push(i);
    }
    out.pop();
  }
  return out;
}

function roundedBrackets(ctx, x0, y0, x1, y1, r, len) {
  const w = x1 - x0, h = y1 - y0;
  const l = Math.min(len, w * 0.4, h * 0.4);
  const rr = Math.min(r, l * 0.8);
  ctx.beginPath();
  ctx.moveTo(x0, y0 + l); ctx.arcTo(x0, y0, x0 + l, y0, rr); ctx.lineTo(x0 + l, y0);
  ctx.moveTo(x1 - l, y0); ctx.arcTo(x1, y0, x1, y0 + l, rr); ctx.lineTo(x1, y0 + l);
  ctx.moveTo(x1, y1 - l); ctx.arcTo(x1, y1, x1 - l, y1, rr); ctx.lineTo(x1 - l, y1);
  ctx.moveTo(x0 + l, y1); ctx.arcTo(x0, y1, x0, y1 - l, rr); ctx.lineTo(x0, y1 - l);
  ctx.stroke();
}

function polyline(c) {
  const pts = Array.isArray(c) ? c : c && (c.points || c.path);
  if (!Array.isArray(pts) || pts.length < 2) return null;
  return pts.map((p) => (Array.isArray(p) ? p : [p.x, p.z]));
}

function plannerPaths(truck) {
  const empty = { list: [], chosen: null };
  if (!truck) return empty;
  const source = Array.isArray(truck.candidates) && truck.candidates.length ? truck.candidates : truck.ghost_paths;
  if (!Array.isArray(source) || !source.length) return empty;
  const scores = Array.isArray(truck.scores) ? truck.scores : Array.isArray(truck.candidate_scores) ? truck.candidate_scores : null;
  const list = [];
  source.forEach((c, i) => {
    const pts = polyline(c);
    if (!pts) return;
    const score = Number(c && c.score != null ? c.score : scores ? scores[i] : 1);
    list.push({ pts, score: Number.isFinite(score) ? score : 0, index: i });
  });
  let lo = Infinity, hi = -Infinity;
  for (const c of list) { lo = Math.min(lo, c.score); hi = Math.max(hi, c.score); }
  for (const c of list) c.weight = hi > lo ? (c.score - lo) / (hi - lo) : 1;
  const idx = ["chosen", "chosen_idx", "choice", "candidate", "candidate_idx", "best"].map((k) => truck[k]).find((v) => Number.isInteger(v));
  const chosen = truck.candidates === source && idx != null && idx >= 0 ? list.find((c) => c.index === idx) : null;
  return { list, chosen: chosen ? chosen.pts : null, learned: truck.candidates === source };
}

function blendRoute(chosen, route, routeY, groundY) {
  const out = chosen.map((p) => [p[0], p[1], groundY]);
  if (!route) return out;
  const [ex, ez] = chosen[chosen.length - 1];
  let best = Infinity, k = -1;
  for (let i = 0; i < route.length; i++) {
    const d = Math.hypot(route[i][0] - ex, route[i][1] - ez);
    if (d < best) { best = d; k = i; }
  }
  for (let i = k + 1; i < route.length; i++) out.push([route[i][0], route[i][1], routeY ? routeY[i] : groundY]);
  return out;
}

class TileHud {
  constructor(hud, name, el, video) {
    this.hud = hud;
    this.name = name;
    this.el = el;
    this.video = video;
    this.meta = null;
    this.dirty = false;
    this.path = new PathBuffer(512);
    this.ribbon = new Ribbon(160);
    this.cam = {};
    this.pt = [0, 0, 0];
    this.corners = new Float32Array(16);
    this.hullIdx = [];
    this.objects = [];
    this.labelsUsed = 0;
    this.labels = [];
    this.last = {};
    this.routeCache = { ref: null, xs: null, zs: null };

    el.classList.add("has-hud");
    this.canvas = document.createElement("canvas");
    this.canvas.className = "hud-layer";
    this.canvas.setAttribute("aria-hidden", "true");
    video.after(this.canvas);
    this.ctx = this.canvas.getContext("2d");
    this.root = document.createElement("div");
    this.root.className = "hud";
    this.root.hidden = true;
    this.root.innerHTML = `
      <div class="hud-labels" aria-hidden="true"></div>
      <div class="hud-telemetry">
        <button type="button" class="hud-speed" title="Toggle units"><span class="hud-speed-val">0</span><span class="hud-speed-unit">km/h</span></button>
        <div class="hud-line">${WHEEL_SVG}<span class="hud-goal"></span></div>
      </div>
      <span class="hud-status" role="status"></span>`;
    el.append(this.root);
    this.dom = {
      labels: this.root.querySelector(".hud-labels"),
      speed: this.root.querySelector(".hud-speed-val"),
      unit: this.root.querySelector(".hud-speed-unit"),
      wheel: this.root.querySelector(".hud-wheel"),
      goal: this.root.querySelector(".hud-goal"),
      status: this.root.querySelector(".hud-status"),
    };
    this.speedShown = 0;
    this.root.querySelector(".hud-speed").addEventListener("click", (e) => {
      e.stopPropagation();
      hud.toggleUnit();
    });
    if (hud.focusOnClick) {
      el.classList.add("hud-zoomable");
      el.addEventListener("click", () => hud.focus(name));
      el.tabIndex = 0;
      el.setAttribute("role", "button");
      el.setAttribute("aria-label", `Open ${name} camera`);
      el.addEventListener("keydown", (e) => {
        if ((e.key === "Enter" || e.key === " ") && !this.focused) { e.preventDefault(); hud.focus(name); }
      });
    }
    this.steer = 0;
  }

  get focused() {
    return this.el.classList.contains("focused") || this.hud.detailed === this.name || this.cssW >= DETAIL_WIDTH;
  }

  setText(key, el, text) {
    if (this.last[key] !== text) { this.last[key] = text; el.textContent = text; }
  }

  resize() {
    const r = this.el.getBoundingClientRect();
    let dpr = Math.min(window.devicePixelRatio || 1, 2);
    if (r.width * r.height * dpr * dpr > MAX_PIXELS) dpr = Math.sqrt(MAX_PIXELS / (r.width * r.height));
    const w = Math.max(1, Math.round(r.width * dpr)), h = Math.max(1, Math.round(r.height * dpr));
    if (this.canvas.width !== w || this.canvas.height !== h) { this.canvas.width = w; this.canvas.height = h; }
    this.dpr = dpr;
    this.cssW = r.width;
    this.cssH = r.height;
  }

  draw(now) {
    this.dirty = false;
    this.resize();
    const ctx = this.ctx;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
    this.labelsUsed = 0;
    const meta = this.meta;
    const vw = this.video.width, vh = this.video.height;
    const truck = meta && meta.truck ? this.hud.truckState(meta.truck) : null;
    const active = !!(meta && meta.cam && meta.truck && vw > 2);
    this.root.hidden = !active;
    if (!active) { this.hideLabels(); return; }

    const full = this.focused;
    if (this.last.full !== full) { this.last.full = full; this.root.classList.toggle("hud-full", full); }
    const s = Math.max(this.canvas.width / vw, this.canvas.height / vh);
    const ox = (this.canvas.width - vw * s) / 2, oy = (this.canvas.height - vh * s) / 2;
    this.s = s; this.ox = ox; this.oy = oy;
    const u = this.dpr / s;
    const cam = cameraModel(meta.cam, vw, vh, this.cam);
    ctx.setTransform(s, 0, 0, s, ox, oy);

    const pose = meta.pose || { x: meta.cam.x, z: meta.cam.z, yaw: meta.cam.yaw };
    const groundY = truck && truck.y != null ? truck.y : pose.y != null ? pose.y : meta.cam.y - CAM_HEIGHT;
    const ego = { x: pose.x, y: groundY, z: pose.z, yaw: pose.yaw };
    const status = truck ? truck.status : "idle";
    const engaged = ENGAGED.has(status) && !!(truck && truck.goal);
    const route = truck && Array.isArray(truck.route) && truck.route.length >= 2 ? truck.route : null;
    const plan = plannerPaths(truck);
    const forward = Math.abs(((meta.cam.yaw - pose.yaw + 540) % 360) - 180) < FORWARD_DEG;
    const holding = engaged && (HOLD.has(status) || (plan.learned && !plan.chosen));

    if (engaged && plan.chosen) routePath(ego, blendRoute(plan.chosen, route, truck.route_y, groundY), null, groundY, MAX_LEN + 20, this.path, 4);
    else if (route) routePath(ego, route, truck.route_y, groundY, MAX_LEN + 20, this.path);
    else if (engaged) predictPath(ego, truck.goal, groundY, MAX_LEN + 20, this.path);
    else straightPath(ego, groundY, 9, this.path);

    const t = meta.t || this.hud.latestT;
    const objs = this.collectObjects(meta, t, cam, ego, engaged);
    const lead = objs.lead;

    let tone = engaged ? (CAUTION.has(status) || holding ? "amber" : "blue") : "grey";
    let len = holding ? HOLD_LEN : engaged ? MAX_LEN : 9;
    let stopAt = null;
    if (engaged && lead && (lead.state === "warn" || lead.state === "stop")) {
      stopAt = Math.max(3, lead.along - lead.radius - 1.5);
      len = Math.min(len, stopAt);
      tone = lead.state === "stop" ? "red" : "amber";
    }

    if (forward && engaged) this.drawCandidates(ctx, plan, cam, groundY, full, u);
    buildRibbon(this.path, ego, cam, HALF_W, len, this.ribbon, 0.45);
    if (forward) this.drawRibbon(ctx, this.ribbon, tone, engaged && !holding, full, u, now, stopAt != null && stopAt <= len + 0.01);
    if (forward && engaged && !holding && truck.goal && !stopAt) this.drawGoal(ctx, cam, truck.goal, groundY, full, u, now);
    this.drawObjects(ctx, cam, objs.list, lead, full, u);
    this.drawDetections2D(ctx, vw, vh, full, u);
    this.hideLabels();
    this.updateTelemetry(meta, truck, engaged, status, lead, route, ego);
  }

  collectObjects(meta, t, cam, ego, engaged) {
    const out = this.objects;
    out.length = 0;
    const hud = this.hud;
    for (const p of hud.actors(t, "pedestrians")) out.push({ kind: "person", id: p.id, x: p.x, y: p.y, z: p.z, yaw: p.yaw || 0, size: PERSON_SIZE, radius: 0.4, half: 0.3 });
    for (const tr of hud.actors(t, "trucks")) if (tr.id !== meta.truck) out.push({ kind: "truck", id: tr.id, x: tr.x, y: tr.y, z: tr.z, yaw: tr.yaw || 0, size: TRUCK_SIZE, radius: 4.7, half: 2.65 });
    for (const o of hud.state.obstacles || []) {
      const r = o.r || 1;
      out.push({ kind: o.kind || "obstacle", x: o.x, y: o.y, z: o.z, yaw: o.yaw || 0, size: [2 * r, Math.min(Math.max(1.5 * r, 1), 7), 2 * r], radius: r, half: r, ground: true });
    }
    for (const d of hud.detections(this.name)) if (d.x != null && d.z != null) out.push({ ...d, kind: d.kind || d.cls || "obstacle", size: d.size || [1, 1, 1], radius: d.radius || (d.size ? Math.max(d.size[0], d.size[2]) / 2 : 0.5) });

    const path = this.path;
    const fx = Math.sin((ego.yaw * Math.PI) / 180), fz = Math.cos((ego.yaw * Math.PI) / 180);
    let lead = null;
    const list = [];
    for (const o of out) {
      if (o.y == null) o.y = ego.y;
      const dx = o.x - ego.x, dz = o.z - ego.z;
      const dist = Math.hypot(dx, dz);
      const range = o.ground ? ROCK_RANGE : OBJECT_RANGE;
      if (dist > range) continue;
      toCamera(cam, o.x, o.y + o.size[1] / 2, o.z, this.pt);
      if (this.pt[2] < 1) continue;
      const halfFov = (cam.w / 2 + 40) / cam.f;
      if (Math.abs(this.pt[0] / this.pt[2]) > halfFov + o.radius / this.pt[2]) continue;
      o.dist = dist;
      o.state = "neutral";
      o.along = dist;
      if (engaged && path.n > 1 && dx * fx + dz * fz > 0) {
        const near = nearestOnPolyline(o.x, o.z, path.x, path.z, path.n);
        const lateral = near.dist - (o.half ?? o.radius);
        o.along = near.along;
        if (lateral < HALF_W + 0.75 && near.along - o.radius > FRONT * 0.5 && near.along < MAX_LEN) {
          const clear = near.along - o.radius - FRONT;
          o.state = clear < STOP_CLEAR ? "stop" : clear < WARN_CLEAR ? "warn" : "path";
          if (!lead || near.along < lead.along) lead = o;
        } else if (o.kind === "person" && lateral < HALF_W + 4 && near.along < 40) {
          o.state = "near";
        }
      }
      list.push(o);
    }
    if (lead && lead.state === "path") lead.state = "lead";
    list.sort((a, b) => b.dist - a.dist);
    return { list, lead };
  }

  drawRibbon(ctx, rib, tone, engaged, full, u, now, stopBar) {
    const n = rib.n;
    if (n < 2) return;
    const base = C[tone === "grey" ? "grey" : tone], hi = C[tone === "grey" ? "white" : tone + "Hi"];
    const strength = engaged ? 1 : tone === "grey" ? 0.35 : 0.75;
    const gx0 = rib.cx[0], gy0 = rib.cy[0], gx1 = rib.cx[n - 1], gy1 = rib.cy[n - 1];
    const ax = gx1 - gx0, ay = gy1 - gy0, al2 = ax * ax + ay * ay || 1;
    const len = rib.s[n - 1] || 1;
    const endAlpha = stopBar ? 0.35 : 0;
    const makeGrad = (c, a0, a1) => {
      const g = ctx.createLinearGradient(gx0, gy0, gx1, gy1);
      let last = 0;
      for (let k = 0; k <= 6; k++) {
        const frac = k / 6;
        const i = Math.min(n - 1, Math.round(frac * (n - 1)));
        const f = Math.min(1, Math.max(last, ((rib.cx[i] - gx0) * ax + (rib.cy[i] - gy0) * ay) / al2));
        last = f;
        const d = rib.s[i] / len;
        const a = a0 * (1 - d) ** 1.35 + a1 * d;
        g.addColorStop(f, rgba(c, a * strength));
      }
      return g;
    };

    const outline = () => {
      ctx.beginPath();
      ctx.moveTo(rib.lx[0], rib.ly[0]);
      for (let i = 1; i < n; i++) ctx.lineTo(rib.lx[i], rib.ly[i]);
      for (let i = n - 1; i >= 0; i--) ctx.lineTo(rib.rx[i], rib.ry[i]);
      ctx.closePath();
    };

    ctx.save();
    if (full && engaged) {
      ctx.shadowColor = rgba(base, 0.55);
      ctx.shadowBlur = 22 * u * this.s;
    }
    outline();
    ctx.fillStyle = makeGrad(base, 0.62, endAlpha);
    ctx.fill();
    ctx.restore();


    if (engaged && !reducedMotion.matches) {
      const period = 2.6, span = len + 16;
      const head = ((now / 1000) % period) / period * span - 8;
      ctx.save();
      ctx.globalCompositeOperation = "lighter";
      ctx.beginPath();
      let first = -1, lastI = -1;
      for (let i = 0; i < n; i++) if (Math.abs(rib.s[i] - head) < 7) { if (first < 0) first = i; lastI = i; }
      if (first >= 0 && lastI > first) {
        for (let i = first; i <= lastI; i++) i === first ? ctx.moveTo(rib.lx[i], rib.ly[i]) : ctx.lineTo(rib.lx[i], rib.ly[i]);
        for (let i = lastI; i >= first; i--) ctx.lineTo(rib.rx[i], rib.ry[i]);
        ctx.closePath();
        const g = ctx.createLinearGradient(rib.cx[first], rib.cy[first], rib.cx[lastI], rib.cy[lastI]);
        const fade = 0.16 * Math.max(0, 1 - head / len);
        g.addColorStop(0, rgba(hi, 0));
        g.addColorStop(0.5, rgba(hi, fade));
        g.addColorStop(1, rgba(hi, 0));
        ctx.fillStyle = g;
        ctx.fill();
      }
      ctx.restore();
    }

    ctx.lineJoin = "round";
    ctx.lineCap = "round";
    ctx.lineWidth = (full ? 2 : 1.25) * u;
    const edge = makeGrad(hi, 0.95, endAlpha + 0.1);
    ctx.strokeStyle = edge;
    ctx.beginPath();
    for (let i = 0; i < n; i++) i ? ctx.lineTo(rib.lx[i], rib.ly[i]) : ctx.moveTo(rib.lx[i], rib.ly[i]);
    ctx.stroke();
    ctx.beginPath();
    for (let i = 0; i < n; i++) i ? ctx.lineTo(rib.rx[i], rib.ry[i]) : ctx.moveTo(rib.rx[i], rib.ry[i]);
    ctx.stroke();

    if (stopBar) {
      const k = n - 1;
      ctx.save();
      ctx.lineCap = "round";
      ctx.lineWidth = (full ? 5 : 3) * u;
      ctx.strokeStyle = rgba(hi, 0.95);
      ctx.shadowColor = rgba(base, 0.9);
      ctx.shadowBlur = 12 * u * this.s;
      ctx.beginPath();
      ctx.moveTo(rib.lx[k], rib.ly[k]);
      ctx.lineTo(rib.rx[k], rib.ry[k]);
      ctx.stroke();
      ctx.restore();
    }
  }

  drawGoal(ctx, cam, goal, groundY, full, u, now) {
    const dist = Math.hypot(goal[0] - this.cam.x, goal[1] - this.cam.z);
    if (dist > MAX_LEN + 5) return;
    const pt = this.pt;
    const pulse = reducedMotion.matches ? 0.5 : 0.5 + 0.5 * Math.sin(now / 420);
    ctx.save();
    for (const [r, a, w] of [[HALF_W + 0.6 + pulse * 0.6, 0.35 * (1 - pulse * 0.6), 1.5], [1.1, 0.9, 2]]) {
      ctx.beginPath();
      let started = false;
      for (let k = 0; k <= 32; k++) {
        const ang = (k / 32) * Math.PI * 2;
        if (project(cam, goal[0] + Math.cos(ang) * r, groundY, goal[1] + Math.sin(ang) * r, pt) < NEAR) { started = false; continue; }
        started ? ctx.lineTo(pt[0], pt[1]) : ctx.moveTo(pt[0], pt[1]);
        started = true;
      }
      ctx.lineWidth = w * u * (full ? 1.3 : 1);
      ctx.strokeStyle = rgba(C.blueHi, a);
      ctx.stroke();
    }
    if (project(cam, goal[0], groundY, goal[1], pt) > NEAR) {
      const x = pt[0], yb = pt[1];
      const top = project(cam, goal[0], groundY + 6, goal[1], pt) > NEAR ? pt[1] : yb - 40 * u;
      const g = ctx.createLinearGradient(x, yb, x, top);
      g.addColorStop(0, rgba(C.blueHi, 0.9));
      g.addColorStop(1, rgba(C.blueHi, 0));
      ctx.strokeStyle = g;
      ctx.lineWidth = 2 * u;
      ctx.beginPath(); ctx.moveTo(x, yb); ctx.lineTo(x, top); ctx.stroke();
    }
    ctx.restore();
  }

  drawCandidates(ctx, plan, cam, groundY, full, u) {
    if (!plan.list.length) return;
    const pt = this.pt;
    ctx.save();
    ctx.lineWidth = (full ? 1.25 : 0.9) * u;
    ctx.lineJoin = "round";
    ctx.lineCap = "round";
    for (const c of plan.list) {
      if (c.pts === plan.chosen) continue;
      ctx.strokeStyle = rgba(C.white, (full ? 0.05 + 0.25 * c.weight : 0.04 + 0.12 * c.weight));
      ctx.beginPath();
      let started = false;
      for (const p of c.pts) {
        if (project(cam, p[0], p.length > 2 ? p[2] : groundY, p[1], pt) < NEAR) { started = false; continue; }
        started ? ctx.lineTo(pt[0], pt[1]) : ctx.moveTo(pt[0], pt[1]);
        started = true;
      }
      ctx.stroke();
    }
    ctx.restore();
  }


  styleFor(o) {
    switch (o.state) {
      case "stop": return { c: C.red, hi: C.redHi, fill: 0.34, line: 1, w: 1.6, bracket: true };
      case "warn": return { c: C.amber, hi: C.amberHi, fill: 0.3, line: 1, w: 1.5, bracket: true };
      case "lead": return { c: C.blue, hi: C.blueHi, fill: 0.32, line: 1, w: 1.5, bracket: true };
      case "path": case "near": return { c: C.blue, hi: C.blueHi, fill: 0.2, line: 0.75, w: 1.1, bracket: false };
      default:
        return o.ground ? { c: C.white, hi: C.white, fill: 0.06, line: 0.22, w: 0.8, bracket: false } : { c: C.white, hi: C.white, fill: 0.16, line: 0.55, w: 1, bracket: false };
    }
  }

  drawObjects(ctx, cam, list, lead, full, u) {
    for (const o of list) {
      if (!full && o.ground && o.state === "neutral") continue;
      const st = this.styleFor(o);
      const box = o.kind === "person" ? this.drawPerson(ctx, cam, o, st, full, u) : this.drawBox(ctx, cam, o, st, full, u);
      if (!box) continue;
      if (st.bracket) {
        ctx.save();
        ctx.lineWidth = (full ? 2.2 : 1.6) * u;
        ctx.strokeStyle = rgba(st.hi, 0.95);
        ctx.lineCap = "round";
        const pad = 4 * u;
        roundedBrackets(ctx, box[0] - pad, box[1] - pad, box[2] + pad, box[3] + pad, 6 * u, 12 * u);
        ctx.restore();
      }
      const showLabel = o === lead || (full && (o.state !== "neutral" || (o.kind === "person" && o.dist < 45) || (o.kind === "truck" && o.dist < 70)));
      if (showLabel) {
        const name = full ? `${KIND_LABEL[o.kind] || o.kind} · ` : "";
        this.label(`${name}${formatDistance(o.dist)}`, (box[0] + box[2]) / 2, box[1] - 6 * u, o.state);
      }
    }
  }

  drawShadow(ctx, cam, o, r, alpha) {
    const pt = this.pt;
    ctx.beginPath();
    for (let k = 0; k < 20; k++) {
      const a = (k / 20) * Math.PI * 2;
      if (project(cam, o.x + Math.cos(a) * r, o.y, o.z + Math.sin(a) * r, pt) < NEAR) return;
      k ? ctx.lineTo(pt[0], pt[1]) : ctx.moveTo(pt[0], pt[1]);
    }
    ctx.closePath();
    ctx.fillStyle = `rgba(0,0,0,${alpha})`;
    ctx.fill();
  }

  drawPerson(ctx, cam, o, st, full, u) {
    const pt = this.pt;
    const zc = project(cam, o.x, o.y, o.z, pt);
    if (zc < NEAR) return null;
    const x = pt[0], yb = pt[1];
    project(cam, o.x, o.y + 1.8, o.z, pt);
    const yt = pt[1];
    const h = yb - yt;
    if (h < 2) return null;
    const scale = h / 1.8;
    const bw = Math.max(0.5 * scale, 2 * u), headR = Math.max(0.13 * scale, 1.2 * u);
    if (full) this.drawShadow(ctx, cam, o, 0.55, 0.22);
    ctx.save();
    ctx.fillStyle = rgba(st.c, st.fill + 0.1);
    ctx.strokeStyle = rgba(st.hi, st.line);
    ctx.lineWidth = st.w * u;
    const bodyTop = yt + 0.36 * scale;
    ctx.beginPath();
    ctx.roundRect(x - bw / 2, bodyTop, bw, yb - bodyTop, bw / 2);
    ctx.fill();
    ctx.stroke();
    ctx.beginPath();
    ctx.arc(x, yt + 0.15 * scale, headR, 0, Math.PI * 2);
    ctx.fill();
    ctx.stroke();
    ctx.restore();
    return [x - bw * 0.9, yt, x + bw * 0.9, yb];
  }

  drawBox(ctx, cam, o, st, full, u) {
    const [w, h, l] = o.size;
    const yaw = (o.yaw * Math.PI) / 180;
    const fx = Math.sin(yaw), fz = Math.cos(yaw), rx = fz, rz = -fx;
    const pts = this.corners, pt = this.pt;
    let k = 0;
    for (const dy of [0, h]) for (const [a, b] of [[-1, -1], [1, -1], [1, 1], [-1, 1]]) {
      const x = o.x + rx * (a * w) / 2 + fx * (b * l) / 2;
      const z = o.z + rz * (a * w) / 2 + fz * (b * l) / 2;
      if (project(cam, x, o.y + dy, z, pt) < NEAR) return null;
      pts[k * 2] = pt[0]; pts[k * 2 + 1] = pt[1]; k++;
    }
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (let i = 0; i < 8; i++) {
      x0 = Math.min(x0, pts[i * 2]); x1 = Math.max(x1, pts[i * 2]);
      y0 = Math.min(y0, pts[i * 2 + 1]); y1 = Math.max(y1, pts[i * 2 + 1]);
    }
    if (x1 < -50 || x0 > cam.w + 50 || x1 - x0 < 1.5) return null;
    if (full && !o.ground) this.drawShadow(ctx, cam, o, Math.max(w, l) * 0.62, 0.2);
    const idx = hull(pts, 8, this.hullIdx);
    const cover = ((Math.min(x1, cam.w) - Math.max(x0, 0)) * (Math.min(y1, cam.h) - Math.max(y0, 0))) / (cam.w * cam.h);
    const fade = Math.max(0.25, Math.min(1, 1 - (cover - 0.06) * 2.2));
    ctx.save();
    ctx.beginPath();
    idx.forEach((i, j) => (j ? ctx.lineTo(pts[i * 2], pts[i * 2 + 1]) : ctx.moveTo(pts[i * 2], pts[i * 2 + 1])));
    ctx.closePath();
    ctx.fillStyle = rgba(st.c, st.fill * fade);
    ctx.fill();
    ctx.restore();
    ctx.beginPath();
    for (let i = 4; i < 8; i++) i === 4 ? ctx.moveTo(pts[i * 2], pts[i * 2 + 1]) : ctx.lineTo(pts[i * 2], pts[i * 2 + 1]);
    ctx.closePath();
    ctx.fillStyle = rgba(st.hi, st.fill * 0.6 * fade);
    ctx.fill();
    ctx.lineJoin = "round";
    ctx.lineWidth = st.w * u;
    ctx.strokeStyle = rgba(st.hi, st.line * 0.4 * fade);
    ctx.beginPath();
    for (let i = 0; i < 4; i++) {
      const j = (i + 1) % 4;
      ctx.moveTo(pts[i * 2], pts[i * 2 + 1]); ctx.lineTo(pts[j * 2], pts[j * 2 + 1]);
      ctx.moveTo(pts[(i + 4) * 2], pts[(i + 4) * 2 + 1]); ctx.lineTo(pts[(j + 4) * 2], pts[(j + 4) * 2 + 1]);
      ctx.moveTo(pts[i * 2], pts[i * 2 + 1]); ctx.lineTo(pts[(i + 4) * 2], pts[(i + 4) * 2 + 1]);
    }
    ctx.stroke();
    ctx.strokeStyle = rgba(st.hi, st.line * (0.45 + 0.55 * fade));
    ctx.beginPath();
    idx.forEach((i, j) => (j ? ctx.lineTo(pts[i * 2], pts[i * 2 + 1]) : ctx.moveTo(pts[i * 2], pts[i * 2 + 1])));
    ctx.closePath();
    ctx.stroke();
    return [x0, y0, x1, y1];
  }

  drawDetections2D(ctx, vw, vh, full, u) {
    for (const d of this.hud.detections(this.name)) {
      if (!d.box) continue;
      const [a, b, c, e] = d.box;
      const x0 = a * vw, y0 = b * vh, x1 = c * vw, y1 = e * vh;
      const col = d.state === "stop" ? C.redHi : d.state === "warn" ? C.amberHi : d.highlight ? C.blueHi : C.white;
      ctx.save();
      ctx.lineWidth = (full ? 2 : 1.4) * u;
      ctx.strokeStyle = rgba(col, 0.9);
      ctx.lineCap = "round";
      roundedBrackets(ctx, x0, y0, x1, y1, 6 * u, 12 * u);
      ctx.restore();
      if (full || d.highlight) {
        const conf = d.conf != null ? ` ${Math.round(d.conf * 100)}%` : "";
        this.label(`${d.cls || "object"}${conf}`, (x0 + x1) / 2, y0 - 6 * u, d.state || (d.highlight ? "lead" : "neutral"));
      }
    }
  }

  label(text, vx, vy, state) {
    let el = this.labels[this.labelsUsed];
    if (!el) {
      el = document.createElement("span");
      el.className = "hud-label";
      this.dom.labels.append(el);
      this.labels.push(el);
    }
    this.labelsUsed++;
    const w = text.length * 3.6 + 12;
    const x = Math.min(this.cssW - w - 6, Math.max(w + 6, (vx * this.s + this.ox) / this.dpr));
    const y = Math.max(this.focused ? 72 : 46, Math.min(this.cssH - 4, (vy * this.s + this.oy) / this.dpr));
    if (el.textContent !== text) el.textContent = text;
    if (el.dataset.state !== state) el.dataset.state = state;
    el.style.transform = `translate(${x.toFixed(1)}px, ${y.toFixed(1)}px) translate(-50%, -100%)`;
    el.hidden = false;
  }

  hideLabels() {
    for (let i = this.labelsUsed; i < this.labels.length; i++) if (!this.labels[i].hidden) this.labels[i].hidden = true;
  }

  distanceToGo(truck, route, ego) {
    if (!truck || !truck.goal) return NaN;
    if (!route) return Math.hypot(truck.goal[0] - ego.x, truck.goal[1] - ego.z);
    const c = this.routeCache;
    if (c.ref !== route) {
      c.ref = route;
      c.xs = Float64Array.from(route, (p) => p[0]);
      c.zs = Float64Array.from(route, (p) => p[1]);
    }
    const near = nearestOnPolyline(ego.x, ego.z, c.xs, c.zs, route.length);
    return near.length - near.along + near.dist;
  }

  updateTelemetry(meta, truck, engaged, status, lead, route, ego) {
    const d = this.dom;
    const unit = this.hud.unit;
    const speed = Math.abs(meta.speed ?? (truck ? truck.speed : 0));
    this.speedShown += (speed - this.speedShown) * 0.3;
    const v = Math.max(0, this.speedShown);
    this.setText("speed", d.speed, unit === "kmh" ? String(Math.round(v * 3.6)) : v.toFixed(1));
    this.setText("unit", d.unit, unit === "kmh" ? "km/h" : "m/s");
    const mode = engaged ? (CAUTION.has(status) || (lead && (lead.state === "warn" || lead.state === "stop")) ? "caution" : "engaged") : "standby";
    if (this.root.dataset.mode !== mode) this.root.dataset.mode = mode;
    const steer = truck ? truck.steer || 0 : 0;
    this.steer += (steer - this.steer) * 0.25;
    const deg = Math.round(this.steer * 1200) / 10;
    if (this.last.deg !== deg) { this.last.deg = deg; d.wheel.style.transform = `rotate(${deg}deg)`; }
    let goal = "";
    if (engaged && truck.goal) {
      const site = truck.goal_site ? truck.goal_site.replace(/[_-]+/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()) : `${truck.goal[0].toFixed(0)}, ${truck.goal[1].toFixed(0)}`;
      goal = `${site}  ${formatDistance(this.distanceToGo(truck, route, ego))}`;
    }
    this.setText("goal", d.goal, goal);
    let note = "";
    if (engaged && lead && lead.state === "stop") note = `Holding for ${(KIND_LABEL[lead.kind] || "object").toLowerCase()}`;
    else if (engaged && lead && lead.state === "warn") note = `${KIND_LABEL[lead.kind] || "Object"} ahead`;
    this.setText("status", d.status, note);
  }

}

export function createHud({ focusOnClick = true } = {}) {
  const huds = new Map();
  const detections = new Map();
  const buffer = [];
  const scratch = { pedestrians: [], trucks: [] };
  let focused = null;
  let placeholder = null;

  const overlay = document.createElement("div");
  overlay.className = "hud-focus";
  overlay.hidden = true;
  overlay.innerHTML = `<div class="hud-focus-backdrop"></div><div class="hud-focus-sheet" role="dialog" aria-modal="true" aria-label="Camera"><div class="hud-focus-slot"></div><button type="button" class="hud-focus-close" aria-label="Close camera view"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg></button></div>`;
  document.body.append(overlay);
  const slot = overlay.querySelector(".hud-focus-slot");
  overlay.querySelector(".hud-focus-backdrop").addEventListener("click", () => hud.unfocus());
  overlay.querySelector(".hud-focus-close").addEventListener("click", () => hud.unfocus());
  addEventListener("keydown", (e) => { if (e.key === "Escape" && focused) hud.unfocus(); });

  const hud = {
    state: { trucks: [], pedestrians: [], obstacles: [] },
    latestT: 0,
    unit: storedUnit(),
    focusOnClick,
    detailed: null,
    trucksById: new Map(),

    attach(name, el, video) {
      if (name instanceof Element) [name, el] = [el, name];
      video ||= el.querySelector("canvas");
      if (huds.has(name)) return huds.get(name);
      const h = new TileHud(hud, name, el, video);
      huds.set(name, h);
      return h;
    },

    frame(name, meta) {
      const h = huds.get(name);
      if (!h) return;
      h.meta = meta || h.meta;
      if (!focused || focused === h) h.draw(performance.now());
    },

    setState(state) {
      hud.state = state;
      hud.trucksById.clear();
      for (const t of state.trucks || []) hud.trucksById.set(t.id, t);
      const t = state.t || performance.now() / 1000;
      hud.latestT = t;
      if (buffer.length && buffer[buffer.length - 1].t >= t) buffer.length = 0;
      buffer.push({ t, pedestrians: state.pedestrians || [], trucks: state.trucks || [] });
      if (buffer.length > STATE_BUFFER) buffer.shift();
      for (const h of huds.values()) if (!h.meta) h.dirty = true;
    },

    setDetections(cameraId, list) {
      detections.set(cameraId, { at: performance.now(), list: Array.isArray(list) ? list : [] });
    },

    detections(cameraId) {
      const d = detections.get(cameraId);
      return d && performance.now() - d.at < DETECTION_TTL_MS ? d.list : [];
    },

    truckState(id) {
      return hud.trucksById.get(id) || null;
    },

    actors(t, key) {
      return interpolateActors(buffer, t, key, scratch[key]);
    },

    toggleUnit() {
      hud.unit = hud.unit === "kmh" ? "ms" : "kmh";
      try { localStorage.setItem(UNIT_KEY, hud.unit); } catch {}
      for (const h of huds.values()) h.dirty = true;
    },

    setFocused(name) {
      if (name) hud.focus(name);
      else hud.unfocus();
    },

    setDetail(name) {
      hud.detailed = name || null;
      for (const h of huds.values()) h.dirty = true;
    },

    focus(name) {
      const h = huds.get(name);
      if (!h || focused === h) return;
      if (focused) hud.unfocus();
      focused = h;
      placeholder = document.createElement("div");
      placeholder.className = "feed feed-placeholder";
      h.el.replaceWith(placeholder);
      slot.append(h.el);
      h.el.classList.add("focused");
      h.el.setAttribute("aria-label", `${name} camera`);
      overlay.hidden = false;
      document.documentElement.classList.add("hud-focus-open");
      requestAnimationFrame(() => overlay.classList.add("open"));
      overlay.querySelector(".hud-focus-close").focus({ preventScroll: true });
      h.dirty = true;
    },

    unfocus() {
      if (!focused) return;
      const h = focused;
      focused = null;
      overlay.classList.remove("open");
      h.el.classList.remove("focused");
      h.el.setAttribute("aria-label", `Open ${h.name} camera`);
      placeholder.replaceWith(h.el);
      placeholder = null;
      overlay.hidden = true;
      document.documentElement.classList.remove("hud-focus-open");
      h.el.focus({ preventScroll: true });
      h.dirty = true;
    },
  };

  const loop = (now) => {
    if (focused) focused.draw(now);
    else for (const h of huds.values()) if (h.dirty) h.draw(now);
    requestAnimationFrame(loop);
  };
  requestAnimationFrame(loop);
  addEventListener("resize", () => { for (const h of huds.values()) h.dirty = true; });
  return hud;
}

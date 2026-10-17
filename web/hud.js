import { D2R, NEAR, PathBuffer, Ribbon, buildRibbon, cameraModel, interpolateActors, nearestOnPolyline, predictPath, project, routePath, straightPath, toCamera } from "./hud_math.js";

const MAX_LEN = 80;
const CAND_BUCKETS = 4;
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
const GT_KEY = "quarry-hud-ground-truth";
const LABEL_MIN_H = 14;
const MAX_PIXELS = 1.4e6;
const DETAIL_WIDTH = 960;
const HOLD_LEN = 14;
const NEAR_LEN = 30;
const BEHIND_PROBE = 12;
const ROUTE_END_ALPHA = 0.14;
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

function storedFlag(key) {
  try { return localStorage.getItem(key) === "1"; } catch { return false; }
}

function storedUnit() {
  try { return localStorage.getItem(UNIT_KEY) === "ms" ? "ms" : "kmh"; } catch { return "kmh"; }
}

function formatDistance(m) {
  if (!Number.isFinite(m)) return "";
  return m >= 1000 ? `${(m / 1000).toFixed(1)} km` : `${Math.max(0, Math.round(m))} m`;
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
    if (!pts || (scores && scores[i] === null)) return;
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

function onGround(pts, groundY) {
  return pts.map((p) => [p[0], p[1], groundY]);
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
    this.nearPath = new PathBuffer(256);
    this.nearRib = new Ribbon(80);
    this.cam = {};
    this.pt = [0, 0, 0];
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
    this.ro = new ResizeObserver(() => { this.measure(); this.dirty = true; });
    this.ro.observe(el);
  }

  get focused() {
    return this.el.classList.contains("focused") || this.hud.detailed === this.name || this.cssW >= DETAIL_WIDTH;
  }

  setText(key, el, text) {
    if (this.last[key] !== text) { this.last[key] = text; el.textContent = text; }
  }

  measure() {
    const r = this.el.getBoundingClientRect();
    this.rect = { width: r.width, height: r.height };
  }

  resize() {
    if (!this.rect) this.measure();
    const r = this.rect;
    let dpr = Math.min(window.devicePixelRatio || 1, 1.5);
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

    if (route) routePath(ego, route, truck.route_y, groundY, MAX_LEN + 20, this.path);
    else if (engaged && plan.chosen) routePath(ego, onGround(plan.chosen, groundY), null, groundY, MAX_LEN + 20, this.path, 4);
    else if (engaged) predictPath(ego, truck.goal, groundY, MAX_LEN + 20, this.path);
    else straightPath(ego, groundY, 9, this.path);
    if (route && this.behind(this.path, ego)) straightPath(ego, groundY, HOLD_LEN + 6, this.path);
    const near = engaged && !!plan.chosen;
    if (near) routePath(ego, onGround(plan.chosen, groundY), null, groundY, NEAR_LEN + 10, this.nearPath, 4);

    const t = meta.t || this.hud.latestT;
    const objs = this.collectObjects(meta, t, cam, ego, engaged);
    const lead = objs.lead;

    let tone = engaged ? (CAUTION.has(status) || holding ? "amber" : "blue") : "grey";
    let len = engaged || route ? MAX_LEN : 9;
    let stopAt = holding ? HOLD_LEN : null;
    if (engaged && lead && (lead.state === "warn" || lead.state === "stop")) {
      stopAt = Math.max(3, lead.along - lead.radius - 1.5);
      len = Math.min(len, stopAt);
      tone = lead.state === "stop" ? "red" : "amber";
    }

    if (forward && engaged && !this.el.classList.contains("thumb")) this.drawCandidates(ctx, plan, cam, groundY, full, u);
    buildRibbon(this.path, ego, cam, HALF_W, len, this.ribbon, 0.45);
    if (forward) this.drawRibbon(ctx, this.ribbon, tone, engaged && !holding, full, u, now, stopAt != null && stopAt <= len + 0.01 ? stopAt : null);
    if (forward && near) {
      buildRibbon(this.nearPath, ego, cam, HALF_W * 0.6, Math.min(len, stopAt ?? NEAR_LEN, NEAR_LEN), this.nearRib, 0.45);
      this.drawNear(ctx, this.nearRib, tone);
    }
    if (forward && engaged && !holding && truck.goal && !stopAt) this.drawGoal(ctx, cam, truck.goal, groundY, full, u, now);
    if (this.hud.showGroundTruth(this.name)) this.drawObjects(cam, objs.list, lead, full);
    this.drawDetections2D(vw, vh, full, engaged);
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
    const strength = tone === "grey" ? 0.35 : 1;
    const gx0 = rib.cx[0], gy0 = rib.cy[0], gx1 = rib.cx[n - 1], gy1 = rib.cy[n - 1];
    const ax = gx1 - gx0, ay = gy1 - gy0, al2 = ax * ax + ay * ay || 1;
    const len = rib.s[n - 1] || 1;
    const endAlpha = ROUTE_END_ALPHA;
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
    outline();
    if (full && engaged) {
      ctx.lineJoin = "round";
      ctx.lineWidth = 10 * u;
      ctx.strokeStyle = rgba(base, 0.14);
      ctx.stroke();
    }
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

    if (stopBar != null) {
      let k = 0;
      while (k < n - 1 && rib.s[k] < stopBar) k++;
      ctx.save();
      ctx.lineCap = "butt";
      ctx.lineWidth = (full ? 5 : 3) * u;
      ctx.strokeStyle = rgba(hi, 1);
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

  behind(path, ego) {
    const p = this.pt;
    path.sampleAt(Math.min(BEHIND_PROBE, path.length()), p);
    return (p[0] - ego.x) * Math.sin(ego.yaw * D2R) + (p[2] - ego.z) * Math.cos(ego.yaw * D2R) < 1;
  }

  drawNear(ctx, rib, tone) {
    const n = rib.n;
    if (n < 2) return;
    const hi = C[tone === "grey" ? "white" : tone + "Hi"];
    const g = ctx.createLinearGradient(rib.cx[0], rib.cy[0], rib.cx[n - 1], rib.cy[n - 1]);
    g.addColorStop(0, rgba(hi, 0.6));
    g.addColorStop(1, rgba(hi, 0.08));
    ctx.beginPath();
    ctx.moveTo(rib.lx[0], rib.ly[0]);
    for (let i = 1; i < n; i++) ctx.lineTo(rib.lx[i], rib.ly[i]);
    for (let i = n - 1; i >= 0; i--) ctx.lineTo(rib.rx[i], rib.ry[i]);
    ctx.closePath();
    ctx.fillStyle = g;
    ctx.fill();
  }

  drawCandidates(ctx, plan, cam, groundY, full, u) {
    if (!plan.list.length) return;
    const pt = this.pt;
    ctx.save();
    ctx.lineWidth = (full ? 1.25 : 0.9) * u;
    ctx.lineJoin = "round";
    ctx.lineCap = "round";
    for (let b = 0; b < CAND_BUCKETS; b++) {
      ctx.strokeStyle = rgba(C.white, full ? 0.05 + 0.25 * ((b + 0.5) / CAND_BUCKETS) : 0.04 + 0.12 * ((b + 0.5) / CAND_BUCKETS));
      ctx.beginPath();
      let any = false;
      for (const c of plan.list) {
        if (c.pts === plan.chosen || Math.min(CAND_BUCKETS - 1, Math.floor(c.weight * CAND_BUCKETS)) !== b) continue;
        let started = false;
        for (const p of c.pts) {
          if (project(cam, p[0], p.length > 2 ? p[2] : groundY, p[1], pt) < NEAR) { started = false; continue; }
          started ? ctx.lineTo(pt[0], pt[1]) : ctx.moveTo(pt[0], pt[1]);
          started = true;
          any = true;
        }
      }
      if (any) ctx.stroke();
    }
    ctx.restore();
  }


  rectColor(state) {
    return state === "stop" ? C.redHi : state === "warn" ? C.amberHi : state === "lead" || state === "path" || state === "near" ? C.blueHi : C.white;
  }

  drawRect(x0, y0, x1, y1, col, full) {
    const ctx = this.ctx, s = this.s;
    const lw = Math.max(1, Math.round(this.dpr * (full ? 1.25 : 1)));
    const off = (lw % 2) / 2;
    const X0 = Math.round(x0 * s + this.ox) + off, Y0 = Math.round(y0 * s + this.oy) + off;
    const X1 = Math.round(x1 * s + this.ox) - off, Y1 = Math.round(y1 * s + this.oy) - off;
    if (X1 - X0 < 2 || Y1 - Y0 < 2) return false;
    ctx.save();
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.lineWidth = lw;
    ctx.strokeStyle = rgba(col, 0.95);
    ctx.strokeRect(X0, Y0, X1 - X0, Y1 - Y0);
    ctx.restore();
    return true;
  }

  projectedBounds(cam, o) {
    const pt = this.pt;
    if (o.kind === "person") {
      if (project(cam, o.x, o.y, o.z, pt) < NEAR) return null;
      const x = pt[0], yb = pt[1];
      project(cam, o.x, o.y + o.size[1], o.z, pt);
      const half = ((yb - pt[1]) / o.size[1]) * o.size[0] * 0.5;
      return [x - half, pt[1], x + half, yb];
    }
    const [w, h, l] = o.size;
    const yaw = (o.yaw * Math.PI) / 180;
    const fx = Math.sin(yaw), fz = Math.cos(yaw), rx = fz, rz = -fx;
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (const dy of [0, h]) for (const [a, b] of [[-1, -1], [1, -1], [1, 1], [-1, 1]]) {
      if (project(cam, o.x + (rx * a * w) / 2 + (fx * b * l) / 2, o.y + dy, o.z + (rz * a * w) / 2 + (fz * b * l) / 2, pt) < NEAR) return null;
      x0 = Math.min(x0, pt[0]); x1 = Math.max(x1, pt[0]);
      y0 = Math.min(y0, pt[1]); y1 = Math.max(y1, pt[1]);
    }
    return x1 < 0 || x0 > cam.w ? null : [x0, y0, x1, y1];
  }

  drawObjects(cam, list, lead, full) {
    for (const o of list) {
      if (!full && o.ground && o.state === "neutral") continue;
      const b = this.projectedBounds(cam, o);
      if (!b || !this.drawRect(b[0], b[1], b[2], b[3], this.rectColor(o.state), full)) continue;
      if (o === lead || full || b[3] - b[1] > LABEL_MIN_H) this.boxLabel(`${(KIND_LABEL[o.kind] || o.kind).toLowerCase()} ${formatDistance(o.dist)}`, b, o.state);
    }
  }

  drawDetections2D(vw, vh, full, engaged) {
    for (const d of this.hud.detections(this.name)) {
      if (!d.box) continue;
      const [a, b, c, e] = d.box;
      const box = [a * vw, b * vh, c * vw, e * vh];
      const ahead = engaged && d.dist != null && a < 0.62 && c > 0.38;
      const state = d.state || (ahead && d.dist - FRONT < STOP_CLEAR ? "stop" : ahead && d.dist - FRONT < WARN_CLEAR ? "warn" : "neutral");
      if (!this.drawRect(box[0], box[1], box[2], box[3], this.rectColor(state), full)) continue;
      if (full || state !== "neutral" || box[3] - box[1] > LABEL_MIN_H) {
        const dist = d.dist != null ? ` ${formatDistance(d.dist)}` : "";
        const conf = full && d.conf != null ? ` ${d.conf.toFixed(2)}` : "";
        this.boxLabel(`${String(d.cls || "object").toLowerCase()}${dist}${conf}`, box, state);
      }
    }
  }

  boxLabel(text, box, state) {
    this.label(text, box[0], box[1] - (2 * this.dpr) / this.s, state, true);
  }

  label(text, vx, vy, state, left = false) {
    let el = this.labels[this.labelsUsed];
    if (!el) {
      el = document.createElement("span");
      el.className = "hud-label";
      this.dom.labels.append(el);
      this.labels.push(el);
    }
    this.labelsUsed++;
    const w = left ? text.length * 6 + 4 : text.length * 3.6 + 12;
    const px = (vx * this.s + this.ox) / this.dpr;
    const x = left ? Math.min(this.cssW - w - 2, Math.max(2, px)) : Math.min(this.cssW - w - 6, Math.max(w + 6, px));
    const y = Math.max(this.focused ? 72 : 46, Math.min(this.cssH - 4, (vy * this.s + this.oy) / this.dpr));
    if (el.textContent !== text) el.textContent = text;
    if (el.dataset.state !== state) el.dataset.state = state;
    el.style.transform = `translate(${x.toFixed(1)}px, ${y.toFixed(1)}px) translate(${left ? "0" : "-50%"}, -100%)`;
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
    groundTruth: storedFlag(GT_KEY),
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
      if (meta && meta.det) hud.setDetections(name, meta.det.items);
      h.dirty = true;
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

    detections(cameraId, live = false) {
      const d = detections.get(cameraId);
      const fresh = !!d && performance.now() - d.at < DETECTION_TTL_MS;
      return live ? fresh : fresh ? d.list : [];
    },

    showGroundTruth(cameraId) {
      return hud.groundTruth || !hud.detections(cameraId, true);
    },

    toggleGroundTruth() {
      hud.groundTruth = !hud.groundTruth;
      try { localStorage.setItem(GT_KEY, hud.groundTruth ? "1" : "0"); } catch {}
      for (const h of huds.values()) h.dirty = true;
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

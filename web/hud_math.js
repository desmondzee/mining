export const D2R = Math.PI / 180;
export const NEAR = 0.6;

export function cameraModel(cam, width, height, out = {}) {
  const cy = Math.cos(cam.yaw * D2R), sy = Math.sin(cam.yaw * D2R);
  const cp = Math.cos((cam.pitch || 0) * D2R), sp = Math.sin((cam.pitch || 0) * D2R);
  const cr = Math.cos((cam.roll || 0) * D2R), sr = Math.sin((cam.roll || 0) * D2R);
  const rot = (x, y, z, o) => {
    const y1 = cp * y - sp * z, z1 = sp * y + cp * z;
    o[0] = cy * x + sy * z1; o[1] = y1; o[2] = -sy * x + cy * z1;
    return o;
  };
  out.right = rot(cr, sr, 0, out.right || [0, 0, 0]);
  out.up = rot(-sr, cr, 0, out.up || [0, 0, 0]);
  out.fwd = rot(0, 0, 1, out.fwd || [0, 0, 0]);
  out.x = cam.x; out.y = cam.y; out.z = cam.z;
  out.w = width; out.h = height;
  out.f = width / 2 / Math.tan(((cam.hfov || 90) * D2R) / 2);
  return out;
}

export function toCamera(c, x, y, z, out) {
  const dx = x - c.x, dy = y - c.y, dz = z - c.z;
  out[0] = dx * c.right[0] + dy * c.right[1] + dz * c.right[2];
  out[1] = dx * c.up[0] + dy * c.up[1] + dz * c.up[2];
  out[2] = dx * c.fwd[0] + dy * c.fwd[1] + dz * c.fwd[2];
  return out;
}

export function project(c, x, y, z, out) {
  toCamera(c, x, y, z, out);
  const zc = out[2];
  if (zc <= 1e-6) return -1;
  out[0] = c.w / 2 + (c.f * out[0]) / zc;
  out[1] = c.h / 2 - (c.f * out[1]) / zc;
  return zc;
}

export function wrapDeg(a) {
  return ((((a + 180) % 360) + 360) % 360) - 180;
}

export function nearestOnPolyline(px, pz, xs, zs, n) {
  let best = Infinity, bi = 0, bt = 0, along = 0, bestAlong = 0;
  for (let i = 0; i < n - 1; i++) {
    const ax = xs[i], az = zs[i], bx = xs[i + 1] - ax, bz = zs[i + 1] - az;
    const len2 = bx * bx + bz * bz;
    const t = len2 > 0 ? Math.max(0, Math.min(1, ((px - ax) * bx + (pz - az) * bz) / len2)) : 0;
    const ex = ax + t * bx - px, ez = az + t * bz - pz;
    const d = ex * ex + ez * ez;
    const len = Math.sqrt(len2);
    if (d < best) { best = d; bi = i; bt = t; bestAlong = along + t * len; }
    along += len;
  }
  return { dist: Math.sqrt(best), index: bi, t: bt, along: bestAlong, length: along };
}

export class PathBuffer {
  constructor(cap) {
    this.x = new Float32Array(cap);
    this.y = new Float32Array(cap);
    this.z = new Float32Array(cap);
    this.s = new Float32Array(cap);
    this.n = 0;
    this.cap = cap;
  }
  clear() { this.n = 0; }
  push(x, y, z) {
    if (this.n >= this.cap) return;
    const i = this.n++;
    this.x[i] = x; this.y[i] = y; this.z[i] = z;
    this.s[i] = i ? this.s[i - 1] + Math.hypot(x - this.x[i - 1], z - this.z[i - 1]) : 0;
  }
  length() { return this.n ? this.s[this.n - 1] : 0; }
  sampleAt(s, out) {
    const n = this.n;
    if (n === 0) return out;
    if (s <= 0 || n === 1) { out[0] = this.x[0]; out[1] = this.y[0]; out[2] = this.z[0]; return out; }
    let lo = 0, hi = n - 1;
    if (s >= this.s[hi]) { out[0] = this.x[hi]; out[1] = this.y[hi]; out[2] = this.z[hi]; return out; }
    while (hi - lo > 1) { const m = (lo + hi) >> 1; if (this.s[m] <= s) lo = m; else hi = m; }
    const span = this.s[hi] - this.s[lo];
    const t = span > 0 ? (s - this.s[lo]) / span : 0;
    out[0] = this.x[lo] + (this.x[hi] - this.x[lo]) * t;
    out[1] = this.y[lo] + (this.y[hi] - this.y[lo]) * t;
    out[2] = this.z[lo] + (this.z[hi] - this.z[lo]) * t;
    return out;
  }
}

function hermite(out, p0x, p0y, p0z, t0x, t0z, p1x, p1y, p1z, t1x, t1z, steps) {
  for (let k = 1; k <= steps; k++) {
    const t = k / steps, t2 = t * t, t3 = t2 * t;
    const h00 = 2 * t3 - 3 * t2 + 1, h10 = t3 - 2 * t2 + t, h01 = -2 * t3 + 3 * t2, h11 = t3 - t2;
    out.push(
      h00 * p0x + h10 * t0x + h01 * p1x + h11 * t1x,
      p0y + (p1y - p0y) * t,
      h00 * p0z + h10 * t0z + h01 * p1z + h11 * t1z,
    );
  }
}

export function routePath(ego, route, routeY, groundY, maxLen, out, lookahead = 10) {
  out.clear();
  const n = route.length;
  const xs = new Float64Array(n), zs = new Float64Array(n);
  for (let i = 0; i < n; i++) { xs[i] = route[i][0]; zs[i] = route[i][1]; }
  const yAt = (i) => (routeY ? routeY[i] : route[i].length > 2 ? route[i][2] : groundY);
  const near = nearestOnPolyline(ego.x, ego.z, xs, zs, n);
  let i = near.index;
  let tx = xs[i] + (xs[i + 1] - xs[i]) * near.t, tz = zs[i] + (zs[i + 1] - zs[i]) * near.t;
  let ty = yAt(i) + (yAt(i + 1) - yAt(i)) * near.t;
  let walked = 0;
  while (i < n - 1) {
    const seg = Math.hypot(xs[i + 1] - tx, zs[i + 1] - tz);
    if (walked + seg >= lookahead) {
      const f = seg > 0 ? (lookahead - walked) / seg : 0;
      tx += (xs[i + 1] - tx) * f; tz += (zs[i + 1] - tz) * f; ty += (yAt(i + 1) - ty) * f;
      walked = lookahead;
      break;
    }
    walked += seg; tx = xs[i + 1]; tz = zs[i + 1]; ty = yAt(i + 1); i++;
  }
  const hx = Math.sin(ego.yaw * D2R), hz = Math.cos(ego.yaw * D2R);
  let dx = i < n - 1 ? xs[i + 1] - xs[i] : xs[n - 1] - xs[Math.max(0, n - 2)];
  let dz = i < n - 1 ? zs[i + 1] - zs[i] : zs[n - 1] - zs[Math.max(0, n - 2)];
  const dl = Math.hypot(dx, dz) || 1;
  const span = Math.max(Math.hypot(tx - ego.x, tz - ego.z), 1);
  out.push(ego.x, ego.y, ego.z);
  if (span < 1.5) return out;
  hermite(out, ego.x, ego.y, ego.z, hx * span, hz * span, tx, ty, tz, (dx / dl) * span, (dz / dl) * span, 12);
  for (let k = i + 1; k < n && out.length() < maxLen; k++) out.push(xs[k], yAt(k), zs[k]);
  return out;
}

export function predictPath(ego, goal, groundY, maxLen, out, opts = {}) {
  const { wheelbase = 5, maxSteerDeg = 30, gainDeg = 45, step = 1, arrive = 4 } = opts;
  out.clear();
  let x = ego.x, z = ego.z, yaw = ego.yaw;
  out.push(x, groundY, z);
  for (let s = 0; s < maxLen; s += step) {
    if (Math.hypot(goal[0] - x, goal[1] - z) < arrive) break;
    const bearing = Math.atan2(goal[0] - x, goal[1] - z) / D2R;
    const steer = Math.max(-1, Math.min(1, wrapDeg(bearing - yaw) / gainDeg));
    yaw += ((step / wheelbase) * Math.tan(steer * maxSteerDeg * D2R)) / D2R;
    x += Math.sin(yaw * D2R) * step;
    z += Math.cos(yaw * D2R) * step;
    out.push(x, groundY, z);
  }
  return out;
}

export function straightPath(ego, groundY, len, out) {
  out.clear();
  out.push(ego.x, groundY, ego.z);
  out.push(ego.x + Math.sin(ego.yaw * D2R) * len, groundY, ego.z + Math.cos(ego.yaw * D2R) * len);
  return out;
}

export class Ribbon {
  constructor(samples) {
    this.cap = samples;
    this.s = new Float32Array(samples);
    this.lx = new Float32Array(samples); this.ly = new Float32Array(samples);
    this.rx = new Float32Array(samples); this.ry = new Float32Array(samples);
    this.cx = new Float32Array(samples); this.cy = new Float32Array(samples);
    this.local = new Float32Array(samples * 3);
    this.prev = new Float32Array(samples * 3);
    this.prevLen = 0;
    this.n = 0;
    this.len = 0;
  }
}

const tmp = [0, 0, 0], tmpL = [0, 0, 0], tmpR = [0, 0, 0];

export function sampleStations(maxLen, cap, out) {
  let n = 0, s = 0;
  while (n < cap && s < maxLen) {
    out[n++] = s;
    s += s < 20 ? 0.75 : s < 45 ? 1.5 : 3;
  }
  if (n < cap) out[n++] = maxLen;
  return n;
}

export function buildRibbon(path, ego, cam, halfWidth, maxLen, rib, smooth = 0) {
  const len = Math.min(path.length(), maxLen);
  if (len < 1) { rib.n = 0; rib.len = 0; return rib; }
  const n = sampleStations(len, rib.cap, rib.s);
  const sy = Math.sin(ego.yaw * D2R), cy = Math.cos(ego.yaw * D2R);
  const L = rib.local, P = rib.prev;
  for (let i = 0; i < n; i++) {
    path.sampleAt(rib.s[i], tmp);
    const dx = tmp[0] - ego.x, dz = tmp[2] - ego.z;
    L[i * 3] = dx * cy - dz * sy;
    L[i * 3 + 1] = tmp[1];
    L[i * 3 + 2] = dx * sy + dz * cy;
  }
  if (smooth > 0 && rib.prevLen > 0) {
    const a = 1 - smooth;
    const m = Math.min(n, rib.prevLen);
    for (let k = 0; k < m * 3; k++) L[k] = P[k] + (L[k] - P[k]) * a;
  }
  P.set(L.subarray(0, n * 3));
  rib.prevLen = n;

  let out = 0, started = false;
  let plx = 0, plz = 0, prx = 0, prz = 0;
  for (let i = 0; i < n; i++) {
    const j = Math.min(n - 1, i + 1), h = Math.max(0, i - 1);
    let tx = L[j * 3] - L[h * 3], tz = L[j * 3 + 2] - L[h * 3 + 2];
    const tl = Math.hypot(tx, tz) || 1;
    tx /= tl; tz /= tl;
    let lxl = L[i * 3] - tz * halfWidth, lzl = L[i * 3 + 2] + tx * halfWidth;
    let rxl = L[i * 3] + tz * halfWidth, rzl = L[i * 3 + 2] - tx * halfWidth;
    if (i > 0) {
      if ((lxl - plx) * tx + (lzl - plz) * tz < 0) { lxl = plx; lzl = plz; }
      if ((rxl - prx) * tx + (rzl - prz) * tz < 0) { rxl = prx; rzl = prz; }
    }
    plx = lxl; plz = lzl; prx = rxl; prz = rzl;
    const y = L[i * 3 + 1];
    const wx = (lx, lz) => ego.x + lx * cy + lz * sy;
    const wz = (lx, lz) => ego.z - lx * sy + lz * cy;
    toCamera(cam, wx(lxl, lzl), y, wz(lxl, lzl), tmpL);
    toCamera(cam, wx(rxl, rzl), y, wz(rxl, rzl), tmpR);
    const ok = tmpL[2] > NEAR && tmpR[2] > NEAR;
    if (!ok) {
      if (started) break;
      rib.prevL = rib.prevL || [0, 0, 0]; rib.prevR = rib.prevR || [0, 0, 0];
      rib.prevL[0] = tmpL[0]; rib.prevL[1] = tmpL[1]; rib.prevL[2] = tmpL[2];
      rib.prevR[0] = tmpR[0]; rib.prevR[1] = tmpR[1]; rib.prevR[2] = tmpR[2];
      rib.prevS = rib.s[i];
      rib.hasPrev = true;
      continue;
    }
    if (!started && rib.hasPrev) {
      const fl = clipT(rib.prevL[2], tmpL[2]), fr = clipT(rib.prevR[2], tmpR[2]);
      const f = Math.max(fl, fr);
      emit(rib, out++, cam, lerp3(rib.prevL, tmpL, f), lerp3b(rib.prevR, tmpR, f), rib.prevS + (rib.s[i] - rib.prevS) * f);
    }
    started = true;
    emit(rib, out++, cam, tmpL, tmpR, rib.s[i]);
    if (out >= rib.cap) break;
  }
  rib.hasPrev = false;
  rib.n = out;
  rib.len = len;
  return rib;
}

function clipT(a, b) {
  return b === a ? 0 : Math.min(1, Math.max(0, (NEAR + 1e-3 - a) / (b - a)));
}

const la = [0, 0, 0], lb = [0, 0, 0];
function lerp3(a, b, t) { la[0] = a[0] + (b[0] - a[0]) * t; la[1] = a[1] + (b[1] - a[1]) * t; la[2] = a[2] + (b[2] - a[2]) * t; return la; }
function lerp3b(a, b, t) { lb[0] = a[0] + (b[0] - a[0]) * t; lb[1] = a[1] + (b[1] - a[1]) * t; lb[2] = a[2] + (b[2] - a[2]) * t; return lb; }

function emit(rib, k, cam, l, r, s) {
  rib.s[k] = s;
  rib.lx[k] = cam.w / 2 + (cam.f * l[0]) / l[2];
  rib.ly[k] = cam.h / 2 - (cam.f * l[1]) / l[2];
  rib.rx[k] = cam.w / 2 + (cam.f * r[0]) / r[2];
  rib.ry[k] = cam.h / 2 - (cam.f * r[1]) / r[2];
  rib.cx[k] = (rib.lx[k] + rib.rx[k]) / 2;
  rib.cy[k] = (rib.ly[k] + rib.ry[k]) / 2;
}

export function lerpAngle(a, b, t) {
  return a + wrapDeg(b - a) * t;
}

export function interpolateActors(buf, t, key, out) {
  out.length = 0;
  if (!buf.length) return out;
  let i = buf.length - 1;
  while (i > 0 && buf[i - 1].t > t) i--;
  const b = buf[i], a = buf[Math.max(0, i - 1)];
  const span = b.t - a.t;
  const f = span > 1e-3 ? Math.max(0, Math.min(1.5, (t - a.t) / span)) : 1;
  const prev = new Map();
  for (const o of a[key]) prev.set(o.id, o);
  for (const o of b[key]) {
    const p = prev.get(o.id);
    if (!p) { out.push(o); continue; }
    out.push({ ...o, x: p.x + (o.x - p.x) * f, z: p.z + (o.z - p.z) * f, yaw: lerpAngle(p.yaw || 0, o.yaw || 0, Math.min(f, 1)), ...(o.y != null && p.y != null ? { y: p.y + (o.y - p.y) * f } : {}) });
  }
  return out;
}

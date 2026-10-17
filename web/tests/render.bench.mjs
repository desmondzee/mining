import { readFile } from "node:fs/promises";
import { extname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium, webkit } from "playwright";

const WEB = fileURLToPath(new URL("..", import.meta.url));
const ASSETS = process.env.BENCH_ASSETS;
const ORIGIN = "http://quarry.test";
const TYPES = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml" };
const IDS = ["truck-0", "truck-1", "truck-2"];
const CAMS = ["overview", ...IDS];
const WORLD = { bounds: [-300, -300, 300, 300], preview: true, preview_bounds: [-300, -300, 300, 300], roads: [{ width_m: 8, points: Array.from({ length: 60 }, (_, i) => ({ x: -250 + i * 8, z: 100 * Math.sin(i / 6) })) }], junctions: [], sites: [{ id: "ore_1", name: "North Face", type: "ore", x: 50, z: 50 }, { id: "crusher", name: "Crusher", type: "crusher", x: -50, z: -50 }] };

const trucks = (t) => IDS.map((id, i) => {
  const a = t * 0.2 + i * 2;
  const x = 150 * Math.cos(a), z = 150 * Math.sin(a), yaw = ((-a * 180) / Math.PI + 180) % 360;
  const dir = [-Math.sin(a), Math.cos(a)];
  const route = Array.from({ length: 200 }, (_, k) => { const b = a + k * 0.004; return [150 * Math.cos(b), 150 * Math.sin(b)]; });
  const candidates = Array.from({ length: 44 }, (_, c) => Array.from({ length: 16 }, (_, k) => [x + dir[0] * k * 5 + (c - 22) * k * 0.4, z + dir[1] * k * 5 + (c - 22) * k * 0.3]));
  return { id, x, z, y: 0, yaw, speed: 8, status: "driving", throttle: 0.4, steer: Math.sin(t), goal: [50, 50], goal_site: "ore_1", route, candidates, candidate_scores: candidates.map((_, c) => c / 44), choice: 22 };
});

async function run(name, type, opts) {
  const browser = await type.launch();
  const ctx = await browser.newContext({ viewport: { width: 1512, height: 860 }, deviceScaleFactor: opts.dpr });
  const page = await ctx.newPage();
  const frame = await readFile(join(ASSETS, "frame.jpg"));
  const preview = await readFile(join(ASSETS, "preview.jpg"));
  await page.route(`${ORIGIN}/**`, async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/world") return route.fulfill({ contentType: "application/json", body: JSON.stringify(WORLD) });
    if (path === "/api/world/preview") return route.fulfill({ contentType: "image/jpeg", body: preview });
    if (path.startsWith("/api/") || path.startsWith("/radio/")) return route.fulfill({ status: 404, body: "" });
    const file = path === "/" ? "index.html" : path.slice(1);
    try { return await route.fulfill({ contentType: TYPES[extname(file)] || "application/octet-stream", body: await readFile(join(WEB, file)) }); } catch { return route.fulfill({ status: 404, body: "" }); }
  });
  await page.route(/fonts\.(googleapis|gstatic)\.com|cdnjs/, (r) => r.abort());
  page.on("pageerror", (e) => console.log("pageerror", e.message));
  page.on("console", (m) => { if (m.type() === "error") console.log("console", m.text()); });
  const timers = [];
  let tick = 0;
  await page.routeWebSocket(/\/ws\//, (ws) => {
    const url = ws.url();
    if (url.includes("/ws/state")) {
      const send = () => {
        const tr = opts.driving ? trucks(tick / 10) : trucks(0).map((t) => ({ ...t, speed: 0, status: "idle", goal: null, route: null, candidates: null }));
        ws.send(JSON.stringify({ sim_connected: true, world_seq: 1, t: tick / 10, bounds: WORLD.bounds, trucks: tr, obstacles: [], paths: [], pedestrians: [{ id: "p", x: 160, z: 5, y: 0 }], cameras: CAMS, views: {} }));
        tick++;
      };
      send();
      timers.push(setInterval(send, 100));
    } else if (url.includes("/ws/video")) {
      const enc = new TextEncoder();
      timers.push(setInterval(() => {
        const trs = trucks(tick / 10);
        CAMS.forEach((cam, ci) => {
          const tr = trs[ci - 1] || trs[0];
          const base = { cam: { x: tr.x, y: 3.6, z: tr.z, yaw: tr.yaw, hfov: 90 }, truck: ci ? tr.id : null };
          const meta = opts.driving ? { ...base, pose: { x: tr.x, z: tr.z, yaw: tr.yaw }, speed: 8, t: tick / 10, det: { items: [{ cls: "person", box: [0.45, 0.4, 0.55, 0.8], dist: 20 }, { cls: "rock", box: [0.1, 0.5, 0.2, 0.7], dist: 40 }] } } : base;
          const m = enc.encode(JSON.stringify(meta));
          const nm = enc.encode(cam);
          const buf = new Uint8Array(1 + nm.length + 2 + m.length + frame.length);
          buf[0] = nm.length; buf.set(nm, 1);
          buf[1 + nm.length] = m.length & 255; buf[2 + nm.length] = m.length >> 8;
          buf.set(m, 3 + nm.length); buf.set(frame, 3 + nm.length + m.length);
          ws.send(Buffer.from(buf));
        });
      }, 90));
    }
  });
  await page.addInitScript(() => { window.VideoDecoder = undefined; });
  await page.goto(`${ORIGIN}/`, { waitUntil: "domcontentloaded" });
  await page.waitForFunction(() => document.querySelectorAll(".feed.live").length >= 3, null, { timeout: 20000 });
  await page.waitForTimeout(1500);
  console.log(await page.evaluate(() => [...document.querySelectorAll(".feed")].map((f) => `${f.dataset.camera}:${f.classList.contains("live")}:${!f.querySelector(".hud").hidden}`).join(" ")));
  await page.waitForTimeout(2500);
  let cdp;
  if (process.env.PROFILE && type === chromium) { cdp = await ctx.newCDPSession(page); await cdp.send("Profiler.enable"); await cdp.send("Profiler.start"); }
  const r = await page.evaluate(() => new Promise((res) => {
    let n = 0, worst = 0, last = performance.now();
    const t0 = last;
    const f = (now) => { n++; worst = Math.max(worst, now - last); last = now; if (now - t0 < 4000) requestAnimationFrame(f); else res({ fps: +(n / ((now - t0) / 1000)).toFixed(1), worstMs: Math.round(worst) }); };
    requestAnimationFrame(f);
  }));
  if (cdp) {
    const { profile } = await cdp.send("Profiler.stop");
    const self = new Map();
    const dt = profile.timeDeltas;
    const byId = new Map(profile.nodes.map((n) => [n.id, n]));
    profile.samples.forEach((id, i) => { const n = byId.get(id); const k = `${n.callFrame.functionName || "(anon)"} ${n.callFrame.url.split("/").pop()}:${n.callFrame.lineNumber + 1}`; self.set(k, (self.get(k) || 0) + dt[i] / 1000); });
    console.log([...self].sort((a, b) => b[1] - a[1]).slice(0, 14).map(([k, v]) => `${v.toFixed(0)}ms ${k}`).join("\n"));
  }
  console.log(name.padEnd(34), JSON.stringify(r));
  timers.forEach(clearInterval);
  await browser.close();
}

for (const [bn, bt] of [["webkit", webkit], ["chromium", chromium]]) {
  for (const dpr of [1, 2]) for (const driving of [false, true]) await run(`${bn} dpr${dpr} ${driving ? "driving" : "parked"}`, bt, { dpr, driving });
}

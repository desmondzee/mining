import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { extname, join } from "node:path";
import { after, before, test } from "node:test";
import { fileURLToPath } from "node:url";
import { webkit } from "playwright";

const WEB = fileURLToPath(new URL("..", import.meta.url));
const ORIGIN = "http://quarry.test";
const TYPES = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css" };
const TRUCKS = ["truck-0", "truck-1", "truck-2"].map((id, i) => ({ id, x: i * 10, z: 0, yaw: 0, speed: 1, status: "driving", throttle: 0.3, steer: 0, goal: [50, 50], goal_site: "ore_1" }));
const STATE = { sim_connected: true, world_seq: 1, bounds: [-100, -100, 100, 100], trucks: TRUCKS, obstacles: [], paths: [], pedestrians: [], cameras: ["overview", ...TRUCKS.map((t) => t.id)] };
const WORLD = { bounds: [-100, -100, 100, 100], roads: [], junctions: [], sites: [{ id: "ore_1", name: "North Face", type: "ore", x: 50, z: 50 }, { id: "crusher", name: "Primary Crusher", type: "crusher", x: -50, z: -50 }] };

const RADIO_STATE = { type: "state", t: 1, mode: "mock", brain_ok: true, trucks: TRUCKS.map((t) => ({ id: t.id, phase: "to_load", task: "to Lower Face (0/3)", speed: 1 })), excavators: [{ id: "exc-0", face_name: "Lower Face", state: "digging", x: 0, z: 0 }] };

const timers = [];
let browser;
let page;
const requests = [];

before(async () => {
  browser = await webkit.launch();
  page = await browser.newPage();
  page.setDefaultTimeout(8000);
  page.on("pageerror", (e) => console.error("pageerror", e.message));
  await page.route(`${ORIGIN}/**`, async (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname;
    if (path.startsWith("/api/trucks/") || path.startsWith("/radio/api/")) {
      requests.push({ method: req.method(), path, body: req.postData() });
      return route.fulfill({ status: 200, contentType: "application/json", body: "{}" });
    }
    if (path.startsWith("/radio/")) return route.fulfill({ status: 404, body: "" });
    if (path === "/api/world") return route.fulfill({ contentType: "application/json", body: JSON.stringify(WORLD) });
    const file = path === "/" ? "index.html" : path.slice(1);
    try {
      return route.fulfill({ contentType: TYPES[extname(file)] || "application/octet-stream", body: await readFile(join(WEB, file)) });
    } catch {
      return route.fulfill({ status: 404, body: "" });
    }
  });
  await page.routeWebSocket(/\/ws\//, (ws) => {
    if (ws.url().includes("/radio/ws/radio")) {
      ws.send(JSON.stringify({ type: "hello", health: { mode: "mock", mock: true, brain_ok: true }, log: [{ type: "radio", id: "a1", from: "Dispatch", kind: "operator", text: "Trucks zero and one, haul to the crusher", t: 1 }] }));
      timers.push(setInterval(() => ws.send(JSON.stringify(RADIO_STATE)), 200));
      return;
    }
    if (ws.url().includes("/ws/state")) {
      const send = () => ws.send(JSON.stringify(STATE));
      send();
      timers.push(setInterval(send, 200));
    }
  });
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (route) => route.abort());
  await page.goto(`${ORIGIN}/`, { waitUntil: "domcontentloaded", timeout: 30000 });
  await page.waitForFunction(() => document.querySelectorAll("#trucks li").length === 3);
});

after(async () => {
  timers.forEach(clearInterval);
  await browser?.close();
});

test("slash opens the palette and 'stop all' cancels every goal", async () => {
  await page.keyboard.press("/");
  await page.locator("#palette.open").waitFor();
  assert.equal(await page.evaluate(() => document.activeElement.id), "palette-input");
  await page.keyboard.type("stop all");
  assert.match(await page.locator('[role="option"][aria-selected="true"]').locator(".label").innerText(), /^Stop all$/);
  requests.length = 0;
  await page.keyboard.press("Enter");
  assert.match(await page.locator('[role="option"][aria-selected="true"]').innerText(), /again/);
  assert.equal(requests.length, 0);
  await page.keyboard.press("Enter");
  await page.waitForFunction(() => document.getElementById("palette").hidden);
  await page.waitForTimeout(150);
  const deletes = requests.filter((r) => r.method === "DELETE").map((r) => r.path).sort();
  assert.deepEqual(deletes, TRUCKS.map((t) => `/api/trucks/${t.id}/goal`));
  assert.ok(requests.some((r) => r.method === "POST" && r.path === "/radio/api/radio/release"));
});

test("natural query ranks the matching send command first", async () => {
  await page.keyboard.press("Control+k");
  await page.locator("#palette.open").waitFor();
  await page.keyboard.type("truck 2 crusher");
  assert.equal(await page.locator('[role="option"][aria-selected="true"] .label').innerText(), "Send truck-2 to Primary Crusher");
  requests.length = 0;
  await page.keyboard.press("Enter");
  await page.waitForTimeout(100);
  assert.deepEqual(requests.map((r) => [r.method, r.path, JSON.parse(r.body)]), [["POST", "/api/trucks/truck-2/goal", { site: "crusher" }]]);
});

test("escape closes the palette and view shortcuts switch tabs", async () => {
  await page.keyboard.press("/");
  await page.locator("#palette.open").waitFor();
  await page.keyboard.press("Escape");
  await page.waitForFunction(() => document.getElementById("palette").hidden);
  await page.keyboard.press("m");
  await page.waitForFunction(() => document.documentElement.dataset.view === "map");
  await page.keyboard.press("2");
  await page.waitForFunction(() => document.documentElement.dataset.view === "video" && document.querySelector('.feed.active').dataset.camera === "truck-0");
});

test("unmatched text falls back to a radio command", async () => {
  await page.keyboard.press("/");
  await page.locator("#palette.open").waitFor();
  await page.keyboard.type("excavators one and two start digging");
  assert.match(await page.locator('[role="option"][aria-selected="true"]').innerText(), /Send to radio/);
  requests.length = 0;
  await page.keyboard.press("Enter");
  await page.waitForTimeout(150);
  assert.deepEqual(requests.map((r) => [r.method, r.path, JSON.parse(r.body)]), [["POST", "/radio/api/radio/command", { text: "excavators one and two start digging" }]]);
});

test("mock radio is flagged and the transcript backfills", async () => {
  await page.waitForFunction(() => document.getElementById("summary").textContent === "mock");
  assert.match(await page.locator("#transcript li").first().textContent(), /haul to the crusher/);
});

test("shift escape twice stops everything", async () => {
  requests.length = 0;
  await page.keyboard.press("Shift+Escape");
  await page.waitForTimeout(50);
  assert.equal(requests.length, 0);
  await page.keyboard.press("Shift+Escape");
  await page.waitForTimeout(150);
  assert.equal(requests.filter((r) => r.method === "DELETE").length, 3);
});

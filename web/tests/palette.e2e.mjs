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

let browser;
let page;
const requests = [];

before(async () => {
  browser = await webkit.launch();
  page = await browser.newPage();
  await page.route(`${ORIGIN}/**`, async (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname;
    if (path.startsWith("/api/trucks/")) {
      requests.push({ method: req.method(), path, body: req.postData() });
      return route.fulfill({ status: 200, contentType: "application/json", body: "{}" });
    }
    if (path === "/api/world") return route.fulfill({ contentType: "application/json", body: JSON.stringify(WORLD) });
    const file = path === "/" ? "index.html" : path.slice(1);
    try {
      return route.fulfill({ contentType: TYPES[extname(file)] || "application/octet-stream", body: await readFile(join(WEB, file)) });
    } catch {
      return route.fulfill({ status: 404, body: "" });
    }
  });
  await page.routeWebSocket(/\/ws\//, (ws) => {
    if (ws.url().includes("/ws/state")) {
      const send = () => ws.send(JSON.stringify(STATE));
      send();
      const timer = setInterval(send, 200);
      ws.onClose(() => clearInterval(timer));
    }
  });
  await page.goto(`${ORIGIN}/`);
  await page.waitForFunction(() => document.querySelectorAll("#trucks li").length === 3);
});

after(async () => {
  await browser?.close();
});

test("slash opens the palette and 'stop all' cancels every goal", async () => {
  await page.keyboard.press("/");
  await page.locator("#palette.open").waitFor();
  assert.equal(await page.evaluate(() => document.activeElement.id), "palette-input");
  await page.keyboard.type("stop all");
  assert.match(await page.locator('[role="option"][aria-selected="true"]').innerText(), /Stop all trucks/);
  requests.length = 0;
  await page.keyboard.press("Enter");
  await page.waitForFunction(() => document.getElementById("palette").hidden);
  await page.waitForTimeout(100);
  const deletes = requests.filter((r) => r.method === "DELETE").map((r) => r.path).sort();
  assert.deepEqual(deletes, TRUCKS.map((t) => `/api/trucks/${t.id}/goal`));
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

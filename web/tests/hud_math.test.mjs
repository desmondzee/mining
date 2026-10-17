import assert from "node:assert/strict";
import test from "node:test";
import { PathBuffer, Ribbon, buildRibbon, cameraModel, interpolateActors, predictPath, project, routePath } from "../hud_math.js";

const W = 640, H = 360, F = 320;
const near = (a, b, eps = 1e-6) => assert.ok(Math.abs(a - b) < eps, `${a} != ${b}`);
const cam = (o = {}) => cameraModel({ x: 0, y: 3.6, z: 0, yaw: 0, pitch: 0, roll: 0, hfov: 90, ...o }, W, H);

test("point straight ahead projects to the image centre", () => {
  const out = [0, 0, 0];
  assert.ok(project(cam(), 0, 3.6, 20, out) > 0);
  near(out[0], W / 2); near(out[1], H / 2);
});

test("yaw follows Unity convention: 0 = +z, clockwise positive", () => {
  const out = [0, 0, 0];
  project(cam({ yaw: 90 }), 30, 3.6, 0, out);
  near(out[0], W / 2); near(out[1], H / 2);
  project(cam({ yaw: 0 }), 10, 3.6, 10, out);
  near(out[0], W);
});

test("ground rows match the fakesim renderer (horizon + f * h / d)", () => {
  const out = [0, 0, 0];
  for (const d of [8, 20, 60]) {
    project(cam(), 0, 0, d, out);
    near(out[1], H / 2 + (F * 3.6) / d);
  }
  project(cam(), 10, 0, 30, out);
  near(out[0], W / 2 + (F * 10) / 30);
});

test("positive pitch looks down, moving the horizon up by f * tan(pitch)", () => {
  const out = [0, 0, 0];
  const c = cam({ pitch: 8 });
  project(c, 0, 3.6, 1e6, out);
  near(out[1], H / 2 - F * Math.tan((8 * Math.PI) / 180), 1e-2);
  near(out[0], W / 2, 1e-6);
});

test("positive roll lifts the camera right side, so the far right horizon drops in the image", () => {
  const out = [0, 0, 0];
  const c = cam({ roll: 10 });
  project(c, 1000, 3.6, 1e5, out);
  const right = out[1];
  project(c, -1000, 3.6, 1e5, out);
  assert.ok(right > out[1]);
});

test("points behind the camera are rejected", () => {
  assert.equal(project(cam(), 0, 0, -5, [0, 0, 0]), -1);
});

test("route path starts at the truck along its heading and reaches the route", () => {
  const out = new PathBuffer(256);
  routePath({ x: 0, y: 0, z: 0, yaw: 0 }, [[0, -5], [0, 50], [50, 50]], null, 0, 80, out);
  near(out.x[0], 0); near(out.z[0], 0);
  assert.ok(out.length() > 60 && out.length() < 110);
  near(out.x[out.n - 1], 50);
});

test("predicted path curves toward the goal", () => {
  const out = new PathBuffer(256);
  predictPath({ x: 0, z: 0, yaw: 0 }, [40, 40], 0, 80, out);
  assert.ok(out.x[out.n - 1] > 25 && out.z[out.n - 1] > 25);
});

test("ribbon is centred and clipped to the near plane", () => {
  const path = new PathBuffer(64);
  path.push(0, 0, 0); path.push(0, 0, 80);
  const rib = buildRibbon(path, { x: 0, z: 0, yaw: 0 }, cam(), 2.65, 80, new Ribbon(128));
  assert.ok(rib.n > 10);
  for (let i = 0; i < rib.n; i++) near(rib.cx[i], W / 2, 1e-3);
  assert.ok(rib.s[0] > 0.5 && rib.s[0] < 1);
  near(rib.ly[rib.n - 1], H / 2 + (F * 3.6) / 80, 1e-3);
  near(rib.rx[rib.n - 1] - rib.lx[rib.n - 1], (F * 5.3) / 80, 1e-3);
});

test("actors interpolate between state snapshots", () => {
  const buf = [
    { t: 1, pedestrians: [{ id: "p", x: 0, z: 0, yaw: 350 }] },
    { t: 2, pedestrians: [{ id: "p", x: 10, z: 0, yaw: 10 }] },
  ];
  const [p] = interpolateActors(buf, 1.5, "pedestrians", []);
  near(p.x, 5); near(((p.yaw % 360) + 360) % 360, 0);
});

import time

import numpy as np
import pytest

from isaac.fx import dust
from isaac.fx.gravel import Gravel, rear_contacts, truck_frame
from isaac.fx.ground import GroundMap, fill, properties, rasterize, road_mask
from isaac.fx.presets import resolve

PALE = (0.78, 0.72, 0.62)
GREEN = (0.22, 0.34, 0.16)


def split_map() -> GroundMap:
    color = np.zeros((100, 100, 3), np.float32)
    color[:, :50], color[:, 50:] = PALE, GREEN
    dust_map = np.where(np.arange(100) < 50, 1.0, 0.05)[None].repeat(100, 0)
    gravel = np.where(np.arange(100) < 50, 0.9, 0.0)[None].repeat(100, 0)
    zero = np.zeros((100, 100))
    return GroundMap((-50.0, -50.0), 1.0, color, dust_map, gravel, zero, zero, zero, zero == 0)


def test_lookup_cells_and_clamp():
    m = split_map()
    d, g, c = m.sample(np.array([-10.0, 10.0, -1000.0, 1000.0]), np.zeros(4))
    assert np.allclose(d, [1.0, 0.05, 1.0, 0.05]) and np.allclose(g, [0.9, 0, 0.9, 0])
    assert np.allclose(c[0], PALE) and np.allclose(c[1], GREEN)


def test_properties_pale_gravel_dusty_vegetation_not():
    rgb = np.array([[PALE, GREEN, (0.12, 0.11, 0.10)]], float)
    var = np.array([[0.1, 0.1, 0.0]])
    color, dusty, gravel = properties(rgb, var, np.zeros((1, 3)), np.zeros((1, 3)))
    assert dusty[0, 0] > 0.8 and dusty[0, 1] < 0.05 and dusty[0, 2] < 0.05
    assert gravel[0, 0] > 0.5 and gravel[0, 1] < 0.05
    _, dusty_road, gravel_road = properties(rgb, var * 0, np.ones((1, 3)), np.zeros((1, 3)))
    assert dusty_road[0, 2] == pytest.approx(0.55) and gravel_road[0, 2] == pytest.approx(0.85)
    _, steep, _ = properties(rgb, var, np.zeros((1, 3)), np.full((1, 3), 40.0))
    assert steep[0, 0] < dusty[0, 0] * 0.5


def test_road_mask_corridor():
    roads = [{"points": [[0.0, 0.0, 0.0], [40.0, 0.0, 0.0]], "width_m": 10.0}]
    m = road_mask(roads, (-50.0, -50.0), 1.0, (100, 100))
    assert m[50, 70] and m[54, 70] and not m[57, 70] and not m[50, 97]


def test_rasterize_top_surface_and_fill():
    pts = np.array([[0.5, 5.0, 0.5], [0.5, 0.0, 0.5], [1.5, 1.0, 0.5]], np.float32)
    cols = np.array([[1.0, 1.0, 1.0], [0.0, 0.0, 0.0], [0.5, 0.5, 0.5]], np.float32)
    rgb, var, known, top = rasterize(pts, cols, (0.0, 0.0), 1.0, (1, 3))
    assert np.allclose(rgb[0, 0], 1.0) and np.allclose(rgb[0, 1], 0.5) and known.tolist() == [[True, True, False]]
    filled = fill(rgb, known)
    assert np.allclose(filled[0, 2], 0.5)


def test_palette_and_tone_monotonic():
    m = split_map()
    pal = m.palette(8)
    luma = pal @ [0.299, 0.587, 0.114]
    assert (np.diff(luma) >= 0).all()
    assert m.tone(np.array([GREEN]), pal)[0] < m.tone(np.array([PALE]), pal)[0]


def test_surface_smooths_over_half_second_and_drives_levels():
    m = split_map()
    s = dust.Surface(m, m.palette())
    cfg = resolve("high", wind=[0.0, 0.0, 0.0])
    contacts = lambda x: np.array([[x, -1.5, 0.0], [x, 1.5, 0.0]])
    st = s.update("t", 0.0, contacts(-10.0), 90.0, 8.0)
    assert np.allclose(st["dust"], 1.0)
    for _ in range(15):
        st = s.update("t", 1 / 60, contacts(10.0), 90.0, 8.0)
    assert 0.5 < st["dust"].mean() < 0.9
    for _ in range(180):
        st = s.update("t", 1 / 60, contacts(10.0), 90.0, 8.0)
    assert st["dust"].mean() < 0.07
    forward, _ = truck_frame(90.0)
    assert dust.levels(8.0, st, np.zeros(3), forward, cfg).max() < 0.07


def test_slip_raises_level():
    m = GroundMap.uniform()
    s = dust.Surface(m, m.palette())
    c = rear_contacts([0, 0, 0], 0.0, -2.4, 1.5)
    s.update("a", 0.0, c, 0.0, 6.0)
    s.update("b", 0.0, c, 0.0, 6.0)
    for k in range(60):
        a = s.update("a", 1 / 60, c, 0.0, 6.0)
        b = s.update("b", 1 / 60, c, (k + 1) * 0.5, 6.0)
    cfg = resolve("high", wind=[0.0, 0.0, 0.0])
    f, _ = truck_frame(0.0)
    assert b["slip"] > 0.4 and a["slip"] == pytest.approx(0.0)
    assert dust.levels(6.0, b, np.zeros(3), f, cfg).mean() > dust.levels(6.0, a, np.zeros(3), f, cfg).mean()


def test_gravel_scale_and_tone():
    g = Gravel(1000, 10.0, seed=1, protos=3, tones=3)
    g.slot("t")
    z = np.zeros((2, 3))
    f, r = truck_frame(0.0)
    none = sum(g.emit("t", 1 / 60, 8.0, z, f, r, 0.0) for _ in range(60))
    half = sum(g.emit("t", 1 / 60, 8.0, z, f, r, 0.5, 0.9) for _ in range(60))
    assert none == 0 and half == pytest.approx(35, abs=1)
    assert set(g.proto[g.alive]) <= {6, 7, 8}


def test_cpu_budget_three_trucks():
    m = split_map()
    s = dust.Surface(m, m.palette())
    g = Gravel(150, 15.0, seed=0, tones=3)
    cfg = resolve("high")
    pos = [np.array([-20.0, y, 0.0]) for y in (-10.0, 0.0, 10.0)]
    t0 = time.perf_counter()
    n = 300
    for _ in range(n):
        batch = []
        for k, p in enumerate(pos):
            p += [8 / 60, 0, 0]
            c = rear_contacts(p, 90.0, -2.4, 1.5)
            st = s.update(f"t{k}", 1 / 60, c, 90.0, 8.0)
            dust.emitters(p, 90.0, 8.0, c, cfg, st)
            f, r = truck_frame(90.0)
            batch.append((f"t{k}", c, f, r, 8.0, float(st["gravel"].mean()), float(st["tone"].mean())))
        g.update(1 / 60, batch)
        g.render_state()
    ms = (time.perf_counter() - t0) / n * 1000
    assert ms < 1.0, ms

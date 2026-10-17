import numpy as np
import pytest

from isaac.fx import dust
from isaac.fx.gravel import DRAG, GRAVITY, Gravel, intensity, rear_contacts, truck_frame
from isaac.fx.presets import resolve

DT = 1 / 60


def run(g: Gravel, speed: float, seconds: float, yaw: float = 0.0, truck: str = "truck-0", height=None) -> list[int]:
    counts = []
    pos = np.zeros(3)
    for _ in range(round(seconds / DT)):
        forward, right = truck_frame(yaw)
        pos = pos + forward * speed * DT
        g.update(DT, [(truck, rear_contacts(pos, yaw, -2.4, 1.8), forward, right, speed)], height)
        counts.append(g.count())
    return counts


def test_truck_frame_matches_sim_yaw():
    f, r = truck_frame(0.0)
    assert np.allclose(f, [0, 1, 0]) and np.allclose(r, [1, 0, 0])
    f, r = truck_frame(90.0)
    assert np.allclose(f, [1, 0, 0]) and np.allclose(r, [0, -1, 0])


def test_rear_contacts_behind_and_at_track():
    c = rear_contacts([10, 20, 3], 0.0, -2.4, 1.8)
    assert np.allclose(c, [[8.2, 17.6, 3], [11.8, 17.6, 3]])


def test_no_spawn_below_min_speed():
    g = Gravel(150, 15.0, seed=1, min_speed=1.0)
    assert max(run(g, 0.9, 2.0)) == 0
    assert max(run(g, 0.0, 1.0)) == 0


def test_spawn_rate_proportional_to_speed():
    g = Gravel(100000, 10.0, seed=1, min_speed=1.0)
    assert g.spawn_rate(5.0) == pytest.approx(40.0)
    assert g.spawn_rate(-5.0) == pytest.approx(40.0)
    spawned = {}
    for speed in (3.0, 5.0, 8.0):
        g = Gravel(100000, 10.0, seed=1, min_speed=1.0)
        g.slot("t")
        total = sum(g.emit("t", DT, speed, np.zeros((2, 3)), np.array([0, 1.0, 0]), np.array([1.0, 0, 0])) for _ in range(600))
        spawned[speed] = total
    assert spawned[3.0] == pytest.approx(10 * 2 * 10, abs=1)
    assert spawned[5.0] == pytest.approx(10 * 4 * 10, abs=1)
    assert spawned[8.0] == pytest.approx(10 * 7 * 10, abs=1)


def test_cap_per_truck():
    g = Gravel(20, 200.0, seed=1)
    counts = run(g, 8.0, 3.0)
    assert max(counts) == 20
    run(g, 8.0, 1.0, truck="truck-1")
    assert g.count("truck-0") <= 20 and g.count("truck-1") <= 20


def test_ballistic_flight():
    g = Gravel(1, 0.0, seed=0)
    g.slot("t")
    g.alive[0] = True
    g.pos[0] = [0.0, 0.0, 10.0]
    g.vel[0] = [3.0, 0.0, 5.0]
    g.size[0] = 0.0
    g.ground[0] = -100.0
    t = 0.5
    for _ in range(round(t / DT)):
        g.step(DT)
    z_exact = 10 + 5 * t - 0.5 * GRAVITY * t * t
    assert g.pos[0, 2] == pytest.approx(z_exact, abs=0.15)
    assert g.pos[0, 0] == pytest.approx(3 * (1 - np.exp(-DRAG * t)) / DRAG, abs=0.02)
    assert g.vel[0, 2] == pytest.approx((5 + GRAVITY / DRAG) * np.exp(-DRAG * t) - GRAVITY / DRAG, abs=0.05)


def test_stones_fly_up_and_back_then_land_and_disappear():
    g = Gravel(150, 15.0, seed=3)
    g.slot("t")
    forward, right = truck_frame(0.0)
    contacts = rear_contacts([0, 0, 0], 0.0, -2.4, 1.8)
    g.emit("t", 1.0, 8.0, contacts, forward, right)
    alive = np.flatnonzero(g.alive)
    rel = g.vel[alive] - forward * 8.0
    assert (rel @ forward < 0).all() and (g.vel[alive, 2] > 0).all()
    assert (g.pos[alive, 1] < -2.4).all()
    for _ in range(round(1.0 / DT)):
        g.step(DT)
    assert (g.pos[g.alive, 2] >= -1e-6).all()
    for _ in range(round(4.0 / DT)):
        g.step(DT)
    assert g.count() == 0


def test_height_function_used():
    g = Gravel(150, 15.0, seed=3)
    run(g, 8.0, 0.5, height=lambda x, y: np.full_like(x, -1.0))
    for _ in range(120):
        g.step(DT, lambda x, y: np.full_like(x, -1.0))
    a = g.alive
    assert (g.pos[a, 2] >= -1.0 - 1e-6).all() and (g.ground[a] == -1.0).all()


def test_deterministic_with_seed():
    a, b, c = Gravel(150, 15.0, seed=7), Gravel(150, 15.0, seed=7), Gravel(150, 15.0, seed=8)
    for g in (a, b, c):
        run(g, 8.0, 2.0)
    pa, pb, pc = a.render_state(), b.render_state(), c.render_state()
    assert all(np.array_equal(x, y) for x, y in zip(pa, pb))
    assert len(pa[0]) != len(pc[0]) or not np.allclose(pa[0], pc[0])


def test_truck_streams_independent():
    a, b = Gravel(150, 15.0, seed=7), Gravel(150, 15.0, seed=7)
    run(a, 8.0, 1.0)
    run(b, 8.0, 1.0)
    run(b, 8.0, 1.0, truck="truck-1")
    run(a, 8.0, 1.0, truck="truck-1")
    assert np.allclose(a.pos[:150][a.alive[:150]], b.pos[:150][b.alive[:150]])


def test_render_state_shapes_and_unit_quaternions():
    g = Gravel(150, 15.0, seed=1)
    run(g, 8.0, 1.0)
    pos, quat, size, proto = g.render_state()
    n = g.count()
    assert pos.shape == (n, 3) and quat.shape == (n, 4) and size.shape == (n,) and proto.shape == (n,)
    assert np.allclose(np.linalg.norm(quat, axis=1), 1.0)
    assert (size >= 0).all() and set(proto) <= {0, 1, 2}


def test_dust_intensity_and_suppression():
    cfg = resolve("high", wind=[0.0, 0.0, 0.0])
    assert intensity(0.5, 1.0, 8.0) == 0.0 and intensity(8.0, 1.0, 8.0) == 1.0 and intensity(12.0, 1.0, 8.0) == 1.0
    contacts = rear_contacts([0, 0, 0], 0.0, -2.4, 1.8)
    stopped = dust.emitters([0, 0, 0], 0.0, 0.5, contacts, cfg)
    fast = dust.emitters([0, 0, 0], 0.0, 8.0, contacts, cfg)
    assert len(fast) == 3 and all(e[3] == 0.0 for e in stopped)
    assert fast[0][3] == pytest.approx(1.0)
    assert all(p[1] < -2.4 for p, *_ in fast)
    tail = resolve("high", wind=[0.0, 6.0, 0.0])
    assert all(e[3] == 0.0 for e in dust.emitters([0, 0, 0], 0.0, 8.0, contacts, tail))
    assert len(dust.emitters([0, 0, 0], 0.0, 8.0, contacts, resolve("low"))) == 2


def test_presets():
    assert resolve("off")["gravel"] == 0 and not resolve("off")["dust"]
    assert resolve("high", seed=5)["seed"] == 5
    with pytest.raises(ValueError):
        resolve("ultra")

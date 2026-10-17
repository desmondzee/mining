import math
import random

import pytest

from isaac.sim import pedestrians as P
from isaac.sim import world as W

QUARRY = W.REPO / "data/worlds/quarry"
STRAIGHT = [{"id": "r", "from": "parking", "to": "ore_1", "width_m": 15.0, "points": [(0.0, 0.0), (0.0, 200.0)]}]
DT = 1 / 30


@pytest.fixture(scope="module")
def network():
    return W.walker_network(W.load_world(QUARRY))


def run(walkers, seconds, trucks=lambda t: (), ground=None):
    log = []
    for k in range(int(seconds / DT)):
        near = trucks(k * DT)
        for w in walkers:
            w.step(DT, ground, near)
        log.append([(w.x, w.z, w.speed, w.area.locate(w.x, w.z)[1]) for w in walkers])
    return log


def test_strip_frame_and_locate():
    s = P.Strip([(0.0, 0.0), (10.0, 0.0), (10.0, 10.0)], 4.0)
    assert s.length == 20.0
    assert s.at(5.0, 1.0) == pytest.approx((5.0, -1.0))
    assert s.at(15.0, 1.0) == pytest.approx((11.0, 5.0))
    assert s.locate(11.0, 5.0) == pytest.approx((15.0, 1.0))
    assert s.locate(4.0, 3.0) == pytest.approx((4.0, -3.0))
    assert s.locate(12.0, -1.0) == pytest.approx((10.0, math.hypot(2.0, 1.0)))


def test_routes_and_areas_on_main_haul_roads(network):
    usage = P.route_usage(*network)
    assert usage == {"spur_7": 3, "haul_6": 2, "haul_2": 1, "haul_4": 1, "ramp_3": 1}
    areas, walkers = P.make_walkers(*network)
    assert [a.road_id for a in areas] == ["spur_7", "spur_7", "haul_6", "haul_2", "haul_4", "ramp_3"]
    assert [w.id for w in walkers] == [f"Pedestrian_{i:02d}" for i in range(12)]
    assert all(P.MIN_PACE <= w.pace <= P.MAX_PACE for w in walkers)
    for a in areas:
        assert 0 < a.lo < a.hi < a.strip.length and a.hi - a.lo <= 2 * P.WINDOW
        assert len(a.polygon()) >= 4


def test_deterministic(network):
    a = run(P.make_walkers(*network)[1], 60)
    b = run(P.make_walkers(*network)[1], 60)
    assert a == b
    c = run(P.make_walkers(*network, seed=2)[1], 60)
    assert a != c


def test_wander_crosses_pauses_and_stays_in_area(network):
    areas, walkers = P.make_walkers(*network)
    log = run(walkers, 600)
    for i, w in enumerate(walkers):
        lat = [row[i][3] for row in log]
        speed = [row[i][2] for row in log]
        assert min(lat) < -w.area.half and max(lat) > w.area.half
        assert max(speed) <= w.pace * P.HURRY + 1e-9
        assert sum(v < 0.05 for v in speed) * DT > 30
        assert max(abs(v) for v in lat) <= w.area.half + P.SHOULDER_OUT + 1.0
        a = w.area
        spine = [a.strip.at(a.lo - P.CROSS_SHIFT + 0.5 * k, 0.0) for k in range(int((a.hi - a.lo + 2 * P.CROSS_SHIFT) / 0.5) + 1)]
        reach = max(min(math.dist((row[i][0], row[i][1]), p) for p in spine) for row in log[::30])
        assert reach <= a.half + P.SHOULDER_OUT + 1.0


def corridor_runs(log, walkers):
    worst_inside, worst_still = [0.0] * len(walkers), [0.0] * len(walkers)
    inside, still = [0.0] * len(walkers), [0.0] * len(walkers)
    for row in log:
        for i, (x, z, v, lat) in enumerate(row):
            hit = abs(lat) < walkers[i].area.half
            inside[i] = inside[i] + DT if hit else 0.0
            still[i] = still[i] + DT if hit and v < 0.05 else 0.0
            worst_inside[i], worst_still[i] = max(worst_inside[i], inside[i]), max(worst_still[i], still[i])
    return worst_inside, worst_still


def test_corridor_time_capped_and_no_stops_in_road(network):
    areas, walkers = P.make_walkers(*network)
    inside, still = corridor_runs(run(walkers, 900), walkers)
    for w, t_in, t_still in zip(walkers, inside, still):
        assert t_in <= w.area.strip.width / w.pace + P.CORRIDOR_SLACK + 1.0
        assert t_still <= P.EDGE_PAUSE[1] + 0.5


def test_no_permanent_blocking_with_trucks(network):
    areas, walkers = P.make_walkers(*network)
    rng = random.Random(3)
    picks = [(rng.choice(areas), rng.uniform(0, 1)) for _ in range(20)]

    def trucks(t):
        a, f = picks[int(t // 45) % len(picks)]
        x, z = a.strip.at(a.lo + (a.hi - a.lo) * f, 0.0)
        return [(x, z, rng.uniform(0, 360), 6.0)]

    log = run(walkers, 900, trucks)
    inside, still = corridor_runs(log, walkers)
    for w, t_in, t_still in zip(walkers, inside, still):
        assert t_in <= w.area.strip.width / w.pace + P.CORRIDOR_SLACK + 1.0
        assert t_still <= P.EDGE_PAUSE[1] + 0.5


def test_attentive_walker_clears_road_for_truck():
    areas, walkers = P.make_walkers(STRAIGHT, {"parking": "parking", "ore_1": "ore"}, count=1, people=1)
    w = walkers[0]
    w.attentive, w.wait = True, 0.0
    w.x, w.z = w.area.strip.at(100.0, -1.0)
    w.goal, w.mode = w.area.strip.at(100.0, 12.0), "cross"
    log = run([w], 6, lambda t: [(0.0, 90.0, 0.0, 6.0)])
    assert log[-1][0][3] <= -w.area.half


def test_attentive_walker_waits_on_shoulder_then_crosses():
    areas, walkers = P.make_walkers(STRAIGHT, {"parking": "parking", "ore_1": "ore"}, count=1, people=1)
    w = walkers[0]
    w.attentive, w.wait = True, 0.0
    w.x, w.z = w.area.strip.at(100.0, -10.0)
    w.goal, w.mode = w.area.strip.at(100.0, 10.0), "cross"
    run([w], 5, lambda t: [(0.0, 92.0, 0.0, 6.0)])
    assert w.area.locate(w.x, w.z)[1] < -w.area.half and w.yielding
    run([w], 14)
    assert w.area.locate(w.x, w.z)[1] > w.area.half


def test_speed_ramps_and_ground_follow():
    areas, walkers = P.make_walkers(STRAIGHT, {"parking": "parking", "ore_1": "ore"}, count=1, people=1)
    w = walkers[0]
    w.wait = 0.0
    speeds = [row[0][2] for row in run([w], 3, ground=lambda x, z, ref: 5.0 + 0.1 * z)]
    assert all(abs(b - a) <= P.ACCEL * DT + 1e-9 for a, b in zip(speeds, speeds[1:]))
    assert max(speeds) > 1.0
    assert w.y == pytest.approx(5.0 + 0.1 * w.z)


def test_brain_copy_identical():
    brain = W.REPO / "brain/brain/pedestrians.py"
    assert brain.read_text() == (W.REPO / "isaac/sim/pedestrians.py").read_text()


def test_state_shape(network):
    w = P.make_walkers(*network)[1][0]
    st = w.state()
    assert set(st) == {"id", "x", "z", "yaw"} and 0 <= st["yaw"] < 360 and math.isfinite(st["x"])

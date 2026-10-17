"""Mock sim trucks follow the roads tightly: drive every site-to-site route and assert they never leave the corridor."""
import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from server import WORLD_PATH, MockBrain, World  # noqa: E402

world = World(WORLD_PATH)
DT = 1 / 20


def points() -> dict[str, tuple[float, float]]:
    pts = {s.id: s.point for s in world.sites.values()}
    for tid, t in world.trucks.items():
        pts[f"spawn:{tid}"] = (t["spawn"][0], t["spawn"][1])
    return pts


PTS = points()
ROUTES = [(a, b) for a in PTS for b in PTS if a != b and not b.startswith("spawn:")]


def drive(a: str, b: str, yaw_flip: bool) -> dict:
    mb = MockBrain(world)
    tid = next(iter(mb.trucks))
    t = mb.trucks[tid]
    (ax, az), (bx, bz) = PTS[a], PTS[b]
    path = world.road_path((ax, az), (bx, bz))
    nx, nz = path[0] if path and math.dist(path[0], (ax, az)) > 1 else (bx, bz)
    yaw = math.degrees(math.atan2(nx - ax, nz - az)) + (180.0 if yaw_flip else 0.0)
    t.update(x=ax, z=az, yaw=yaw % 360, speed=0.0)
    t["goal"], t["status"] = (bx, bz), "driving"
    worst, out_of_bounds, steps, lat = -math.inf, False, 0, 0.0
    hw = {i: s[4] for i, s in enumerate(mb.corridor.segs)}
    limit = int(900 / DT)
    while t["status"] != "arrived" and steps < limit:
        mb._step(t, DT)
        steps += 1
        worst = max(worst, mb.corridor.off_road(t["x"], t["z"]))
        out_of_bounds |= not mb.corridor.in_bounds(t["x"], t["z"])
        tan_d = math.tan(math.radians(t["steer"] * mb.MAX_STEER))
        lat = max(lat, t["speed"] ** 2 * abs(tan_d) / mb.WHEELBASE)
    return dict(arrived=t["status"] == "arrived", worst=worst, oob=out_of_bounds, seconds=steps * DT, lat=lat, hw=min(hw.values()))


def test_min_turn_radius_is_8_7m():
    mb = MockBrain(world)
    assert mb.min_radius == pytest.approx(8.66, abs=0.05)


@pytest.mark.parametrize("yaw_flip", [False, True], ids=["facing-route", "facing-away"])
@pytest.mark.parametrize("a,b", ROUTES, ids=[f"{a}->{b}" for a, b in ROUTES])
def test_mock_truck_stays_on_road(a, b, yaw_flip):
    r = drive(a, b, yaw_flip)
    assert r["arrived"], f"{a}->{b} did not arrive in {r['seconds']:.0f}s"
    assert r["worst"] < 0.0, f"{a}->{b} left the corridor by {r['worst']:.2f} m"
    assert not r["oob"], f"{a}->{b} left world bounds"
    assert r["lat"] <= MockBrain.LAT_ACC + 0.05, f"{a}->{b} lateral accel {r['lat']:.2f} m/s^2"


def test_off_road_goal_is_pulled_onto_corridor():
    import asyncio

    mb = MockBrain(world)
    tid = next(iter(mb.trucks))
    x0, z0, x1, z1 = mb.corridor.bounds
    asyncio.run(mb.set_goal(tid, x1 + 500, z1 + 500))
    gx, gz = mb.trucks[tid]["goal"]
    assert mb.corridor.off_road(gx, gz) < 0 and mb.corridor.in_bounds(gx, gz)

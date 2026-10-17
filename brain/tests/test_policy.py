import numpy as np
import pytest

from brain.policy import act, ground_mask, heading_error, planner_points, sector_ranges, sensor_rotation
from brain.world import Truck

CLEAR = np.full((36, 64), 100.0, np.float32)


def test_idle_without_goal():
    assert act(Truck("t"), None, CLEAR).status == "idle"


def test_arrived_near_goal():
    t = Truck("t", x=10, z=10, goal=(11, 11))
    a = act(t, None, CLEAR)
    assert a.status == "arrived" and a.throttle == 0


@pytest.mark.parametrize(
    "goal,expected",
    [((0, 10), 0), ((10, 0), 90), ((-10, 0), -90), ((0, -10), -180)],
)
def test_heading_error_unity_convention(goal, expected):
    assert heading_error(Truck("t"), *goal) == pytest.approx(expected)


def test_drives_straight_at_clear_goal():
    a = act(Truck("t", goal=(0, 50)), None, CLEAR)
    assert a.status == "driving" and a.steer == pytest.approx(0) and a.throttle == pytest.approx(5 / 8)


def test_speed_cap_env_and_goal_slowdown(monkeypatch):
    monkeypatch.setenv("MAX_SPEED", "8")
    assert act(Truck("t", goal=(0, 50)), None, CLEAR).throttle == pytest.approx(1)
    assert act(Truck("t", goal=(0, 20)), None, CLEAR).throttle == pytest.approx(3 / 8)


def test_turns_toward_goal():
    assert act(Truck("t", goal=(50, 0)), None, CLEAR).steer > 0
    assert act(Truck("t", goal=(-50, 0)), None, CLEAR).steer < 0


def test_avoids_obstacle_ahead_toward_open_side():
    depth = CLEAR.copy()
    depth[:, 24:40] = 3.0
    depth[:, :20] = 10.0
    a = act(Truck("t", goal=(0, 50)), None, depth)
    assert a.status == "avoiding" and a.steer > 0 and a.throttle < 0.5


def test_sector_ranges_ignore_invalid_depth():
    depth = CLEAR.copy()
    depth[:, :] = np.nan
    depth[18, 40] = 7.0
    left, centre, right = sector_ranges(depth)
    assert left == np.inf and centre == pytest.approx(7.0) and right == np.inf




def isaac_euler(r: np.ndarray) -> tuple[float, float, float]:
    yaw = np.degrees(np.arctan2(r[0, 2], r[2, 2]))
    pitch = np.degrees(np.arctan2(-r[1, 2], np.hypot(r[1, 0], r[1, 1])))
    roll = np.degrees(np.arctan2(r[1, 0], r[1, 1]))
    return yaw % 360.0, pitch, roll


@pytest.mark.parametrize("angles", [(0.0, 0.0, 0.0), (59.5, 1.5, 7.0), (275.0, -8.0, -3.0)])
def test_sensor_rotation_inverts_isaac_cam_field(angles):
    assert isaac_euler(sensor_rotation(*angles)) == pytest.approx(angles)


def test_planner_points_moves_front_sensor_to_truck_centre_and_drops_ground():
    t = Truck("t", x=10.0, z=20.0, yaw=90.0)
    sensor = {"x": 15.0, "y": 3.0, "z": 20.0, "yaw": 90.0, "pitch": 0.0, "roll": 0.0}
    pts = planner_points(np.array([[0.0, -1.5, 10.0], [0.0, 0.0, 10.0]], np.float32), sensor, t, 0.0, 2.7)
    assert len(pts) == 1 and pts[0] == pytest.approx([0.0, 0.3, 15.0], abs=1e-5)


def flat_scan(height: float, pedestrian: tuple[float, float] | None = None) -> np.ndarray:
    el = np.radians(np.linspace(-19.5, 9.5, 30))[:, None]
    az = np.radians(np.linspace(-59.75, 59.75, 240))[None, :]
    rng = np.where(np.sin(el) < 0, -height / np.sin(el), np.inf) * np.ones_like(az)
    if pedestrian is not None:
        bearing, dist = np.arctan2(*pedestrian), np.hypot(*pedestrian)
        on = (np.abs(az - bearing) * dist < 0.3) & (np.abs(dist * np.tan(el) + height - 0.9) < 0.9)
        rng = np.where(on & (dist / np.cos(el) < rng), dist / np.cos(el), rng)
    ok = rng < 100
    el, az = np.broadcast_arrays(el, az)
    pts = np.stack([np.cos(el) * np.sin(az), np.sin(el), np.cos(el) * np.cos(az)], axis=-1)[ok] * rng[ok, None]
    return pts.astype(np.float32)


def test_ground_mask_keeps_pedestrian_and_drops_bumpy_ground():
    pts = flat_scan(2.7, pedestrian=(2.0, 30.0))
    world = pts + [0.0, 2.7, 0.0]
    world[:, 1] += 0.9 * np.sin(world[:, 2] / 6.0) * (np.hypot(world[:, 0] - 2.0, world[:, 2] - 30.0) > 1.0)
    ground = ground_mask(pts, world, np.array([0.0, 2.7, 0.0]), 2.7)
    person = np.hypot(world[:, 0] - 2.0, world[:, 2] - 30.0) < 0.5
    assert person.sum() >= 2 and not ground[person & (world[:, 1] > 0.8)].any()
    assert ground[~person].all()

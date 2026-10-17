import math

import numpy as np
import pytest

from isaac.sim.kinematics import COMMAND_TIMEOUT, MAX_SPEED, MAX_TILT_DEG, GroundFollow, Truck, quat_angle, quat_matrix, yaw_matrix


def drive(truck, throttle, steer, seconds, dt=1 / 60):
    t = 0.0
    while t < seconds:
        truck.command(throttle, steer, t)
        truck.step(dt, t)
        t += dt


def test_straight_line_speed_response():
    t = Truck("a", 0, 0, 0)
    drive(t, 1.0, 0.0, 10.0)
    assert t.speed == pytest.approx(MAX_SPEED, rel=1e-3)
    assert t.x == pytest.approx(0, abs=1e-9) and t.z > 50


def test_yaw_convention_heading_east():
    t = Truck("a", 0, 0, 90)
    drive(t, 1.0, 0.0, 2.0)
    assert t.x > 1 and abs(t.z) < 1e-9


def test_positive_steer_turns_clockwise():
    t = Truck("a", 0, 0, 0)
    drive(t, 0.5, 1.0, 1.0)
    assert 0 < t.yaw < 180 and t.x > 0


def test_turn_radius():
    t = Truck("a", 0, 0, 0)
    t.speed = 4.0
    drive(t, 0.5, 1.0, 20.0)
    radius = 5.0 / math.tan(math.radians(30))
    assert math.hypot(t.x - radius, t.z) == pytest.approx(radius, rel=0.02)


def test_command_timeout_stops():
    t = Truck("a", 0, 0, 0)
    drive(t, 1.0, 0.0, 3.0)
    now = 3.0
    while now < 3.0 + COMMAND_TIMEOUT - 0.05:
        t.step(1 / 60, now)
        now += 1 / 60
    assert t.speed > 5
    for _ in range(600):
        t.step(1 / 60, now)
        now += 1 / 60
    assert t.speed < 0.01


def test_clamps_inputs():
    t = Truck("a", 0, 0, 0)
    t.command(5, -9, 0)
    assert (t.throttle, t.steer) == (1.0, -1.0)


def plane(slope_z):
    def probe(xs, zs, tops, lengths):
        h = np.asarray(zs) * slope_z
        return np.where(np.asarray(tops) - h <= np.asarray(lengths), h, np.nan)

    return probe


def test_ground_follow_flat_and_snap():
    t = Truck("a", 10, 20, 45, y=50.0)
    g = GroundFollow((5.0, 4.0, 9.0))
    g.update(t, plane(0.0), 1 / 60)
    assert t.y == pytest.approx(0.0)
    assert quat_angle(g.tilt) == pytest.approx(0.0, abs=1e-6)


def test_ground_follow_pitch_on_slope():
    slope = 0.1
    t = Truck("a", 0, 0, 0, y=0.0)
    g = GroundFollow((5.0, 4.0, 9.0))
    for _ in range(120):
        g.update(t, plane(slope), 1 / 60)
    fwd = g.rotation(t.yaw) @ [0, 0, 1]
    assert math.degrees(math.atan2(fwd[1], fwd[2])) == pytest.approx(math.degrees(math.atan(slope)), abs=0.1)
    up = g.rotation(t.yaw) @ [0, 1, 0]
    assert up[1] > 0.99


def test_ground_follow_roll_and_yaw():
    t = Truck("a", 0, 0, 90, y=0.0)
    g = GroundFollow((5.0, 4.0, 9.0))
    for _ in range(120):
        g.update(t, plane(0.2), 1 / 60)
    r = g.rotation(t.yaw)
    right = r @ [1, 0, 0]
    assert right[2] < 0 and right[1] < 0
    assert (r @ [0, 0, 1])[0] > 0.99


def test_tilt_clamped():
    t = Truck("a", 0, 0, 0)
    g = GroundFollow((5.0, 4.0, 9.0))
    for _ in range(240):
        g.update(t, plane(2.0), 1 / 60)
    assert quat_angle(g.tilt) == pytest.approx(MAX_TILT_DEG, abs=0.5)


def test_quat_roundtrip():
    from isaac.sim.kinematics import quat_from_matrix

    r = yaw_matrix(33) @ yaw_matrix(10)
    np.testing.assert_allclose(quat_matrix(quat_from_matrix(r)), r, atol=1e-9)
    np.testing.assert_allclose(yaw_matrix(90) @ [0, 0, 1], [1, 0, 0], atol=1e-12)

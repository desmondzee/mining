import math

import numpy as np
import pytest

from isaac.sim.zoom import OverviewZoom, aim, box_target

ASPECT = 16 / 9
BASE = aim(np.array([100.0, -50.0, 80.0]), np.array([-0.6, 0.5, -0.55]))


def project(m: np.ndarray, hfov: float, ray: np.ndarray) -> tuple[float, float]:
    local = ray @ m[:3, :3]
    tx = math.tan(math.radians(hfov) / 2)
    x, y = local[0] / -local[2], local[1] / -local[2]
    return (x / tx + 1) / 2, (1 - y / (tx / ASPECT)) / 2


def ray(m: np.ndarray, hfov: float, u: float, v: float) -> np.ndarray:
    tx = math.tan(math.radians(hfov) / 2)
    r = m[:3, :3] @ np.array([(2 * u - 1) * tx, (1 - 2 * v) * tx / ASPECT, -1.0])
    return r / np.linalg.norm(r)


def test_full_box_is_identity():
    f, hfov = box_target(BASE, 60.0, ASPECT, [0, 0, 1, 1])
    np.testing.assert_allclose(f, -BASE[:3, 2], atol=1e-12)
    assert hfov == pytest.approx(60.0)


def test_centred_box_halves_tangent():
    f, hfov = box_target(BASE, 60.0, ASPECT, [0.25, 0.25, 0.75, 0.75])
    np.testing.assert_allclose(f, -BASE[:3, 2], atol=1e-12)
    assert math.tan(math.radians(hfov) / 2) == pytest.approx(math.tan(math.radians(30)) / 2)


@pytest.mark.parametrize("box", [[0.6, 0.1, 0.9, 0.3], [0.05, 0.55, 0.2, 0.95], [0.3, 0.4, 0.9, 0.5]])
def test_off_centre_box_fills_frame_keeping_aspect(box):
    f, hfov = box_target(BASE, 60.0, ASPECT, box)
    m = aim(BASE[:3, 3], f)
    centre = project(m, hfov, ray(BASE, 60.0, (box[0] + box[2]) / 2, (box[1] + box[3]) / 2))
    assert centre == pytest.approx((0.5, 0.5), abs=1e-9)
    corners = np.array([project(m, hfov, ray(BASE, 60.0, u, v)) for u in box[0::2] for v in box[1::2]])
    assert corners.min() >= -1e-9 and corners.max() <= 1 + 1e-9
    spread = np.abs(corners - 0.5).max(axis=0)
    assert spread.max() == pytest.approx(0.5, abs=1e-9)
    assert abs(m[2, 0]) < 1e-12
    assert hfov < 60.0


def test_zoom_eases_in_and_resets_exactly():
    z = OverviewZoom(BASE, 60.0, ASPECT)
    assert not z.step(0.1) and z.settled and z.meta() == {"box": None, "settled": True}
    box = [0.6, 0.1, 0.9, 0.3]
    z.set(box)
    assert not z.settled
    f, hfov = box_target(BASE, 60.0, ASPECT, box)
    seen = []
    for _ in range(4):
        assert z.step(0.1)
        seen.append(z.hfov)
    assert seen == sorted(seen, reverse=True) and seen[0] > hfov
    assert z.settled and z.hfov == pytest.approx(hfov) and z.meta() == {"box": box, "settled": True}
    np.testing.assert_allclose(-z.matrix[:3, 2], f, atol=1e-12)
    np.testing.assert_allclose(z.matrix[:3, 3], BASE[:3, 3])
    z.set(None)
    for _ in range(5):
        z.step(0.1)
    assert z.settled and z.box is None and z.hfov == pytest.approx(60.0)
    np.testing.assert_array_equal(z.matrix, BASE)


def test_bridge_dispatches_view_messages():
    import socket

    from brain.protocol import encode, encode_view

    from isaac.sim.bridge import Bridge

    views, actions = [], []
    bridge = Bridge("127.0.0.1", 0, dict, lambda *a: actions.append(a), lambda *a: views.append(a))
    a, b = socket.socketpair()
    a.sendall(encode_view("overview", [0.1, 0.2, 0.3, 0.4]) + encode({"type": "action", "truck": "t", "throttle": 1, "steer": 0}) + encode_view("overview", None))
    a.close()
    bridge.receive(b)
    assert views == [("overview", [0.1, 0.2, 0.3, 0.4]), ("overview", None)] and len(actions) == 1

"""Coordinate conventions shared by build_stage.py, load_world.py, animate.py and the (future) brain bridge.

Contract frame (world.json, collider.glb, surround.glb, brain protocol, Unity):
    metres, Y up, x east, z "north", heading_deg: 0 = +z, clockwise seen from above, heading = atan2(dx, dz).

Isaac frame (what build_stage.py writes; stage upAxis = Z, metersPerUnit = 1):
    X_isaac =  x
    Y_isaac = -z
    Z_isaac =  y
This is a proper rotation (+90 deg about X), so meshes are not mirrored, triangle winding and handedness are
unchanged, and every relative pose in the contract data stays the same. Contract "north" (+z) is Isaac -Y.

Vehicle / sensor local frame in Isaac (ROS / Isaac convention): +X forward, +Y left, +Z up.
A contract heading h (deg) therefore becomes an Isaac yaw about +Z of  yaw = h - 90  (deg, CCW positive):
    forward_contract = (sin h, 0, cos h)  ->  forward_isaac = (sin h, -cos h, 0) = (cos(h-90), sin(h-90), 0).
"""
from __future__ import annotations

import math

import numpy as np

# 3x3 / 4x4 matrices, column-vector convention (p' = M @ p)
C3 = np.array([[1.0, 0.0, 0.0],
               [0.0, 0.0, -1.0],
               [0.0, 1.0, 0.0]])
C4 = np.eye(4)
C4[:3, :3] = C3

# glTF model frame (Y up, +Z forward, +X = model's left/cab side for our assets) -> Isaac vehicle frame
# (+X forward, +Y left, +Z up):  x_i = z_g, y_i = x_g, z_i = y_g.  Proper rotation (det = +1).
G3 = np.array([[0.0, 0.0, 1.0],
               [1.0, 0.0, 0.0],
               [0.0, 1.0, 0.0]])
G4 = np.eye(4)
G4[:3, :3] = G3


def contract_to_isaac(p) -> np.ndarray:
    """[x, y, z] contract -> [X, Y, Z] Isaac. Works on (3,) or (N, 3)."""
    p = np.asarray(p, dtype=np.float64)
    return p @ C3.T


def isaac_to_contract(p) -> np.ndarray:
    p = np.asarray(p, dtype=np.float64)
    return p @ C3  # C3 is orthonormal: inverse = transpose


def heading_to_yaw_deg(heading_deg: float) -> float:
    """world.json heading (0 = +z, clockwise) -> Isaac yaw about +Z for a +X-forward body, in (-180, 180]."""
    y = (heading_deg - 90.0) % 360.0
    return y - 360.0 if y > 180.0 else y


def yaw_to_heading_deg(yaw_deg: float) -> float:
    """Inverse of heading_to_yaw_deg, in [0, 360) (brain protocol `yaw`)."""
    h = (yaw_deg + 90.0) % 360.0
    return 0.0 if h > 360.0 - 1e-6 else h


def yaw_quat_wxyz(yaw_deg: float) -> tuple[float, float, float, float]:
    """Quaternion (w, x, y, z) for a rotation of yaw_deg about +Z."""
    h = math.radians(yaw_deg) * 0.5
    return (math.cos(h), 0.0, 0.0, math.sin(h))


def quat_wxyz_to_yaw_deg(q) -> float:
    w, x, y, z = (float(v) for v in q)
    return math.degrees(math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z)))


def isaac_pose_to_brain(pos_isaac, yaw_isaac_deg: float) -> dict:
    """Isaac world pose -> brain protocol pose {x, z, yaw} (contract metres / heading degrees)."""
    c = isaac_to_contract(pos_isaac)
    return {"x": float(c[0]), "z": float(c[2]), "yaw": yaw_to_heading_deg(yaw_isaac_deg)}


def isaac_sensor_points_to_unity(points_xyz: np.ndarray) -> np.ndarray:
    """Sensor-local Isaac points (+X fwd, +Y left, +Z up) -> brain/Unity sensor-local (x right, y up, z fwd)."""
    p = np.asarray(points_xyz, dtype=np.float32).reshape(-1, 3)
    return np.stack([-p[:, 1], p[:, 2], p[:, 0]], axis=1)


def _selftest() -> None:
    # heading 90 (east, +x) -> yaw 0 (+X); heading 0 (+z north) -> yaw -90 (-Y = contract north)
    assert heading_to_yaw_deg(90) == 0.0 and heading_to_yaw_deg(0) == -90.0
    for h in (0.0, 37.5, 90.0, 181.0, 352.5):
        fwd_c = np.array([math.sin(math.radians(h)), 0.0, math.cos(math.radians(h))])
        y = math.radians(heading_to_yaw_deg(h))
        assert np.allclose(contract_to_isaac(fwd_c), [math.cos(y), math.sin(y), 0.0], atol=1e-9)
        assert abs(yaw_to_heading_deg(heading_to_yaw_deg(h)) - h) < 1e-9
    assert np.isclose(np.linalg.det(C3), 1.0) and np.isclose(np.linalg.det(G3), 1.0)
    assert np.allclose(isaac_to_contract(contract_to_isaac([1, 2, 3])), [1, 2, 3])


if __name__ == "__main__":
    _selftest()
    print("mine_frames self-test ok")

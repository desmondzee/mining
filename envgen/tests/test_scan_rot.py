"""scan.ROT regression tests (plain asserts).

    envgen/.venv.nosync/bin/python -m envgen.tests.test_scan_rot     # or: python envgen/tests/test_scan_rot.py
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from envgen.scan import ROT  # noqa: E402

UP = {"+x": [1, 0, 0], "-x": [-1, 0, 0], "+y": [0, 1, 0], "-y": [0, -1, 0], "+z": [0, 0, 1], "-z": [0, 0, -1]}


def euler_xyz(deg):
    """Documented convention: active, right-handed, R = Rz(ez) @ Ry(ey) @ Rx(ex)."""
    x, y, z = np.radians(deg)
    cx, sx, cy, sy, cz, sz = np.cos(x), np.sin(x), np.cos(y), np.sin(y), np.cos(z), np.sin(z)
    Rx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
    Ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    Rz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
    return Rz @ Ry @ Rx


def test_rot_table():
    assert set(ROT) == set(UP), sorted(ROT)
    for k, (R, eul) in ROT.items():
        R = np.asarray(R, float)
        assert R.shape == (3, 3), k
        assert np.allclose(R @ R.T, np.eye(3)), f"{k}: not orthonormal"
        assert np.isclose(np.linalg.det(R), 1.0), f"{k}: det {np.linalg.det(R)}"
        assert np.allclose(R @ np.array(UP[k], float), [0, 1, 0]), f"{k}: up -> {R @ UP[k]}"
        assert np.allclose(euler_xyz(eul), R, atol=1e-12), f"{k}: euler {eul} -> {euler_xyz(eul).round(6)} != {R}"
        # 4x4 'matrix' as scan.py builds it: [s*R | t]
        s, t = 2.5, np.array([3.0, 0.0, -4.0])
        M = np.eye(4); M[:3, :3] = s * R; M[:3, 3] = t
        p = np.array([0.3, -1.2, 0.7])
        assert np.allclose((M @ np.append(p, 1))[:3], s * (R @ p) + t), k


def test_plus_y_identity():
    R, eul = ROT["+y"]
    assert np.array_equal(np.asarray(R), np.eye(3)) and list(eul) == [0, 0, 0]


if __name__ == "__main__":
    test_rot_table()
    test_plus_y_identity()
    print("test_scan_rot: all passed")

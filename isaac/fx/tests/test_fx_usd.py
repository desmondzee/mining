import numpy as np
import pytest

pxr = pytest.importorskip("pxr")
from pxr import Usd, UsdGeom  # noqa: E402

from isaac.fx.fx import ROOT, TruckFx  # noqa: E402


def test_truckfx_without_flow_writes_gravel_instancer():
    stage = Usd.Stage.CreateInMemory()
    fx = TruckFx(stage, ["truck-0", "truck-1"], "high", seed=3)
    assert not fx.flow
    for k in range(60):
        fx.update(1 / 30, [("truck-0", (0.0, k * 8 / 30, 0.0), 0.0, 8.0, None), ("truck-1", (10.0, 0.0, 0.0), 0.0, 0.0, None)])
    inst = UsdGeom.PointInstancer(stage.GetPrimAtPath(ROOT + "/Gravel"))
    pos = np.asarray(inst.GetPositionsAttr().Get())
    assert len(pos) == fx.gravel.count() > 0
    assert len(inst.GetOrientationsAttr().Get()) == len(pos) == len(inst.GetProtoIndicesAttr().Get())
    assert len(inst.GetPrototypesRel().GetTargets()) == 9
    assert fx.gravel.count("truck-1") == 0
    assert fx.surface_state("truck-0")["dust"].shape == (2,)


def test_off_preset_authors_nothing():
    stage = Usd.Stage.CreateInMemory()
    fx = TruckFx(stage, ["truck-0"], "off")
    fx.update(1 / 30, [("truck-0", (0.0, 0.0, 0.0), 0.0, 8.0, None)])
    assert not stage.GetPrimAtPath(ROOT)

import json
import math

import numpy as np
import pytest

from isaac.look.look import PRESETS, TONEMAP, dome_rotation_for, latlong_azimuth, load_preset, merge, pano_sun, render_settings, sun_vector


def test_merge_is_deep_and_non_destructive():
    base = {"a": {"b": 1, "c": 2}, "d": [1]}
    out = merge(base, {"a": {"c": 3}, "d": [2]})
    assert out == {"a": {"b": 1, "c": 3}, "d": [2]}
    assert base == {"a": {"b": 1, "c": 2}, "d": [1]}


@pytest.mark.parametrize("name", json.loads(PRESETS.read_text())["presets"])
def test_every_preset_resolves_to_render_settings(name):
    cfg = load_preset(name)
    s = render_settings(cfg)
    assert s["/rtx/post/tonemap/op"] in TONEMAP.values()
    assert s["/rtx/directLighting/domeLight/denoisingTechnique"] == 0
    assert cfg["sky"]["intensity"] > 0 and cfg["sun"]["intensity"] > 0


def test_overrides_reach_settings():
    s = render_settings(load_preset("day", fog={"enabled": True}, exposure={"ev": 1.0}))
    assert s["/rtx/fog/enabled"] is True
    assert s["/rtx/post/tonemap/filmIso"] == pytest.approx(load_preset("day")["exposure"]["iso"] * 2)


def test_unknown_preset_raises():
    with pytest.raises(KeyError):
        load_preset("nope")


def test_pano_sun_finds_bright_blob_across_seam():
    img = np.full((256, 512, 3), 120, np.uint8)
    img[60:66, 0:3] = 255
    img[60:66, 509:512] = 255
    u, v, ratio = pano_sun(img)
    assert min(u, 1 - u) < 0.01
    assert v == pytest.approx(63 / 256, abs=0.01)
    assert ratio > 1.5


def test_dome_rotation_puts_pano_sun_at_requested_azimuth():
    for u in (0.1, 0.45, 0.9):
        for az in (0.0, 135.0, 300.0):
            rot = dome_rotation_for(u, az, 90.0, -1.0)
            assert latlong_azimuth(u, rot, 90.0, -1.0) == pytest.approx(az % 360.0)


def test_sun_vector_compass_convention():
    assert sun_vector(0, 0) == pytest.approx([0, 1, 0], abs=1e-9)
    assert sun_vector(0, 90) == pytest.approx([1, 0, 0], abs=1e-9)
    assert sun_vector(90, 0)[2] == pytest.approx(1.0)
    assert np.linalg.norm(sun_vector(33, 211)) == pytest.approx(1.0)
    assert math.degrees(math.asin(sun_vector(40, 10)[2])) == pytest.approx(40)

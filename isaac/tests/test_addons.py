"""Tests for our add-ons on top of isaac/sim: isaac/excavators.py and isaac/rl_env.py. No Isaac needed.

    uv run --with numpy --with pillow --with pytest --with warp-lang --with usd-core pytest isaac/tests -q

Warp tests skip without warp-lang, USD tests without usd-core (pxr); the renderer is a mock.
"""

import http.server
import json
import math
import shutil
import subprocess
import threading
from types import SimpleNamespace

import numpy as np
import pytest

from isaac import excavators as X
from isaac import rl_env as R
from isaac.sim import world as W
from isaac.sim.kinematics import yaw_matrix
from isaac.sim.sensors import CAM_H, CAM_W, DEPTH_H, DEPTH_W
from isaac.sim.sim import Sim

QUARRY = W.REPO / "data/worlds/quarry"
PLANE = (np.array([[-100, 0, -100], [100, 0, -100], [100, 0, 100], [-100, 0, 100]], float), np.array([[0, 2, 1], [0, 3, 2]]))


@pytest.fixture(scope="module")
def rig():
    return X.Rig()


@pytest.fixture(scope="module")
def world():
    return W.load_world(QUARRY)


def fresh_sim():
    return Sim(QUARRY, raycast=False)


# ------------------------------------------------------------------------------------------------------ rig maths
def test_cycle_pose_keys_and_ease():
    keys = [[0.0, 0, 0, 0, 0], [0.5, 90, 10, -20, -30], [1.0, 0, 0, 0, 0]]
    np.testing.assert_allclose(X.cycle_pose(keys, 0.0), [0, 0, 0, 0])
    np.testing.assert_allclose(X.cycle_pose(keys, 0.5), [90, 10, -20, -30])
    np.testing.assert_allclose(X.cycle_pose(keys, 0.25), [45, 5, -10, -15])   # smootherstep(0.5) = 0.5
    assert X.cycle_pose(keys, 0.05)[0] < 90 * 0.1                              # eased start


def test_rig_rest_pose_is_identity(rig):
    for m in rig.transforms((0, 0, 0, 0)).values():
        np.testing.assert_allclose(m, np.eye(4), atol=1e-12)
    pin = rig.bucket_pin((0, 0, 0, 0))
    assert pin[2] > 3 and pin[1] > 1          # bucket pin ahead of the machine (+z model forward), above ground


def test_house_uses_world_yaw_convention(rig):
    """+house turns the arm from +z toward +x (envgen/exc_aim + preview), about the house pivot."""
    p0, p90 = rig.bucket_pin((0, 0, 0, 0)), rig.bucket_pin((90, 0, 0, 0))
    c = rig.pivot["house"]
    np.testing.assert_allclose(p90 - c, yaw_matrix(90) @ (p0 - c), atol=1e-9)
    assert p90[0] - c[0] > 3


def test_boom_down_and_stick_in(rig):
    rest = rig.bucket_pin((0, 0, 0, 0))
    assert rig.bucket_pin((0, 15, 0, 0))[1] < rest[1] - 1          # + boom = down
    assert rig.bucket_pin((0, 0, 30, 0))[2] < rest[2]              # + stick = in (toward the machine)
    t = rig.transforms((0, 0, 0, 40))                              # bucket curl moves only the bucket
    np.testing.assert_allclose(t["stick"], np.eye(4), atol=1e-12)
    assert not np.allclose(t["bucket"], np.eye(4))


def world_pin(rig, entry, pose):
    return np.asarray(entry["pos3"], float) + yaw_matrix(float(entry["heading_deg"])) @ rig.bucket_pin(pose)


def test_world_keys_reach_dig_and_dump_targets(rig, world):
    entries = [e for e in world.equipment if e.get("exc_keys_deg") and e.get("dump_target")]
    if not entries:
        pytest.skip("quarry world.json has no exc_keys_deg yet (envgen/exc_aim.py)")
    for e in entries:
        keys, _, source = X.keys_for(e, rig)
        assert source == "world.json exc_keys_deg"
        dump = next(k for k in keys if k[0] == 0.70)
        dig = next(k for k in keys if k[0] == 0.12)
        assert np.linalg.norm(world_pin(rig, e, dump[1:]) - e["dump_target"]) < 0.1, e["id"]
        assert np.linalg.norm(world_pin(rig, e, dig[1:]) - e["dig_target"]) < 0.1, e["id"]


def test_dump_ik_from_targets(rig, world):
    pytest.importorskip("envgen.exc_aim")
    entries = [e for e in world.equipment if e.get("dump_target") and e.get("dig_target")]
    if not entries:
        pytest.skip("no dig/dump targets in quarry world.json")
    for e in entries:
        bare = {k: v for k, v in e.items() if k not in ("exc_keys_deg",)}
        keys, _, source = X.keys_for(bare, rig)
        assert source.startswith("world.json dig/dump targets")
        dump = next(k for k in keys if k[0] == 0.70)
        assert np.linalg.norm(world_pin(rig, bare, dump[1:]) - e["dump_target"]) < 0.3, e["id"]
        assert dump[1] == pytest.approx(X.wrap(e["dump_yaw_deg"] - e["heading_deg"]), abs=0.5)


def test_key_fallbacks(rig):
    entry = {"id": "exc-9", "heading_deg": 10.0, "dig_yaw_deg": 100.0, "dump_yaw_deg": 0.0}
    keys, period, source = X.keys_for(entry, rig)
    assert "yaw" in source and period == rig.period
    assert keys[0][1] == pytest.approx(90.0) and next(k for k in keys if k[0] == 0.70)[1] == pytest.approx(-10.0)
    keys, _, source = X.keys_for({}, rig)
    assert source == "rig.json dig_cycle" and keys == rig.keys


# ---------------------------------------------------------------------------------------------------- animation
def test_animator_digs_then_rests():
    sim = fresh_sim()
    ex = X.Excavators(sim)
    assert len(ex.items) == len(sim.excavators) > 0
    houses = set()
    for _ in range(int(12 / 0.05)):
        ex.step(0.05)
        houses.add(round(float(ex.items[0].pose[0])))
    assert max(houses) - min(houses) > 60                     # swung between face and truck
    assert ex.set_state(ex.items[0].id.replace("-", "_"), "holding")  # prim-style ids accepted
    assert ex.set_state(ex.items[1].face, "holding")                  # face ids accepted
    for _ in range(int(6 / 0.05)):
        ex.step(0.05)
    assert np.abs(ex.items[0].pose).max() < 1.0 and np.abs(ex.items[1].pose).max() < 1.0
    assert not ex.set_state("exc-404", "digging")


class FakeRadio:
    def __init__(self, body):
        self.body, self.requests = body, []
        radio = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                radio.requests.append(("GET", self.path))
                data = json.dumps(radio.body).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):  # noqa: N802
                radio.requests.append(("POST", self.path))
                self.send_response(405)
                self.end_headers()

            def log_message(self, *a):
                pass

        self.server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def close(self):
        self.server.shutdown()


def test_radio_link_read_only_list_and_dict_bodies():
    sim = fresh_sim()
    ex = X.Excavators(sim)
    a, b = ex.items[0], ex.items[1]
    radio = FakeRadio([{"id": a.id, "face": a.face, "state": "holding"}, {"id": "", "face": b.face, "state": "holding"},
                       {"id": "exc-404", "state": "digging"}, {"id": a.id, "state": "bogus"}])
    try:
        link = X.RadioLink(radio.url)
        ex.links.append(link)
        link.poll_once()
        ex.step(0.05)
        assert not a.digging and not b.digging and link.states == {a.id: "holding", b.face: "holding"}
        radio.body = {"excavators": [{"id": a.id, "state": "digging"}]}
        link.poll_once()
        ex.step(0.05)
        assert a.digging
        radio.body = {"items": [{"id": a.id, "state": "holding"}]}
        threaded = X.RadioLink(radio.url, period_s=0.02).start()
        for _ in range(200):
            if threaded.polls:
                break
            threading.Event().wait(0.01)
        threaded.stop()
        threaded.apply(ex)
        assert threaded.polls >= 1 and not a.digging
        assert radio.requests and all(r == ("GET", "/api/radio/excavators") for r in radio.requests)
    finally:
        radio.close()


def test_radio_down_keeps_state():
    sim = fresh_sim()
    ex = X.Excavators(sim)
    link = X.RadioLink("http://127.0.0.1:9", timeout_s=0.2)
    ex.links.append(link)
    link.poll_once()
    ex.step(0.05)
    assert link.errors == 1 and all(x.digging for x in ex.items)


class FakeCaster:
    def __init__(self):
        self.boxes = None

    def set_boxes(self, c, r, h):
        self.boxes = (np.asarray(c), np.asarray(r), np.asarray(h))

    def set_cylinders(self, bases, radius, height):
        pass


def test_install_animates_caster_boxes():
    sim = fresh_sim()
    sim.caster = FakeCaster()
    ex = X.install(sim, None, "")
    sim.sync_obstacles()
    c0, r0, h0 = sim.caster.boxes
    n, k = len(sim.trucks), len(sim.excavators)
    assert len(c0) == n + len(X.PARTS) * k
    for _ in range(60):                               # sim.step is wrapped: the cycle advances with the sim
        sim.step(1 / 30, sim.time + 1 / 30)
    sim.sync_obstacles()
    c1, r1, _ = sim.caster.boxes
    np.testing.assert_allclose(c1[:n], c0[:n])        # trucks untouched (parked)
    boom = n + X.PARTS.index("boom")
    assert np.linalg.norm(c1[boom] - c0[boom]) > 0.1
    for r in r1:
        np.testing.assert_allclose(r.T @ r, np.eye(3), atol=1e-9)
    assert ex.items[0].pose.any()


def test_warp_rays_see_the_swinging_arm(rig):
    wp = pytest.importorskip("warp")  # noqa: F841
    from isaac.sim.raycast import Caster

    box = W.Box("exc-0", 0.0, 0.0, 0.0, 0.0, tuple(rig.size), rig.size[1] / 2)
    keys = [[0.0, 0, 0, 0, 0], [1.0, 0, 0, 0, 0]]
    sim = SimpleNamespace(world=SimpleNamespace(equipment=[{"id": "exc-0", "exc_keys_deg": keys}]), excavators=[box], trucks=[],
                          caster=Caster(PLANE, device="cpu"))
    ex = X.Excavators(sim, rig=rig)
    X.hook_caster(sim, ex)
    pin = rig.bucket_pin((0, 0, 0, 0))
    origin, direction = np.array([[-30.0, pin[1], pin[2]]]), np.array([[1.0, 0, 0]])

    def ray():
        sim.caster.set_boxes(np.array([[0, rig.size[1] / 2, 0]]), np.eye(3)[None], np.array(rig.size)[None] / 2)
        return sim.caster.cast(origin, direction, 100)[0]

    assert ray() < 30                                 # the arm is in front at rest
    ex.items[0].pose[:] = [90, 0, 0, 0]               # swung toward +x: nothing left at that height in front
    assert math.isinf(ray()) or ray() > 30 + 2


def test_usd_rig_replaces_static_model(tmp_path):
    pytest.importorskip("pxr")
    from pxr import Usd, UsdGeom

    sim = fresh_sim()
    stage = Usd.Stage.CreateInMemory()
    for b in sim.excavators:
        UsdGeom.Xform.Define(stage, f"/World/Equipment/{b.id.replace('-', '_')}")
        stage.DefinePrim(f"/World/Equipment/{b.id.replace('-', '_')}/model", "Xform")
    calls = []
    scene = SimpleNamespace(stage=stage, update=lambda s, dt: calls.append(dt))
    X.install(sim, scene, "", cache=tmp_path)
    base = f"/World/Equipment/{sim.excavators[0].id.replace('-', '_')}"
    assert not stage.GetPrimAtPath(base + "/model").IsActive()
    for part in X.PARTS:
        prim = stage.GetPrimAtPath(f"{base}/rig/{part}")
        assert prim and any(p.IsA(UsdGeom.Mesh) for p in Usd.PrimRange(prim)), part
    attr = stage.GetPrimAtPath(base + "/rig/boom").GetAttribute("xformOp:transform")
    before = np.array(attr.Get())
    for _ in range(45):
        sim.step(1 / 30, sim.time + 1 / 30)
    scene.update(sim, 1 / 30)
    assert calls == [1 / 30]
    assert not np.allclose(np.array(attr.Get()), before)
    assert len(list(tmp_path.glob("excavator_*.usdc"))) == len(X.PARTS)


# ------------------------------------------------------------------------------------------------------ RL env
def check_obs(obs):
    assert obs["rgb"].shape == (CAM_H, CAM_W, 3) and obs["rgb"].dtype == np.uint8
    assert obs["depth"].shape == (DEPTH_H, DEPTH_W) and obs["depth"].dtype == np.float32
    assert obs["lidar"].shape == (R.MAX_POINTS, 3) and 0 <= obs["lidar_count"] <= R.MAX_POINTS
    assert obs["pose"].shape == (3,) and obs["speed"].shape == (1,) and obs["goal"].shape == (2,)


def test_env_headless_without_warp_shapes_and_driving():
    env = R.QuarryEnv(QUARRY, raycast=False)
    obs, info = env.reset(seed=1, options={"goal": "crusher"} if "crusher" in env.graph.nodes else None)
    check_obs(obs)
    assert obs["rgb_valid"] == 0 and obs["depth_valid"] == 0 and obs["lidar_count"] == 0
    assert len(info["route"]) >= 2
    start = obs["pose"].copy()
    for _ in range(30):
        obs, reward, terminated, truncated, info = env.step({"throttle": 1.0, "steer": 0.0})
        check_obs(obs)
        assert isinstance(reward, float) and {"collision", "offroad", "goal_dist", "progress_m"} <= set(info)
    assert np.hypot(*(obs["pose"][:2] - start[:2])) > 5 and obs["speed"][0] > 3


def test_env_mock_renderer_rgb():
    class Mock:
        def __init__(self):
            self.updates = 0

        def update(self, sim, dt):
            self.updates += 1

        def render(self):
            return {t.id: np.full((CAM_H, CAM_W, 3), 7, np.uint8) for t in env.sim.trucks}

    mock = Mock()
    env = R.QuarryEnv(QUARRY, raycast=False, render=mock)
    obs, _ = env.reset(seed=0)
    assert obs["rgb_valid"] == 1 and int(obs["rgb"].mean()) == 7
    env.step(np.array([0.5, 0.1]))
    assert mock.updates == 2


def test_env_warp_sensors_match_sim():
    pytest.importorskip("warp")
    env = R.QuarryEnv(QUARRY, raycast=True)
    obs, _ = env.reset(seed=0)
    assert obs["depth_valid"] == 1 and obs["lidar_count"] > 0
    i = env.sim.trucks.index(env.truck())
    np.testing.assert_allclose(obs["depth"], env.sim.depths()[i], atol=1e-4)
    assert obs["lidar_count"] == len(env.sim.lidars()[i])


def test_default_reward_terms():
    env = SimpleNamespace(arrive_radius=4.0)
    base = {"remaining": 100.0, "goal_dist": 50.0, "collision": False, "offroad": False}
    r, term, info = R.default_reward(env, None, base, {**base, "remaining": 98.0})
    assert r == pytest.approx(2.0 - 0.01) and not term and info["progress_m"] == pytest.approx(2.0)
    r, term, _ = R.default_reward(env, None, base, {**base, "collision": True})
    assert term and r < -9
    r, _, _ = R.default_reward(env, None, base, {**base, "offroad": True})
    assert r == pytest.approx(-0.11)
    r, term, info = R.default_reward(env, None, base, {**base, "goal_dist": 3.0})
    assert term and info["success"] and r > 99


def test_env_success_and_custom_reward():
    seen = []

    def reward_fn(env, truck, prev, now):
        seen.append(now["goal_dist"])
        return 1.5, False, {}

    env = R.QuarryEnv(QUARRY, raycast=False)
    t = env.sim.trucks[0]
    obs, _ = env.reset(seed=0, options={"goal": (t.x + 2.0, t.z)})
    _, reward, terminated, _, info = env.step([0.0, 0.0])
    assert terminated and info.get("success")
    env2 = R.QuarryEnv(QUARRY, raycast=False, reward_fn=reward_fn, max_steps=2)
    env2.reset(seed=0)
    assert env2.step([0, 0])[1] == 1.5 and env2.step([0, 0])[3] is True and len(seen) == 2


def test_env_random_road_spawn_is_on_a_road():
    env = R.QuarryEnv(QUARRY, raycast=False, spawn="road")
    for seed in range(3):
        obs, _ = env.reset(seed=seed)
        assert env.graph.offroad_distance(obs["pose"][:2].astype(float)) <= 0.01


def test_bounds_guard_resolve():
    g = R.BoundsGuard([-50, -50, 50, 50], margin=5.0, half_width=1.0, half_length=2.0)
    assert g.blocked_corners(0, 0, 0) == 0 and g.blocked_corners(44, 0, 90) == 2
    pose, st = g.resolve((40.0, 0.0, 90.0), (41.0, 0.0, 90.0))
    assert st == "ok" and pose == (41.0, 0.0, 90.0)
    pose, st = g.resolve((42.9, 0.0, 90.0), (43.5, 0.0, 90.0))
    assert st == "blocked" and pose == (42.9, 0.0, 90.0)
    pose, st = g.resolve((42.0, 0.0, 45.0), (43.0, 1.0, 45.0))       # diagonal into the wall: slide along z
    assert st == "slide" and pose[1] > 0 and g.blocked_corners(*pose) == 0
    mask = np.ones((10, 10), bool)
    mask[:, 6:] = False                                               # x >= 10 impassable
    g2 = R.BoundsGuard([-50, -50, 50, 50], R.NavGrid.from_mask(mask, 10.0, -50.0, -50.0), 0.0, 1.0, 2.0)
    assert g2.blocked_corners(9, 0, 90) == 2 and g2.blocked_corners(0, 0, 90) == 0


def east_pose(env, gap: float):
    hi = env.sim.world.bounds[2] - R.BOUNDS_MARGIN
    return (hi - R.FOOTPRINT_HALF_LENGTH - gap, 0.0, 90.0)


def test_keep_in_bounds_penalty_leaves_dynamics():
    plain = R.QuarryEnv(QUARRY, raycast=False)
    wrapped = R.KeepInBounds(R.QuarryEnv(QUARRY, raycast=False), mode="penalty", penalty=0.5, terminate_after=1000)
    opts = {"spawn_pose": east_pose(plain, 3.0), "goal": (0.0, 0.0)}
    plain.reset(seed=0, options=opts)
    wrapped.reset(seed=0, options=opts)
    outs = []
    for _ in range(30):
        o1, r1, *_ = plain.step([1.0, 0.0])
        o2, r2, _, _, info = wrapped.step([1.0, 0.0])
        np.testing.assert_allclose(o1["pose"], o2["pose"])
        outs.append(info["out_of_bounds"])
        assert r2 == pytest.approx(r1 - (0.5 if info["out_of_bounds"] else 0.0))
    assert outs[0] == 0 and outs[-1] > 0


def test_keep_in_bounds_clamp_stops_at_the_wall():
    env = R.KeepInBounds(R.QuarryEnv(QUARRY, raycast=False), mode="clamp")
    env.reset(seed=0, options={"spawn_pose": east_pose(env, 3.0), "goal": (0.0, 0.0)})
    statuses = []
    for _ in range(60):
        obs, reward, terminated, truncated, info = env.step([1.0, 0.0])
        statuses.append(info["guard"])
    t = env.truck()
    assert env.guard.blocked_corners(t.x, t.z, t.yaw) == 0
    assert "blocked" in statuses and t.speed < 0.5
    with pytest.raises(ValueError):
        R.KeepInBounds(env.env, mode="bogus")


def test_multi_truck_env():
    env = R.KeepInBounds(R.MultiTruckEnv(QUARRY, raycast=False))
    obs, infos = env.reset(seed=0)
    ids = [t.id for t in env.sim.trucks]
    assert sorted(obs) == sorted(ids) == sorted(infos)
    for o in obs.values():
        check_obs(o)
    obs, rew, term, trunc, info = env.step({i: [0.5, 0.0] for i in ids})
    assert sorted(rew) == sorted(ids) and all(isinstance(r, float) for r in rew.values())
    assert all("out_of_bounds" in info[i] for i in ids)


def test_env_with_excavators_headless():
    env = R.QuarryEnv(QUARRY, raycast=False, excavators=True)
    env.reset(seed=0)
    env.step([0.0, 0.0])
    assert env.excavators is not None and any(x.pose.any() for x in env.excavators.items)
    env.close()


# ------------------------------------------------------------------------------------------------------ patches
@pytest.mark.parametrize("name", ["keep_in_bounds.patch", "main_excavators.patch"])
def test_patches_apply_to_isaac_sim(name):
    if not shutil.which("git") or not (W.REPO / ".git").exists():
        pytest.skip("not a git checkout")
    r = subprocess.run(["git", "-C", str(W.REPO), "apply", "--check", str(W.REPO / "isaac/patches" / name)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr

import asyncio
import sys
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent))

import server  # noqa: E402
from server import Limiter, RateGate, Radio, RealBrain, brain_routes, route_points  # noqa: E402


class FakeBrain(RealBrain):
    def __init__(self, state: dict, known_sites: set[str] | None = None):
        self.st = state
        self.known = known_sites
        self.calls: list[tuple] = []

    async def state(self) -> dict:
        return self.st

    async def set_goal(self, tid, x, z):
        self.calls.append(("xz", tid, x, z))
        t = next(t for t in self.st["trucks"] if t["id"] == tid)
        t["goal"], t["status"] = [x, z], "driving"

    async def set_site(self, tid, site):
        if self.known is not None and site not in self.known:
            req = httpx.Request("POST", "http://brain/api")
            raise httpx.HTTPStatusError("422", request=req, response=httpx.Response(422, request=req))
        self.calls.append(("site", tid, site))
        t = next(t for t in self.st["trucks"] if t["id"] == tid)
        t["goal_site"], t["status"] = site, "driving"

    async def clear_goal(self, tid):
        self.calls.append(("clear", tid))
        t = next(t for t in self.st["trucks"] if t["id"] == tid)
        t["goal"], t["status"] = None, "idle"
        if "goal_site" in t:
            t["goal_site"] = None


def flat_state() -> dict:
    spawns = server.World(server.WORLD_PATH).trucks
    return {"sim_connected": True, "trucks": [
        {"id": tid, "x": t["spawn"][0], "z": t["spawn"][1], "yaw": 0.0, "speed": 0.0, "goal": None, "status": "idle"}
        for tid, t in sorted(spawns.items())]}


def routed_state() -> dict:
    st = flat_state()
    st["roads"] = [{"id": "haul_2", "points": [[0, 0, 0], [10, 0, 0]]}]
    st["sites"] = [{"id": s} for s in ("ore_1", "ore_2", "ore_3", "crusher", "dump", "parking", "fuel")]
    for t in st["trucks"]:
        t.update(goal_site=None, route=[])
    return st


async def no_tts(text, speaker):
    return None


def make_radio(state: dict, **kw) -> tuple[Radio, FakeBrain]:
    r = Radio()
    fb = FakeBrain(state, **kw)
    r.brain, r.state, r.brain_ok = fb, state, True
    r.tts = no_tts
    r.update_routing()
    return r, fb


def run(coro):
    return asyncio.run(coro)


def test_detection():
    assert not brain_routes(flat_state())
    assert brain_routes(routed_state())
    st = flat_state()
    st["roads"] = [{"id": "haul_2"}]
    assert not brain_routes(st)
    assert route_points([[1, 2], {"x": 3, "z": 4}, [5, 0, 6]]) == [(1, 2), (3, 4), (5, 6)]


def test_waypoint_mode_chains_xz_goals():
    r, fb = make_radio(flat_state())
    assert not r.brain_routing
    run(r.execute([{"name": "go_to", "args": {"trucks": ["truck-0"], "site": "ore_1"}}]))
    assert [c[0] for c in fb.calls] == ["xz"]
    ctl = r.ctl["truck-0"]
    assert len(ctl.route) > 1 and ctl.dest is None
    assert (fb.calls[0][2], fb.calls[0][3]) == (round(ctl.route[0].x, 2), round(ctl.route[0].z, 2))


def test_waypoint_mode_brain_clearing_goal_on_arrival_is_not_an_override():
    r, fb = make_radio(flat_state())
    run(r.execute([{"name": "haul", "args": {"trucks": ["truck-1"], "from_site": "ore_2", "to_site": "crusher"}}]))
    ctl, t = r.ctl["truck-1"], r.state["trucks"][1]
    last = ctl.route[-1]
    ctl.route = [last]
    ctl.goal, ctl.goal_set_at = (last.x, last.z), time.monotonic() - 5
    t["x"], t["z"], t["goal"], t["status"] = last.x + 4.5, last.z, None, "idle"
    run(r.tick_truck(t))
    assert ctl.phase == "loading"
    run(r.tick_truck(t))
    assert ctl.phase == "loading" and ctl.job is not None


def test_routed_mode_sends_one_site_goal():
    r, fb = make_radio(routed_state())
    assert r.brain_routing and r.health()["routing"] == "brain"
    run(r.execute([{"name": "haul", "args": {"trucks": ["truck-1"], "from_site": "ore_2", "to_site": "crusher"}}]))
    assert fb.calls == [("site", "truck-1", "ore_2")]
    ctl = r.ctl["truck-1"]
    assert ctl.route == [] and ctl.site_goal == "ore_2" and ctl.dest == r.world.sites["ore_2"].point


def test_routed_mode_falls_back_to_xz_when_site_refused():
    r, fb = make_radio(routed_state(), known_sites=set())
    r.state["sites"] = []
    run(r.execute([{"name": "go_to", "args": {"trucks": ["truck-0"], "site": "crusher"}}]))
    pt = r.world.sites["crusher"].point
    assert fb.calls == [("xz", "truck-0", round(pt[0], 2), round(pt[1], 2))]


def test_routed_mode_skips_sites_brain_does_not_list():
    r, fb = make_radio(routed_state())
    r.state["sites"] = [{"id": "somewhere_else"}]
    run(r.execute([{"name": "go_to", "args": {"trucks": ["truck-0"], "site": "dump"}}]))
    assert fb.calls[0][0] == "xz"


def test_routed_haul_cycle_and_arrival():
    r, fb = make_radio(routed_state())
    run(r.execute([{"name": "haul", "args": {"trucks": ["truck-1"], "from_site": "ore_2", "to_site": "crusher", "loads": 1}}]))
    ctl = r.ctl["truck-1"]
    t = r.state["trucks"][1]
    t["route"] = [[t["x"], t["z"]], [50.0, 60.0]]
    run(r.tick_truck(t))
    assert ctl.dest == (50.0, 60.0) and ctl.phase == "to_load"
    t["x"], t["z"] = 49.0, 60.0
    run(r.tick_truck(t))
    assert ctl.phase == "loading" and ctl.dest is None
    ctl.dwell_until = time.monotonic() - 1
    run(r.tick_truck(t))
    assert ctl.phase == "to_dump" and fb.calls[-1] == ("site", "truck-1", "crusher")


def test_routed_arrival_by_status():
    r, fb = make_radio(routed_state())
    run(r.execute([{"name": "go_to", "args": {"trucks": ["truck-0"], "site": "dump"}}]))
    ctl, t = r.ctl["truck-0"], r.state["trucks"][0]
    t["status"] = "arrived"
    run(r.tick_truck(t))
    assert ctl.job is not None
    ctl.goal_set_at -= 5
    run(r.tick_truck(t))
    assert ctl.job is None and ctl.phase == "idle"


def test_routed_override_releases_task():
    r, fb = make_radio(routed_state())
    run(r.execute([{"name": "go_to", "args": {"trucks": ["truck-0"], "site": "dump"}}]))
    ctl, t = r.ctl["truck-0"], r.state["trucks"][0]
    ctl.goal_set_at -= 5
    t["goal_site"] = "fuel"
    run(r.tick_truck(t))
    assert ctl.job is None and ctl.dest is None
    assert "override" in r.events[-1]["text"]


def test_release_clears_routed_goals():
    r, fb = make_radio(routed_state())
    run(r.execute([{"name": "go_to", "args": {"trucks": ["all"], "site": "dump"}}]))
    run(r.release())
    assert [c for c in fb.calls if c[0] == "clear"] == [("clear", f"truck-{i}") for i in range(3)]
    assert all(c.dest is None and c.job is None for c in r.ctl.values())


def test_limiter_window():
    lim = Limiter(2)
    assert lim.room("a", 0) and (lim.hit("a", 0) or True)
    lim.hit("a", 1)
    assert not lim.room("a", 2)
    assert lim.room("b", 2)
    assert lim.room("a", 61)


def test_rate_gate_global_cap():
    g = RateGate(2, 3)
    assert [g.allow("a"), g.allow("a"), g.allow("a")] == [True, True, False]
    assert [g.allow("b"), g.allow("c")] == [True, False]


@pytest.fixture
def client(monkeypatch):
    r, fb = make_radio(flat_state())
    monkeypatch.setattr(server, "radio", r)
    monkeypatch.setattr(server, "SESSIONS", {})
    monkeypatch.setattr(server, "LIMITS", {
        "stt": RateGate(2, 100), "command": RateGate(3, 100), "login": RateGate(3, 100), "tts": Limiter(0)})

    async def fake_stt(data, filename, ctype):
        return "status report"

    monkeypatch.setattr(r, "transcribe", fake_stt)
    return TestClient(server.app)


GATED = [("get", "/api/radio/state"), ("get", "/api/radio/world"), ("post", "/api/radio/release"),
         ("get", "/api/radio/audio/0123456789abcdef0123.mp3")]


def test_pin_gates_everything(client, monkeypatch):
    monkeypatch.setattr(server, "RADIO_PIN", "4321")
    for m, url in GATED:
        assert getattr(client, m)(url).status_code == 401, url
    assert client.post("/api/radio/command", json={"text": "everyone stop"}).status_code == 401
    assert client.post("/api/radio/transcribe", files={"audio": ("a.webm", b"x" * 2000, "audio/webm")}).status_code == 401
    with pytest.raises(Exception):
        with client.websocket_connect("/ws/radio") as ws:
            ws.receive_json()
    h = client.get("/api/radio/health").json()
    assert h["auth_required"] and "brain_url" not in h and "last_error" not in h
    assert client.get("/").status_code == 200

    assert client.post("/api/radio/login", json={"pin": "0000"}).status_code == 401
    r = client.post("/api/radio/login", json={"pin": "4321"})
    assert r.status_code == 200
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie and "secure" not in cookie
    assert client.get("/api/radio/state").status_code == 200
    assert client.post("/api/radio/command", json={"text": "status report"}).status_code == 200
    with client.websocket_connect("/ws/radio") as ws:
        assert ws.receive_json()["type"] == "hello"


def test_cookie_secure_behind_https_proxy(client, monkeypatch):
    monkeypatch.setattr(server, "RADIO_PIN", "4321")
    r = client.post("/api/radio/login", json={"pin": "4321"}, headers={"x-forwarded-proto": "https"})
    assert "secure" in r.headers["set-cookie"].lower()


def test_login_rate_limited(client, monkeypatch):
    monkeypatch.setattr(server, "RADIO_PIN", "4321")
    codes = [client.post("/api/radio/login", json={"pin": f"{i:04d}"}).status_code for i in range(5)]
    assert codes == [401, 401, 401, 429, 429]
    assert client.post("/api/radio/login", json={"pin": "4321"}).status_code == 429


def test_command_and_stt_rate_limits(client):
    codes = [client.post("/api/radio/command", json={"text": "status report"}).status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]
    up = {"audio": ("a.webm", b"x" * 2000, "audio/webm")}
    codes = [client.post("/api/radio/transcribe", files=up).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


def test_upload_limits(client, monkeypatch):
    monkeypatch.setattr(server, "MAX_AUDIO_BYTES", 4096)
    assert client.post("/api/radio/transcribe", files={"audio": ("a.webm", b"x" * 5000, "audio/webm")}).status_code == 413
    assert client.post("/api/radio/transcribe", files={"audio": ("a.sh", b"x" * 2000, "text/x-shellscript")}).status_code == 415
    assert client.post("/api/radio/transcribe", files={"audio": ("../../x.webm", b"x" * 2000, "audio/webm")}).json()["text"] == "status report"
    assert client.post("/api/radio/command", json={"text": "x" * 400}).status_code == 413


@pytest.mark.parametrize("url", [
    "/../server.py", "/..%2fserver.py", "/radio/secrets/elevenlabs_api_key", "/secrets/elevenlabs_api_key",
    "/web/index.html", "/server.py", "/.env", "/api/radio/audio/..%2f..%2fsecrets%2felevenlabs_api_key",
    "/api/radio/audio/%2e%2e%2fserver.py", "/api/radio/audio/../../server.py", "/api/radio/audio/",
    "/docs", "/openapi.json", "/static/", "/cache/audio/",
])
def test_no_file_access_outside_page(client, url):
    r = client.get(url)
    assert r.status_code in (404, 405), url
    assert b"import" not in r.content and b"sk_" not in r.content


def test_ws_ignores_junk_and_caps_size(client):
    with client.websocket_connect("/ws/radio") as ws:
        assert ws.receive_json()["type"] == "hello"
        ws.receive_json()
        ws.send_text("[1,2]")
        ws.send_text('{"type":"ping","t":"<script>"}')
        while (m := ws.receive_json())["type"] != "pong":
            pass
        assert m["t"] is None
        ws.send_text("x" * 2000)
        with pytest.raises(Exception):
            while True:
                ws.receive_json()


def test_secret_file_env(tmp_path, monkeypatch):
    f = tmp_path / "k"
    f.write_text("abc\n")
    monkeypatch.delenv("X_TEST_KEY", raising=False)
    monkeypatch.setenv("X_TEST_KEY_FILE", str(f))
    assert server.secret("X_TEST_KEY") == "abc"
    monkeypatch.setenv("X_TEST_KEY", "env")
    assert server.secret("X_TEST_KEY") == "env"


def test_dotenv(tmp_path):
    f = tmp_path / ".env"
    f.write_text("# c\nELEVEN_LABS_API_KEY='q1'\nexport OTHER=2\n")
    assert server.dotenv(f) == {"ELEVEN_LABS_API_KEY": "q1", "OTHER": "2"}

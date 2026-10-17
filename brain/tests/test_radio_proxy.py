import asyncio
import json
import socket
import threading

import httpx
import pytest
import websockets
from fastapi import FastAPI
from fastapi.testclient import TestClient

from brain import radio_proxy


def fake_radio(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/api/radio/health":
        return httpx.Response(200, json={"mode": "mock", "cookie": request.headers.get("cookie")})
    if request.url.path == "/api/radio/command":
        return httpx.Response(200, json={"heard": json.loads(request.content)["text"]}, headers={"set-cookie": "radio_session=abc; Path=/"})
    if request.url.path == "/view":
        return httpx.Response(307, headers={"location": "/envgen/preview/index.html?live=1"})
    return httpx.Response(404, json={"detail": "not found"})


def app_with(base: str, transport=None) -> TestClient:
    app = FastAPI()
    radio_proxy.mount(app, base=base, transport=transport)
    return TestClient(app)


def test_http_get_forwards_cookies():
    c = app_with("http://radio.test", httpx.MockTransport(fake_radio))
    r = c.get("/radio/api/radio/health", headers={"cookie": "radio_session=xyz"})
    assert r.status_code == 200
    assert r.json() == {"mode": "mock", "cookie": "radio_session=xyz"}


def test_http_post_body_and_set_cookie():
    c = app_with("http://radio.test", httpx.MockTransport(fake_radio))
    r = c.post("/radio/api/radio/command", json={"text": "all stop"})
    assert r.json() == {"heard": "all stop"}
    assert "radio_session=abc" in r.headers["set-cookie"]


def test_redirects_stay_under_prefix():
    c = app_with("http://radio.test", httpx.MockTransport(fake_radio))
    r = c.get("/radio/view", follow_redirects=False)
    assert r.status_code == 307
    assert r.headers["location"] == "/radio/envgen/preview/index.html?live=1"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_unreachable_radio_is_502():
    c = app_with(f"http://127.0.0.1:{free_port()}")
    assert c.post("/radio/api/radio/release").status_code == 502


@pytest.fixture
def ws_upstream():
    port = free_port()
    loop = asyncio.new_event_loop()
    ready = threading.Event()
    stop = None

    async def handler(ws):
        await ws.send(json.dumps({"type": "hello", "cookie": ws.request.headers.get("cookie")}))
        async for msg in ws:
            await ws.send(json.dumps({"type": "echo", "msg": json.loads(msg)}))

    async def serve():
        nonlocal stop
        stop = asyncio.Event()
        async with websockets.serve(handler, "127.0.0.1", port):
            ready.set()
            await stop.wait()

    thread = threading.Thread(target=lambda: loop.run_until_complete(serve()), daemon=True)
    thread.start()
    ready.wait(5)
    yield f"http://127.0.0.1:{port}"
    loop.call_soon_threadsafe(stop.set)
    thread.join(5)


def test_websocket_passthrough(ws_upstream):
    c = app_with(ws_upstream)
    with c.websocket_connect("/radio/ws/radio", headers={"cookie": "radio_session=xyz"}) as ws:
        assert ws.receive_json() == {"type": "hello", "cookie": "radio_session=xyz"}
        ws.send_json({"type": "ping", "t": 1})
        assert ws.receive_json() == {"type": "echo", "msg": {"type": "ping", "t": 1}}


def test_websocket_unreachable_closes():
    c = app_with(f"http://127.0.0.1:{free_port()}")
    with pytest.raises(Exception):
        with c.websocket_connect("/radio/ws/radio") as ws:
            ws.receive_json()

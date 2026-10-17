"""Same-origin reverse proxy to the radio service: /radio/* -> RADIO_URL, including the /ws/radio socket."""

import asyncio
import logging
import os

import httpx
import websockets
from fastapi import FastAPI, Request, Response, WebSocket, WebSocketDisconnect

log = logging.getLogger(__name__)

DEFAULT_RADIO_URL = "http://127.0.0.1:8090"
TIMEOUT_S = 15.0
MAX_BODY = 25 * 1024 * 1024
FORWARD_HEADERS = ("content-type", "cookie", "accept", "x-requested-with")
RETURN_HEADERS = ("content-type", "set-cookie", "cache-control", "location")


def radio_url() -> str:
    return os.environ.get("RADIO_URL", DEFAULT_RADIO_URL).rstrip("/")


def mount(app: FastAPI, base: str | None = None, transport: httpx.AsyncBaseTransport | None = None) -> None:
    upstream = (base or radio_url()).rstrip("/")
    ws_upstream = "ws" + upstream[4:] if upstream.startswith("http") else upstream
    client = httpx.AsyncClient(base_url=upstream, timeout=TIMEOUT_S, transport=transport, follow_redirects=False)

    @app.websocket("/radio/ws/radio")
    async def radio_ws(ws: WebSocket):
        headers = {"cookie": ws.headers["cookie"]} if "cookie" in ws.headers else {}
        try:
            up = await websockets.connect(f"{ws_upstream}/ws/radio", additional_headers=headers, open_timeout=5, max_size=2**22)
        except Exception as e:
            log.info("radio websocket unavailable: %s", e)
            await ws.close(code=1013)
            return
        await ws.accept()

        async def down():
            async for msg in up:
                await (ws.send_bytes(msg) if isinstance(msg, bytes) else ws.send_text(msg))

        async def upward():
            while True:
                m = await ws.receive()
                if m["type"] == "websocket.disconnect":
                    return
                if m.get("text") is not None:
                    await up.send(m["text"])
                elif m.get("bytes") is not None:
                    await up.send(m["bytes"])

        tasks = [asyncio.create_task(down()), asyncio.create_task(upward())]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            for t in tasks:
                t.cancel()
            await up.close()
            try:
                await ws.close()
            except RuntimeError:
                pass

    @app.api_route("/radio/{path:path}", methods=["GET", "POST", "DELETE", "PUT"])
    async def radio_http(path: str, request: Request):
        body = await request.body()
        if len(body) > MAX_BODY:
            return Response("request too large", status_code=413)
        headers = {k: v for k, v in request.headers.items() if k.lower() in FORWARD_HEADERS}
        try:
            r = await client.request(request.method, f"/{path}", params=request.query_params, content=body, headers=headers)
        except httpx.HTTPError as e:
            log.info("radio unreachable: %s", e)
            return Response('{"detail":"radio unreachable"}', status_code=502, media_type="application/json")
        out = Response(r.content, status_code=r.status_code)
        for k in RETURN_HEADERS:
            for v in r.headers.get_list(k):
                if k == "location" and v.startswith("/"):
                    v = f"/radio{v}"
                out.headers.append(k, v)
        return out

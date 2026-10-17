# mining

Autonomous haul trucks in a simulated quarry, with every truck's front camera streamed live
to a website. One shared world; anyone with the page open watches the same trucks and can
send them somewhere.

```
Unity player (or fakesim) ── TCP :9000 ──▶ brain ── HTTP/WS :8080 ──▶ browser
  quarry, trucks, cameras    frames+poses   policy (RGB + depth)        camera grid
                          ◀── throttle/steer ─ JPEG fan-out             map: click to set goals
```

- **`brain/`** (Python, FastAPI): receives frames, runs the per-truck policy (drive to goal,
  avoid obstacles from depth), sends throttle/steer back, re-encodes frames as JPEG and fans
  them out to every viewer over WebSocket. Slow viewers skip frames rather than lag.
- **`web/`**: static page served by the brain. Camera grid plus a map; select a truck, click
  the map to give it a goal.
- **`unity/`**: C# scripts for the Unity scene, plus the wire protocol. See `unity/README.md`.
- **`brain/brain/fakesim.py`**: stand-in for Unity (3 trucks, rocks, crude renderer) speaking
  the same protocol, so everything else runs without a Unity build.

## Run on watson (GB10)

```bash
# on watson
cd ~/Documents/mining
docker compose --profile fake up -d --build   # brain + fake sim
docker compose up -d --build                  # brain only, waiting for Unity

# on your laptop
brev port-forward watson --port 8080:8080     # then open http://localhost:8080
brev port-forward watson --port 9000:9000     # only if running the Unity Editor locally
```

Ports are bound to `127.0.0.1` on watson; nothing is exposed publicly.

## Develop locally

```bash
cd brain
uv sync
uv run pytest
uv run brain                 # http://localhost:8080, sim port 9000
uv run fakesim               # in another terminal
```

## Status / next steps

- Working: protocol, policy, streaming and website end to end with the fake sim.
- Unity scripts are written but not yet compiled in a project.
- Not yet verified: a Unity Linux ARM64 player rendering headless on the GB10 (see `unity/README.md`).
- Streaming is per-camera JPEG over WebSocket (simple, works through the brev tunnel). Move to
  H.264 if bandwidth becomes a problem with more trucks.
- The policy is a placeholder: straight line to goal plus depth-based avoidance. No path
  planning, no RGB detector yet (`policy.act` already receives the RGB frame).

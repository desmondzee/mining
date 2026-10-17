# mining

Autonomous haul trucks in a simulated quarry, camera feeds streamed to a shared website. Parameters: [spec.md](spec.md). Rows with a `Source` are enforced by `brain/tests/test_spec.py`.

```
Unity player / fakesim ── TCP :9000 ──▶ brain ── HTTP/WS :8080 ──▶ browser
                       ◀── throttle/steer ─┘
```

| Path | Contents |
|---|---|
| `brain/` | FastAPI server: sim bridge, policy, frame fan-out |
| `brain/brain/fakesim.py` | Unity stand-in speaking the same protocol |
| `web/` | Camera grid and goal map |
| `unity/` | Unity project |
| `tools/fetch_rocketbox.py` | downloads Rocketbox people into the Unity project |
| `unity/Assets/MineSim/` | world importer, road graph, ground follow, cinematic look ([README](unity/Assets/MineSim/README.md)) |
| `data/worlds/` | generated worlds: `quarry` (drone scan), `default` (procedural) |
| `envgen/` | offline world generator and browser preview ([README](envgen/README.md)) |
| `assets/models/` | excavator model and truck recolour script |

## Run on watson

```bash
cd ~/Documents/mining
docker compose --profile fake up -d --build
docker compose up -d --build
```

```bash
ssh -N -L 8080:127.0.0.1:8080 watson
```

Open http://localhost:8080.

## Develop locally

```bash
cd brain
uv sync
uv run pytest
uv run brain
uv run fakesim
```

## Status

| Area | State |
|---|---|
| Brain, policy, streaming, website with fakesim | working, tested |
| Unity scripts | written, not compiled |
| Unity ARM64 player on GB10 | not verified |
| Policy | straight line to goal + depth avoidance, no path planning, no RGB detector |
| Video | per-camera JPEG over WebSocket |

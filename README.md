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
| `tools/fetch_planner.py` | downloads the learned planner checkpoint (Hugging Face `pi-hack-mining`) into `models/` and exports it |
| `sim2d/` | 2D driving sim, BC + PPO training and evaluation for the learned planner (meiru-cam) |
| `planner/`, `risk_monitor/`, `docs/planner.html` | grid A* benchmark, PoNR risk monitor, planner explainer (meiru-cam) |
| `tests/` | tests for `sim2d`, `risk_monitor` and brain/sim2d feature parity |
| `models/` | learned planner checkpoints (gitignored): `ppo.pt` → exported `ppo.npz` |
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

Public domain (Cloudflare Tunnel, origin `http://localhost:8080`, Access policy on allowed emails):

```bash
cp .env.example .env
docker compose --profile public up -d tunnel
```

## Develop locally

```bash
cd brain
uv sync
uv run pytest
uv run brain
uv run fakesim
```

## Learned planner

Trucks use the rule-based candidate chooser unless `models/ppo.npz` exists ([spec](spec.md#learned-planner)). Install a checkpoint:

```bash
uv run --with torch --with numpy tools/fetch_planner.py                                      # from Hugging Face pi-hack-mining
cd brain && uv run --with torch python -m brain.planning.export_scorer ../models/ppo.pt     # or a local ppo.pt
docker compose up -d brain                                                                   # mounts ./models read-only
PLANNER_CHECKPOINT=../models/ppo.npz uv run brain                                            # local
```

## Status

| Area | State |
|---|---|
| Brain, policy, streaming, website with fakesim | working, tested |
| Unity scripts | written, not compiled |
| Unity ARM64 player on GB10 | not verified |
| Policy | route planner + candidate paths; chooser = rules, or the learned scorer when `models/ppo.npz` exists |
| Video | per-camera JPEG over WebSocket |

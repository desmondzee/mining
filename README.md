<p align="center"><img src="assets/brand/r2-logo.svg" alt="R² · Rocky Robotics" width="520"></p>

# R² · Rocky Robotics: Mining autonomy

**🥇 1st place, [Physical AI Hack London](https://luma.com/physicalaihack) (Reasoning Project), 17 Oct 2026**

Retrofit autonomy for quarry haul trucks and excavators:

- **Sensor pod (concept):** cameras, lidar and compute that bolt onto the trucks a quarry already owns
- **Driving policy:** route planner with a rule-based or learned path scorer, using camera, depth and lidar
- **Voice-radio operations:** say "Truck one, go unload at excavator one" and the machines act and answer back
- **Sims from drone scans:** a drone scan of a real quarry becomes a simulator with haul roads, berms and loading points (Unity and NVIDIA Isaac Sim)

## Demo

| | |
|---|---|
| Final demo: sim, dashboard, trucks | [youtu.be/9VnViBXvA9Q](https://youtu.be/9VnViBXvA9Q) |
| Pitch + voice-radio demo (1 min, recorded during the hack) | [youtu.be/mcdJXTuMu2Y](https://youtu.be/mcdJXTuMu2Y) |
| Pitch deck | [presentation/deck.html](presentation/deck.html) (download and open in a browser) |

### Try it locally (no hardware)

```bash
cd brain && uv sync && uv run brain     # terminal 1: brain + dashboard on http://localhost:8080
cd brain && uv run fakesim              # terminal 2: simulated trucks
radio/run.sh                            # terminal 3: voice radio on http://localhost:8090, 3D quarry view on /view
```

The radio needs an ElevenLabs key for speech (`zsh radio/add_keys.sh`); an OpenAI key enables free-form commands. Without the brain running, the radio falls back to its own mock trucks. Full setup: [radio/README.md](radio/README.md). Running the real stack on the GPU server: see [Run on watson](#run-on-watson) below and [isaac/sim/README.md](isaac/sim/README.md).

## Team

| | Owned |
|---|---|
| **Desmond Zee** ([@desmondzee](https://github.com/desmondzee)) | Brain server and driving policy, sim protocol, dashboard and video streaming, Unity project, Isaac Sim port, pedestrians, radio hardening |
| **Meiru Zhang** ([@meiru-cam](https://github.com/meiru-cam)) | Learned planner (2D sim, BC + PPO), A* benchmark, point-of-no-return risk monitor |
| **Pushkar Ambastha** ([@Pushkar1853](https://github.com/Pushkar1853)) | Object detection on the truck cameras |
| **Vakhtang (Vato) Inasaridze** ([@termvato](https://github.com/termvato)) | Drone-scan → sim world generation and mine design spec, voice radio, 3D ops view, excavator and truck animation, branding, business case |

Third-party assets (CC-BY 4.0): quarry scan by 3Dnovator, Komatsu HD465 by drcrazzie, excavator by Dioramass design; see [assets/models/CREDITS.md](assets/models/CREDITS.md).

Side notes: market and unit economics in [reports/](reports/) (business case and sourced financials).

License: proprietary, all rights reserved; see [LICENSE](LICENSE).

---

## Architecture

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
| `tools/fetch_detector.py` | downloads the team YOLO detector (or `--placeholder` COCO YOLO11n) into `models/` as ONNX |
| `tools/fetch_planner.py` | downloads the learned planner checkpoint (Hugging Face `pi-hack-mining`) into `models/` and exports it |
| `sim2d/` | 2D driving sim, BC + PPO training and evaluation for the learned planner (meiru-cam) |
| `planner/`, `risk_monitor/`, `docs/planner.html` | grid A* benchmark, PoNR risk monitor, planner explainer (meiru-cam) |
| `tests/` | tests for `sim2d`, `risk_monitor` and brain/sim2d feature parity |
| `models/` | learned planner checkpoints and detector ONNX (gitignored): `ppo_v3.pt` → exported `ppo_v3.npz`, `yolo11n.onnx` |
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

## Detector

```bash
uv run tools/fetch_detector.py                 # team model from huggingface.co/pi-hack-mining
uv run tools/fetch_detector.py --placeholder   # COCO YOLO11n
scp models/*.onnx watson:~/Documents/mining/models/
DETECTOR_MODEL=/app/models/<file>.onnx docker compose up -d brain
```

Stats at `/api/detector`. Ground truth boxes: palette, "Show ground truth boxes".

## Develop locally

```bash
cd brain
uv sync
uv run pytest
uv run brain
uv run fakesim
```

## Learned planner

Trucks use the rule-based candidate chooser unless `models/ppo_v3.npz` exists ([spec](spec.md#learned-planner)). Install a checkpoint:

```bash
uv run --with torch --with numpy tools/fetch_planner.py                                      # from Hugging Face pi-hack-mining
cd brain && uv run --extra model python -m brain.planning.export_scorer ../models/ppo_v3.pt     # or a local ppo_v3.pt
docker compose up -d brain                                                                   # mounts ./models read-only
PLANNER_CKPT=../models/ppo_v3.npz uv run brain                                            # local
```

## Status

| Area | State |
|---|---|
| Brain, policy, streaming, website with fakesim | working, tested |
| Unity scripts | written, not compiled |
| Unity ARM64 player on GB10 | not verified |
| Policy | route planner + candidate paths; chooser = rules, or the learned scorer when `models/ppo_v3.npz` exists |
| Detector | ONNX YOLO on truck cameras (CPU), boxes in the HUD; placeholder COCO model until the team model is published |
| Video | per-camera JPEG over WebSocket |

# Demo day: Isaac go / no-go (15 minutes)

Pipeline under test: **isaac/sim** (team Isaac port, Isaac Sim 6.2 container, spec.md "Isaac sim") → brain
`127.0.0.1:9000` → web `:8080`. Our add-ons sit on top of isaac/sim and are optional: `isaac/excavators.py`
(animated excavators, `--excavators` once `isaac/patches/main_excavators.patch` is in) and `isaac/rl_env.py`.
KeepInBounds for the demo exists only as `isaac/patches/keep_in_bounds.patch` (`--keep-in-bounds`) until Des merges it.

Every step has a pass condition. If a step fails and its fix is not done by its deadline, go to **Fallback**.
Run everything on watson from the repo root, unless the step says laptop.

| t (min) | # | Check | Command | PASS when |
|---|---|---|---|---|
| T-60 | 0 | Image pulled, shaders warm | `docker pull nvcr.io/nvidia/isaac-sim:6.2.0` then `isaac/run_watson.sh smoke` once | the day before or an hour early: a first run compiles shaders for minutes |
| 0 | 1 | Host OK | `isaac/run_watson.sh check` | no `FAIL` rows. `WARN` for the brain is fine before step 2 |
| 1 | 2 | Brain container up, fakesim off | `docker compose up -d brain && docker compose stop fakesim; curl -s localhost:8080/api/state \| head -c 300` | JSON comes back. The brain keeps only one sim connection, so fakesim must be stopped |
| 2 | 3 | Isaac smoke test | `isaac/run_watson.sh smoke` (about 2–4 min warm) | `RESULT: PASS`. Look at `isaac/logs/smoke/truck-0.png` and `truck-0_after_drive.png`: terrain visible, not black, not mirrored (compare with the Unity / preview screenshot) |
| 6 | 4 | Sim up and bridge connected | `isaac/run_watson.sh up` | prints `CONNECTED: sim_connected=True trucks=['truck-0','truck-1','truck-2'] cameras=['overview','truck-0',...]` |
| 7 | 5 | Cameras streaming to the dashboard | laptop: `ssh -N -L 8080:127.0.0.1:8080 watson`, open http://localhost:8080 | overview + 3 truck tiles moving (H.264). Grey tiles: `docker logs mining-isaac-sim \| grep fps=` must show `connected=True` and `sent=` rising |
| 8 | 6 | Lidar not empty | `isaac/run_watson.sh status` | `lidar truck-N: >0 points` for all 3 (Warp raycasts). `0` means warp did not import: smoke row `warp` |
| 9 | 7 | Goal → truck moves | `curl -s -XPOST localhost:8080/api/trucks/truck-0/goal -H 'content-type: application/json' -d '{"site":"crusher"}'`, wait 10 s, `isaac/run_watson.sh status` | `truck-0` is in `moving=[...]` and moves on the dashboard map and its camera |
| 10 | 8 | Radio goal → truck moves | radio running (`radio/run.sh`) pointed at the brain; `curl -s localhost:8090/api/radio/health` shows `"mode": "brain"`. Then `curl -s -XPOST localhost:8090/api/radio/command -H 'content-type: application/json' -d '{"text":"truck two haul from the middle face to the crusher"}'` | truck-1 gets a goal (`goal_site` in `/api/state`) and moves. Mode `mock` means the radio does not see the brain (BRAIN_URL / tunnel) |
| 11 | 9 | Keep-in-bounds on | only with `isaac/patches/keep_in_bounds.patch` applied and `--keep-in-bounds` on the main.py command; smoke row `guard` says `KeepInBounds on` | a truck sent to a map corner stops or slides at the wall instead of leaving the world. Without the patch there is **no** guard; the brain planner keeps trucks on roads |
| 12 | 10 | Stable for 3 min | `docker logs --tail 5 mining-isaac-sim` (fps line every 5 s) | `fps` ≥ 12, `dropped` not climbing fast, no Traceback, `nvidia-smi` memory steady |
| 15 | | **GO / NO-GO** | | steps 1–7 PASS = GO with Isaac. Any of 3–7 failing = Fallback now |

## Quick fixes (2 minutes each, no more)

| Symptom | Fix |
|---|---|
| `SyntaxError` at `isaac/sim/main.py` line 91 | Isaac 5.x (Python 3.11). Use the 6.2 container (`ISAAC_TAG=6.2.0`, default in run_watson.sh) |
| `unresolvable CDI devices nvidia.com/gpu=all` | `sudo nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml`, or `ISAAC_MODE=native` (smoke already uses `--gpus all`) |
| `Permission denied` under `/cache` | `chmod 777 downloads/isaac/cache` (container uid 1234) |
| Black camera tiles at first | shaders still compiling, wait 2–3 min. Still black: `SIM_ARGS="--renderer RaytracedLighting" isaac/run_watson.sh up` after `down` |
| `brain not reachable` looping in sim log | start the brain (step 2). Port 9000 must be free of other sims |
| Low fps / dropped frames | `down`, then `SIM_ARGS="--fps 15 --no-views --fx off"`, then `up` |
| Sky sideways / wrong sun | cosmetic. `--dome-rotation` is documented but **not parsed** by `isaac/sim/main.py` (see the bug list); edit `isaac/sim/config.json` `dome.rotation` |

## Fallback (choose within 1 minute, then run it)

1. **fakesim** (brain's own sim, same protocol, real quarry roads): `isaac/run_watson.sh down && docker compose --profile fake up -d fakesim`.
   The dashboard and the radio work the same, but the camera tiles are synthetic.
2. **Unity player on a laptop** (macOS build `unity/Builds/mac/Quarry.app`, Tools > Mining > Build macOS Player):
   run `ssh -N -L 9000:127.0.0.1:9000 -L 8080:127.0.0.1:8080 watson`, stop the Isaac or fake sim on watson, then start
   the player with `-brain-host 127.0.0.1 -brain-port 9000`. You get RTX-free but real camera frames.
3. **Radio mock + aerial view** (works without watson): `RADIO_MOCK=1 bash radio/run.sh`, open
   `http://localhost:8090/view` (3D quarry preview that follows the radio's truck state) and the phone UI. Voice → goal →
   trucks move in the mock.
4. **Recorded proof:** show `isaac/logs/smoke/*.png` and the `report.json` from the morning run to prove Isaac rendered
   the scene on GB10.

Teardown after the demo: `isaac/run_watson.sh down`.

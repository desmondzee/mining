# Isaac sim

Isaac Sim 6.2 replacement for the Unity player. Speaks the brain's TCP sim protocol (`spec.md` "Sim protocol") on `127.0.0.1:9000`, like `brain/brain/fakesim.py`.

## Run

| What | Command |
|---|---|
| Tests (Mac) | `uv run --with numpy --with pillow --with pytest --with warp-lang --with usd-core pytest isaac/sim/tests -q` |
| Dry run (Mac, no Isaac) | `cd brain && PORT=8099 SIM_PORT=9099 uv run brain`, then `uv run --with numpy --with pillow --with warp-lang python isaac/sim/main.py --dry-run --brain 127.0.0.1:9099 --world data/worlds/quarry` |
| Container (watson) | `mkdir -p downloads/isaac/cache && chmod 777 downloads/isaac/cache && docker compose -f isaac/docker/compose.yml --profile isaac up -d --build` |
| Logs | `docker logs -f mining-isaac-sim` |
| Stop | `docker compose -f isaac/docker/compose.yml --profile isaac down` |
| Other image tag | `ISAAC_TAG=6.2.0 docker compose -f isaac/docker/compose.yml --profile isaac up -d --build` |
| Save frames | add `--save-dir /cache/frames --save-every 15` to the command |

Run one Isaac container at a time on watson, and stop `mining-fakesim-1` first: the brain keeps only the latest sim connection.

## Options

| Flag | Default | Meaning |
|---|---|---|
| `--brain` | `127.0.0.1:9000` | brain sim socket |
| `--world` | `data/worlds/quarry` | world directory (`world.json` + GLBs) |
| `--dry-run` | off | no Isaac imports, black JPEG frames |
| `--raycast` | `auto` | Warp depth/lidar/ground raycasts (`auto` = if `warp` imports); `off` uses the heightmap for ground and sends no depth or lidar points |
| `--lidar` | `raycast` | lidar method |
| `--cache` | `downloads/isaac/cache` | generated USD files |
| `--renderer` | `RealTimePathTracing` | RTX mode (`PathTracing` for offline quality) |
| `--fps` | 15 | frame rate |
| `--dome-rotation` | `0 0 0` | sky dome rotateXYZ (deg) |
| `--duration` | 0 (forever) | exit after N seconds |

## Files

| File | Isaac needed | Role |
|---|---|---|
| `world.py` | no | `world.json` → dataclasses, crossing paths, truck spawn layout, excavator obstacles, hello payload |
| `protocol.py` | no | framing and encoders (byte-identical to `brain.protocol`) |
| `kinematics.py` | no | bicycle model, command timeout, ground follow + tilt (Unity `MineGroundFollow`) |
| `pedestrians.py` | no | ping-pong walkers, seeded speeds |
| `sensors.py` | no | camera/depth/lidar ray patterns, mounts, `cam` pose field |
| `raycast.py` | Warp | collider + surround meshes, truck/excavator boxes, pedestrian cylinders |
| `glb.py` | no | GLB reader (node transforms, normals, UVs, colours, textures) |
| `usdbuild.py` | pxr | GLB → Z-up USD with axis swap baked in, cached |
| `sim.py` | no | sim state shared by dry run and Isaac |
| `bridge.py` | no | TCP client thread, reconnect, bounded outbox |
| `scene.py` | Isaac | stage, sky, sun, scan, trucks, excavators, people, cameras, annotators |
| `main.py` | Isaac unless `--dry-run` | fixed-step loop, 15 Hz frames, 10 Hz lidar/actors |

## Status

| Feature | State |
|---|---|
| Protocol (hello with roads/sites/junctions, frame JPEG + depth + `cam`, lidar int16cm, actors, action) | done, parity test vs `brain.protocol` |
| Trucks: bicycle model, timeout, terrain height + tilt | done |
| Spawn: slots, road heading, spread with slope search | done |
| Pedestrians: 4 road crossings × 3, ping-pong, ground follow, hit by depth/lidar | done (capsule visuals) |
| Depth 64 × 36 and lidar 240 × 30 | done, Warp raycasts |
| RTX RGB cameras, overview orthographic | done |
| Excavators: static models + boxes + hello obstacles | done |
| Animated people | not yet (orange capsules) |
| RTX lidar | not yet (raycast pattern matches Unity exactly) |

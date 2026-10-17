# Isaac sim

Isaac Sim 6.2 replacement for the Unity player. Speaks the brain's TCP sim protocol (`spec.md` "Sim protocol") like `brain/brain/fakesim.py`.

## Run

| What | Command |
|---|---|
| Tests (Mac) | `uv run --with numpy --with pillow --with pytest --with warp-lang --with usd-core pytest isaac/sim/tests -q` |
| Dry run (Mac, no Isaac) | `cd brain && PORT=8099 SIM_PORT=9099 uv run brain`, then `uv run --with numpy --with pillow --with warp-lang python isaac/sim/main.py --dry-run --brain 127.0.0.1:9099 --world data/worlds/quarry` |
| Container (watson) | `mkdir -p downloads/isaac/cache && chmod 777 downloads/isaac/cache && docker compose -f isaac/docker/compose.yml --profile isaac up -d --build` |
| Logs | `docker logs -f mining-isaac-sim` |
| Stop | `docker compose -f isaac/docker/compose.yml --profile isaac down` |
| Other image tag | `ISAAC_TAG=6.2.0 docker compose -f isaac/docker/compose.yml --profile isaac up -d --build` |
| Test against a private brain | `docker run -d --name mining-isaac-testbrain --network host -e HOST=127.0.0.1 -e PORT=8180 -e SIM_PORT=9180 mining-brain`, sim `--brain 127.0.0.1:9180` |
| Look development | `isaac/sim/lookdev.py --config C --variants V.json --save-dir D` renders every camera per lighting variant |

The brain keeps only the latest sim connection: stop `mining-fakesim-1` before pointing the sim at 9000. Run one Isaac container at a time.

## Options

| Flag | Default | Meaning |
|---|---|---|
| `--brain` | `127.0.0.1:9000` | brain sim socket |
| `--world` | `data/worlds/quarry` | world directory (`world.json` + GLBs) |
| `--config` | `isaac/sim/config.json` | fps, renderer, lighting, RTX settings, extra views |
| `--fps` | config `fps` (15) | camera frame rate; lidar and actors stay 10 Hz |
| `--renderer` | config (`RealTimePathTracing`) | RTX mode |
| `--fx` | `off` | `isaac.fx.TruckFx` Flow dust preset (`off`, `low`, `high`); Flow roughly halves fps with four cameras |
| `--no-views` | off | drop the extra views |
| `--dry-run` | off | no Isaac imports, black JPEG frames |
| `--raycast` | `auto` | Warp depth/lidar/ground raycasts (`auto` = if `warp` imports); `off` uses the heightmap and sends no depth or lidar points |
| `--cache` | `downloads/isaac/cache` | generated USD files |
| `--save-dir`, `--save-every` | off | write every Nth JPEG of every camera |
| `--duration` | 0 (forever) | exit after N seconds |

## Config (`config.json`)

| Key | Meaning |
|---|---|
| `fps` | camera rate (default 15; fresh frames, measured 26 fps achievable for 4 cameras with other Isaac containers running) |
| `tiled` | overview + truck cameras in one 2 × 2 tiled render product (off: on 6.2 the moving truck cameras render with a wrong view in tiles) |
| `step` | `step_nowait` (`rep.orchestrator.step(wait_for_render=False)`, frames lag the pose by one tick; the sim sends the previous tick's pose/cam/depth with them), `step`, `step_dt0`, `update` (stale frames) |
| `rt_subframes` | RTX subframes per step |
| `lidar_frame` | `sensor` (default, tilts with the truck like Unity `tiltSensors`) or `level` (sensor origin, gravity-aligned axes) |
| `look` | preset passed to `isaac.look.apply_look(stage, preset)` when that package exists (then `sun`/`dome`/`scan` are skipped) |
| `renderer` | `RealTimePathTracing`, `RaytracedLighting`, `PathTracing` |
| `noise_sigma` | Gaussian RGB noise (8-bit units), 0 = off |
| `sun` | distant light: `intensity`, `elevation`/`azimuth` (deg, azimuth = world yaw toward the sun), `angle`, `color` |
| `dome` | `sky_pano.jpg` latlong dome: `intensity`, `rotation` (rotateXYZ deg), `texture` |
| `scan` | scan material: `tint` (multiplies the texture), `roughness`, `specular` |
| `surround` | `tint` baked into the surround vertex colours |
| `rtx` | carb settings applied at start (motion blur, DOF, AA/DLSS, tonemapper, exposure) |
| `views` | extra cameras sent as `frame` with `truck: ""` and a `cam` pose (default none) |

| View field | Modes | Meaning |
|---|---|---|
| `name`, `width`, `height`, `hfov` | all | camera name in hello/frames, resolution, horizontal FOV |
| `mode` | | `chase`, `orbit`, `fixed` |
| `truck`, `distance`, `elevation`, `target_height`, `smooth` | chase | follows a truck from behind and above, smoothed |
| `center`, `radius`, `period`, `position[1]` | orbit | circles `center` (x, z) at `position[1]` m above its ground |
| `position`, `look_at` | fixed | world coordinates |

## Coordinates

| Item | Convention |
|---|---|
| Sim state, protocol, hooks | world.json frame: x east, y up, z north, metres; yaw deg, 0 = +z, clockwise from above |
| USD stage | Z up, metres per unit 1 |
| World → stage | `(X, Y, Z) = (x, z, y)` (`usdbuild.to_isaac`); a mirror, so baked meshes reverse triangle winding |
| Rotations | `R_stage = S R_world S`, `S` = swap of axes 1 and 2 (`usdbuild.isaac_matrix`) |
| Truck prim | `/World/Trucks/truck_N`, transform = position + yaw + terrain tilt; body local forward +Y, up +Z, origin at bottom centre |
| Cameras | USD camera looks down local -Z: columns (right, up, -forward) of the world frame, swapped (`usdbuild.camera_matrix`) |
| GLB assets | `collider.glb`, `surround.glb` are world frame; `visual.glb` is node transforms then `scan_transform.matrix`; truck/excavator GLBs are X-flipped (Unity import), scaled, origin bottom centre |

## Live cut-over (watson)

| Step | Command |
|---|---|
| Stop fake sim | `docker stop mining-fakesim-1` |
| Start Isaac on 9000 | `cd ~/Documents/mining && mkdir -p downloads/isaac/cache && chmod 777 downloads/isaac/cache && docker compose -f isaac/docker/compose.yml --profile isaac up -d --build` |
| Check | `docker logs -f mining-isaac-sim` until `connected to brain at 127.0.0.1:9000`; `curl -s 127.0.0.1:8080/api/state` shows `truck_model: komatsu_hd465` |
| Roll back | `docker compose -f isaac/docker/compose.yml --profile isaac down && docker start mining-fakesim-1` |

## Hooks

| Hook | Contract |
|---|---|
| Look | config `look` = preset name or `{preset, ...overrides}` (`false` = built-in sun/dome/scan): `apply_look(stage, preset, world_dir, cache/look)` after trucks and excavators exist; config `rtx` is applied after it |
| Excavators | world.json equipment (radio controlled: `RADIO_URL` polls `/api/radio/excavators` for `digging`/`holding` and heading) plus config `extra_excavators` (always digging); 8 s teammate cycle; dump side = lower-terrain side, or toward a truck stopped within 16 m; `Sim.excavator_state()` |
| Cab cameras | config `excavator_cameras` (`true` or id list): 640×360 sensor camera per excavator in the cab, riding the house swing, sent as `frame` with `cam`, listed in hello `cameras` |
| Bucket material | config `material` `{enabled, lift_deg, model}`: `isaac.material.BucketMaterial` fed the rig's bucket matrix and phase, trucks under an excavator, `aim_points()` steer the dump swing; truck `frame` carries `load` 0..1 |
| Props | config `props` `{skip, no_raycast}` (kinds): `isaac/props/load.py add_props` from `<world>/props.json` and `isaac/assets/props`; `prop_triangles` become a Warp raycast mesh (depth/lidar), entries with `obstacle {x,z,r}` go into hello `obstacles` |
| Truck FX | `--fx low|high`: `TruckFx(stage, truck_ids, preset, load_or_bake(world, cache), **config["fx"])`, `fx.update(dt, [(id, isaac_pos, yaw, speed, None)], ground.height_at)` per frame |
| Overview | config `overview_res` (default 1280×720); brain H.264 bitrate scales with pixels |

## Files

| File | Isaac needed | Role |
|---|---|---|
| `world.py` | no | `world.json` → dataclasses, crossing paths, truck spawn layout, excavator and prop obstacles, hello payload |
| `protocol.py` | no | framing and encoders (byte-identical to `brain.protocol`) |
| `kinematics.py` | no | bicycle model, command timeout, ground follow + tilt (Unity `MineGroundFollow`) |
| `pedestrians.py` | no | ping-pong walkers, seeded speeds |
| `sensors.py` | no | camera/depth/lidar ray patterns, mounts, `cam` pose field |
| `views.py` | no | chase/orbit/fixed extra camera poses |
| `raycast.py` | Warp | collider, surround and props meshes, truck/excavator boxes, pedestrian cylinders |
| `glb.py` | no | GLB reader (node transforms, normals, UVs, colours, textures) |
| `usdbuild.py` | pxr | GLB → Z-up USD with the axis swap baked in, cached |
| `sim.py` | no | sim state shared by dry run and Isaac |
| `bridge.py` | no | TCP client thread, reconnect, bounded outbox |
| `scene.py` | Isaac | stage, sky, sun, scan, trucks, excavators, people, cameras, annotators, hooks |
| `main.py` | Isaac unless `--dry-run` | real-time loop, frames at `fps`, 10 Hz lidar/actors, JPEG + send on a worker thread |
| `lookdev.py` | Isaac | renders lighting variants |

## Status

| Feature | State |
|---|---|
| Protocol (hello with roads/sites/junctions, frame JPEG + depth + `cam`, lidar int16cm, actors, action) | done, parity test vs `brain.protocol` |
| Trucks: Komatsu model, bicycle model, timeout, terrain height + tilt | done |
| Spawn: slots, road heading, spread with slope search | done |
| Excavators: model from `excavator.glb`, `equipment` pose, box for raycasts, hello obstacles | done |
| Pedestrians: 4 road crossings × 3, ping-pong, ground follow, hit by depth/lidar | done, capsule visuals |
| Depth 64 × 36 and lidar 240 × 30 | done, Warp raycasts |
| RTX RGB truck cameras, orthographic overview, extra views | done |
| Animated people | not yet; Rocketbox FBX through `omni.kit.asset_converter` gives an unskinned T-pose mesh in cm |
| RTX lidar | not used (raycast pattern matches Unity exactly) |

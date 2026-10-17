# Spec

## Platform

| Item | Value |
|---|---|
| Host | watson (Brev), NVIDIA GB10, aarch64 |
| NVIDIA driver | 580.178.04 |
| Repo on host | `~/Documents/mining` |
| Simulator | Unity 6.3 LTS (6000.3.26f1), URP |
| Unity player target | Linux ARM64, IL2CPP, `com.unity.sdk.linux-arm64` |
| Unity editor | macOS (no ARM64 Linux editor) |
| Brain | Python 3.12, FastAPI, uv |
| Containers | Docker Compose, project `mining` |
| Unity project | `unity/` |
| Packages | URP 17.3.0, glTFast 6.15.0 |
| Scene | `Assets/Scenes/Quarry.unity`, built by Tools > Mining > Build Quarry Scene |
| Environment | flat ground plane and box rocks with colliders |
| Ground colour | RGB (0.55, 0.47, 0.37), URP Lit |
| Rock colour | RGB (0.36, 0.34, 0.33), URP Lit |

## Bandwidth

| Link | Measured | Load |
|---|---|---|
| Unity → brain, same host | localhost | 4 × ~10 KB JPEG × 15 fps + 3 × ~27 KB lidar × 10 Hz ≈ 1.4 MB/s |
| Mac → watson (SSH via Brev) | 0.35–0.45 MB/s | too low for Unity on Mac with brain on watson; run brain locally |
| watson → viewer (SSH via Brev) | 1.4–1.9 MB/s | H.264: ≤0.3 MB/s video + ~0.04 MB/s lidar per viewer |

| Per viewer, 4 cameras 640×360 15 fps + lidar 10 Hz (5 viewers, local) | Before (JPEG, raw lidar) | After (H.264, zlib lidar) |
|---|---|---|
| Photographic frames (quarry pano crops, ~22 KB JPEG) | 1346 KB/s video | 294 KB/s video |
| Unity quarry trucks (35–41 KB JPEG, measured on watson) | ~2300 KB/s video | ≤275 KB/s video (bitrate cap) |
| Fakesim flat world | 459 KB/s video | 60 KB/s video |
| Lidar, selected truck (fakesim scan) | 34.5 KB/scan, 345 KB/s | 3.8 KB/scan, 38 KB/s |
| H.264 keyframe size | | 15–19 KB |
| Brain CPU (Mac M-series, 4 encodes + 4 JPEG decodes + policy) | 39% of a core (JPEG fallback, encoders still running) | 31% of a core |
| x264 encode time per 640×360 frame (M-series) | | 1.8 ms |

## Network

| Item | Value | Source |
|---|---|---|
| Website + API port | 8080 | |
| Sim TCP port | 9000 | `unity/Assets/Scripts/BrainClient.cs:port` |
| Host binding | 127.0.0.1 | |
| Remote access | `ssh -N -L 8080:127.0.0.1:8080 watson` | |
| Public access | Cloudflare Tunnel, compose profile `public`, `cloudflared` 2026.9.3, token `TUNNEL_TOKEN` in gitignored `.env` | |
| Tunnel origin | `http://localhost:8080` (public hostname set in Cloudflare dashboard) | |
| Access control | Cloudflare Access policy: allowed emails (one-time PIN and/or Google login) | |
| Editor → remote brain | `ssh -N -L 9000:127.0.0.1:9000 watson` | |

## World

| Item | Value | Source |
|---|---|---|
| Units | metres, seconds, degrees | |
| Ground plane | x / z, y up | |
| Yaw | `eulerAngles.y`, 0 = +z, clockwise positive | |
| Default bounds | [-100, -100, 100, 100] (xmin, zmin, xmax, zmax) | |
| Trucks | 3 | |
| Truck IDs | `truck-0` … `truck-N` | |
| Overview camera ID | `overview` | |

## Cameras

| Item | Value | Source |
|---|---|---|
| Truck camera width | 640 | `unity/Assets/Scripts/SensorCamera.cs:width` |
| Truck camera height | 360 | `unity/Assets/Scripts/SensorCamera.cs:height` |
| Frame rate | 15 | `unity/Assets/Scripts/SensorCamera.cs:fps` |
| Horizontal FOV | 90 | `unity/Assets/Scripts/SensorCamera.cs:horizontalFov` |
| Readback format | RGBA32, bottom-up (`flip: true`) | |
| Transport encoding | JPEG in Unity, passed through to viewers unchanged | |
| Unity JPEG quality | 80 | `unity/Assets/Scripts/SensorCamera.cs:jpegQuality` |
| Depth width | 64 | `unity/Assets/Scripts/SensorCamera.cs:depthWidth` |
| Depth height | 36 | `unity/Assets/Scripts/SensorCamera.cs:depthHeight` |
| Depth range (m) | 100 | `unity/Assets/Scripts/SensorCamera.cs:depthRange` |
| Depth format | float32 metres, perpendicular distance, row 0 = top, miss = range | |
| Depth method | `RaycastCommand` grid against colliders | |

## Lidar

| Item | Value | Source |
|---|---|---|
| Method | `RaycastCommand` batch against colliders | |
| Range (m) | 100 | `unity/Assets/Scripts/LidarSensor.cs:range` |
| Horizontal FOV (deg) | 120 | `unity/Assets/Scripts/LidarSensor.cs:horizontalFov` |
| Vertical min (deg) | -20 | `unity/Assets/Scripts/LidarSensor.cs:verticalMin` |
| Vertical max (deg) | 10 | `unity/Assets/Scripts/LidarSensor.cs:verticalMax` |
| Columns | 240 | `unity/Assets/Scripts/LidarSensor.cs:columns` |
| Channels | 30 | `unity/Assets/Scripts/LidarSensor.cs:channels` |
| Rate (Hz) | 10 | `unity/Assets/Scripts/LidarSensor.cs:rate` |
| Resolution | 0.5° H × 1° V | |
| Beam angles | cell centres: az = -h_fov/2 + (j + 0.5)·h_fov/columns, el = v_min + (i + 0.5)·(v_max - v_min)/channels | |
| Max points per scan | 7200 | |
| Point format (sim → brain, brain → viewer) | int16 x, y, z in centimetres, little-endian, 6 bytes per point, header `format: int16cm` | |
| Point format (HTTP API) | float32 x, y, z metres, 12 bytes per point | |
| Precision / limit | 1 cm, ±327.67 m | |
| Frame | sensor-local, Unity axes: x right, y up, z forward, metres | |
| Mount | `LidarSensor` transform, unit scale | |
| Misses | dropped | |
| Noise | none | |
| Fake sim mount height (m) | 3 | `brain/brain/fakesim.py:LIDAR_HEIGHT` |
| Fake sim range (m) | 100 | `brain/brain/fakesim.py:LIDAR_RANGE` |
| Fake sim horizontal FOV (deg) | 120 | `brain/brain/fakesim.py:LIDAR_HFOV` |
| Fake sim vertical min (deg) | -20 | `brain/brain/fakesim.py:LIDAR_VMIN` |
| Fake sim vertical max (deg) | 10 | `brain/brain/fakesim.py:LIDAR_VMAX` |
| Fake sim columns | 240 | `brain/brain/fakesim.py:LIDAR_COLS` |
| Fake sim channels | 30 | `brain/brain/fakesim.py:LIDAR_CHANNELS` |
| Fake sim rate (Hz) | 10 | `brain/brain/fakesim.py:LIDAR_HZ` |

## Truck models

| Item | Value | Source |
|---|---|---|
| Folder | `unity/Assets/Models/Trucks/` | |
| Komatsu HD465-7EO | `komatsu_hd465.glb`, 27k triangles, CC-BY-4.0 | |
| Komatsu length (m) | 9.35 | |
| Komatsu import rotation (deg) | (-90, 0, 0) | |
| CAT 785C | `cat_785c.glb`, 396k triangles, licence not documented | |
| CAT length (m) | 11.02 | |
| CAT import rotation (deg) | (-90, -90, 0) | |
| Scaling | uniform, renderer bounds length along z = real length | |
| Origin | bottom centre of renderer bounds | |
| Collider | `BoxCollider` = renderer bounds | |
| Body | kinematic `Rigidbody` | |
| Trucks in scene | 3 | `unity/Assets/Editor/QuarrySceneBuilder.cs:TruckCount` |
| Start positions | (-15, 0, -70), (0, 0, -70), (15, 0, -70), yaw 0 | |
| Sensor offset ahead of collider (m) | 0.2 | `unity/Assets/Editor/QuarrySceneBuilder.cs:SensorOffset` |
| Camera height (fraction of truck height) | 0.8 | `unity/Assets/Editor/QuarrySceneBuilder.cs:CameraHeightFraction` |
| Lidar height (fraction of truck height) | 0.6 | `unity/Assets/Editor/QuarrySceneBuilder.cs:LidarHeightFraction` |

## Pedestrians

| Item | Value | Source |
|---|---|---|
| Control | fixed waypoint paths, no policy | |
| Script | `PedestrianWalker` | |
| Count | 12 | |
| Pedestrians per path | 3 | `unity/Assets/Editor/QuarrySceneBuilder.cs:PedestriansPerPath` |
| Start positions | path fractions 0, 1/3, 2/3 | |
| Min walking speed (m/s) | 1.2 | `unity/Assets/Editor/QuarrySceneBuilder.cs:MinWalkSpeed` |
| Max walking speed (m/s) | 1.6 | `unity/Assets/Editor/QuarrySceneBuilder.cs:MaxWalkSpeed` |
| Speed assignment | seeded uniform per pedestrian | |
| Walk clip natural speed (m/s) | 1.4 | `unity/Assets/Scripts/PedestrianWalker.cs:clipSpeed` |
| Animation speed | walking speed / clip speed | |
| Turn rate (deg/s) | 360 | `unity/Assets/Scripts/PedestrianWalker.cs:turnRate` |
| Path modes | loop (3+ waypoints), ping-pong (2 waypoints) | |
| Height (m) | 1.8 | `unity/Assets/Editor/QuarrySceneBuilder.cs:PedestrianHeight` |
| Collider radius (m) | 0.3 | `unity/Assets/Editor/QuarrySceneBuilder.cs:PedestrianRadius` |
| Body | kinematic `Rigidbody`, `CapsuleCollider` | |
| Models | Microsoft Rocketbox, MIT, fetched by `tools/fetch_rocketbox.py` into `unity/Assets/Models/People/` (gitignored) | |
| Avatars | Construction_Male_01–08, Construction_Female_01, Male_Adult_01, Female_Adult_01, Security_Male_01 | |
| Assignment | avatars sorted by path, round-robin over pedestrians | |
| Texture format | TGA RLE | |
| Texture max size (px) | 1024 | `tools/fetch_rocketbox.py:TEXTURE_SIZE` |
| Rig | Humanoid, avatar per model | |
| Walk clips | `m_walk_neutral_01`, `f_walk_neutral_01` (Rocketbox `motextr_xy`), loop, root motion off | |
| LOD levels | hipoly, midpoly, lowpoly, ultralowpoly | |
| LOD screen heights | 0.25, 0.08, 0.03, 0.005; last present level culls at 0.005 | |
| LOD levels imported | Rocketbox FBX imports one renderer (hipoly) | |
| Materials | URP Lit, base colour white, `opacity` materials alpha-clipped at 0.5, double-sided | |
| Fallback | capsule placeholder when `Models/People` is empty | |
| Paths | 4 | |
| Rock clearance from paths (m) | 4 | `unity/Assets/Editor/QuarrySceneBuilder.cs:PathClearance` |
| Position report rate (Hz) | 10 | `unity/Assets/Scripts/BrainClient.cs:actorsRate` |
| Pedestrian IDs | Unity: `Pedestrian_NN`; fake sim: `pedestrian-NN` | |

| Path | Waypoints (x, z) | Mode |
|---|---|---|
| 0 | (-30, -40), (30, -40) | ping-pong |
| 1 | (-10, -10), (10, -10), (10, 10), (-10, 10) | loop |
| 2 | (-50, 20), (-10, 60) | ping-pong |
| 3 | (40, -20), (40, 40) | ping-pong |

## Quarry scene

| Item | Value | Source |
|---|---|---|
| Build | Tools > Mining > Build Quarry Scene (Quarry World), `QuarrySceneBuilder.BuildQuarry` | |
| World | `data/worlds/quarry/` (scan visual, 300k-triangle `MeshCollider`, surround collider) | |
| Bounds | [-217.5, -217.5, 217.5, 217.5] | |
| Truck model | Komatsu HD465 | |
| Obstacles | 3 excavators from `equipment`, one circle per excavator (collider bounds) | |
| Debug layer | `MineDebug` (8): site markers, junctions, road lines, labels | `unity/ProjectSettings/TagManager.asset` |
| Debug layer seen by | editor only; excluded from truck and overview cameras, depth and lidar masks | |
| Truck spawn | `world.json` slots, heading along the road leaving the spawn site | |
| Heading look-ahead along road (m) | 20 | `unity/Assets/Editor/QuarrySceneBuilder.cs:SpawnLookAhead` |
| Minimum gap between parked trucks (m) | 2 | `unity/Assets/Editor/QuarrySceneBuilder.cs:SpawnGap` |
| Slot-line shift search (m) | 10 | `unity/Assets/Editor/QuarrySceneBuilder.cs:SpawnSearch` |
| Slot-line shift step (m) | 0.5 | `unity/Assets/Editor/QuarrySceneBuilder.cs:SpawnSearchStep` |
| Slot-line shift choice | least maximum ground slope under the trucks | |
| Ground following | `MineGroundFollow`, height from the scan collider, model and sensors tilt with the ground | |
| Pedestrian ground probe height (m) | 30 | `unity/Assets/Scripts/PedestrianWalker.cs:probeHeight` |
| Crossing paths | 4 | `unity/Assets/Editor/QuarrySceneBuilder.cs:CrossingCount` |
| Crossing length beyond road width (m) | 12 | `unity/Assets/Editor/QuarrySceneBuilder.cs:CrossingMargin` |
| Crossing roads | first 4 roads by ordinal id | |
| Crossing geometry | 2 points at the x/z arc-length midpoint, ± (width_m + 12) / 2 along the right-hand normal (t.z, -t.x) of the segment containing it; point 0 = left | |
| Crossing mode | ping-pong | |

| Path | Road | Waypoints (x, z) |
|---|---|---|
| 0 | haul_1 | (-27.41, -4.98), (-39.46, 19.18) |
| 1 | haul_2 | (-131.61, 16.83), (-126.85, 43.41) |
| 2 | haul_3 | (31.33, 57.19), (31.33, 84.19) |
| 3 | haul_6 | (-5.30, -13.04), (11.04, -34.53) |

| Hello field | Count |
|---|---|
| roads | 8 |
| sites | 7 |
| junctions | 2 |
| obstacles | 3 |
| paths | 4 |
| header bytes | 15188 |

| Measurement (macOS player, M-series Mac, local brain) | Value |
|---|---|
| Unity frame time (ms) | 8.7 |
| Camera rate per camera, overview and truck-0..2 (fps) | 13.2–14.7 |
| Truck JPEG size (KB) | 35–41 |
| Overview JPEG size (KB) | 29 |
| Player size (MB) | 982 |

## Fake sim

| Item | Value | Source |
|---|---|---|
| Frame rate | 15 | `brain/brain/fakesim.py:FPS` |
| Horizontal FOV | 90 | `brain/brain/fakesim.py:FOV_DEG` |
| Depth range (m) | 100 | `brain/brain/fakesim.py:MAX_RANGE` |
| Truck camera | 640 × 360 RGB + 640 × 360 depth | |
| Overview size (px) | 480 | `brain/brain/fakesim.py:OVERVIEW_PX` |
| Camera height (m) | 3.6 | `brain/brain/fakesim.py:CAM_HEIGHT` |
| Rendering | per-column raycast, far-to-near layers, ground plane depth | |
| Obstacles | rocks (cylinders, height 1.5 r), trucks, pedestrians, 15 m walls | |
| Pedestrians | wander rules of "Pedestrian wander", `brain/brain/pedestrians.py` (byte-identical copy of `isaac/sim/pedestrians.py`), IDs `pedestrian-NN` | |
| Pedestrian radius (m) | 0.3 | `brain/brain/pedestrians.py:PEDESTRIAN_RADIUS` |
| Pedestrian height (m) | 1.8 | `brain/brain/pedestrians.py:PEDESTRIAN_HEIGHT` |
| Flat world roads | the 4 flat pedestrian polylines as roads, one area each | |
| Flat world road width (m) | 10 | `brain/brain/fakesim.py:FLAT_ROAD_WIDTH` |
| Pedestrian colour | RGB (255, 120, 20) | |
| Actors rate (Hz) | 10 | `brain/brain/fakesim.py:ACTORS_HZ` |
| Modes | flat test world (default), world mode `--world <dir>` | |

## Fake sim world

| Item | Value | Source |
|---|---|---|
| Option | `fakesim --world ../data/worlds/quarry`; empty = flat world | |
| Input | `<dir>/world.json` | |
| Bounds | `world.json` `bounds`, walls at bounds | |
| Ground | flat, y = 0 for cameras and collisions | |
| Lidar terrain | point y offset by `navgrid.json` ground at the hit minus ground under the truck (planner ground filter) | |
| Roads | `roads[]` with `points` reduced to x, z | |
| Sites | `sites[]` `{id, type, name}`, x, z from `pos` | |
| Junctions | `junctions[]`, x, z from `pos` | |
| Trucks | `trucks[]` by `slot`, `spawn`, yaw = `heading_deg` | |
| Obstacles | `equipment[]` as cylinders, no rocks | |
| Equipment radius (m) | 5 | `brain/brain/fakesim.py:EQUIPMENT_RADIUS` |
| Equipment height (m) | 5 | `brain/brain/fakesim.py:EQUIPMENT_HEIGHT` |
| Overview | roads drawn at `width_m` | |
| Docker | `docker compose --profile fake up` runs `/app/data/worlds/quarry`; `FAKESIM_WORLD=` for the flat world | |
| Data | `./data` mounted read-only at `/app/data` (brain and fakesim) | |

## Pedestrian wander

Shared by the Isaac sim and the fake sim (`isaac/sim/pedestrians.py`, copied to `brain/brain/pedestrians.py`; a test keeps them identical).

| Item | Value | Source |
|---|---|---|
| People | 12 | `isaac/sim/pedestrians.py:PEOPLE` |
| Wander areas | 6 | `isaac/sim/pedestrians.py:AREA_COUNT` |
| Area roads | shortest road routes from `parking` to every `ore` site (Dijkstra on road length); roads ranked by routes using them, then `id`; one area per road per round while the road has room | |
| Area spacing along a road (m) | 40 | `isaac/sim/pedestrians.py:AREA_GAP` |
| Road end clearance (m), at most a quarter of the road | 20 | `isaac/sim/pedestrians.py:END_CLEAR` |
| Area half-length along the road (m) | 20 | `isaac/sim/pedestrians.py:WINDOW` |
| Area shift when the road bends too much (m) | 10 | `isaac/sim/pedestrians.py:AREA_SHIFT` |
| Quarry areas | spur_7 × 2, haul_6, haul_2, haul_4, ramp_3 (parking → ore_1/ore_2/ore_3 routes) | |
| People per area | contiguous split, 2 each for 6 areas | |
| Pace | seeded uniform (`random.Random(1)`) | |
| Min pace (m/s) | 1.2 | `isaac/sim/pedestrians.py:MIN_PACE` |
| Max pace (m/s) | 1.6 | `isaac/sim/pedestrians.py:MAX_PACE` |
| Crossing / exiting pace factor | 1.25 | `isaac/sim/pedestrians.py:HURRY` |
| Acceleration (m/s²), also braking to arrive | 1.5 | `isaac/sim/pedestrians.py:ACCEL` |
| Turn rate (deg/s) | 240 | `isaac/sim/pedestrians.py:TURN_RATE` |
| Shoulder band start beyond the road edge (m) | 1.5 | `isaac/sim/pedestrians.py:SHOULDER_IN` |
| Shoulder band end beyond the road edge (m) | 6 | `isaac/sim/pedestrians.py:SHOULDER_OUT` |
| Next goal | stroll on own shoulder, cross to the far shoulder, or stand on the road edge | |
| Cross probability | 0.3 | `isaac/sim/pedestrians.py:P_CROSS` |
| Cross shift along the road (m) | 4 | `isaac/sim/pedestrians.py:CROSS_SHIFT` |
| Edge stand probability | 0.15 | `isaac/sim/pedestrians.py:P_EDGE` |
| Edge stand inside the road edge (m) | 1 | `isaac/sim/pedestrians.py:EDGE_IN` |
| After an edge stand or exit | cross or go back to a shoulder, never another road goal | |
| Pause on a shoulder (s) | 2–6 | |
| Pause skipped | 0.3 | `isaac/sim/pedestrians.py:SKIP_PAUSE` |
| Edge stand (s) | 1.5–4, the only stop inside the road | |
| Road corridor | distance to the road centreline < `width_m` / 2 | |
| Corridor cap | exit to the nearest shoulder once time inside + time to leave would exceed `width_m` / pace + slack | |
| Corridor slack (s) | 6 | `isaac/sim/pedestrians.py:CORRIDOR_SLACK` |
| Truck reaction radius (m), person ahead of the truck | 15 | `isaac/sim/pedestrians.py:YIELD_RADIUS` |
| Truck reaction min truck speed (m/s) | 0.5 | `isaac/sim/pedestrians.py:YIELD_SPEED` |
| Attentive share | 2/3 (seeded per person); attentive people leave the road and wait on the shoulder for an approaching truck, the rest ignore trucks | |
| Seed, per person `random.Random(1000 * seed + index)` | 1 | `isaac/sim/pedestrians.py:WALK_SEED` |
| Sent as | `actors` `{id, x, z, yaw}` at 10 Hz; hello `paths` = one closed polygon per area (both shoulders) | |

## Truck dynamics

| Item | Value | Source |
|---|---|---|
| Model | kinematic bicycle | |
| Max speed (m/s) | 8 | `unity/Assets/Scripts/TruckAgent.cs:maxSpeed` |
| Max steer (deg) | 30 | `unity/Assets/Scripts/TruckAgent.cs:maxSteerDeg` |
| Wheelbase (m) | 5 | `unity/Assets/Scripts/TruckAgent.cs:wheelbase` |
| Speed response (1/s) | 1.5 | `unity/Assets/Scripts/TruckAgent.cs:accel` |
| Command timeout (s) | 0.5 | `unity/Assets/Scripts/TruckAgent.cs:commandTimeout` |
| Fake sim max speed (m/s) | 8 | `brain/brain/fakesim.py:MAX_SPEED` |
| Fake sim max steer (deg) | 30 | `brain/brain/fakesim.py:MAX_STEER_DEG` |
| Fake sim wheelbase (m) | 5 | `brain/brain/fakesim.py:WHEELBASE` |

## Policy

| Item | Value | Source |
|---|---|---|
| Inputs | pose, goal, RGB, depth, latest lidar points | |
| Outputs | throttle -1..1 (negative only for dead-end reverse; Isaac and fake sim honour it), steer -1..1 (positive = right) | |
| Arrive radius (m) | 4 | `brain/brain/policy.py:ARRIVE_RADIUS` |
| Stop distance (m) | 6 | `brain/brain/policy.py:STOP_DIST` |
| Slow distance (m) | 15 | `brain/brain/policy.py:SLOW_DIST` |
| Full-steer heading error (deg) | 45 | `brain/brain/policy.py:STEER_GAIN_DEG` |
| Depth band | rows 35 %–65 %, split into left / centre / right thirds | |
| Statuses | `idle`, `driving`, `avoiding`, `holding`, `arrived` | |

## Planner

| Item | Value | Source |
|---|---|---|
| World | `data/worlds/quarry/` (`world.json`, `navgrid.json`), override with `WORLD_DIR`; `default` is also shipped | |
| Road carving | nav-grid cells within half a road's `width_m` of its centreline are made passable at load | |
| Route | road graph (Dijkstra over world.json roads, endpoints snapped within the node snap), else A* on the grown grid, else A* on the raw grid; Chaikin smoothing, 2 m spacing | |
| Node snap (m) | 15.0 | `brain/brain/planning/navmap.py:NODE_SNAP_M` |
| Route edge cost | step cost + edge cost × (1 − clearance / safe clearance), clearance = cell centre to nearest blocked cell | |
| Edge cost | 2.0 | `brain/brain/planning/navmap.py:EDGE_COST` |
| Safe clearance (m) | 6.0 | `brain/brain/planning/navmap.py:SAFE_CLEARANCE` |
| Used when | the truck has a route; otherwise the straight-to-goal policy above | |
| Candidates | 11 steer × 4 target speeds (8, 5, 2.5, 0 m/s), kinematic bicycle rollout | |
| Candidate horizon (s) | 3.0 | `brain/brain/planning/driver.py:HORIZON` |
| Rollout step (s) | 0.1 | `brain/brain/planning/driver.py:DT` |
| Planner max speed (m/s) | 8.0 | `brain/brain/planning/driver.py:MAX_V` |
| Planner arrive radius (m) | 5.0 | `brain/brain/planning/driver.py:ARRIVE_RADIUS` |
| Output | throttle = target speed / 8, steer = candidate steer | |
| Chooser | hand-tuned score (rules), or the learned scorer when `PLANNER_CKPT` loads (see Learned planner) | |
| Lidar mount height (m) | 3.0 | `brain/brain/planning/driver.py:LIDAR_HEIGHT` |
| Lidar mount override | `LIDAR_HEIGHT` env var | |
| Tilted / offset sensor | when the lidar message has `sensor`, points go to world with its pose, ground returns are dropped, and the rest are re-expressed as a level sensor `LIDAR_HEIGHT` above the nav-grid ground at the truck centre (`brain/brain/policy.py:planner_points`) | |
| Ground returns | per 0.5° lidar column, outwards: a return is ground if it rises at most tan 20° × distance + 0.2 m above the last ground return (start: sensor y − `LIDAR_HEIGHT`) (`brain/brain/policy.py:ground_mask`) | |
| Ground column (deg) | 0.5 | `brain/brain/policy.py:COLUMN_DEG` |
| Ground tolerance (m) | 0.2 | `brain/brain/policy.py:GROUND_TOL` |
| Speed cap | env `MAX_SPEED`, applied to the chosen throttle after the chooser | |
| Speed cap default (m/s) | 5.0 | `brain/brain/policy.py:MAX_SPEED` |
| Speed near goal (m/s) | 3.0 | `brain/brain/policy.py:GOAL_SPEED` |
| Near goal distance (m) | 25.0 | `brain/brain/policy.py:GOAL_SLOW_DIST` |
| Dead-end recovery | no safe forward candidate for the stuck time: straight reverse (status `reversing`, throttle −speed / 8) while the nav grid 3 m behind is clear for the whole width, up to the reverse distance, then replan; no rear lidar, so other vehicles behind are not checked | `brain/brain/recovery.py` |
| Stuck time (s) | 3.0 | `brain/brain/recovery.py:STUCK_AFTER` |
| Reverse speed (m/s) | 1.5 | `brain/brain/recovery.py:REVERSE_SPEED` |
| Reverse distance (m) | 10.0 | `brain/brain/recovery.py:REVERSE_DIST` |
| Min obstacle height above ground (m) | 0.8 | `brain/brain/planning/driver.py:MIN_OBSTACLE_HEIGHT` |
| Obstacle radius (m) | 4.0 | `brain/brain/planning/driver.py:OBSTACLE_RADIUS` |
| Obstacles | lidar points above the nav-grid ground, except points within the wall snap of a blocked cell | |
| Wall snap (m) | 1.0 | `brain/brain/planning/driver.py:WALL_SNAP_M` |
| Truck half width (m) | 2.12 | `brain/brain/planning/driver.py:TRUCK_HALF_W` |
| Edge margin (m) | 0.5 | `brain/brain/planning/driver.py:EDGE_MARGIN` |
| Unsafe candidate | the centre or either side (half width + edge margin) hits a blocked cell, or comes within the hit margin of an obstacle while closing in | |
| Hit margin (m) | 0.5 | `brain/brain/planning/driver.py:HIT_MARGIN` |
| Holding | chosen target speed is 0, or no candidate is safe | |
| Re-plan (static) | an obstacle stays in one 2 m cell for the static time and lies near the next 30 route points; cells near it are blocked for A* | |
| Static time (s) | 1.5 | `brain/brain/planning/driver.py:STATIC_AFTER` |
| Re-plan (waiting) | stopped for the wait time; all seen obstacles are blocked for A* | |
| Wait time (s) | 4.0 | `brain/brain/planning/driver.py:REPLAN_AFTER` |
| Re-plan cooldown (s) | 1.0 | `brain/brain/planning/follow.py:REPLAN_COOLDOWN` |
| Re-plan rejected | the new route starts more than 90° from the truck heading (no reverse) | |
| Scorer lidar bins | 60 | `brain/brain/planning/driver.py:N_RAYS` |
| Truck state | `path` (route, `[x, z]`), `path_idx`, `ghost_paths` (safe candidates), `planner`, `choice`, `candidates`, `candidate_scores` | |
| Route in state | `trucks[].route` = `path` | |
| Goal resolution | `{site}` → hello `sites` ∪ `junctions` position (fallback `world.json` sites), then A* route | |
| On arrival | `goal`, `goal_site`, `path`, `ghost_paths` cleared, status `arrived` then `idle` | |

## Learned planner

meiru-cam's candidate-path scorer (`sim2d/`, trained with BC then PPO in a 2D sim on the same nav grid). It replaces only the rule-based choice among the 44 planner candidates; routes, safety mask, re-planning and holds are unchanged.

| Item | Value | Source |
|---|---|---|
| Enable | `PLANNER_CKPT` = path to an exported `.npz` (a `.pt` is accepted if a sibling `.npz` exists or torch is installed; `PLANNER_CHECKPOINT` also read) | |
| Default | unset: rules. Docker compose sets `/app/models/ppo_v3.npz` (meiru-cam's PPO v3) with `./models` mounted read-only; missing or invalid file: warning log, rules | |
| Runtime | numpy MLP forward, no torch in the brain image (`brain/brain/planning/learned.py`) | |
| Export | `cd brain && uv run --extra model python -m brain.planning.export_scorer ../models/ppo_v3.pt` → `models/ppo_v3.npz` (state_dict + config JSON with width, dims, source, sha256); fails if the input layer sizes differ from OBS_DIM / CAND_DIM | |
| Fetch | `uv run --with torch --with numpy tools/fetch_planner.py` (Hugging Face org `pi-hack-mining`, newest `.pt` whose repo or file name contains `--want`, default `ppo_v3`; `HF_TOKEN` for a private repo) → `models/ppo_v3.pt`, `models/ppo_v3.npz` | |
| Network | scene MLP (obs → 256 → 256), path MLP (candidate → 128 → 128), head (384 → 256 → 1), ReLU | |
| Choice | argmax logit over safe candidates; no safe candidate → hold (target speed 0, steer 0) | |
| Output | throttle = candidate target speed / 8, steer = candidate steer; same action semantics as sim2d (target speed, first-order response 1.5 /s, bicycle model) | |
| Train | `uv run --with numpy --with torch --with scipy python -m sim2d.datagen --out data/bc.npz`, then `sim2d.train_bc --out runs/bc.pt`, then `sim2d.train_ppo --init runs/bc.pt --out runs/ppo.pt` | |
| Evaluate | `python -m sim2d.evaluate --worlds quarry --policy expert\|runs/ppo_v3.pt\|models/ppo_v3.npz` | |
| sim2d worlds | `data/worlds/<id>` (override `SIM2D_WORLDS_DIR`); worlds used `SIM2D_WORLDS`, default `default,quarry` | |
| Tests | `uv run --with numpy --with torch --with scipy --with pytest pytest tests -q` (constants, candidates, grid and feature parity with `DriveEnv.observe()`, numpy vs torch logits within 1e-5) | |

| Feature | Layout | Brain source |
|---|---|---|
| Observation | 79 floats = 60 lidar + 16 route + 3 state | `driver.features` |
| Lidar | 60 rays over 120° ahead (left to right), min range per ray bin / 100; range = min(lidar points above the nav-grid ground, nav-grid ray march at 1 m steps), lidar range rounded up to 1 m as in sim2d | |
| Route | 8 route points 5 m apart ahead of the nearest route index, ego frame (forward, right) / 40 | |
| State | speed / 8, last steer command, load (brain sends 0; sim2d trains on 0..1) | |
| Candidate | 33 floats = 15 rollout points ego / 30, steer, target speed / 8, min(clearance, 20) / 20 | |
| Clearance | nearest dynamic lidar obstacle point minus 4 m (sim2d: mover centre minus 3.5 m truck radius minus mover radius) | |
| Safety mask | brain rule checks (whole-width off-road, obstacle closing within hit margin) | |

| Constant | Value | Source |
|---|---|---|
| Lidar rays | 60 | `sim2d/env.py:N_RAYS` |
| Lidar range (m) | 100.0 | `sim2d/env.py:LIDAR_RANGE` |
| Route points | 8 | `sim2d/env.py:ROUTE_POINTS` |
| Route point spacing (m) | 5.0 | `sim2d/env.py:ROUTE_SPACING` |
| Route resample step (m) | 2.0 | `brain/brain/planning/driver.py:ROUTE_STEP` |
| Candidate horizon (s) | 3.0 | `sim2d/env.py:HORIZON` |
| Candidate point step (s) | 0.2 | `sim2d/env.py:RDT` |
| Speed response (1/s) | 1.5 | `brain/brain/planning/driver.py:ACCEL` |
| Max speed (m/s) | 8.0 | `sim2d/env.py:MAX_V` |
| Wheelbase (m) | 5.0 | `sim2d/env.py:WHEELBASE` |
| Truck half width (m) | 2.12 | `brain/brain/planning/driver.py:TRUCK_HALF_W` |
| Sim tick (s) | 0.1 | `sim2d/env.py:DT` |

| HUD state (`trucks[]`) | Meaning |
|---|---|
| `planner` | `learned` or `rules` |
| `choice` | index into `candidates` of the driven path, -1 when none |
| `candidates` | all 44 rollouts `[[x, z], …]` (15 points, 0.2 s apart) |
| `candidate_scores` | per candidate, higher = preferred (logit, or minus the rule cost); `null` = unsafe |
| Map | chosen path bold, other safe candidates faint, in the truck colour |

## Sim protocol (TCP)

| Item | Value | Source |
|---|---|---|
| Framing | `u32 header_len \| u32 payload_len \| header JSON \| payload`, little-endian | |
| Max header (bytes) | 65536 | `brain/brain/protocol.py:MAX_HEADER` |
| Max payload (bytes) | 67108864 | `brain/brain/protocol.py:MAX_PAYLOAD` |

| Message | Direction | Header | Payload |
|---|---|---|---|
| `hello` | sim → brain | `trucks`, `cameras`, `bounds`, `truck_model`, `obstacles[{x,z,r}]`, `paths[{points[{x,z}]}]`; optional `roads[{id,from,to,width_m,one_way,points[{x,z}]}]` (points from → to), `sites[{id,type,name,x,z}]`, `junctions[{id,x,z}]` | none |
| `frame` | sim → brain | `camera`, `truck` (`""` if none), `rgb{w,h,flip,channels,format,bytes}`, `depth{w,h}` (0×0 if none), `pose{x,z,yaw}`, `speed` | `format` raw: RGB(A) `w·h·channels` bytes; `format` jpeg: `bytes` JPEG bytes; then depth float32 |
| `actors` | sim → brain | `pedestrians[{id,x,z,yaw}]` | none |
| `lidar` | sim → brain | `truck`, `points`, `format` (`int16cm`; `f32` accepted), `pose{x,z,yaw}`, optional `sensor{x,y,z,yaw,pitch,roll,hfov}` (lidar world pose, same Euler convention as `cam`; Isaac sends it) | `points` × 3 int16 cm (or float32 m) |
| `action` | brain → sim | `truck`, `throttle`, `steer` | none |
| `view` | brain → sim | `camera` (`overview`), `box` `[x0,y0,x1,y1]` normalised top-left image coords of the unzoomed view, or null to reset; resent after every `hello` | none |

## Web

| Item | Value | Source |
|---|---|---|
| Video | `GET /ws/video` binary, one H.264 access unit per message: `u8 name_len \| name \| u8 flags (bit0 keyframe) \| u32 LE timestamp_ms \| u16 LE meta_len \| meta JSON \| H.264 Annex B AU`; meta is the source frame's HUD metadata (see HUD), `meta_len` 0 if none | |
| Video encoder | libx264 via PyAV (bundled in the aarch64/x86_64/macOS wheels, no NVENC), one encoder thread per camera shared by all viewers, `tune=zerolatency`, no B-frames, SPS/PPS in band on every keyframe, VBV cap = bitrate over 1 s | |
| Video profile | x264 `baseline` → constrained baseline, level 2.2 at 640×360 (`avc1.42c016`); browser reads the codec string from the SPS | |
| x264 preset | `veryfast` | |
| Video frame rate (fps) | 15 | `brain/brain/video.py:FPS` |
| Keyframe interval (frames) | 15 | `brain/brain/video.py:GOP_FRAMES` |
| Truck camera bitrate (kbps) | 600 | `brain/brain/video.py:TRUCK_KBPS` |
| Overview bitrate (kbps) | 400 | `brain/brain/video.py:OVERVIEW_KBPS` |
| Video join | new viewer gets the current GOP (latest keyframe + following AUs), then live AUs | |
| Viewer queue (AUs per camera) | 30 | `brain/brain/video.py:VIEWER_QUEUE` |
| Slow viewer | queue full → drop that camera's AUs until the next keyframe | |
| Encoder overload | latest frame wins, frames arriving while encoding are dropped | |
| Browser decode | WebCodecs `VideoDecoder`, Annex B (no `description`), `web/video.js` | |
| Video fallback | `GET /ws/video?codec=jpeg` binary: `u8 name_len \| name \| u16 LE meta_len \| meta JSON \| JPEG`, latest frame per camera; used when `VideoDecoder` lacks `avc1.42e01e` | |
| JPEG quality | 75 | `brain/brain/bridge.py:JPEG_QUALITY` |
| State | `GET /ws/state` JSON | |
| State rate (Hz) | 10 | `brain/brain/app.py:STATE_HZ` |
| Lidar stream | `GET /ws/lidar?truck={id}` binary: `u8 name_len \| name \| 3×f32 pose (x, z, yaw) \| zlib(planar delta int16 cm)` | |
| Lidar payload | int16 cm, planes x[N], y[N], z[N], each delta coded from 0 (wrapping int16), zlib (`protocol.py compress_points`); browser inflates with `DecompressionStream("deflate")`, `web/lidar_stream.js` | |
| Lidar zlib level | 6 | `brain/brain/protocol.py:LIDAR_ZLIB_LEVEL` |
| Lidar compression | encoded once per scan in the bridge, shared by all viewers | |
| Lidar viewer rate (Hz), env `LIDAR_VIEWER_HZ` | 10 | `brain/brain/app.py:LIDAR_VIEWER_HZ` |
| Lidar latest | `GET /api/trucks/{id}/lidar` → N×3 f32 body, `X-Lidar` header JSON `{truck, seq, points, pose, sensor}` | |
| Lidar view | three.js 0.169.0, selected truck, coloured by range | |
| Map | preview background, roads (scaled by `width_m`), sites (labelled, coloured by type), junctions, obstacles, pedestrian paths, pedestrians, truck routes, goals, trucks | |
| Map extent | hello `bounds` | |
| Map canvas (px) | 800 × 800, CSS-scaled | |
| Map click | `POST` `{x,z}` for the selected truck (brain snaps to a node when roads exist) | |
| Send to | per-truck site select, `POST` `{site}` | |
| Truck list | status, speed, `goal_site` | |
| Read state | `GET /api/state`: `bounds`, `roads`, `sites`, `junctions`, `world_seq`, `cameras`, `truck_model`, `obstacles`, `paths`, `pedestrians`, `trucks[{id,x,z,yaw,speed,goal,goal_site,route,path_idx,ghost_paths,status,throttle,steer}]` | |
| State stream | `/ws/state` = `/api/state` without `roads`, `sites`, `junctions`; refetch `/api/world` when `world_seq` changes | |
| World | `GET /api/world` → `{bounds, roads, sites, junctions, preview, preview_bounds}` | |
| World preview | `GET /api/world/preview` → `WORLD_DIR/world.json` `assets.preview` (PNG, top-down, north up, aligned to `bounds`), 404 if unset | |
| Preview shown | when `preview_bounds` = hello `bounds` and roads exist | |
| `WORLD_DIR` | brain env (planner nav grid and preview), default `data/worlds/default`, Docker compose `/app/data/worlds/quarry` | |
| Set goal | `POST /api/trucks/{id}/goal` `{"site"}` or `{"x","z"}`, plans a route | |
| Sites | `GET /api/sites` → `[{"id","type","pos"}]` (from `world.json`) | |
| Go to site | `POST /api/trucks/{id}/site/{site_id}` (same as `{"site"}`) | |
| Goal fields | `goal` [x, z], `goal_site` node id or null, `route` [[x, z], …] (planner path) | |
| Clear goal | `DELETE /api/trucks/{id}/goal` (clears `goal`, `goal_site`, `route`) | |
| Overview zoom | `POST /api/view/overview` `{"box":[x0,y0,x1,y1]}` (normalised, top-left origin, of the unzoomed frame; 409 while zoomed, one level), `DELETE /api/view/overview` resets; state `views.overview.box`. Shared: one viewer's zoom applies to every viewer | |
| Zoom box minimum (normalised) | 0.02 | `brain/brain/app.py:MIN_BOX` |
| Zoom box max aspect | 8 | `brain/brain/app.py:MAX_BOX_ASPECT` |
| Zoom in sim | Isaac re-aims the overview at the box centre ray and narrows the focal length so the box fills the frame (smaller side expanded to the image aspect), eased over 0.4 s; overview frame meta `view{box,settled}`; fakesim renders the sub-region at full resolution | `isaac/sim/zoom.py` |
| Zoom UI | crosshair on the overview, drag (≥ 8 px) selects, box expanded to the tile aspect; the frame scales to the box until a settled zoomed frame arrives; Esc or palette "Reset overview zoom" resets | `web/zoom.js` |

## Radio

| Item | Value | Source |
|---|---|---|
| Port | 8090, bound to 127.0.0.1 unless `HOST` is set | |
| Brain poll rate (Hz) | 5 | `radio/server.py:POLL_HZ` |
| Goals | brain routes (`roads` + per-truck `route`): one `{"site"}` per leg; otherwise chained `{"x","z"}` waypoints | |
| Brain-cleared goal counts as reached within (m) | 6 | `radio/server.py:BRAIN_CLEAR_M` |
| Max command length (chars) | 300 | `radio/server.py:MAX_COMMAND_CHARS` |
| Max WebSocket message (bytes) | 512 | `radio/server.py:WS_MAX_MSG` |
| Max audio upload (KB) | 600 (`RADIO_MAX_AUDIO_KB`) | |
| STT per session / global (per min) | 10 / 30 | |
| Commands per session / global (per min) | 20 / 60 | |
| TTS syntheses global (per min) | 60 | |
| Login attempts per IP / global (per min) | 5 / 20 | |

## Isaac sim

| Item | Value | Source |
|---|---|---|
| Code | `isaac/sim/`, container files `isaac/docker/` | |
| Image | `nvcr.io/nvidia/isaac-sim:6.2.0` (ARM64), build arg `ISAAC_TAG` | |
| Entry | `/isaac-sim/python.sh /mining/isaac/sim/main.py --brain 127.0.0.1:9000 --world /data/worlds/quarry --cache /cache` | |
| Container | `mining-isaac-sim`, compose `isaac/docker/compose.yml`, profile `isaac`, host network, `nvidia.com/gpu=all` | |
| Renderer | RTX `RealTimePathTracing`, headless, Replicator `rgb` annotators | |
| Stage | Z up, metres; Isaac (X, Y, Z) = world (x, z, y) | |
| Yaw | world yaw (0 = +z, clockwise) = rotation about Isaac -Z from +Y | |
| Assets | own GLB reader → USD (`isaac/sim/usdbuild.py`), axis swap and transforms baked into points, cached in `downloads/isaac/cache` | |
| Scan visual | `visual.glb` node transforms, then `scan_transform.matrix`, then axis swap | |
| Collider / surround | world frame, axis swap only | |
| Sky | `sky_pano.jpg` latlong dome light, rotation `--dome-rotation` | |
| Raycasts | NVIDIA Warp `mesh_query_ray` on collider + surround meshes, oriented truck/excavator boxes, pedestrian cylinders | |
| Lidar method | Warp raycasts (RTX lidar not used) | |
| Frame rate (default; `fps` in `isaac/sim/config.json`) | 15 | `isaac/sim/sensors.py:FPS` |
| Truck camera width | 640 | `isaac/sim/sensors.py:CAM_W` |
| Truck camera height | 360 | `isaac/sim/sensors.py:CAM_H` |
| Horizontal FOV | 90 | `isaac/sim/sensors.py:HFOV` |
| Depth width | 64 | `isaac/sim/sensors.py:DEPTH_W` |
| Depth height | 36 | `isaac/sim/sensors.py:DEPTH_H` |
| Depth range (m) | 100 | `isaac/sim/sensors.py:DEPTH_RANGE` |
| Sensor offset ahead of box (m) | 0.2 | `isaac/sim/sensors.py:SENSOR_OFFSET` |
| Camera height (fraction of truck height) | 0.8 | `isaac/sim/sensors.py:CAMERA_HEIGHT_FRACTION` |
| Lidar height (fraction of truck height) | 0.6 | `isaac/sim/sensors.py:LIDAR_HEIGHT_FRACTION` |
| Lidar range (m) | 100 | `isaac/sim/sensors.py:LIDAR_RANGE` |
| Lidar horizontal FOV (deg) | 120 | `isaac/sim/sensors.py:LIDAR_HFOV` |
| Lidar vertical min (deg) | -20 | `isaac/sim/sensors.py:LIDAR_VMIN` |
| Lidar vertical max (deg) | 10 | `isaac/sim/sensors.py:LIDAR_VMAX` |
| Lidar columns | 240 | `isaac/sim/sensors.py:LIDAR_COLS` |
| Lidar channels | 30 | `isaac/sim/sensors.py:LIDAR_CHANNELS` |
| Lidar rate (Hz) | 10 | `isaac/sim/sensors.py:LIDAR_HZ` |
| Actors rate (Hz) | 10 | `isaac/sim/sensors.py:ACTORS_HZ` |
| JPEG quality | 80 | `isaac/sim/protocol.py:JPEG_QUALITY` |
| Overview | perspective, looks at the road network's centre from the side opposite the ore faces | |
| Overview elevation (deg) | 32 | `isaac/sim/sensors.py:OVERVIEW_ELEVATION` |
| Overview distance (× road extent) | 0.62 | `isaac/sim/sensors.py:OVERVIEW_DISTANCE` |
| Overview horizontal FOV (deg) | 60 | `isaac/sim/sensors.py:OVERVIEW_HFOV` |
| Truck frame `cam` | `{x, y, z, yaw, pitch, roll, hfov}` camera world pose, Unity Euler (pitch > 0 looks down) | |
| Physics substep (s) | 1/60, real-time paced | |
| Max speed (m/s) | 8 | `isaac/sim/kinematics.py:MAX_SPEED` |
| Max steer (deg) | 30 | `isaac/sim/kinematics.py:MAX_STEER_DEG` |
| Wheelbase (m) | 5 | `isaac/sim/kinematics.py:WHEELBASE` |
| Speed response (1/s) | 1.5 | `isaac/sim/kinematics.py:ACCEL` |
| Command timeout (s) | 0.5 | `isaac/sim/kinematics.py:COMMAND_TIMEOUT` |
| Ground follow | 4 wheels + centre, Warp ray down the collider (surround fallback), height smoothing, tilt of body and sensors | |
| Height smoothing (s) | 0.06 | `isaac/sim/kinematics.py:HEIGHT_SMOOTH` |
| Snap distance (m) | 4 | `isaac/sim/kinematics.py:SNAP_DISTANCE` |
| Max tilt (deg) | 25 | `isaac/sim/kinematics.py:MAX_TILT_DEG` |
| Tilt smoothing (s) | 0.15 | `isaac/sim/kinematics.py:TILT_SMOOTH` |
| Truck model | Komatsu HD465, GLB X flipped (Unity frame), length along z | |
| Truck length (m) | 9.35 | `isaac/sim/world.py:TRUCK_LENGTH` |
| Trucks | 3 | `isaac/sim/world.py:TRUCK_COUNT` |
| Spawn | `world.json` spawns, heading along the first road leaving the nearest site, spread along the slot line | |
| Heading look-ahead (m) | 20 | `isaac/sim/world.py:SPAWN_LOOK_AHEAD` |
| Minimum gap between parked trucks (m) | 2 | `isaac/sim/world.py:SPAWN_GAP` |
| Slot-line shift search (m) | 10 | `isaac/sim/world.py:SPAWN_SEARCH` |
| Slot-line shift step (m) | 0.5 | `isaac/sim/world.py:SPAWN_SEARCH_STEP` |
| Excavator length (m) | 10 | `isaac/sim/world.py:EXCAVATOR_LENGTH` |
| Pedestrians | "Pedestrian wander" rules, ground height probed every step | |
| Pedestrian height (m) | 1.8 | `isaac/sim/pedestrians.py:PEDESTRIAN_HEIGHT` |
| Pedestrian radius (m) | 0.3 | `isaac/sim/pedestrians.py:PEDESTRIAN_RADIUS` |
| Pedestrian IDs | `Pedestrian_NN` | |
| Pedestrian visual | "Isaac people" | |
| Dry run | `--dry-run`: no Isaac imports, black JPEG frames, Warp raycasts when `warp` is importable, heightmap otherwise | |

## HUD

| Item | Value | Source |
|---|---|---|
| Sim → brain camera pose | optional `frame` header field `cam{x,y,z,yaw,pitch,roll,hfov}`: camera world position (m, Unity axes) and Unity Euler angles (deg): `yaw` = `eulerAngles.y` (0 = +z, clockwise), `pitch` = `eulerAngles.x` in −180..180 (positive looks down), `roll` = `eulerAngles.z` in −180..180, `hfov` horizontal FOV | `unity/Assets/Scripts/SensorCamera.cs` |
| Truck ground height | optional `pose.y` in `frame` (truck transform y) | |
| Fake sim camera | truck position (renderer origin), y = ground + `CAM_HEIGHT` (ground from the nav-grid terrain in `--world` mode, else 0; `pose.y` = ground), pitch 0, roll 0, hfov `FOV_DEG`; pinhole columns `atan((col + 0.5 − W/2) / f)` | `brain/brain/fakesim.py` |
| Frame metadata | JSON `{t, cam, pose, speed, truck, det}`, `t` = brain wall clock (s) when the frame arrived; travels with the same frame through the encoder, one per access unit; `det` on truck frames when the detector is on (see Detector) | `brain/brain/hud.py` |
| State additions | `t` (brain wall clock, s); with a heightmap: `y` on trucks, pedestrians, obstacles and `route_y` (one height per `route` point) | `brain/brain/hud.py` |
| Heightmap | `WORLD_DIR/heightmap.raw` + `heightmap_meta.json` (uint16, row 0 = min z, col 0 = min x), bilinear; none when `WORLD_DIR` unset or files missing | |
| Ground fallback | horizontal plane at state truck `y`, else `pose.y`, else `cam.y − 3.6` | `web/hud.js` |
| Projection | pinhole, square pixels, `f = W/2 / tan(hfov/2)`, `R = Ry(yaw)·Rx(pitch)·Rz(roll)` | `web/hud_math.js` |
| Sync | overlay is drawn when the video frame is drawn, with that frame's `cam`; actors interpolated between 10 Hz state snapshots at the frame's `t` | |
| Path source | chosen planner candidate (`candidates[chosen]`, also `chosen_idx`/`candidate`/`candidate_idx`/`best`) blended into `route` beyond its end; else `route` (with `route_y`); else kinematic prediction toward `goal`; else 9 m grey stub | |
| Alternatives | other `candidates` (or `ghost_paths` without candidates) as thin faint lines, opacity from normalised score (`score` field or `scores[i]`) | |
| Hold | status `holding`/`hold`/`blocked`, or `candidates` present with no chosen index: short amber stub | |
| Hold bar (m) | 14 | `web/hud.js:HOLD_LEN` |
| Chosen candidate segment (m) | 30 | `web/hud.js:NEAR_LEN` |
| Route ribbon far alpha | 0.14 | `web/hud.js:ROUTE_END_ALPHA` |
| Front camera only | ribbon and candidates drawn when camera yaw is within the forward cone of truck yaw | |
| Forward cone half-angle (deg) | 60 | `web/hud.js:FORWARD_DEG` |
| Path length (m) | 80 | `web/hud.js:MAX_LEN` |
| Ribbon half-width (m) | 2.65 | `web/hud.js:HALF_W` |
| Ribbon | global route (`route` + `route_y`) always drawn, 0.62 alpha near the truck fading to the far alpha; learned planner's chosen candidate (null-score candidates skipped) as a bright 0.6 × width near segment; other candidates faint thin lines; holding keeps the full route, tinted amber, with a solid stop bar at the hold distance | |
| Ribbon colour | blue engaged, amber avoiding/holding or object within 20 m clear, red within 8 m clear (ribbon ends at the object with a stop bar) | |
| Stop clearance (m) | 8 | `web/hud.js:STOP_CLEAR` |
| Warn clearance (m) | 20 | `web/hud.js:WARN_CLEAR` |
| Objects | thin axis-aligned rectangle per object, label `class distance` above the top-left corner (confidence added when focused); white, amber within warn clearance, red within stop clearance (detections: box spanning the centre 24 % of the frame) | `web/hud.js` |
| Detections | `meta.det.items` → `hud.setDetections(camera, items)`; 2D `{box:[x0,y0,x1,y1] normalised, cls, conf, dist}` or 3D `{x,z,size,kind}`; expire after the TTL | `web/hud.js` |
| Detection TTL (ms) | 1000 | `web/hud.js:DETECTION_TTL_MS` |
| Ground truth boxes | pedestrians (0.6 × 1.8 m), other trucks (5.3 × 4.5 × 9.35 m), obstacles (2r) projected to their image bounds; drawn when the palette toggle "Show ground truth boxes" is on (stored in `localStorage`) or the camera has no live detections | |
| Label min box height (px) | 14 | `web/hud.js:LABEL_MIN_H` |
| API | `createHud({focusOnClick})`, `attach(container, cameraId)`, `frame(cameraId, meta)`, `setState(state)`, `setDetections(cameraId, list)`, `toggleGroundTruth()`, `setFocused(cameraId)`, `setDetail(cameraId)` | `web/hud.js` |
| Detail level | full HUD when focused, `setDetail`, or container at least the detail width | |
| Detail width (px) | 960 | `web/hud.js:DETAIL_WIDTH` |

## Detector

| Item | Value | Source |
|---|---|---|
| Model | YOLO detector as ONNX at `DETECTOR_MODEL` (compose default `/app/models/yolo11n.onnx`, `./models` mounted read-only); off when unset or missing | `brain/brain/detector.py` |
| Team model | `uv run tools/fetch_detector.py` downloads the first `pi-hack-mining` Hugging Face model (`--repo`, `--file` to choose; `.pt` exported to ONNX) into `models/` | `tools/fetch_detector.py` |
| Placeholder | `uv run tools/fetch_detector.py --placeholder`: Ultralytics YOLO11n COCO (2.6 M params), exported at 384 × 640 | `tools/fetch_detector.py` |
| Classes | from ONNX metadata `names`; `DETECTOR_CLASSES` comma list filters; COCO models keep person, bicycle, car, motorcycle, bus, truck | `brain/brain/detector.py` |
| Output layouts | `(1, 4+nc, N)` YOLOv8/11, `(1, N, 5+nc)` YOLOv5, `(1, N, 6)` end-to-end (metadata `end2end`) | `brain/brain/detector.py` |
| Input height (px) | 384 | `brain/brain/detector.py:INPUT_H` |
| Input width (px) | 640 | `brain/brain/detector.py:INPUT_W` |
| Preprocess | letterbox (bilinear resize, centred pad), RGB, /255, NCHW; fixed model input shape wins over the defaults | |
| Pad value | 114 | `brain/brain/detector.py:PAD_VALUE` |
| Confidence threshold | 0.35 | `brain/brain/detector.py:CONF` |
| NMS IoU | 0.5 | `brain/brain/detector.py:IOU` |
| Max detections | 50 | `brain/brain/detector.py:MAX_DET` |
| Runtime | onnxruntime (extra `detector`), providers from `DETECTOR_PROVIDERS` else all available (CPU on watson) | |
| Intra-op threads | 2 | `brain/brain/detector.py:THREADS` |
| Worker threads | 2 | `brain/brain/detector.py:WORKERS` |
| Scheduling | one pending frame per camera (newer replaces older), workers take the oldest pending camera; truck cameras only | |
| Meta | `det: {t, items:[{box, cls, conf, dist}]}`; `t` = arrival time of the frame the boxes came from; latest result per camera carried on every later frame | `brain/brain/bridge.py` |
| Max carry age (s) | 1 | `brain/brain/detector.py:MAX_AGE_S` |
| Box | normalised to the frame, 4 decimals | |
| Distance | depth at the box bottom band (bottom 20 %, centre half of the width), 20th percentile, × `hypot(1, (cx − W/2)/f)` | `brain/brain/detector.py` |
| Depth percentile | 20 | `brain/brain/detector.py:DEPTH_PERCENTILE` |
| Stats | `GET /api/detector` → `{enabled, model, input, classes, runs, dropped, infer_ms}` | `brain/brain/app.py` |
| Throughput (YOLO11n, watson CPU, Isaac running) | 21 ms single stream at 4 threads; 56 inferences/s with 2 workers × 4 threads (target 45) | |

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

## Network

| Item | Value | Source |
|---|---|---|
| Website + API port | 8080 | |
| Sim TCP port | 9000 | `unity/Assets/Scripts/BrainClient.cs:port` |
| Host binding | 127.0.0.1 | |
| Remote access | `ssh -N -L 8080:127.0.0.1:8080 watson` | |
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
| Point format | float32 x, y, z, little-endian, 12 bytes per point | |
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
| Pedestrians per path | 3 | `brain/brain/fakesim.py:PEDESTRIANS_PER_PATH` |
| Pedestrian min speed (m/s) | 1.2 | `brain/brain/fakesim.py:MIN_WALK_SPEED` |
| Pedestrian max speed (m/s) | 1.6 | `brain/brain/fakesim.py:MAX_WALK_SPEED` |
| Pedestrian radius (m) | 0.3 | `brain/brain/fakesim.py:PEDESTRIAN_RADIUS` |
| Pedestrian height (m) | 1.8 | `brain/brain/fakesim.py:PEDESTRIAN_HEIGHT` |
| Pedestrian colour | RGB (255, 120, 20) | |
| Actors rate (Hz) | 10 | `brain/brain/fakesim.py:ACTORS_HZ` |

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
| Outputs | throttle 0..1, steer -1..1 (positive = right) | |
| Arrive radius (m) | 4 | `brain/brain/policy.py:ARRIVE_RADIUS` |
| Stop distance (m) | 6 | `brain/brain/policy.py:STOP_DIST` |
| Slow distance (m) | 15 | `brain/brain/policy.py:SLOW_DIST` |
| Full-steer heading error (deg) | 45 | `brain/brain/policy.py:STEER_GAIN_DEG` |
| Depth band | rows 35 %–65 %, split into left / centre / right thirds | |
| Statuses | `idle`, `driving`, `avoiding`, `arrived` | |

## Sim protocol (TCP)

| Item | Value | Source |
|---|---|---|
| Framing | `u32 header_len \| u32 payload_len \| header JSON \| payload`, little-endian | |
| Max header (bytes) | 65536 | `brain/brain/protocol.py:MAX_HEADER` |
| Max payload (bytes) | 67108864 | `brain/brain/protocol.py:MAX_PAYLOAD` |

| Message | Direction | Header | Payload |
|---|---|---|---|
| `hello` | sim → brain | `trucks`, `cameras`, `bounds`, `truck_model`, `obstacles[{x,z,r}]`, `paths[{points[{x,z}]}]` | none |
| `frame` | sim → brain | `camera`, `truck` (`""` if none), `rgb{w,h,flip,channels}`, `depth{w,h}` (0×0 if none), `pose{x,z,yaw}`, `speed` | RGB(A) bytes, then depth float32 |
| `actors` | sim → brain | `pedestrians[{id,x,z,yaw}]` | none |
| `lidar` | sim → brain | `truck`, `points`, `pose{x,z,yaw}` | `points` × 3 float32 |
| `action` | brain → sim | `truck`, `throttle`, `steer` | none |

## Web

| Item | Value | Source |
|---|---|---|
| Video | `GET /ws/video` binary: `u8 name_len \| name \| JPEG` | |
| Video delivery | latest frame per camera, slow clients skip frames | |
| JPEG quality | 75 | `brain/brain/bridge.py:JPEG_QUALITY` |
| State | `GET /ws/state` JSON | |
| State rate (Hz) | 10 | `brain/brain/app.py:STATE_HZ` |
| Lidar stream | `GET /ws/lidar?truck={id}` binary: `u8 name_len \| name \| 3×f32 pose (x, z, yaw) \| N×3 f32 points` | |
| Lidar latest | `GET /api/trucks/{id}/lidar` → N×3 f32 body, `X-Lidar` header JSON `{truck, seq, points, pose}` | |
| Lidar view | three.js 0.169.0, selected truck, coloured by range | |
| Map | rocks, pedestrian paths, pedestrians, trucks, goals | |
| Read state | `GET /api/state` | |
| Set goal | `POST /api/trucks/{id}/goal` `{"x","z"}` | |
| Clear goal | `DELETE /api/trucks/{id}/goal` | |

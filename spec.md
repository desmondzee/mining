# Spec

## Platform

| Item | Value |
|---|---|
| Host | watson (Brev), NVIDIA GB10, aarch64 |
| NVIDIA driver | 580.178.04 |
| Repo on host | `~/Documents/mining` |
| Simulator | Unity 6.3 LTS, URP |
| Unity player target | Linux ARM64, IL2CPP, `com.unity.sdk.linux-arm64` |
| Unity editor | macOS (no ARM64 Linux editor) |
| Brain | Python 3.12, FastAPI, uv |
| Containers | Docker Compose, project `mining` |
| Environment source | World Labs Marble, Gaussian splat `.ply` / `.spz` |
| Splat renderer | aras-p/UnityGaussianSplatting |
| Collision | separate colliders (splats have none) |

## Network

| Item | Value | Source |
|---|---|---|
| Website + API port | 8080 | |
| Sim TCP port | 9000 | `unity/Scripts/BrainClient.cs:port` |
| Host binding | 127.0.0.1 | |
| Remote access | `brev port-forward watson --port 8080:8080` | |
| Editor → remote brain | `brev port-forward watson --port 9000:9000` | |

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
| Truck camera width | 640 | `unity/Scripts/SensorCamera.cs:width` |
| Truck camera height | 360 | `unity/Scripts/SensorCamera.cs:height` |
| Frame rate | 15 | `unity/Scripts/SensorCamera.cs:fps` |
| Horizontal FOV | 90 | `unity/Scripts/SensorCamera.cs:horizontalFov` |
| Readback format | RGBA32, bottom-up (`flip: true`) | |
| Depth width | 64 | `unity/Scripts/SensorCamera.cs:depthWidth` |
| Depth height | 36 | `unity/Scripts/SensorCamera.cs:depthHeight` |
| Depth range (m) | 100 | `unity/Scripts/SensorCamera.cs:depthRange` |
| Depth format | float32 metres, perpendicular distance, row 0 = top, miss = range | |
| Depth method | `RaycastCommand` grid against colliders | |

## Fake sim

| Item | Value | Source |
|---|---|---|
| Frame rate | 15 | `brain/brain/fakesim.py:FPS` |
| Horizontal FOV | 90 | `brain/brain/fakesim.py:FOV_DEG` |
| Depth range (m) | 100 | `brain/brain/fakesim.py:MAX_RANGE` |
| Truck camera | 640 × 360 RGB + 640 × 360 depth | |
| Overview size (px) | 480 | `brain/brain/fakesim.py:OVERVIEW_PX` |

## Truck dynamics

| Item | Value | Source |
|---|---|---|
| Model | kinematic bicycle | |
| Max speed (m/s) | 8 | `unity/Scripts/TruckAgent.cs:maxSpeed` |
| Max steer (deg) | 30 | `unity/Scripts/TruckAgent.cs:maxSteerDeg` |
| Wheelbase (m) | 5 | `unity/Scripts/TruckAgent.cs:wheelbase` |
| Speed response (1/s) | 1.5 | `unity/Scripts/TruckAgent.cs:accel` |
| Command timeout (s) | 0.5 | `unity/Scripts/TruckAgent.cs:commandTimeout` |
| Fake sim max speed (m/s) | 8 | `brain/brain/fakesim.py:MAX_SPEED` |
| Fake sim max steer (deg) | 30 | `brain/brain/fakesim.py:MAX_STEER_DEG` |
| Fake sim wheelbase (m) | 5 | `brain/brain/fakesim.py:WHEELBASE` |

## Policy

| Item | Value | Source |
|---|---|---|
| Inputs | pose, goal, RGB, depth | |
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
| `hello` | sim → brain | `trucks`, `cameras`, `bounds` | none |
| `frame` | sim → brain | `camera`, `truck` (`""` if none), `rgb{w,h,flip,channels}`, `depth{w,h}` (0×0 if none), `pose{x,z,yaw}`, `speed` | RGB(A) bytes, then depth float32 |
| `action` | brain → sim | `truck`, `throttle`, `steer` | none |

## Web

| Item | Value | Source |
|---|---|---|
| Video | `GET /ws/video` binary: `u8 name_len \| name \| JPEG` | |
| Video delivery | latest frame per camera, slow clients skip frames | |
| JPEG quality | 75 | `brain/brain/bridge.py:JPEG_QUALITY` |
| State | `GET /ws/state` JSON | |
| State rate (Hz) | 10 | `brain/brain/app.py:STATE_HZ` |
| Read state | `GET /api/state` | |
| Set goal | `POST /api/trucks/{id}/goal` `{"x","z"}` | |
| Clear goal | `DELETE /api/trucks/{id}/goal` | |

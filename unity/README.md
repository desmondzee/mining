# Unity side

Unity is the world: it loads the Marble quarry, simulates the trucks, renders the cameras
and streams them to the brain. All decisions are made in the brain.

Target: **Unity 6.3 LTS, URP**, Linux ARM64 player (the GB10 is aarch64; the Unity Editor
doesn't run there, so develop on the Mac and cross-build).

## Scripts (`Scripts/`)

| Script | Put it on | Does |
|---|---|---|
| `BrainClient.cs` | one empty GameObject | TCP link to the brain; reconnects automatically |
| `TruckAgent.cs` | each truck (needs a Rigidbody) | applies throttle/steer from the brain; stops if commands stop |
| `SensorCamera.cs` | each truck's front camera, plus one overview camera | streams RGB at 15 fps; truck cameras also send a raycast depth image |
| `BrainProtocol.cs` | — | message framing |

Scene setup:

1. Copy `Scripts/` into the Unity project's `Assets/`.
2. Add a `BrainClient`. Set `bounds` to the quarry extent (xmin, zmin, xmax, zmax) so the website map lines up.
3. For each truck: `TruckAgent` with a unique `truckId` (`truck-0`, `truck-1`, …), a child Camera
   facing forward with `SensorCamera` (`cameraName` = the truck id, `truck` = the TruckAgent).
4. An overview Camera with `SensorCamera` (`cameraName` = `overview`, `truck` empty).

Brain address: `-brain-host` / `-brain-port` command-line args, or `BRAIN_HOST` / `BRAIN_PORT`
env vars, or the inspector. To run the **Editor on the Mac against the brain on watson**:
`brev port-forward watson --port 9000:9000` and leave the host as `127.0.0.1`.

## Marble quarry (Gaussian splats)

Load the `.ply`/`.spz` export with a Gaussian splatting renderer such as
[aras-p/UnityGaussianSplatting](https://github.com/aras-p/UnityGaussianSplatting) (supports
URP via a renderer feature).

**Splats have no colliders.** Trucks need something to drive on, and the depth image is
made by physics raycasts, so it only sees colliders. Until there is collision geometry for
the quarry, use a flat ground plane plus simple box/mesh colliders for walls and rocks.

## Protocol

TCP, each message `u32 header_len | u32 payload_len | header JSON | payload` (little-endian).

Sim → brain:

- `hello` (on connect): `{"type":"hello","trucks":[...],"cameras":[...],"bounds":[xmin,zmin,xmax,zmax]}`
- `frame`: `{"type":"frame","camera":"truck-0","truck":"truck-0","rgb":{"w","h","flip","channels"},"depth":{"w","h"},"pose":{"x","z","yaw"},"speed"}`
  - payload = RGB(A) bytes (`w*h*channels`, row 0 at the top unless `flip`) then depth float32 metres (`w*h*4`)
  - non-truck cameras send `"truck":""` and `"depth":{"w":0,"h":0}`
  - `yaw` is Unity's `eulerAngles.y` (0 = +z, clockwise)

Brain → sim:

- `action`: `{"type":"action","truck":"truck-0","throttle":0..1,"steer":-1..1}` (one per truck frame)

## Running the player on the GB10 (not yet done)

Untested: whether a Unity Linux ARM64 player renders with the GPU, headless, inside a
container on the GB10. Next step: build a minimal scene with these scripts for Linux ARM64
(IL2CPP, with the `com.unity.sdk.linux-arm64` sysroot package), copy it to watson and run it
against the brain. The usual difficulty is that the player wants a display; `-batchmode
-nographics` turns rendering off, so it won't work for cameras.

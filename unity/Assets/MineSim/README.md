# Unity environment importer (Mine Sim)

This folder builds the mine environment (terrain, sites, roads, junctions, road graph) from `data/worlds/<id>/` inside Unity 6 (6000.x). Navigation, driving and compute live elsewhere. This part only gives you the world and a route API.

## Setup
1. Create or open a Unity 6 project (6000.x; the team uses 6.3 LTS + URP). The importer works in URP and Built-in. The cinematic look (`MineSim.Look`) needs URP.
2. Copy or symlink `unity/Assets/MineSim` into the project's `Assets/`. If the project sits at `unity/`, it is already in place.
3. Assemblies. The core assemblies reference no optional packages, so they compile in a clean Unity 6 project. Each integration is a separate small assembly that only compiles when its package is installed (`versionDefines` set a symbol, `defineConstraints` require it). The core calls them through hooks:

   | Assembly (folder) | References | Compiles when | Adds |
   |---|---|---|---|
   | `MineSim.Runtime` (`Runtime/`) | nothing | always | world model, road graph, labels, hotkeys (legacy `Input` behind `ENABLE_LEGACY_INPUT_MANAGER`, or the `MineInput.KeyDownHook`) |
   | `MineSim.Editor` (`Editor/`) | `MineSim.Runtime` | always (Editor) | importer. `MINESIM_GLTFAST` is only a define: the importer uses glTFast through `AssetDatabase` (no glTFast types), so no assembly reference is needed |
   | `MineSim.InputSystem` (`Runtime/Integrations/InputSystem/`) | `MineSim.Runtime`, `Unity.InputSystem` | `com.unity.inputsystem` >= 1.0 | sets `MineInput.KeyDownHook` from `[RuntimeInitializeOnLoadMethod]` |
   | `MineSim.TMP.Editor` (`Editor/Integrations/TMP/`) | `MineSim.Runtime`, `MineSim.Editor`, `Unity.TextMeshPro` | `com.unity.ugui` >= 2.0 or `com.unity.textmeshpro` >= 3.0 | TextMeshPro labels (`MineImporterHooks.CreateTextLabel`). Run *Window > TextMeshPro > Import TMP Essential Resources* once, otherwise labels fall back to `TextMesh` |
   | `MineSim.Splines.Editor` (`Editor/Integrations/Splines/`) | `MineSim.Runtime`, `MineSim.Editor`, `Unity.Splines`, `Unity.Mathematics` | `com.unity.splines` >= 2.0 | `SplineContainer` on every `Road_*` (`MineImporterHooks.AddRoadSpline`) |
   | `MineSim.Look` (`Runtime/Look/`) | `MineSim.Runtime`, `Unity.RenderPipelines.Core.Runtime`, `Unity.RenderPipelines.Universal.Runtime` | `com.unity.render-pipelines.universal` >= 14 | cinematic look runtime (IL2CPP-safe: no editor APIs outside `UNITY_EDITOR`, no reflection) |
   | `MineSim.Look.Editor` (`Editor/Look/`) | `MineSim.Runtime`, `MineSim.Editor`, `MineSim.Look`, URP Core + Universal Runtime | URP installed (Editor) | *Apply Cinematic Look* menu |

   If a package is missing, its assembly is skipped completely, with no errors and no warnings. glTFast (`com.unity.cloud.gltfast` >= 5 or `com.atteneder.gltfast` >= 4) is optional for Marble worlds and **required for scan worlds** (`visual.glb`).

## Import
- **Tools > Mine Sim > Import World...** and pick `data/worlds/<id>/` (for example `data/worlds/default`).
- **Tools > Mine Sim > Reimport Last** rebuilds from the same folder. It replaces any `MineWorld_<id>` in the active scene.
- Generated assets (TerrainData, terrain layers, textures, materials, a copy of `world.json`) go to `Assets/MineSim/Generated/<id>/`. Save the scene afterwards.

Hierarchy:
```
MineWorld_<id>        MineWorldRoot, MineRoadGraph, MineLabelToggle, [MineDesignSpec]
  Terrain             Unity Terrain + TerrainCollider (from heightmap.raw + heightmap_meta.json; navgrid fallback)
  Sites/Site_<id>     MineSiteMarker (siteId, siteType, siteName, tonnes), marker primitive (no collider), Label
  Junctions/Junction_<id>   MineJunction, Label
  Roads/Road_<id>     MineRoad (points, from, to, width_m, grade_pct, one_way), Line (LineRenderer), WP_000.., Label, [SplineContainer]
  Collider            (Marble + glTFast) invisible MeshColliders
  Splat (import manually)   (Marble) MineAssetNote with instructions + the splat_transform already applied
```

## Nav-team API (namespace `MineSim`)
```csharp
var world = FindFirstObjectByType<MineWorldRoot>();
var graph = world.graph;                                   // MineRoadGraph

// Site ids differ per world, so look sites up by type
MineSiteMarker crusher = world.FirstSiteOfType("crusher");
List<MineSiteMarker> ores = MineSiteMarker.FindByType("ore");   // sorted by id

// World-space polyline along roads (Dijkstra by road length; roads are reversed when driven to->from)
List<Vector3> route = graph.GetRoute(ores[0].siteId, crusher.siteId);
List<Vector3> r2 = graph.GetRoute("pit_floor", "crusher", avoidNodes: new[] { "j_ramp_top" });
List<Vector3> r3 = graph.GetRouteFrom(truck.position, "parking");   // starts at the nearest node
List<MineRoadGraph.Leg> legs = graph.GetRouteLegs(a, b);           // which roads, in which direction (null if unreachable)
string nearest = graph.NearestNodeId(pos);

// Raw roads
MineRoad road = world.FindRoad("ramp_main_1");
Vector3[] pts = road.points;        // centreline, ordered from -> to, ~5 m spacing; also child WP_000..
```
- `GetRoute` returns an empty list (and logs a warning) when there's no route. When the start and goal are the same node it returns that node's position.
- A road with `one_way: true` can only be driven from → to.
- The graph builds itself from the children the first time you use it. Call `graph.Rebuild()` after you change roads at runtime.
- Without roads in world.json, routes are empty. Fall back to navgrid A*.

## Runtime hotkeys
`MineLabelToggle` sits on the root: **L** shows/hides labels, **R** shows/hides the road lines. You can change the keys in the inspector. If R collides with your driving controls, set it to `None`.

## Coordinates
- Contract `(x, y, z)` (metres, Y up, X east, Z north) maps to Unity `Vector3(x, y, z)` with no conversion. Ground `[x, z]` maps to `(x, terrainY, z)`.
- Terrain: position = `(origin.x, min_height_m, origin.z)`, size = `(size_m.x, height_range_m, size_m.z)`. Resolution comes from `heightmap_meta.json` and is never hardcoded (513 and 1025 both work). Raw row 0 = south (min z) = Unity terrain row 0, so the importer never flips it.
- Site positions use `pos3` when present. Otherwise the importer samples the terrain.
- Scan worlds: see "Scanned worlds" below.
- Marble collider: the holder gets scale `(-s, s, s)`, rotation `splat_transform.rotation_euler_deg`, and y = `ground_offset`. The `-x` undoes glTFast's handedness flip. If it looks mirrored, make it positive.

## Scanned worlds (`source: "scan"`, e.g. `data/worlds/quarry`)
- Import as usual (**Tools > Mine Sim > Import World...** and pick `data/worlds/quarry`). No Terrain is built: the scan mesh is the ground. Set `MineWorldImporter.BuildTerrainForScanWorlds = true` if you want the heightmap terrain anyway. Sites, junctions, roads and labels come from world.json as before.
- `Scan/Collider`: an invisible ground `MeshCollider`. The depth camera and lidar raycast against it. `collider.glb` is **already in world frame** (contract), so it does *not* get `scan_transform`. `MineGlbMeshReader` reads its positions and indices directly (no glTFast needed, no axis flip, glTF winding kept, which is front-facing up in Unity; it is flipped automatically if most of the area faces down) and saves `Generated/<id>/ScanGround_collider.asset`. The object has an identity transform and sits on layer `MineGround` if that layer exists (add it in *Tags and Layers*, then reimport), otherwise on `Default`. `MineWorldRoot.groundCollider` points to it, and `GroundHeight()` raycasts it when there is no Terrain.
- `Scan/Visual`: `visual.glb` imported by glTFast (copied to `Generated/<id>/visual.glb`, so it ships in the Linux player). The holder applies `scan_transform`, composed with glTFast's axis flip:
  - contract: `p_world = M · p_gltf`, `M = scan_transform.matrix = [s·R | t]` (glTF axes = contract axes = Unity axes)
  - glTFast: `u = F · p_gltf` with `F = diag(-1, 1, 1)` (negates X and re-winds triangles)
  - holder `H` with `p_world = H · u`, so `H = M · F⁻¹ = M · F = [s·R·F | t]`
  - result: `localPosition = t` (not mirrored; it is applied after the flip is undone), rotation and scale decomposed from `s·R·F`. For the quarry (`R = I`, `s = 15.796`) that is rotation identity and `localScale = (-15.796, 15.796, 15.796)`. The negative determinant makes Unity invert culling, which cancels glTFast's re-winding.
  - The importer logs visual vs collider bounds after import. They should overlap (about ±130 m on X, -50..8 m on Y). If X looks mirrored, negate `Visual.localScale.x`.
- `Scan/Surround` (optional, `assets.surround`, e.g. `surround.glb`): a large ground apron with distant hills so the quarry doesn't float in empty space. It is in world frame like `collider.glb` (no `scan_transform`). It is copied to `Generated/<id>/`. The visual is a glTFast import under a holder with scale `(-1, 1, 1)`, which only undoes glTFast's X flip; glTFast's material carries the vertex colours. Without glTFast you get a plain ochre Lit mesh read by `MineGlbMeshReader`. `MineWorldImporter.SurroundCollider` (default **on**) also builds a coarse, vertex-clustered `MeshCollider` (at most `SurroundColliderMaxTris` = 150k tris, saved to `Generated/<id>/Surround_collider.asset`) on the same ground layer, so lidar beyond the pit edge hits ground instead of void. It is stored in `MineWorldRoot.surroundCollider`. Turn the option off for a visual-only surround.
- `visual.glb` uses `KHR_materials_unlit` (baked photogrammetry lighting), so the sun and shadows do not shade it. Fog and post-processing still apply.

## Make it look great (Tools > Mine Sim > Apply Cinematic Look)
1. Import the world, then run **Tools > Mine Sim > Apply Cinematic Look** (needs URP). It is idempotent, so re-run it after a reimport. It sets up:
   - **Sun**: reuses the scene's directional light (others are disabled). Golden hour by default (13° elevation, warm colour, soft shadows).
   - **Sky**: if world.json has `assets.pano`, the panorama is copied to `Generated/<id>/` and used as a `Skybox/Panoramic` material. Otherwise a dusty `Skybox/Procedural` sky. Ambient and reflections come from the sky.
   - **Fog**: exponential, dusty ochre, density scaled to the world size.
   - **Dust**: `MineLook/Atmosphere/DustMotes` + `DustHaze` (soft particles), plus a small mote box riding with every cab camera.
   - **Post**: a global Volume (`Generated/Look/MineLook_PostFX.asset`) with ACES, colour adjustments, white balance, split toning, low-quality bloom, vignette and light film grain. These are cheap uber-pass effects for 15 fps server cameras. A cab-only Gaussian DoF Volume (`MineLook_CabDoF.asset`, layer `cabVolumeLayer` = 2) is **off by default**. Turn it on with `cabDepthOfField`.
   - **Overview camera**: `MineOverviewCamera` goes on the camera named/`cameraName` "overview", else `Camera.main`, else a new "overview" camera. It is a slow orbit/dolly sized from the world, easing between sites.
   - **Roads**: amber emissive lines (URP Unlit + HDR colour through a MaterialPropertyBlock, pulse wave from the pit centre; `MineRoadGlow`).
   - **Labels**: a dark plate with a site-type accent bar behind site labels. `MineMultiViewLabels` turns every label toward each camera as it renders, and hides labels in truck cameras unless `labelsInCabViews` is on.
2. Tweak `Assets/MineSim/Generated/Look/MineLookSettings.asset` (**Tools > Mine Sim > Select Look Settings**). Changes apply live in edit and Play mode. Save the scene afterwards; the scene must contain `MineLook` for anything to happen in the player.
3. URP settings. The menu writes these to every URP asset used by any quality level (`applyUrpAssetSettings`): **HDR on, MSAA 4x, shadow cascades 4**, shadow distance 1400 m, soft shadows, shadowmap 2048, depth texture on (soft particles need it), HDR colour grading. On the GB10 server, 2 cascades halve the shadow cost per camera.

### Team cameras and trucks (SensorCamera / TruckAgent)
- In Play mode, `MineLookController` attaches the rigs automatically every 2 s (settings "Auto attach"): `MineOverviewCamera` on the SensorCamera whose `cameraName` is `overview`, `MineCabCamera` on SensorCameras named `truck-*` (it keeps their current mount pose and adds stabilisation and light vibration only), and `MineDustTrail` on every `TruckAgent`. Names are read with `JsonUtility` (IL2CPP-safe, no reflection, no link.xml needed).
- On a camera with a SensorCamera, the rigs **only move the transform**. FOV, `targetTexture` and rect are never changed. They do turn on URP post-processing and the depth texture for that camera (`enablePostOnAttachedCameras`), and raise the far clip if it is too short.
- Manual use: add `MineCabCamera` to a truck (or **Tools > Mine Sim > Add Cab Camera To Selected**). It drives the truck's SensorCamera child if it has one, otherwise it creates its own camera (hFOV 90) in Play mode. `mountOffset` / `mountEuler` are metres and degrees in the truck frame (the default is the driver seat of a 4.4 m HD465, looking 8° down over the hood). From code: `MineCabCamera.Attach(truckTransform, "Truck 0")`.
- Dust: add `MineDustTrail` to the truck root (or **Add Dust Trail To Selected**). Emission scales with speed (Rigidbody if assigned, else position delta). Adjust `emitterOffset` (behind the rear wheels) and `emitterWidth`.
- Optional preview of the demo layout (overview left, up to 4 cab views right, with tags): **Tools > Mine Sim > Add Split-Screen Preview**. It is off by default because the real layout is the web UI. It draws render-texture cameras into their slots and never changes a SensorCamera's rect.

## Team scene integration (Tools > Mine Sim > Integrate World Into Open Scene)
Open the team's scene (`Assets/Scenes/Quarry.unity` from *Tools > Mining > Build Quarry Scene*, or any scene) and run the menu. Options are under **Tools > Mine Sim > Integration Options...** and are saved in EditorPrefs. The menu is idempotent and never deletes or edits the team's scripts. Save the scene afterwards. It:
1. imports the world (default `data/worlds/quarry`), or reuses the `MineWorld_<id>` already in the scene ("Reimport world" forces a rebuild).
2. **disables** (does not delete) the team's flat `Ground` plane and `Rocks`. You can keep either one.
3. places excavators: one per world.json `equipment` entry (`type` containing "excavator"/"shovel"; `pos`/`pos3`, `heading_deg`, `site`, `length_m`). Without `equipment`, one goes behind each ore site, facing it (option). The model is the first project asset named like `excavator` (`Assets/Models/...` first), else `assets/models/excavator/excavator.glb` copied to `Assets/MineSim/Generated/Models/` (**Tools > Mine Sim > Copy Excavator Model Into Project**, needs glTFast), else a primitive placeholder. Scaling is uniform: the longest horizontal extent equals `excavatorLength` (10 m). The origin is at the bottom centre. A static `BoxCollider` makes it visible to lidar and depth. Everything sits under the `MineEquipment` scene root.
4. sets `BrainClient.bounds` from world.json `bounds` (found through SerializedObject: Vector4, Rect or float[4]). It sets `BrainClient.obstacles` to `MineEquipment` when the rocks are off, so excavators appear on the brain map.
5. moves every `TruckAgent` (`truckId` = `truck-N`) to its world.json `spawn` + `heading_deg` (or to the parking `slots[i]` when "Prefer parking slots" is on, or when the truck has no spawn), on the ground. It then adds **`MineGroundFollow`** (below).
6. pedestrians, in one of these modes: `RoadCrossings` (default: each `Path_N` is rebuilt as a small loop across a haul road), `ProjectOntoGround` (keep x/z, drop onto the ground), `Disable`, or `Leave`. Waypoints are densified to at most 5 m on the ground, because `PedestrianWalker` only probes ±3 m around its current height. Paths off the world or with no ground under them are disabled, and `BrainClient` then doesn't report them.
7. frames the overview camera (SensorCamera `overview`): top-down over the world bounds, with a far clip that covers the pit floor.
8. bakes **`MineGoals`** and adds a **disabled** `MineGoalCycler` on the `MineIntegration` root.
9. checks that every `LidarSensor.mask` / `SensorCamera.depthMask` includes the ground layer, and prints a checklist to the Console.

### MineGroundFollow (uneven ground for the team's kinematic trucks)
`TruckAgent` moves a kinematic Rigidbody in x/z at a constant y, with `Euler(0, yaw, 0)`. `MineGroundFollow` runs **before** it (`[DefaultExecutionOrder(-100)]`). In every FixedUpdate it raycasts the world ground (`MineWorldRoot.groundCollider` / Terrain via `Collider.Raycast`, so no trucks, pedestrians or props get hit; the surround collider is used when the scan misses; with no world it falls back to `Physics.RaycastNonAlloc` with `groundMask`, skipping its own colliders and any collider with a Rigidbody). It samples the four wheel points (derived from the root BoxCollider) and sets `rb.position.y` directly. TruckAgent then reads `body.position` and calls `MovePosition(...)` with a horizontal step, so the corrected height carries through the physics step. Yaw is never touched. With no hit, the truck keeps its last height. Tilt is applied smoothly to the **model child** only (pivot = root origin). With `tiltSensors`, the sensor mounts (Camera / SensorCamera / LidarSensor children) are tilted too. The integration turns this on, because a level depth camera or lidar sees an up-ramp as a wall in the policy's depth band. `MineCabCamera` follows the tilted frame.

## Driving policy integration
The brain's policy (brain/brain/policy.py) steers each truck straight at its **goal** `(x, z)` and stops within **4 m** (`ARRIVE_RADIUS`). It avoids obstacles with the depth band (stop at 6 m, slow at 15 m). Throttle/steer come back over TCP, exactly as in the flat scene. Goals are set on the brain's web API (spec.md "Web"):
```
POST http://127.0.0.1:8080/api/trucks/{truck-id}/goal   {"x": -159.9, "z": 22.8}
DELETE http://127.0.0.1:8080/api/trucks/{truck-id}/goal
GET  http://127.0.0.1:8080/api/state                     (truck pose/status: idle | driving | avoiding | arrived)
```
Where the goal coordinates come from:
- world.json `sites[]`: `load_point` (where a truck is loaded; ore faces) and `dump_point` (where it tips; crusher, dump), as `[x, z]` / `[x, y, z]` / `{pos: ...}`. Without them, the site `pos`. Parking `slots[]` give the ready-line poses.
- In Unity: `MineGoals.Instance.FirstOfType("crusher").dumpPoint`, `MineGoals.Instance.OfType("ore")[i].Goal` (baked into the scene, so it works in the player), or `MineSiteMarker.FindByType(...)`.
- Road-following routes: `MineWorldRoot.graph.GetRouteFrom(truckPos, siteId)`. Feed the points one at a time and advance before the 4 m arrive radius, or the policy will stop at each one.
- From Python, read `data/worlds/<id>/world.json` directly. Same frame: contract (x, z) = Unity (x, z) = brain (x, z).

`MineGoalCycler` (optional, **off by default**) is a demo of autonomous hauling driven by the team's policy. Enable the component, or start the player with `-mine-haul` / `MINE_HAUL=1`. For every TruckAgent it loops: ore `load_point` (round-robin over ore sites) → wait `dwellSeconds` → crusher `dump_point` → wait → repeat. By default it follows the road graph, posting one goal every ~20 m. Intermediate goals advance at 9 m (above the 4 m arrive radius, so the truck never stops between them). A leg ends at 4 m + 1 m slack. A goal is re-posted every 5 s, so a restarted brain picks it up again. A stuck truck skips a waypoint after 25 s without progress. The base URL defaults to `http://127.0.0.1:8080`; override it with the inspector, `-brain-api http://host:8080` or `BRAIN_API`. Transport: HTTP/1.1 over `TcpClient` on a worker thread, the same API BrainClient already uses under IL2CPP. This path ignores Unity's "Allow downloads over HTTP" setting. `useUnityWebRequest` switches to UnityWebRequest (compiled only when the `com.unity.modules.unitywebrequest` module is present, define `MINESIM_UWR`), which needs *Player Settings > Allow downloads over HTTP = Always allowed* for `http://`. Goals are DELETEd when the component is disabled.

## Files
- `Assets/MineSim/Editor/MineWorldImporter.cs`: menu and builder. `MineImporterHooks.cs`: TMP/Splines hooks. `MineGlbMeshReader.cs`: GLB geometry reader for scan colliders
- `Assets/MineSim/Editor/Integrations/{TMP,Splines}/`, `Assets/MineSim/Runtime/Integrations/InputSystem/`: optional package assemblies
- `Assets/MineSim/Runtime/Look/`: `MineLookSettings`, `MineLookController`, `MineLookUtil` (+ `MineLookPost`), `MineOverviewCamera`, `MineCabCamera`, `MineDustTrail`, `MineAmbientDust`, `MineRoadGlow`, `MineMultiViewLabels`, `MineSplitScreenLayout`
- `Assets/MineSim/Editor/Look/MineLookMenu.cs`: *Apply Cinematic Look* and helper menus
- `Assets/MineSim/Editor/MineQuarryIntegration.cs`: *Integrate World Into Open Scene*, *Integration Options...*, *Copy Excavator Model Into Project*
- `Assets/MineSim/Runtime/MineGroundFollow.cs`, `MineGoals.cs`, `MineGoalCycler.cs`: ground following for TruckAgent, goal coordinates, optional haul-cycle goal poster
- `Assets/MineSim/Runtime/`: `MineWorldData` (world.json model) + `MiniJson`, `MineWorldRoot`, `MineSiteMarker`, `MineJunction`, `MineRoad`, `MineRoadGraph`, `MineLabel`, `MineBillboard`, `MineLabelToggle`, `MineInput`, `MineDesignSpec`, `MineAssetNote`

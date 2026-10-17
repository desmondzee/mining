// Tools/Mine Sim/Integrate World Into Open Scene
// Drops an imported Mine Sim world (e.g. the scanned quarry) into the team's scene built by QuarrySceneBuilder
// (Tools/Mining/Build Quarry Scene), or into any scene, WITHOUT touching the team's scripts:
//   world import, flat Ground/Rocks disabled, BrainClient bounds/obstacles, trucks at their world.json spawn/slot poses +
//   MineGroundFollow, excavators from "equipment", pedestrian paths moved onto the ground, overview camera framing,
//   MineGoals (+ optional MineGoalCycler, disabled). Idempotent: re-running replaces what it created.
// The team's types (TruckAgent, BrainClient, SensorCamera, LidarSensor, PedestrianWalker) live in Assembly-CSharp, which an
// asmdef cannot reference, so they are found by type name and edited through SerializedObject.
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;
using Object = UnityEngine.Object;

namespace MineSim.EditorTools
{
    public static class MineQuarryIntegration
    {
        public enum PedestrianMode
        {
            /// <summary>Rebuild each path as a loop across a haul road (pedestrians cross the trucks' roads).</summary>
            RoadCrossings,
            /// <summary>Keep the path's x/z, drop waypoints onto the ground (densified every 5 m); paths off the world are disabled.</summary>
            ProjectOntoGround,
            /// <summary>Deactivate all pedestrians (BrainClient then reports none).</summary>
            Disable,
            /// <summary>Do nothing.</summary>
            Leave,
        }

        [Serializable]
        public class Options
        {
            public string worldDir = "";
            public bool reimportWorld = false;
            public bool keepFlatGround = false;
            public bool keepRocks = false;
            public bool setBrainBounds = true;
            public bool moveTrucks = true;
            public bool preferParkingSlots = false;
            public bool addGroundFollow = true;
            public bool tiltSensors = true;
            public bool placeExcavators = true;
            public bool excavatorsAtOreSitesWhenNoEquipment = true;
            public float excavatorLength = 10f;
            public float excavatorYawOffset = 0f;
            public float excavatorBackoff = 18f;
            public bool excavatorsAsBrainObstacles = true;
            public PedestrianMode pedestrians = PedestrianMode.RoadCrossings;
            public bool frameOverviewCamera = true;
            public bool addGoals = true;
            public bool addGoalCycler = true;
        }

        const string PrefKey = "MineSim.QuarryIntegration.Options";
        const string GeneratedModels = "Assets/MineSim/Generated/Models";
        const string EquipmentRootName = "MineEquipment";
        const string IntegrationRootName = "MineIntegration";

        public static Options LoadOptions()
        {
            var o = new Options();
            string json = EditorPrefs.GetString(PrefKey, "");
            if (!string.IsNullOrEmpty(json))
            {
                try { EditorJsonUtility.FromJsonOverwrite(json, o); }
                catch (Exception) { o = new Options(); }
            }
            return o;
        }

        public static void SaveOptions(Options o) => EditorPrefs.SetString(PrefKey, EditorJsonUtility.ToJson(o));

        static string RepoRoot => Path.GetFullPath(Path.Combine(Application.dataPath, "..", ".."));

        // ------------------------------------------------------------------ menus
        [MenuItem("Tools/Mine Sim/Integrate World Into Open Scene", false, 40)]
        static void IntegrateMenu() => Run(LoadOptions());

        [MenuItem("Tools/Mine Sim/Integration Options...", false, 41)]
        static void OptionsMenu() => MineQuarryIntegrationWindow.Open();

        [MenuItem("Tools/Mine Sim/Copy Excavator Model Into Project", false, 42)]
        static void CopyExcavatorMenu()
        {
            string p = CopyExcavatorModel(true);
            if (p != null) EditorGUIUtility.PingObject(AssetDatabase.LoadAssetAtPath<Object>(p));
        }

        /// <summary>Copies &lt;repo&gt;/assets/models/excavator/excavator.glb into Assets/MineSim/Generated/Models/ (needs glTFast to import).</summary>
        public static string CopyExcavatorModel(bool verbose)
        {
#if MINESIM_GLTFAST
            string src = Path.Combine(RepoRoot, "assets", "models", "excavator", "excavator.glb");
            if (!File.Exists(src))
            {
                if (verbose) Debug.LogWarning("[MineSim] excavator model not found at " + src);
                return null;
            }
            EnsureFolder(GeneratedModels);
            string dst = GeneratedModels + "/excavator.glb";
            var si = new FileInfo(src);
            var di = new FileInfo(Path.GetFullPath(dst));
            if (!di.Exists || di.Length != si.Length)
            {
                File.Copy(src, di.FullName, true);
                AssetDatabase.ImportAsset(dst, ImportAssetOptions.ForceUpdate);
            }
            if (verbose) Debug.Log("[MineSim] excavator model at " + dst + " (CC-BY-4.0, Dioramass design; see assets/models/CREDITS.md)");
            return dst;
#else
            if (verbose) Debug.LogWarning("[MineSim] glTFast (com.unity.cloud.gltfast) is not installed: cannot import excavator.glb. A placeholder excavator will be used.");
            return null;
#endif
        }

        // ------------------------------------------------------------------ main
        public static void Run(Options o)
        {
            var log = new StringBuilder();
            var scene = SceneManager.GetActiveScene();
            if (!scene.IsValid())
            {
                Debug.LogError("[MineSim] no open scene");
                return;
            }
            Undo.IncrementCurrentGroup();
            int undoGroup = Undo.GetCurrentGroup();
            Undo.SetCurrentGroupName("Integrate Mine World");

            try
            {
                // 1. world
                var world = Object.FindFirstObjectByType<MineWorldRoot>();
                string dir = ResolveWorldDir(o, world);
                if (dir == null) { Debug.LogWarning("[MineSim] integration cancelled: no world folder"); return; }
                bool sameWorld = world != null && !string.IsNullOrEmpty(world.worldDir) &&
                                 Path.GetFullPath(world.worldDir).TrimEnd('/', '\\') == Path.GetFullPath(dir).TrimEnd('/', '\\');
                if (world == null || o.reimportWorld || !sameWorld)
                {
                    var rootGo = MineWorldImporter.Import(dir);
                    world = rootGo != null ? rootGo.GetComponent<MineWorldRoot>() : null;
                    Line(log, world != null, "World imported from " + dir);
                }
                else Line(log, true, "World '" + world.worldId + "' already in scene (reused; enable 'Reimport world' to rebuild)");
                if (world == null) { Debug.LogError("[MineSim] world import failed; integration stopped"); return; }
                o.worldDir = dir;
                SaveOptions(o);

                string json = ReadWorldJson(world, dir);
                var data = MineWorldData.Parse(json);
                Physics.SyncTransforms();
                Func<float, float, float> gy = (x, z) => world.GroundHeight(x, z);

                var tmpGo = new GameObject("tmp") { hideFlags = HideFlags.HideAndDontSave };
                var goalsTmp = tmpGo.AddComponent<MineGoals>();
                goalsTmp.Bake(json, gy);
                Vector4 bounds = goalsTmp.bounds;

                // 2. team's flat ground + rocks
                var groundPlane = FindRoot(scene, "Ground");
                if (groundPlane != null)
                {
                    SetActive(groundPlane, o.keepFlatGround);
                    Line(log, true, "Flat 'Ground' plane " + (o.keepFlatGround ? "kept" : "disabled (not deleted)"));
                }
                var rocks = FindRoot(scene, "Rocks");
                if (rocks != null)
                {
                    SetActive(rocks, o.keepRocks);
                    Line(log, true, "Random 'Rocks' " + (o.keepRocks ? "kept" : "disabled (not deleted)"));
                }

                // 3. excavators (before BrainClient so they can be its obstacles)
                Transform equipmentRoot = null;
                if (o.placeExcavators) equipmentRoot = PlaceExcavators(o, scene, world, data, goalsTmp, log);
                else RemoveRoot(scene, EquipmentRootName);

                // 4. BrainClient
                var brain = FindByTypeName("BrainClient").FirstOrDefault();
                if (brain == null) Line(log, null, "No BrainClient in scene (bounds not set)");
                else
                {
                    var so = new SerializedObject(brain);
                    if (o.setBrainBounds)
                    {
                        string how = SetBounds(so.FindProperty("bounds"), bounds);
                        Line(log, how != null, how != null
                            ? $"BrainClient.bounds = [{bounds.x}, {bounds.y}, {bounds.z}, {bounds.w}] ({how}); spec default was [-100,-100,100,100]"
                            : "BrainClient has no 'bounds' field of a known type (Vector4/Rect/float[4])");
                    }
                    var obs = so.FindProperty("obstacles");
                    if (obs != null && obs.propertyType == SerializedPropertyType.ObjectReference)
                    {
                        bool rocksActive = rocks != null && rocks.activeSelf;
                        var cur = obs.objectReferenceValue as Transform;
                        if (o.excavatorsAsBrainObstacles && equipmentRoot != null && (!rocksActive || cur == null))
                        {
                            obs.objectReferenceValue = equipmentRoot;
                            Line(log, true, "BrainClient.obstacles -> " + EquipmentRootName + " (excavators appear on the brain map)");
                        }
                        else if (!rocksActive && cur != null && rocks != null && cur == rocks.transform)
                        {
                            obs.objectReferenceValue = null;
                            Line(log, true, "BrainClient.obstacles cleared (rocks disabled)");
                        }
                    }
                    so.ApplyModifiedProperties();
                }

                // 5. trucks
                var agents = FindByTypeName("TruckAgent").OrderBy(a => GetString(a, "truckId") ?? a.name, StringComparer.Ordinal).ToList();
                if (agents.Count == 0) Line(log, null, "No TruckAgent in scene");
                var parking = goalsTmp.FirstOfType("parking");
                for (int i = 0; i < agents.Count; i++)
                {
                    var a = agents[i];
                    string id = GetString(a, "truckId") ?? a.name;
                    if (o.moveTrucks) PlaceTruck(o, world, data, parking, a, id, i, log);
                    if (o.addGroundFollow)
                    {
                        var gf = a.GetComponent<MineGroundFollow>();
                        if (gf == null) gf = Undo.AddComponent<MineGroundFollow>(a.gameObject);
                        Undo.RecordObject(gf, "Ground follow");
                        gf.tiltSensors = o.tiltSensors;
                        gf.useWorldGround = true;
                        gf.autoFromCollider = true;
                        EditorUtility.SetDirty(gf);
                        PrefabUtility.RecordPrefabInstancePropertyModifications(gf);
                    }
                }
                if (o.addGroundFollow && agents.Count > 0)
                    Line(log, true, $"MineGroundFollow on {agents.Count} truck(s) (tilt sensors: {o.tiltSensors})");

                // 6. pedestrians
                HandlePedestrians(o, world, bounds, log);

                // 7. overview camera
                if (o.frameOverviewCamera) FrameOverview(world, bounds, log);

                // 8. goals (+ cycler)
                var integ = FindRoot(scene, IntegrationRootName);
                if (o.addGoals || o.addGoalCycler)
                {
                    if (integ == null)
                    {
                        integ = new GameObject(IntegrationRootName);
                        Undo.RegisterCreatedObjectUndo(integ, "Integration root");
                    }
                    var goals = integ.GetComponent<MineGoals>();
                    if (goals == null) goals = Undo.AddComponent<MineGoals>(integ);
                    Undo.RecordObject(goals, "Bake goals");
                    goals.Bake(json, gy);
                    EditorUtility.SetDirty(goals);
                    int lp = goals.sites.Count(s => s.hasLoadPoint), dp = goals.sites.Count(s => s.hasDumpPoint);
                    Line(log, true, $"MineGoals baked: {goals.sites.Count} sites ({lp} load_point, {dp} dump_point; others fall back to the site position), {goals.equipment.Count} equipment");
                    if (o.addGoalCycler)
                    {
                        var cyc = integ.GetComponent<MineGoalCycler>();
                        if (cyc == null)
                        {
                            cyc = Undo.AddComponent<MineGoalCycler>(integ);
                            cyc.enabled = false;   // OFF by default
                        }
                        Line(log, true, "MineGoalCycler present (" + (cyc.enabled ? "ENABLED" : "disabled; tick it or run the player with -mine-haul") + ")");
                    }
                }
                Object.DestroyImmediate(goalsTmp.gameObject);

                // 9. compatibility checks
                CheckSensors(world, log);

                EditorSceneManager.MarkSceneDirty(scene);
                Undo.CollapseUndoOperations(undoGroup);
                Debug.Log("[MineSim] Integration checklist (save the scene!):\n" + log);
            }
            catch (Exception e)
            {
                Debug.LogException(e);
                Debug.LogError("[MineSim] integration failed. Partial checklist:\n" + log);
            }
            finally
            {
                foreach (var g in Resources.FindObjectsOfTypeAll<MineGoals>())
                    if (g != null && g.gameObject.name == "tmp" && g.gameObject.hideFlags == HideFlags.HideAndDontSave) Object.DestroyImmediate(g.gameObject);
            }
        }

        // ------------------------------------------------------------------ world
        static string ResolveWorldDir(Options o, MineWorldRoot world)
        {
            if (!string.IsNullOrEmpty(o.worldDir) && File.Exists(Path.Combine(o.worldDir, "world.json"))) return o.worldDir;
            if (world != null && !string.IsNullOrEmpty(world.worldDir) && File.Exists(Path.Combine(world.worldDir, "world.json"))) return world.worldDir;
            string quarry = Path.Combine(RepoRoot, "data", "worlds", "quarry");
            if (File.Exists(Path.Combine(quarry, "world.json"))) return quarry;
            string picked = EditorUtility.OpenFolderPanel("Pick data/worlds/<id>/", Path.Combine(RepoRoot, "data", "worlds"), "");
            return string.IsNullOrEmpty(picked) ? null : picked;
        }

        static string ReadWorldJson(MineWorldRoot world, string dir)
        {
            string p = Path.Combine(dir, "world.json");
            if (File.Exists(p)) return File.ReadAllText(p);
            string gen = Path.GetFullPath("Assets/MineSim/Generated/" + world.worldId + "/world.json");
            return File.ReadAllText(gen);
        }

        // ------------------------------------------------------------------ trucks
        static void PlaceTruck(Options o, MineWorldRoot world, MineWorldData data, MineGoals.SiteGoal parking, MonoBehaviour agent, string id, int index, StringBuilder log)
        {
            Vector3 pos;
            float heading;
            string source;
            var info = data.trucks.FirstOrDefault(t => t.id == id);
            bool haveSlot = parking != null && index < parking.slots.Count;
            if (haveSlot && (o.preferParkingSlots || info == null))
            {
                var s = parking.slots[index];
                pos = s.position;
                heading = s.hasHeading ? s.headingDeg : (info != null ? info.headingDeg : 0f);
                source = "parking slot " + s.id;
            }
            else if (info != null)
            {
                pos = new Vector3(info.spawn.x, 0f, info.spawn.y);
                heading = info.headingDeg;
                source = "world.json spawn";
            }
            else if (parking != null)
            {
                float side = (index - 1) * 8f;
                pos = parking.position + new Vector3(side, 0f, 0f);
                heading = 0f;
                source = "parking site (offset, no spawn/slot for this id)";
            }
            else
            {
                Line(log, false, $"{id}: no spawn, slot or parking site in world.json; truck left in place");
                return;
            }
            pos.y = world.GroundHeight(pos.x, pos.z);
            var tr = agent.transform;
            Undo.RecordObject(tr, "Place truck");
            tr.SetPositionAndRotation(pos, Quaternion.Euler(0f, heading, 0f));
            PrefabUtility.RecordPrefabInstancePropertyModifications(tr);
            Line(log, true, $"{id} -> ({pos.x:0.0}, {pos.y:0.0}, {pos.z:0.0}) heading {heading:0.#} deg [{source}]");
        }

        // ------------------------------------------------------------------ excavators
        static Transform PlaceExcavators(Options o, Scene scene, MineWorldRoot world, MineWorldData data, MineGoals goals, StringBuilder log)
        {
            RemoveRoot(scene, EquipmentRootName);
            var entries = goals.equipment.Where(e => string.IsNullOrEmpty(e.type) || e.type.ToLowerInvariant().Contains("excavat") || e.type.ToLowerInvariant().Contains("shovel")).ToList();

            bool fallback = false;
            if (entries.Count == 0 && o.excavatorsAtOreSitesWhenNoEquipment)
            {
                fallback = true;
                foreach (var s in goals.OfType("ore"))
                {
                    Vector3 dir = RoadDirFromSite(world, data, s);
                    var e = new MineGoals.Equipment
                    {
                        id = "excavator_" + s.siteId,
                        type = "excavator",
                        siteId = s.siteId,
                        position = s.position - dir * o.excavatorBackoff,
                        hasHeading = true,
                        headingDeg = Mathf.Atan2(dir.x, dir.z) * Mathf.Rad2Deg,
                    };
                    entries.Add(e);
                }
            }
            if (entries.Count == 0)
            {
                Line(log, null, "No 'equipment' excavators in world.json (and fallback off): none placed");
                return null;
            }

            var model = FindExcavatorModel();
            var root = new GameObject(EquipmentRootName);
            Undo.RegisterCreatedObjectUndo(root, "Equipment");
            foreach (var e in entries)
            {
                float len = e.lengthM > 0.5f ? e.lengthM : o.excavatorLength;
                var go = BuildExcavator(model, e.id, len);
                go.transform.SetParent(root.transform, false);
                float heading;
                if (e.hasHeading) heading = e.headingDeg;
                else
                {
                    var site = string.IsNullOrEmpty(e.siteId) ? null : goals.Find(e.siteId);
                    Vector3 target = site != null ? site.Goal : e.position + Vector3.forward;
                    Vector3 d = target - e.position;
                    heading = d.sqrMagnitude > 0.01f ? Mathf.Atan2(d.x, d.z) * Mathf.Rad2Deg : 0f;
                }
                Vector3 p = e.position;
                p.y = world.GroundHeight(p.x, p.z) - 0.15f;
                go.transform.SetPositionAndRotation(p, Quaternion.Euler(0f, heading + o.excavatorYawOffset, 0f));
            }
            Line(log, true, $"{entries.Count} excavator(s) placed{(fallback ? " (fallback: behind each ore site, no 'equipment' in world.json)" : " from 'equipment'")}, " +
                            $"model {(model != null ? AssetDatabase.GetAssetPath(model) : "PLACEHOLDER (no excavator model found; run 'Copy Excavator Model Into Project' with glTFast installed)")}, " +
                            $"length {o.excavatorLength} m, static BoxCollider (seen by lidar/depth)");
            return root.transform;
        }

        static Vector3 RoadDirFromSite(MineWorldRoot world, MineWorldData data, MineGoals.SiteGoal s)
        {
            foreach (var r in data.roads)
            {
                if (r.points == null || r.points.Length < 2) continue;
                Vector3 a, b;
                if (r.from == s.siteId) { a = r.points[0]; b = r.points[Mathf.Min(3, r.points.Length - 1)]; }
                else if (r.to == s.siteId) { a = r.points[r.points.Length - 1]; b = r.points[Mathf.Max(0, r.points.Length - 4)]; }
                else continue;
                var d = new Vector3(b.x - a.x, 0f, b.z - a.z);
                if (d.sqrMagnitude > 0.01f) return d.normalized;
            }
            var c = new Vector3(world.origin.x + world.sizeM.x * 0.5f - s.position.x, 0f, world.origin.y + world.sizeM.y * 0.5f - s.position.z);
            return c.sqrMagnitude > 0.01f ? c.normalized : Vector3.forward;
        }

        static GameObject FindExcavatorModel()
        {
            var paths = AssetDatabase.FindAssets("excavator t:GameObject")
                .Select(AssetDatabase.GUIDToAssetPath)
                .Where(p => !string.IsNullOrEmpty(p) && Path.GetFileNameWithoutExtension(p).ToLowerInvariant().Contains("excavator")
                            && !Path.GetFileNameWithoutExtension(p).ToLowerInvariant().Contains("source"))
                .OrderBy(p => p.StartsWith("Assets/Models/") ? 0 : p.StartsWith(GeneratedModels) ? 2 : 1)
                .ThenBy(p => p, StringComparer.Ordinal)
                .ToList();
            if (paths.Count == 0)
            {
                string copied = CopyExcavatorModel(false);
                if (copied != null) paths.Add(copied);
            }
            foreach (var p in paths)
            {
                var go = AssetDatabase.LoadAssetAtPath<GameObject>(p);
                if (go != null && go.GetComponentInChildren<Renderer>(true) != null) return go;
            }
            return null;
        }

        static GameObject BuildExcavator(GameObject model, string id, float length)
        {
            var go = new GameObject("Excavator_" + id);
            GameObject vis;
            if (model != null)
            {
                vis = PrefabUtility.InstantiatePrefab(model, go.transform) as GameObject;
                if (vis == null) vis = Object.Instantiate(model, go.transform, false);
            }
            else vis = Placeholder(go.transform);
            vis.name = "model";
            foreach (var c in vis.GetComponentsInChildren<Collider>(true)) Object.DestroyImmediate(c);

            // uniform scale: longest horizontal extent = length; then origin at bottom centre
            var b = LocalBounds(go.transform, vis);
            float horiz = Mathf.Max(b.size.x, b.size.z);
            if (horiz > 1e-4f) vis.transform.localScale *= length / horiz;
            b = LocalBounds(go.transform, vis);
            vis.transform.localPosition -= new Vector3(b.center.x, b.min.y, b.center.z);
            b = LocalBounds(go.transform, vis);

            var box = go.AddComponent<BoxCollider>();
            box.center = b.center;
            box.size = b.size;
            go.isStatic = true;
            return go;
        }

        static GameObject Placeholder(Transform parent)
        {
            var root = new GameObject("placeholder");
            root.transform.SetParent(parent, false);
            void Part(PrimitiveType t, Vector3 pos, Vector3 scale, Vector3 euler)
            {
                var p = GameObject.CreatePrimitive(t);
                Object.DestroyImmediate(p.GetComponent<Collider>());
                p.transform.SetParent(root.transform, false);
                p.transform.localPosition = pos;
                p.transform.localScale = scale;
                p.transform.localEulerAngles = euler;
            }
            Part(PrimitiveType.Cube, new Vector3(0f, 0.5f, 0f), new Vector3(3.4f, 1f, 4.6f), Vector3.zero);           // tracks
            Part(PrimitiveType.Cube, new Vector3(0f, 1.8f, -0.4f), new Vector3(3f, 1.6f, 3.6f), Vector3.zero);        // house
            Part(PrimitiveType.Cube, new Vector3(-0.8f, 2.9f, 0.9f), new Vector3(1f, 1f, 1.2f), Vector3.zero);        // cab
            Part(PrimitiveType.Cube, new Vector3(0.5f, 3.4f, 2.6f), new Vector3(0.5f, 0.5f, 4.2f), new Vector3(-30f, 0f, 0f)); // boom
            Part(PrimitiveType.Cube, new Vector3(0.5f, 2.6f, 4.9f), new Vector3(0.4f, 0.4f, 2.6f), new Vector3(60f, 0f, 0f));  // stick
            Part(PrimitiveType.Cube, new Vector3(0.5f, 1.2f, 5.4f), new Vector3(1.4f, 0.9f, 0.9f), Vector3.zero);     // bucket
            return root;
        }

        static Bounds LocalBounds(Transform space, GameObject go)
        {
            var b = new Bounds();
            bool any = false;
            foreach (var mf in go.GetComponentsInChildren<MeshFilter>(true))
            {
                if (mf.sharedMesh == null) continue;
                Encapsulate(space, mf.transform, mf.sharedMesh.bounds, ref b, ref any);
            }
            foreach (var sr in go.GetComponentsInChildren<SkinnedMeshRenderer>(true))
                if (sr.sharedMesh != null) Encapsulate(space, sr.transform, sr.sharedMesh.bounds, ref b, ref any);
            return any ? b : new Bounds(Vector3.zero, Vector3.one);
        }

        static void Encapsulate(Transform space, Transform t, Bounds mb, ref Bounds b, ref bool any)
        {
            var m = space.worldToLocalMatrix * t.localToWorldMatrix;
            for (int i = 0; i < 8; i++)
            {
                var c = mb.center + Vector3.Scale(mb.extents, new Vector3((i & 1) == 0 ? -1 : 1, (i & 2) == 0 ? -1 : 1, (i & 4) == 0 ? -1 : 1));
                var p = m.MultiplyPoint3x4(c);
                if (!any) { b = new Bounds(p, Vector3.zero); any = true; }
                else b.Encapsulate(p);
            }
        }

        // ------------------------------------------------------------------ pedestrians
        static void HandlePedestrians(Options o, MineWorldRoot world, Vector4 bounds, StringBuilder log)
        {
            var walkers = FindByTypeName("PedestrianWalker");
            if (walkers.Count == 0) { Line(log, null, "No PedestrianWalker in scene"); return; }
            if (o.pedestrians == PedestrianMode.Leave) { Line(log, null, "Pedestrians left untouched (option)"); return; }

            var byPath = new Dictionary<Transform, List<MonoBehaviour>>();
            var noPath = new List<MonoBehaviour>();
            foreach (var w in walkers)
            {
                var p = new SerializedObject(w).FindProperty("path");
                var t = p != null ? p.objectReferenceValue as Transform : null;
                if (t == null) { noPath.Add(w); continue; }
                if (!byPath.TryGetValue(t, out var l)) byPath[t] = l = new List<MonoBehaviour>();
                l.Add(w);
            }

            if (o.pedestrians == PedestrianMode.Disable)
            {
                foreach (var w in walkers) SetActive(w.gameObject, false);
                Line(log, true, $"{walkers.Count} pedestrians disabled (BrainClient reports none)");
                return;
            }

            var roads = world.GetComponentsInChildren<MineRoad>(true)
                .Where(r => r.points != null && r.points.Length >= 4 && r.Length > 40f)
                .OrderBy(r => r.roadId, StringComparer.Ordinal).ToList();
            var mode = o.pedestrians;
            if (mode == PedestrianMode.RoadCrossings && roads.Count == 0)
            {
                mode = PedestrianMode.ProjectOntoGround;
                Line(log, null, "No roads in this world: pedestrians projected onto the ground instead of road crossings");
            }

            var paths = byPath.Keys.OrderBy(t => t.name, StringComparer.Ordinal).ToList();
            int ok = 0, disabled = 0;
            for (int i = 0; i < paths.Count; i++)
            {
                var path = paths[i];
                List<Vector3> pts;
                bool loop;
                if (mode == PedestrianMode.RoadCrossings)
                {
                    var road = roads[(i * roads.Count / Mathf.Max(1, paths.Count) + i) % roads.Count];
                    pts = CrossingLoop(road, world);
                    loop = true;
                }
                else
                {
                    pts = new List<Vector3>();
                    for (int k = 0; k < path.childCount; k++) pts.Add(path.GetChild(k).position);
                    loop = byPath[path].Count > 0 && GetBool(byPath[path][0], "loop", pts.Count > 2);
                }
                pts = Densify(pts, loop, 5f);
                bool valid = pts.Count >= 2;
                for (int k = 0; valid && k < pts.Count; k++)
                {
                    var p = pts[k];
                    if (p.x < bounds.x || p.x > bounds.z || p.z < bounds.y || p.z > bounds.w) { valid = false; break; }
                    if (!GroundHit(world, p.x, p.z, out float y)) { valid = false; break; }
                    p.y = y;
                    pts[k] = p;
                }
                if (!valid)
                {
                    foreach (var w in byPath[path]) SetActive(w.gameObject, false);
                    SetActive(path.gameObject, false);
                    disabled += byPath[path].Count;
                    continue;
                }

                // rebuild waypoints (children of the team's Path_N; the Transform itself is kept so walker.path stays valid)
                SetActive(path.gameObject, true);
                for (int k = path.childCount - 1; k >= 0; k--) Undo.DestroyObjectImmediate(path.GetChild(k).gameObject);
                for (int k = 0; k < pts.Count; k++)
                {
                    var wp = new GameObject("WP_" + k).transform;
                    Undo.RegisterCreatedObjectUndo(wp.gameObject, "Waypoint");
                    wp.SetParent(path, false);
                    wp.position = pts[k];
                }
                foreach (var w in byPath[path])
                {
                    SetActive(w.gameObject, true);
                    var so = new SerializedObject(w);
                    var lp = so.FindProperty("loop");
                    if (lp != null && lp.propertyType == SerializedPropertyType.Boolean) lp.boolValue = loop;
                    so.ApplyModifiedProperties();
                    Undo.RecordObject(w.transform, "Pedestrian");
                    w.transform.position = pts[0];   // PedestrianWalker.PlaceOnPath re-places it on Awake
                }
                ok += byPath[path].Count;
            }
            foreach (var w in noPath) SetActive(w.gameObject, false);
            Line(log, ok > 0, $"Pedestrians: {ok} on {paths.Count} path(s) [{mode}]" +
                              (disabled > 0 ? $", {disabled} disabled (path off the world/ground)" : "") +
                              (noPath.Count > 0 ? $", {noPath.Count} without a path disabled" : "") +
                              ". Waypoints every <=5 m on the ground (PedestrianWalker only probes +-3 m).");
        }

        static List<Vector3> CrossingLoop(MineRoad road, MineWorldRoot world)
        {
            var pts = road.points;
            int mid = pts.Length / 2;
            Vector3 a = pts[Mathf.Max(0, mid - 1)], b = pts[Mathf.Min(pts.Length - 1, mid + 1)];
            var t = new Vector3(b.x - a.x, 0f, b.z - a.z);
            t = t.sqrMagnitude > 1e-4f ? t.normalized : Vector3.forward;
            var n = new Vector3(t.z, 0f, -t.x);
            float half = road.widthM * 0.5f + 3f;
            Vector3 c = pts[mid];
            const float gap = 4f;
            return new List<Vector3>
            {
                c - n * half, c + n * half, c + n * half + t * gap, c - n * half + t * gap,
            };
        }

        static List<Vector3> Densify(List<Vector3> pts, bool loop, float step)
        {
            var r = new List<Vector3>();
            int n = pts.Count;
            if (n < 2) return new List<Vector3>(pts);
            int segs = loop ? n : n - 1;
            for (int i = 0; i < segs; i++)
            {
                Vector3 a = pts[i], b = pts[(i + 1) % n];
                float d = new Vector2(b.x - a.x, b.z - a.z).magnitude;
                int k = Mathf.Max(1, Mathf.CeilToInt(d / step));
                for (int j = 0; j < k; j++) r.Add(Vector3.Lerp(a, b, (float)j / k));
            }
            if (!loop) r.Add(pts[n - 1]);
            return r;
        }

        static bool GroundHit(MineWorldRoot world, float x, float z, out float y)
        {
            y = 0f;
            var ray = new Ray(new Vector3(x, 10000f, z), Vector3.down);
            if (world.groundCollider != null && world.groundCollider.Raycast(ray, out var h, 20000f)) { y = h.point.y; return true; }
            if (world.terrain != null) { y = world.GroundHeight(x, z); return true; }
            return false;
        }

        // ------------------------------------------------------------------ overview camera
        static void FrameOverview(MineWorldRoot world, Vector4 bounds, StringBuilder log)
        {
            Camera cam = null;
            foreach (var s in FindByTypeName("SensorCamera"))
                if (GetString(s, "cameraName") == "overview") { cam = s.GetComponent<Camera>(); break; }
            if (cam == null)
            {
                var go = GameObject.Find("Overview");
                if (go != null) cam = go.GetComponent<Camera>();
            }
            if (cam == null) { Line(log, null, "No overview camera (SensorCamera 'overview' / 'Overview') to frame"); return; }

            float minY = -100f, maxY = 50f;
            if (world.groundCollider != null) { minY = world.groundCollider.bounds.min.y; maxY = world.groundCollider.bounds.max.y; }
            else if (world.terrain != null)
            {
                minY = world.terrain.transform.position.y;
                maxY = minY + world.terrain.terrainData.size.y;
            }
            var center = new Vector3((bounds.x + bounds.z) * 0.5f, 0f, (bounds.y + bounds.w) * 0.5f);
            float halfX = (bounds.z - bounds.x) * 0.5f, halfZ = (bounds.w - bounds.y) * 0.5f;
            float aspect = 16f / 9f;
            var sc = FindByTypeName("SensorCamera").FirstOrDefault(s => s.GetComponent<Camera>() == cam);
            if (sc != null)
            {
                var so = new SerializedObject(sc);
                var w = so.FindProperty("width");
                var h = so.FindProperty("height");
                if (w != null && h != null && h.intValue > 0) aspect = (float)w.intValue / h.intValue;
            }
            Undo.RecordObject(cam, "Overview camera");
            Undo.RecordObject(cam.transform, "Overview camera");
            float height = maxY + 200f;
            if (cam.orthographic)
            {
                cam.orthographicSize = Mathf.Max(halfZ, halfX / aspect) * 1.03f;
                cam.transform.SetPositionAndRotation(new Vector3(center.x, height, center.z), Quaternion.Euler(90f, 0f, 0f));
            }
            else
            {
                float vfov = cam.fieldOfView * Mathf.Deg2Rad;
                float need = Mathf.Max(halfZ, halfX / aspect) * 1.05f / Mathf.Tan(vfov * 0.5f);
                height = maxY + need;
                cam.transform.SetPositionAndRotation(new Vector3(center.x, height, center.z), Quaternion.Euler(90f, 0f, 0f));
            }
            cam.nearClipPlane = Mathf.Max(cam.nearClipPlane, 0.3f);
            cam.farClipPlane = Mathf.Max(cam.farClipPlane, height - minY + 100f);
            EditorUtility.SetDirty(cam);
            Line(log, true, $"Overview camera framed: top-down at y={height:0}, {(cam.orthographic ? "ortho size " + cam.orthographicSize.ToString("0") : "fov " + cam.fieldOfView.ToString("0"))}, far {cam.farClipPlane:0} " +
                            "(MineOverviewCamera from 'Apply Cinematic Look' will orbit it in Play mode if auto-attach is on)");
        }

        // ------------------------------------------------------------------ checks
        static void CheckSensors(MineWorldRoot world, StringBuilder log)
        {
            var groundCols = new List<Collider>();
            if (world.groundCollider != null) groundCols.Add(world.groundCollider);
            if (world.surroundCollider != null) groundCols.Add(world.surroundCollider);
            if (world.terrain != null && world.terrain.GetComponent<TerrainCollider>() != null) groundCols.Add(world.terrain.GetComponent<TerrainCollider>());
            if (groundCols.Count == 0) { Line(log, false, "World has no ground collider: lidar/depth will see nothing"); return; }
            int bad = 0, total = 0;
            foreach (var pair in new[] { ("LidarSensor", "mask"), ("SensorCamera", "depthMask") })
                foreach (var s in FindByTypeName(pair.Item1))
                {
                    var p = new SerializedObject(s).FindProperty(pair.Item2);
                    if (p == null) continue;
                    total++;
                    foreach (var g in groundCols)
                        if ((p.intValue & (1 << g.gameObject.layer)) == 0)
                        {
                            bad++;
                            Line(log, false, $"{s.name}.{pair.Item1}.{pair.Item2} excludes layer '{LayerMask.LayerToName(g.gameObject.layer)}' of {g.name}: it won't see the ground");
                            break;
                        }
                }
            if (bad == 0 && total > 0)
                Line(log, true, $"{total} sensor mask(s) include the ground layer '{LayerMask.LayerToName(groundCols[0].gameObject.layer)}'");
            if (!Physics.queriesHitBackfaces)
                Line(log, null, "Physics.queriesHitBackfaces is off (default): rays only hit the scan's upward faces, as intended");
        }

        // ------------------------------------------------------------------ helpers
        static void Line(StringBuilder sb, bool? ok, string text)
        {
            sb.Append(ok == null ? "  [-] " : ok.Value ? "  [x] " : "  [!] ").AppendLine(text);
        }

        public static List<MonoBehaviour> FindByTypeName(string typeName)
        {
            var r = new List<MonoBehaviour>();
            foreach (var mb in Object.FindObjectsByType<MonoBehaviour>(FindObjectsInactive.Include, FindObjectsSortMode.InstanceID))
                if (mb != null && mb.GetType().Name == typeName && !EditorUtility.IsPersistent(mb)) r.Add(mb);
            return r;
        }

        static string GetString(Object o, string field)
        {
            var p = new SerializedObject(o).FindProperty(field);
            return p != null && p.propertyType == SerializedPropertyType.String ? p.stringValue : null;
        }

        static bool GetBool(Object o, string field, bool def)
        {
            var p = new SerializedObject(o).FindProperty(field);
            return p != null && p.propertyType == SerializedPropertyType.Boolean ? p.boolValue : def;
        }

        /// <summary>Writes [xmin, zmin, xmax, zmax] into a Vector4 / Rect / float[4] / Vector2+Vector2 property. Returns how, or null.</summary>
        static string SetBounds(SerializedProperty p, Vector4 b)
        {
            if (p == null) return null;
            switch (p.propertyType)
            {
                case SerializedPropertyType.Vector4:
                    p.vector4Value = b;
                    return "Vector4 (xmin, zmin, xmax, zmax)";
                case SerializedPropertyType.Rect:
                    p.rectValue = Rect.MinMaxRect(b.x, b.y, b.z, b.w);
                    return "Rect min/max";
                case SerializedPropertyType.Quaternion:
                    p.quaternionValue = new Quaternion(b.x, b.y, b.z, b.w);
                    return "Quaternion components";
            }
            if (p.isArray && p.propertyType == SerializedPropertyType.Generic)
            {
                p.arraySize = 4;
                for (int i = 0; i < 4; i++)
                {
                    var e = p.GetArrayElementAtIndex(i);
                    if (e.propertyType == SerializedPropertyType.Float) e.floatValue = b[i];
                    else if (e.propertyType == SerializedPropertyType.Integer) e.intValue = Mathf.RoundToInt(b[i]);
                    else return null;
                }
                return "array[4]";
            }
            return null;
        }

        static GameObject FindRoot(Scene scene, string name)
        {
            foreach (var go in scene.GetRootGameObjects()) if (go.name == name) return go;
            return null;
        }

        static void RemoveRoot(Scene scene, string name)
        {
            foreach (var go in scene.GetRootGameObjects()) if (go.name == name) Undo.DestroyObjectImmediate(go);
        }

        static void SetActive(GameObject go, bool active)
        {
            if (go == null || go.activeSelf == active) return;
            Undo.RecordObject(go, active ? "Enable" : "Disable");
            go.SetActive(active);
        }

        static void EnsureFolder(string path)
        {
            var parts = path.Split('/');
            string cur = parts[0];
            for (int i = 1; i < parts.Length; i++)
            {
                string next = cur + "/" + parts[i];
                if (!AssetDatabase.IsValidFolder(next)) AssetDatabase.CreateFolder(cur, parts[i]);
                cur = next;
            }
        }
    }

    /// <summary>Options window for the integration (Tools/Mine Sim/Integration Options...).</summary>
    public class MineQuarryIntegrationWindow : EditorWindow
    {
        MineQuarryIntegration.Options o;
        Vector2 scroll;

        public static void Open()
        {
            var w = GetWindow<MineQuarryIntegrationWindow>(true, "Mine Sim Integration");
            w.minSize = new Vector2(420, 560);
            w.o = MineQuarryIntegration.LoadOptions();
        }

        void OnGUI()
        {
            if (o == null) o = MineQuarryIntegration.LoadOptions();
            scroll = EditorGUILayout.BeginScrollView(scroll);
            EditorGUILayout.LabelField("World", EditorStyles.boldLabel);
            using (new EditorGUILayout.HorizontalScope())
            {
                o.worldDir = EditorGUILayout.TextField("World folder", o.worldDir);
                if (GUILayout.Button("...", GUILayout.Width(28)))
                {
                    string p = EditorUtility.OpenFolderPanel("Pick data/worlds/<id>/", o.worldDir, "");
                    if (!string.IsNullOrEmpty(p)) o.worldDir = p;
                }
            }
            o.reimportWorld = EditorGUILayout.Toggle("Reimport world", o.reimportWorld);
            o.keepFlatGround = EditorGUILayout.Toggle("Keep flat 'Ground' plane", o.keepFlatGround);
            o.keepRocks = EditorGUILayout.Toggle("Keep random 'Rocks'", o.keepRocks);

            EditorGUILayout.Space();
            EditorGUILayout.LabelField("Brain + trucks", EditorStyles.boldLabel);
            o.setBrainBounds = EditorGUILayout.Toggle("Set BrainClient bounds", o.setBrainBounds);
            o.moveTrucks = EditorGUILayout.Toggle("Move trucks to spawn", o.moveTrucks);
            o.preferParkingSlots = EditorGUILayout.Toggle("Prefer parking slots", o.preferParkingSlots);
            o.addGroundFollow = EditorGUILayout.Toggle("Add MineGroundFollow", o.addGroundFollow);
            o.tiltSensors = EditorGUILayout.Toggle("Tilt sensors with the ground", o.tiltSensors);

            EditorGUILayout.Space();
            EditorGUILayout.LabelField("Equipment", EditorStyles.boldLabel);
            o.placeExcavators = EditorGUILayout.Toggle("Place excavators", o.placeExcavators);
            o.excavatorsAtOreSitesWhenNoEquipment = EditorGUILayout.Toggle("Fallback: one per ore site", o.excavatorsAtOreSitesWhenNoEquipment);
            o.excavatorLength = EditorGUILayout.FloatField("Excavator length (m)", o.excavatorLength);
            o.excavatorYawOffset = EditorGUILayout.FloatField("Model yaw offset (deg)", o.excavatorYawOffset);
            o.excavatorBackoff = EditorGUILayout.FloatField("Fallback backoff (m)", o.excavatorBackoff);
            o.excavatorsAsBrainObstacles = EditorGUILayout.Toggle("Excavators = brain obstacles", o.excavatorsAsBrainObstacles);

            EditorGUILayout.Space();
            EditorGUILayout.LabelField("Scene", EditorStyles.boldLabel);
            o.pedestrians = (MineQuarryIntegration.PedestrianMode)EditorGUILayout.EnumPopup("Pedestrians", o.pedestrians);
            o.frameOverviewCamera = EditorGUILayout.Toggle("Frame overview camera", o.frameOverviewCamera);
            o.addGoals = EditorGUILayout.Toggle("Bake MineGoals", o.addGoals);
            o.addGoalCycler = EditorGUILayout.Toggle("Add MineGoalCycler (disabled)", o.addGoalCycler);
            EditorGUILayout.EndScrollView();

            EditorGUILayout.Space();
            using (new EditorGUILayout.HorizontalScope())
            {
                if (GUILayout.Button("Save")) MineQuarryIntegration.SaveOptions(o);
                if (GUILayout.Button("Reset")) o = new MineQuarryIntegration.Options();
                if (GUILayout.Button("Integrate now", GUILayout.Height(28)))
                {
                    MineQuarryIntegration.SaveOptions(o);
                    MineQuarryIntegration.Run(o);
                }
            }
        }
    }
}

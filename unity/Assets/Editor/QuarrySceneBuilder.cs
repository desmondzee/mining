using System.IO;
using System.Linq;
using UnityEditor;
using UnityEditor.Animations;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.Rendering;
using UnityEngine.Rendering.Universal;

public static class QuarrySceneBuilder
{
    const string ModelDir = "Assets/Models/Trucks";
    const string PrefabDir = "Assets/Prefabs";
    const string ScenePath = "Assets/Scenes/Quarry.unity";
    const string PipelinePath = "Assets/Settings/URP.asset";
    const string RendererPath = "Assets/Settings/URP_Renderer.asset";
    const float HalfSize = 100f;
    const int TruckCount = 3;
    const float SensorOffset = 0.2f;
    const float CameraHeightFraction = 0.8f;
    const float LidarHeightFraction = 0.6f;
    const int RockCount = 30;
    const float PathClearance = 4f;
    const float PedestrianHeight = 1.8f;
    const float PedestrianRadius = 0.3f;
    const int PedestriansPerPath = 3;
    const float MinWalkSpeed = 1.2f;
    const float MaxWalkSpeed = 1.6f;
    const string PeopleDir = "Assets/Models/People";
    const string ControllerDir = "Assets/Generated/People";

    static readonly string[] LodNames = { "hipoly", "midpoly", "lowpoly", "ultralowpoly" };
    static readonly float[] LodHeights = { 0.25f, 0.08f, 0.03f, 0.005f };

    const string MaterialDir = "Assets/Generated/Materials";

    static readonly Color GroundColour = new Color(0.55f, 0.47f, 0.37f);
    static readonly Color RockColour = new Color(0.36f, 0.34f, 0.33f);

    struct TruckModel
    {
        public string file;
        public float length;
        public Vector3 rotation;
    }

    static readonly TruckModel Komatsu = new TruckModel { file = "komatsu_hd465", length = 9.35f, rotation = new Vector3(-90, 0, 0) };
    static readonly TruckModel Cat = new TruckModel { file = "cat_785c", length = 11.02f, rotation = new Vector3(-90, -90, 0) };

    static readonly Vector3[][] PedestrianPaths =
    {
        new[] { new Vector3(-30, 0, -40), new Vector3(30, 0, -40) },
        new[] { new Vector3(-10, 0, -10), new Vector3(10, 0, -10), new Vector3(10, 0, 10), new Vector3(-10, 0, 10) },
        new[] { new Vector3(-50, 0, 20), new Vector3(-10, 0, 60) },
        new[] { new Vector3(40, 0, -20), new Vector3(40, 0, 40) },
    };

    [MenuItem("Tools/Mining/Build Quarry Scene (Komatsu HD465)")]
    public static void BuildKomatsu() => Build(Komatsu);

    [MenuItem("Tools/Mining/Build Quarry Scene (CAT 785C)")]
    public static void BuildCat() => Build(Cat);

    static void Build(TruckModel model)
    {
        EnsurePipeline();
        ReimportForPipeline();
        var truckPrefab = BuildTruckPrefab(model);
        var scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);

        var sun = new GameObject("Sun").AddComponent<Light>();
        sun.type = LightType.Directional;
        sun.shadows = LightShadows.Soft;
        sun.transform.rotation = Quaternion.Euler(50, -30, 0);

        var ground = GameObject.CreatePrimitive(PrimitiveType.Plane);
        ground.name = "Ground";
        ground.transform.localScale = new Vector3(HalfSize / 5, 1, HalfSize / 5);
        ground.GetComponent<Renderer>().sharedMaterial = LitMaterial("Ground", GroundColour);

        var rocks = AddRocks();
        AddPedestrians();
        var brain = new GameObject("BrainClient").AddComponent<BrainClient>();
        brain.truckModel = model.file;
        brain.obstacles = rocks;

        for (int i = 0; i < TruckCount; i++)
        {
            var truck = (GameObject)PrefabUtility.InstantiatePrefab(truckPrefab);
            truck.name = $"truck-{i}";
            truck.transform.position = new Vector3(-15 + 15 * i, 0, -70);
            var agent = truck.GetComponent<TruckAgent>();
            agent.truckId = truck.name;
            var cam = truck.GetComponentInChildren<SensorCamera>();
            cam.cameraName = truck.name;
            PrefabUtility.RecordPrefabInstancePropertyModifications(agent);
            PrefabUtility.RecordPrefabInstancePropertyModifications(cam);
        }

        var overview = new GameObject("Overview").AddComponent<Camera>();
        overview.orthographic = true;
        overview.orthographicSize = HalfSize;
        overview.farClipPlane = 400;
        overview.transform.SetPositionAndRotation(new Vector3(0, 200, 0), Quaternion.Euler(90, 0, 0));
        overview.gameObject.AddComponent<SensorCamera>().cameraName = "overview";

        Directory.CreateDirectory(Path.GetDirectoryName(ScenePath));
        EditorSceneManager.SaveScene(scene, ScenePath);
        EditorBuildSettings.scenes = new[] { new EditorBuildSettingsScene(ScenePath, true) };
    }

    static void EnsurePipeline()
    {
        if (GraphicsSettings.defaultRenderPipeline is UniversalRenderPipelineAsset) return;
        Directory.CreateDirectory(Path.GetDirectoryName(PipelinePath));
        var rendererData = ScriptableObject.CreateInstance<UniversalRendererData>();
        AssetDatabase.CreateAsset(rendererData, RendererPath);
        var pipeline = UniversalRenderPipelineAsset.Create(rendererData);
        AssetDatabase.CreateAsset(pipeline, PipelinePath);
        GraphicsSettings.defaultRenderPipeline = pipeline;
        QualitySettings.renderPipeline = pipeline;
        AssetDatabase.SaveAssets();
    }

    static void ReimportForPipeline()
    {
        foreach (var file in new[] { Komatsu.file, Cat.file })
        {
            var path = $"{ModelDir}/{file}.glb";
            var materials = AssetDatabase.LoadAllAssetsAtPath(path).OfType<Material>();
            if (materials.Any(m => !m.shader.name.StartsWith("Shader Graphs/")))
                AssetDatabase.ImportAsset(path, ImportAssetOptions.ForceUpdate);
        }
    }

    static GameObject BuildTruckPrefab(TruckModel model)
    {
        var glb = AssetDatabase.LoadAssetAtPath<GameObject>($"{ModelDir}/{model.file}.glb");
        var root = new GameObject(model.file);
        root.AddComponent<Rigidbody>().isKinematic = true;
        var agent = root.AddComponent<TruckAgent>();

        var mesh = (GameObject)PrefabUtility.InstantiatePrefab(glb, root.transform);
        mesh.transform.localRotation = Quaternion.Euler(model.rotation);
        mesh.transform.localScale *= model.length / RendererBounds(mesh).size.z;
        var b = RendererBounds(mesh);
        mesh.transform.localPosition = -new Vector3(b.center.x, b.min.y, b.center.z);
        b = RendererBounds(mesh);

        var box = root.AddComponent<BoxCollider>();
        box.center = b.center;
        box.size = b.size;

        float front = b.max.z + SensorOffset;
        var cam = new GameObject("FrontCamera", typeof(Camera), typeof(SensorCamera));
        cam.transform.SetParent(root.transform, false);
        cam.transform.localPosition = new Vector3(0, b.size.y * CameraHeightFraction, front);
        cam.GetComponent<SensorCamera>().truck = agent;

        var lidar = new GameObject("Lidar", typeof(LidarSensor));
        lidar.transform.SetParent(root.transform, false);
        lidar.transform.localPosition = new Vector3(0, b.size.y * LidarHeightFraction, front);
        lidar.GetComponent<LidarSensor>().truck = agent;

        Directory.CreateDirectory(PrefabDir);
        var prefab = PrefabUtility.SaveAsPrefabAsset(root, $"{PrefabDir}/{model.file}.prefab");
        Object.DestroyImmediate(root);
        return prefab;
    }

    static Transform AddRocks()
    {
        var parent = new GameObject("Rocks").transform;
        var material = LitMaterial("Rock", RockColour);
        var rng = new System.Random(0);
        for (int i = 0; i < RockCount; i++)
        {
            var p = new Vector3(Rand(rng) * HalfSize * 0.9f, 0, Rand(rng) * HalfSize * 0.9f);
            float size = 2 + (float)rng.NextDouble() * 4;
            if (Vector3.Distance(p, new Vector3(0, 0, -70)) < 25 || NearPath(p, size / 2 + PathClearance)) continue;
            var rock = GameObject.CreatePrimitive(PrimitiveType.Cube);
            rock.name = $"Rock_{i:00}";
            rock.transform.SetParent(parent);
            rock.transform.SetPositionAndRotation(p + Vector3.up * size / 2, Quaternion.Euler(0, (float)rng.NextDouble() * 360, 0));
            rock.transform.localScale = Vector3.one * size;
            rock.GetComponent<Renderer>().sharedMaterial = material;
        }
        return parent;
    }

    static void AddPedestrians()
    {
        var parent = new GameObject("Pedestrians").transform;
        var avatars = LoadAvatars();
        var rng = new System.Random(1);
        int index = 0;
        for (int i = 0; i < PedestrianPaths.Length; i++)
        {
            var path = new GameObject($"Path_{i}").transform;
            path.SetParent(parent);
            foreach (var point in PedestrianPaths[i])
            {
                var waypoint = new GameObject($"WP_{path.childCount}").transform;
                waypoint.SetParent(path);
                waypoint.position = point;
            }

            for (int k = 0; k < PedestriansPerPath; k++, index++)
            {
                var person = new GameObject($"Pedestrian_{index:00}", typeof(CapsuleCollider), typeof(Rigidbody), typeof(PedestrianWalker));
                person.transform.SetParent(parent);
                person.transform.position = PedestrianPaths[i][0];
                var capsule = person.GetComponent<CapsuleCollider>();
                capsule.height = PedestrianHeight;
                capsule.radius = PedestrianRadius;
                capsule.center = Vector3.up * PedestrianHeight / 2;
                var walker = person.GetComponent<PedestrianWalker>();
                walker.path = path;
                walker.loop = PedestrianPaths[i].Length > 2;
                walker.startFraction = (float)k / PedestriansPerPath;
                walker.speed = Mathf.Lerp(MinWalkSpeed, MaxWalkSpeed, (float)rng.NextDouble());

                walker.PlaceOnPath();
                if (avatars.Length > 0) AddAvatar(person, avatars[index % avatars.Length]);
                else AddPlaceholder(person);
            }
        }
    }

    static GameObject[] LoadAvatars() =>
        AssetDatabase.FindAssets("t:Model", new[] { PeopleDir })
            .Select(AssetDatabase.GUIDToAssetPath)
            .Where(p => p.Contains("/Export/"))
            .OrderBy(p => p)
            .Select(AssetDatabase.LoadAssetAtPath<GameObject>)
            .ToArray();

    static void AddAvatar(GameObject person, GameObject avatar)
    {
        var model = (GameObject)PrefabUtility.InstantiatePrefab(avatar, person.transform);
        var animator = model.GetComponent<Animator>();
        animator.runtimeAnimatorController = WalkController(avatar.name.Contains("Female") ? "f" : "m");
        animator.applyRootMotion = false;
        animator.cullingMode = AnimatorCullingMode.AlwaysAnimate;

        var renderers = model.GetComponentsInChildren<Renderer>(true);
        var levels = Enumerable.Range(0, LodNames.Length)
            .Select(i => renderers.Where(r => LodLevel(r.name) == i).ToArray())
            .Where(level => level.Length > 0)
            .ToArray();
        var lods = levels.Select((level, i) => new LOD(i == levels.Length - 1 ? LodHeights[^1] : LodHeights[i], level)).ToArray();
        model.AddComponent<LODGroup>().SetLODs(lods);
    }

    static int LodLevel(string name)
    {
        name = name.ToLower();
        for (int i = LodNames.Length - 1; i >= 0; i--)
            if (name.Contains(LodNames[i])) return i;
        return 0;
    }

    static RuntimeAnimatorController WalkController(string prefix)
    {
        var path = $"{ControllerDir}/Walk_{prefix}.controller";
        var existing = AssetDatabase.LoadAssetAtPath<RuntimeAnimatorController>(path);
        if (existing != null) return existing;
        var clip = AssetDatabase.LoadAllAssetsAtPath($"{PeopleDir}/Animations/{prefix}_walk_neutral_01.max.fbx")
            .OfType<AnimationClip>()
            .First(c => !c.name.StartsWith("__preview__"));
        Directory.CreateDirectory(ControllerDir);
        return AnimatorController.CreateAnimatorControllerAtPathWithClip(path, clip);
    }

    static void AddPlaceholder(GameObject person)
    {
        var placeholder = GameObject.CreatePrimitive(PrimitiveType.Capsule);
        placeholder.name = "Placeholder";
        Object.DestroyImmediate(placeholder.GetComponent<Collider>());
        placeholder.transform.SetParent(person.transform, false);
        placeholder.transform.localPosition = Vector3.up * PedestrianHeight / 2;
        placeholder.transform.localScale = new Vector3(PedestrianRadius * 2, PedestrianHeight / 2, PedestrianRadius * 2);
    }

    static bool NearPath(Vector3 p, float distance)
    {
        foreach (var path in PedestrianPaths)
            for (int i = 0; i < path.Length; i++)
            {
                var a = path[i];
                var b = path[(i + 1) % path.Length];
                var t = Mathf.Clamp01(Vector3.Dot(p - a, b - a) / (b - a).sqrMagnitude);
                if (Vector3.Distance(p, a + t * (b - a)) < distance) return true;
            }
        return false;
    }

    static Material LitMaterial(string name, Color colour)
    {
        Directory.CreateDirectory(MaterialDir);
        var path = $"{MaterialDir}/{name}.mat";
        var material = AssetDatabase.LoadAssetAtPath<Material>(path);
        if (material == null)
        {
            material = new Material(Shader.Find("Universal Render Pipeline/Lit"));
            AssetDatabase.CreateAsset(material, path);
        }
        material.SetColor("_BaseColor", colour);
        EditorUtility.SetDirty(material);
        return material;
    }

    static float Rand(System.Random rng) => (float)rng.NextDouble() * 2 - 1;

    static Bounds RendererBounds(GameObject go)
    {
        var renderers = go.GetComponentsInChildren<Renderer>();
        var b = renderers[0].bounds;
        foreach (var r in renderers) b.Encapsulate(r.bounds);
        return b;
    }
}

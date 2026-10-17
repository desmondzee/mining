using System;
using System.Collections.Generic;
using System.IO;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.Rendering;
using URP = UnityEngine.Rendering.Universal;
using Object = UnityEngine.Object;

namespace MineSim.EditorTools
{
    /// <summary>
    /// Tools/Mine Sim/Apply Cinematic Look: idempotent setup of lighting, sky, fog, dust, URP post, cameras,
    /// road glow and label plates in the open scene. Generated assets go to Assets/MineSim/Generated/Look/
    /// (panorama skybox: Assets/MineSim/Generated/&lt;id&gt;/).
    /// </summary>
    public static class MineLookMenu
    {
        private const string Gen = "Assets/MineSim/Generated";
        private const string Look = Gen + "/Look";
        private const string RootName = "MineLook";

        [MenuItem("Tools/Mine Sim/Apply Cinematic Look", false, 100)]
        public static void ApplyLook()
        {
            try
            {
                EditorUtility.DisplayProgressBar("Mine Sim", "Cinematic look: assets", 0.1f);
                EnsureFolder(Look);
                var world = Object.FindFirstObjectByType<MineWorldRoot>();

                var s = LoadOrCreate<MineLookSettings>(Look + "/MineLookSettings.asset");
                var dot = EnsureSoftDot(Look + "/MineLook_SoftDot.png");
                s.dustMaterial = EnsureParticleMat(Look + "/MineLook_DustMotes.mat", dot, 2f, 0.3f, 1.5f);
                s.hazeMaterial = EnsureParticleMat(Look + "/MineLook_DustHaze.mat", dot, 25f, 20f, 90f);
                s.trailMaterial = EnsureParticleMat(Look + "/MineLook_DustTrail.mat", dot, 2.5f, 0.5f, 3f);
                s.roadGlowMaterial = EnsureRoadGlowMat(Look + "/MineLook_RoadGlow.mat", s);
                s.skyboxMaterial = EnsureSky(world, s);
                s.postProfile = EnsureProfile(Look + "/MineLook_PostFX.asset", true);
                s.cabDofProfile = EnsureProfile(Look + "/MineLook_CabDoF.asset", false);
                EditorUtility.SetDirty(s);
                if (s.applyUrpAssetSettings) ApplyPipelineSettings(s);

                EditorUtility.DisplayProgressBar("Mine Sim", "Cinematic look: scene", 0.5f);
                var ctrl = Object.FindFirstObjectByType<MineLookController>();
                if (ctrl == null)
                {
                    var go = new GameObject(RootName);
                    Undo.RegisterCreatedObjectUndo(go, "Apply Cinematic Look");
                    ctrl = go.AddComponent<MineLookController>();
                }
                var root = ctrl.transform;
                ctrl.settings = s;
                ctrl.world = world;
                ctrl.sun = EnsureSun(ctrl.sun, root);

                var atmo = Child(root, "Atmosphere");
                EnsureDust(atmo, "DustMotes", MineAmbientDust.Mode.WorldMotes);
                EnsureDust(atmo, "DustHaze", MineAmbientDust.Mode.WorldHaze);

                ctrl.postVolume = EnsureVolume(root, "PostFX Volume", 0);
                ctrl.cabDofVolume = EnsureVolume(root, "CabDoF Volume", Mathf.Clamp(s.cabVolumeLayer, 0, 31));

                GetOrAdd<MineRoadGlow>(root.gameObject);
                GetOrAdd<MineMultiViewLabels>(root.gameObject);

                MineLookSettings.Active = s;
                ctrl.Apply();

                var ov = EnsureOverview(s);
                if (ov != null)
                {
                    var cam = ov.GetComponent<Camera>();
                    MineLookUtil.WorldBounds(world, out Vector3 _, out float extent, out Vector2 _);
                    MineLookUtil.ConfigureCamera(cam, s, false, extent * 3f);
                    ov.world = world;
                    ov.Recompute();
                    ov.SnapToStart();
                    EditorUtility.SetDirty(ov);
                    EditorUtility.SetDirty(ov.transform);
                }

                EditorUtility.DisplayProgressBar("Mine Sim", "Cinematic look: roads and labels", 0.8f);
                int roads = StyleRoads(world, s);
                int plates = StyleLabels(world, s);

                EditorUtility.SetDirty(ctrl);
                EditorSceneManager.MarkSceneDirty(ctrl.gameObject.scene);
                AssetDatabase.SaveAssets();
                Selection.activeObject = s;
                Debug.Log("[MineSim] Cinematic look applied: sun, " + (s.skyboxMaterial != null ? s.skyboxMaterial.name : "no sky") +
                          ", fog, dust, URP post (" + AssetDatabase.GetAssetPath(s.postProfile) + "), overview camera " +
                          (ov != null ? "'" + ov.name + "'" : "none") + ", " + roads + " glowing roads, " + plates +
                          " label plates. Tweak " + AssetDatabase.GetAssetPath(s) + " (live in Play mode). Save the scene.");
            }
            catch (Exception e)
            {
                Debug.LogException(e);
                EditorUtility.DisplayDialog("Mine Sim", "Apply Cinematic Look failed:\n" + e.Message + "\n(see Console)", "OK");
            }
            finally
            {
                EditorUtility.ClearProgressBar();
            }
        }

        [MenuItem("Tools/Mine Sim/Add Cab Camera To Selected", false, 101)]
        private static void AddCab()
        {
            int n = 0;
            foreach (var t in Selection.transforms)
            {
                var cam = t.GetComponent<Camera>();
                var c = t.GetComponent<MineCabCamera>();
                if (c == null) c = Undo.AddComponent<MineCabCamera>(t.gameObject);
                if (c == null) continue;
                c.target = cam != null ? t.parent : t;
                n++;
            }
            Debug.Log("[MineSim] MineCabCamera added to " + n + " object(s). On a truck without a camera it creates one in Play mode.");
        }

        [MenuItem("Tools/Mine Sim/Add Dust Trail To Selected", false, 102)]
        private static void AddTrail()
        {
            int n = 0;
            foreach (var t in Selection.transforms)
                if (t.GetComponent<MineDustTrail>() == null) { Undo.AddComponent<MineDustTrail>(t.gameObject); n++; }
            Debug.Log("[MineSim] MineDustTrail added to " + n + " object(s).");
        }

        [MenuItem("Tools/Mine Sim/Add Cab Camera To Selected", true)]
        [MenuItem("Tools/Mine Sim/Add Dust Trail To Selected", true)]
        private static bool HasSelection() => Selection.transforms.Length > 0;

        [MenuItem("Tools/Mine Sim/Add Split-Screen Preview", false, 103)]
        private static void AddSplit()
        {
            var ctrl = Object.FindFirstObjectByType<MineLookController>();
            if (ctrl == null)
            {
                EditorUtility.DisplayDialog("Mine Sim", "Run Tools/Mine Sim/Apply Cinematic Look first.", "OK");
                return;
            }
            var l = GetOrAdd<MineSplitScreenLayout>(ctrl.gameObject);
            var ov = Object.FindFirstObjectByType<MineOverviewCamera>();
            if (ov != null) l.overview = ov.GetComponent<Camera>();
            EditorSceneManager.MarkSceneDirty(ctrl.gameObject.scene);
            Selection.activeObject = l;
        }

        [MenuItem("Tools/Mine Sim/Select Look Settings", false, 104)]
        private static void SelectSettings()
        {
            var s = AssetDatabase.LoadAssetAtPath<MineLookSettings>(Look + "/MineLookSettings.asset");
            if (s == null) EditorUtility.DisplayDialog("Mine Sim", "Run Tools/Mine Sim/Apply Cinematic Look first.", "OK");
            else Selection.activeObject = s;
        }

        // ------------------------------------------------------------------ assets
        private static T LoadOrCreate<T>(string path) where T : ScriptableObject
        {
            var a = AssetDatabase.LoadAssetAtPath<T>(path);
            if (a != null) return a;
            a = ScriptableObject.CreateInstance<T>();
            AssetDatabase.CreateAsset(a, path);
            return a;
        }

        private static Texture2D EnsureSoftDot(string path)
        {
            var t = AssetDatabase.LoadAssetAtPath<Texture2D>(path);
            if (t != null) return t;
            var tex = MineLookUtil.CreateSoftDotTexture(128);
            File.WriteAllBytes(Path.GetFullPath(path), tex.EncodeToPNG());
            Object.DestroyImmediate(tex);
            AssetDatabase.ImportAsset(path, ImportAssetOptions.ForceUpdate);
            var imp = AssetImporter.GetAtPath(path) as TextureImporter;
            if (imp != null)
            {
                imp.textureType = TextureImporterType.Default;
                imp.alphaSource = TextureImporterAlphaSource.FromInput;
                imp.alphaIsTransparency = true;
                imp.wrapMode = TextureWrapMode.Clamp;
                imp.mipmapEnabled = true;
                imp.sRGBTexture = true;
                imp.SaveAndReimport();
            }
            return AssetDatabase.LoadAssetAtPath<Texture2D>(path);
        }

        private static Material EnsureParticleMat(string path, Texture tex, float softFar, float camNear, float camFar)
        {
            var m = AssetDatabase.LoadAssetAtPath<Material>(path);
            if (m == null)
            {
                m = MineLookUtil.CreateParticleMaterial(tex, softFar, camNear, camFar);
                if (m == null) return null;
                AssetDatabase.CreateAsset(m, path);
            }
            else MineLookUtil.ConfigureParticleMaterial(m, tex, softFar, camNear, camFar);
            EditorUtility.SetDirty(m);
            return m;
        }

        private static Material EnsureRoadGlowMat(string path, MineLookSettings s)
        {
            var sh = Shader.Find("Universal Render Pipeline/Unlit");
            if (sh == null) sh = Shader.Find("Unlit/Color");
            if (sh == null) return null;
            var m = AssetDatabase.LoadAssetAtPath<Material>(path);
            if (m == null)
            {
                m = new Material(sh);
                AssetDatabase.CreateAsset(m, path);
            }
            else if (m.shader != sh) m.shader = sh;
            Color lin = s.roadGlowColor.linear;
            var v = new Vector4(lin.r * s.roadGlowIntensity, lin.g * s.roadGlowIntensity, lin.b * s.roadGlowIntensity, 1f);
            if (m.HasProperty("_BaseColor")) m.SetVector("_BaseColor", v);
            if (m.HasProperty("_Color")) m.SetVector("_Color", v);
            if (m.HasProperty("_Cull")) m.SetFloat("_Cull", 0f);
            m.enableInstancing = false;
            EditorUtility.SetDirty(m);
            return m;
        }

        private static Material EnsureSpriteMat(string path, Color c, int queue)
        {
            var sh = Shader.Find("Sprites/Default");
            if (sh == null) return null;
            var m = AssetDatabase.LoadAssetAtPath<Material>(path);
            if (m == null)
            {
                m = new Material(sh);
                AssetDatabase.CreateAsset(m, path);
            }
            m.SetColor("_Color", c);
            m.renderQueue = queue;
            EditorUtility.SetDirty(m);
            return m;
        }

        private static Material EnsureSky(MineWorldRoot world, MineLookSettings s)
        {
            string pano = null;
            string id = world != null ? SafeName(world.worldId) : "default";
            if (s.usePanoramaIfAvailable && world != null && !string.IsNullOrEmpty(world.worldDir))
            {
                try
                {
                    var data = MineWorldData.Load(world.worldDir);
                    string f = data.Asset("pano");
                    if (f != null)
                    {
                        string src = Path.Combine(world.worldDir, f);
                        if (File.Exists(src)) pano = src;
                        else Debug.LogWarning("[MineSim] assets.pano set but missing: " + src);
                    }
                }
                catch (Exception e)
                {
                    Debug.LogWarning("[MineSim] could not read world.json for the panorama: " + e.Message);
                }
            }

            if (pano != null)
            {
                string folder = EnsureFolder(Gen + "/" + id);
                string dst = folder + "/pano" + Path.GetExtension(pano).ToLowerInvariant();
                string full = Path.GetFullPath(dst);
                var si = new FileInfo(pano);
                var di = new FileInfo(full);
                if (!di.Exists || di.Length != si.Length) File.Copy(pano, full, true);
                AssetDatabase.ImportAsset(dst, ImportAssetOptions.ForceUpdate);
                var imp = AssetImporter.GetAtPath(dst) as TextureImporter;
                if (imp != null)
                {
                    imp.textureType = TextureImporterType.Default;
                    imp.textureShape = TextureImporterShape.Texture2D;
                    imp.mipmapEnabled = false;            // avoids the seam line at u = 0/1
                    imp.wrapModeU = TextureWrapMode.Repeat;
                    imp.wrapModeV = TextureWrapMode.Clamp;
                    imp.npotScale = TextureImporterNPOTScale.None;
                    imp.maxTextureSize = 8192;
                    imp.sRGBTexture = true;
                    imp.textureCompression = TextureImporterCompression.CompressedHQ;
                    imp.SaveAndReimport();
                }
                var tex = AssetDatabase.LoadAssetAtPath<Texture2D>(dst);
                var sh = Shader.Find("Skybox/Panoramic");
                if (tex != null && sh != null)
                {
                    var m = LoadOrCreateMat(folder + "/MineLook_SkyPano.mat", sh);
                    m.SetTexture("_MainTex", tex);
                    m.SetFloat("_Mapping", 1f);
                    m.EnableKeyword("_MAPPING_LATITUDE_LONGITUDE_LAYOUT");
                    m.DisableKeyword("_MAPPING_6_FRAMES_LAYOUT");
                    m.SetFloat("_ImageType", 0f);
                    m.SetFloat("_MirrorOnBack", 0f);
                    m.SetFloat("_Layout", 0f);
                    EditorUtility.SetDirty(m);
                    return m;
                }
                Debug.LogWarning("[MineSim] panorama import failed, using the procedural sky");
            }

            var psh = Shader.Find("Skybox/Procedural");
            if (psh == null) return null;
            var pm = LoadOrCreateMat(Look + "/MineLook_SkyProcedural.mat", psh);
            pm.SetFloat("_SunDisk", 2f);
            pm.EnableKeyword("_SUNDISK_HIGH_QUALITY");
            pm.DisableKeyword("_SUNDISK_SIMPLE");
            pm.DisableKeyword("_SUNDISK_NONE");
            pm.SetFloat("_SunSizeConvergence", 6f);
            EditorUtility.SetDirty(pm);
            return pm;
        }

        private static Material LoadOrCreateMat(string path, Shader sh)
        {
            var m = AssetDatabase.LoadAssetAtPath<Material>(path);
            if (m == null)
            {
                m = new Material(sh);
                AssetDatabase.CreateAsset(m, path);
            }
            else if (m.shader != sh) m.shader = sh;
            return m;
        }

        private static VolumeProfile EnsureProfile(string path, bool main)
        {
            var p = AssetDatabase.LoadAssetAtPath<VolumeProfile>(path);
            if (p == null)
            {
                p = ScriptableObject.CreateInstance<VolumeProfile>();
                AssetDatabase.CreateAsset(p, path);
            }
            if (main)
            {
                EnsureComp<URP.Tonemapping>(p);
                EnsureComp<URP.ColorAdjustments>(p);
                EnsureComp<URP.WhiteBalance>(p);
                EnsureComp<URP.SplitToning>(p);
                EnsureComp<URP.Bloom>(p);
                EnsureComp<URP.Vignette>(p);
                EnsureComp<URP.FilmGrain>(p);
            }
            EnsureComp<URP.DepthOfField>(p);
            EditorUtility.SetDirty(p);
            return p;
        }

        private static void EnsureComp<T>(VolumeProfile p) where T : VolumeComponent
        {
            if (p.TryGet<T>(out _)) return;
            var c = p.Add<T>(false);
            c.name = typeof(T).Name;
            c.hideFlags = HideFlags.HideInInspector | HideFlags.HideInHierarchy;
            AssetDatabase.AddObjectToAsset(c, p);
        }

        /// <summary>HDR, MSAA 4x, shadows, depth texture on every URP asset used by any quality level (incl. the Linux player's).</summary>
        private static void ApplyPipelineSettings(MineLookSettings s)
        {
            var list = new List<RenderPipelineAsset>();
            void Add(RenderPipelineAsset a) { if (a != null && !list.Contains(a)) list.Add(a); }
            Add(GraphicsSettings.defaultRenderPipeline);
            for (int i = 0; i < QualitySettings.names.Length; i++) Add(QualitySettings.GetRenderPipelineAssetAt(i));
            if (list.Count == 0)
            {
                Debug.LogWarning("[MineSim] No render pipeline asset assigned: the look needs URP (Project Settings > Graphics).");
                return;
            }
            foreach (var rp in list)
            {
                if (!(rp is URP.UniversalRenderPipelineAsset)) continue;
                var so = new SerializedObject(rp);
                var done = new List<string>();
                SetBool(so, "m_SupportsHDR", true, done);
                SetInt(so, "m_MSAA", 4, done);
                SetFloat(so, "m_ShadowDistance", s.shadowDistance, done);
                SetInt(so, "m_ShadowCascadeCount", Mathf.Clamp(s.shadowCascades, 1, 4), done);
                SetBool(so, "m_SoftShadowsSupported", s.softShadows, done);
                SetInt(so, "m_MainLightShadowmapResolution", s.shadowResolution, done);
                SetBool(so, "m_RequireDepthTexture", true, done);
                SetInt(so, "m_ColorGradingMode", 1, done);   // High Dynamic Range grading (ACES)
                so.ApplyModifiedPropertiesWithoutUndo();
                EditorUtility.SetDirty(rp);
                Debug.Log("[MineSim] URP asset '" + rp.name + "': set " + string.Join(", ", done));
            }
        }

        private static void SetBool(SerializedObject so, string n, bool v, List<string> done)
        {
            var p = so.FindProperty(n);
            if (p != null && p.propertyType == SerializedPropertyType.Boolean) { p.boolValue = v; done.Add(n); }
        }

        private static void SetInt(SerializedObject so, string n, int v, List<string> done)
        {
            var p = so.FindProperty(n);
            if (p == null) return;
            if (p.propertyType == SerializedPropertyType.Integer) { p.intValue = v; done.Add(n); }
            else if (p.propertyType == SerializedPropertyType.Enum) { p.intValue = v; done.Add(n); }
        }

        private static void SetFloat(SerializedObject so, string n, float v, List<string> done)
        {
            var p = so.FindProperty(n);
            if (p != null && p.propertyType == SerializedPropertyType.Float) { p.floatValue = v; done.Add(n); }
        }

        // ------------------------------------------------------------------ scene
        private static Light EnsureSun(Light current, Transform root)
        {
            Light sun = current;
            if (sun == null) sun = RenderSettings.sun;
            if (sun == null)
                foreach (var l in Object.FindObjectsByType<Light>(FindObjectsSortMode.None))
                    if (l.type == UnityEngine.LightType.Directional) { sun = l; break; }
            if (sun == null)
            {
                var go = new GameObject("Sun");
                go.transform.SetParent(root, false);
                sun = go.AddComponent<Light>();
                sun.type = UnityEngine.LightType.Directional;
            }
            foreach (var l in Object.FindObjectsByType<Light>(FindObjectsSortMode.None))
                if (l != sun && l.type == UnityEngine.LightType.Directional && l.enabled)
                {
                    l.enabled = false;
                    Debug.Log("[MineSim] disabled extra directional light '" + l.name + "' (one sun only)");
                }
            sun.enabled = true;
            return sun;
        }

        private static void EnsureDust(Transform parent, string name, MineAmbientDust.Mode mode)
        {
            var t = Child(parent, name);
            if (t.GetComponent<ParticleSystem>() == null) t.gameObject.AddComponent<ParticleSystem>();
            var d = GetOrAdd<MineAmbientDust>(t.gameObject);
            d.mode = mode;
        }

        private static Volume EnsureVolume(Transform root, string name, int layer)
        {
            var t = Child(root, name);
            t.gameObject.layer = layer;
            var v = GetOrAdd<Volume>(t.gameObject);
            v.isGlobal = true;
            return v;
        }

        private static MineOverviewCamera EnsureOverview(MineLookSettings s)
        {
            var ov = Object.FindFirstObjectByType<MineOverviewCamera>();
            if (ov != null) return ov;

            Camera pick = null;
            foreach (var c in Object.FindObjectsByType<Camera>(FindObjectsSortMode.None))
            {
                if (c.GetComponent<MineCabCamera>() != null) continue;
                var sensor = MineLookUtil.FindComponentByName(c.gameObject, s.sensorComponentName);
                string id = sensor != null ? MineLookUtil.ReadStringField(sensor, "cameraName") : null;
                if (string.Equals(id, s.overviewCameraName, StringComparison.OrdinalIgnoreCase) ||
                    string.Equals(c.name, s.overviewCameraName, StringComparison.OrdinalIgnoreCase))
                {
                    pick = c;
                    break;
                }
            }
            if (pick == null && Camera.main != null && Camera.main.GetComponent<MineCabCamera>() == null) pick = Camera.main;
            if (pick == null)
            {
                var go = new GameObject(s.overviewCameraName);
                Undo.RegisterCreatedObjectUndo(go, "Apply Cinematic Look");
                go.tag = "MainCamera";
                pick = go.AddComponent<Camera>();
                pick.nearClipPlane = 0.5f;
                if (Object.FindFirstObjectByType<AudioListener>() == null) go.AddComponent<AudioListener>();
            }
            pick.clearFlags = CameraClearFlags.Skybox;
            return Undo.AddComponent<MineOverviewCamera>(pick.gameObject);
        }

        private static int StyleRoads(MineWorldRoot world, MineLookSettings s)
        {
            if (s.roadGlowMaterial == null) return 0;
            int n = 0;
            var roads = world != null
                ? world.GetComponentsInChildren<MineRoad>(true)
                : Object.FindObjectsByType<MineRoad>(FindObjectsInactive.Include, FindObjectsSortMode.None);
            foreach (var r in roads)
            {
                if (r.line == null) continue;
                r.line.sharedMaterial = s.roadGlowMaterial;
                EditorUtility.SetDirty(r.line);
                n++;
            }
            return n;
        }

        private static readonly Dictionary<string, Color> TypeAccent = new Dictionary<string, Color>
        {
            { "crusher", new Color(0.95f, 0.30f, 0.22f) },
            { "dump", new Color(0.70f, 0.70f, 0.72f) },
            { "ore", new Color(1.00f, 0.62f, 0.16f) },
            { "parking", new Color(0.30f, 0.62f, 1.00f) },
            { "fuel", new Color(0.35f, 0.85f, 0.45f) },
        };

        private static int StyleLabels(MineWorldRoot world, MineLookSettings s)
        {
            var labels = world != null
                ? world.GetComponentsInChildren<MineLabel>(true)
                : Object.FindObjectsByType<MineLabel>(FindObjectsInactive.Include, FindObjectsSortMode.None);
            var plateMat = EnsureSpriteMat(Look + "/MineLook_LabelPlate.mat", s.plateColor, 2998);
            int n = 0;
            foreach (var l in labels)
            {
                var old = l.transform.Find("LabelPlate");
                if (old != null) Undo.DestroyObjectImmediate(old.gameObject);
                bool want = s.labelPlates && (l.kind == "site" || (l.kind == "road" && s.plateOnRoadLabels) ||
                                              (l.kind == "junction" && s.plateOnJunctionLabels));
                if (!want || plateMat == null) continue;

                EstimateText(l, out float w, out float h);
                float pw = w + h * 1.2f, ph = h * 1.7f;
                var plate = new GameObject("LabelPlate").transform;
                plate.SetParent(l.transform, false);
                Quad(plate, "Bg", new Vector3(0f, 0f, h * 0.06f), new Vector3(pw, ph, 1f), plateMat);

                var site = l.GetComponentInParent<MineSiteMarker>();
                string type = site != null && !string.IsNullOrEmpty(site.siteType) ? site.siteType : "default";
                if (!TypeAccent.TryGetValue(type, out Color acc)) acc = s.uiAccent;
                var accMat = EnsureSpriteMat(Look + "/MineLook_LabelAccent_" + SafeName(type) + ".mat", acc, 2999);
                float bar = h * 0.14f;
                Quad(plate, "Accent", new Vector3(0f, -ph * 0.5f + bar * 0.5f, h * 0.04f), new Vector3(pw, bar, 1f), accMat);
                n++;
            }
            return n;
        }

        private static void Quad(Transform parent, string name, Vector3 pos, Vector3 scale, Material m)
        {
            var q = GameObject.CreatePrimitive(PrimitiveType.Quad);
            q.name = name;
            var col = q.GetComponent<Collider>();
            if (col != null) Object.DestroyImmediate(col);
            q.transform.SetParent(parent, false);
            q.transform.localPosition = pos;
            q.transform.localRotation = Quaternion.identity;
            q.transform.localScale = scale;
            var r = q.GetComponent<MeshRenderer>();
            r.sharedMaterial = m;
            r.shadowCastingMode = ShadowCastingMode.Off;
            r.receiveShadows = false;
            r.lightProbeUsage = LightProbeUsage.Off;
            r.reflectionProbeUsage = ReflectionProbeUsage.Off;
        }

        /// <summary>Local text size of a label (TextMesh exact-ish; other text components via renderer bounds or importer defaults).</summary>
        private static void EstimateText(MineLabel l, out float w, out float h)
        {
            string text = string.IsNullOrEmpty(l.text) ? "label" : l.text;
            int len = Mathf.Max(3, text.Length);
            var tm = l.GetComponent<TextMesh>();
            if (tm != null)
            {
                h = tm.characterSize * Mathf.Max(1, tm.fontSize) / 10f;
                w = len * h * 0.55f;
                return;
            }
            h = l.kind == "site" ? MineWorldImporter.SiteLabelHeight : l.kind == "road" ? MineWorldImporter.RoadLabelHeight : MineWorldImporter.JunctionLabelHeight;
            w = len * h * 0.55f;
            var r = l.GetComponent<Renderer>();
            if (r != null)
            {
                Vector3 e = r.localBounds.size;
                if (e.x > h * 0.5f && e.x < len * h * 1.2f) w = e.x;
                if (e.y > h * 0.3f && e.y < h * 3f) h = e.y;
            }
        }

        // ------------------------------------------------------------------ helpers
        private static T GetOrAdd<T>(GameObject go) where T : Component
        {
            var c = go.GetComponent<T>();
            if (c == null) c = Undo.AddComponent<T>(go);
            return c;
        }

        private static Transform Child(Transform parent, string name)
        {
            var t = parent.Find(name);
            if (t != null) return t;
            var go = new GameObject(name);
            go.transform.SetParent(parent, false);
            return go.transform;
        }

        private static string EnsureFolder(string path)
        {
            var parts = path.Split('/');
            string cur = parts[0];
            for (int i = 1; i < parts.Length; i++)
            {
                string next = cur + "/" + parts[i];
                if (!AssetDatabase.IsValidFolder(next)) AssetDatabase.CreateFolder(cur, parts[i]);
                cur = next;
            }
            return path;
        }

        private static string SafeName(string s)
        {
            if (string.IsNullOrEmpty(s)) return "world";
            foreach (char ch in Path.GetInvalidFileNameChars()) s = s.Replace(ch, '_');
            return s.Replace('/', '_').Replace('\\', '_');
        }
    }
}

using UnityEngine;
using UnityEngine.Rendering;
using UnityEngine.Rendering.Universal;

namespace MineSim
{
    /// <summary>Shared helpers for the MineSim look (URP). Runtime-safe: no editor APIs, no reflection.</summary>
    public static class MineLookUtil
    {
        /// <summary>Pit centre (ground level), largest horizontal extent (m) and size of the current world.</summary>
        public static void WorldBounds(MineWorldRoot w, out Vector3 center, out float extent, out Vector2 size)
        {
            size = new Vector2(800f, 800f);
            Vector2 c2 = Vector2.zero;
            if (w != null)
            {
                if (w.terrain != null && w.terrain.terrainData != null)
                {
                    Vector3 p = w.terrain.transform.position, s = w.terrain.terrainData.size;
                    size = new Vector2(s.x, s.z);
                    c2 = new Vector2(p.x + s.x * 0.5f, p.z + s.z * 0.5f);
                }
                else if (w.sizeM.x > 1f && w.sizeM.y > 1f)
                {
                    size = w.sizeM;
                    c2 = w.origin + size * 0.5f;
                }
            }
            float y = w != null ? w.GroundHeight(c2.x, c2.y) : 0f;
            center = new Vector3(c2.x, y, c2.y);
            extent = Mathf.Max(size.x, size.y);
        }

        /// <summary>First MonoBehaviour on <paramref name="go"/> whose class name is <paramref name="typeName"/> (no type reference needed).</summary>
        public static MonoBehaviour FindComponentByName(GameObject go, string typeName)
        {
            if (go == null || string.IsNullOrEmpty(typeName)) return null;
            var list = go.GetComponents<MonoBehaviour>();
            for (int i = 0; i < list.Length; i++)
                if (list[i] != null && list[i].GetType().Name == typeName) return list[i];
            return null;
        }

        /// <summary>True when the team's capture component (SensorCamera) sits on this camera.</summary>
        public static bool HasSensor(Camera cam, MineLookSettings s)
        {
            return cam != null && FindComponentByName(cam.gameObject, s.sensorComponentName) != null;
        }

        /// <summary>Reads a string field from any component through JsonUtility (works under IL2CPP, no reflection).</summary>
        public static string ReadStringField(MonoBehaviour mb, string field)
        {
            if (mb == null) return null;
            try
            {
                return MiniJson.Str(MiniJson.Parse(JsonUtility.ToJson(mb)), field, null);
            }
            catch (System.Exception)
            {
                return null;
            }
        }

        /// <summary>Nearest ancestor (or self) carrying <paramref name="truckType"/>; else the parent.</summary>
        public static Transform FindTruck(Transform t, string truckType)
        {
            if (t == null) return null;
            for (var p = t; p != null; p = p.parent)
                if (FindComponentByName(p.gameObject, truckType) != null) return p;
            return t.parent;
        }

        /// <summary>
        /// HDR/MSAA, URP post-processing and depth texture (soft particles), plus the cab-DoF volume layer bit.
        /// Never touches fieldOfView, targetTexture or rect.
        /// </summary>
        public static void ConfigureCamera(Camera cam, MineLookSettings s, bool cab, float minFar)
        {
            if (cam == null) return;
            cam.allowHDR = true;
            cam.allowMSAA = true;
            if (cam.farClipPlane < minFar) cam.farClipPlane = minFar;
            var d = cam.GetUniversalAdditionalCameraData();
            if (d == null) return;
            if (s.enablePostOnAttachedCameras) d.renderPostProcessing = true;
            d.requiresDepthTexture = true;
            if (s.cabVolumeLayer > 0 && s.cabVolumeLayer < 32)
            {
                int bit = 1 << s.cabVolumeLayer;
                int mask = d.volumeLayerMask.value;
                d.volumeLayerMask = cab ? (mask | bit) : (mask & ~bit);
            }
        }

        // ------------------------------------------------------------------ particles
        public static Shader ParticleShader()
        {
            Shader sh = Shader.Find("Universal Render Pipeline/Particles/Unlit");
            if (sh == null) sh = Shader.Find("Particles/Standard Unlit");
            if (sh == null) sh = Shader.Find("Sprites/Default");
            return sh;
        }

        /// <summary>Soft round blob with a little wispy noise (RGBA32, white, alpha falloff).</summary>
        public static Texture2D CreateSoftDotTexture(int size = 128)
        {
            var tex = new Texture2D(size, size, TextureFormat.RGBA32, true);
            tex.name = "MineLook_SoftDot";
            tex.wrapMode = TextureWrapMode.Clamp;
            var px = new Color32[size * size];
            float inv = 1f / (size - 1);
            for (int y = 0; y < size; y++)
                for (int x = 0; x < size; x++)
                {
                    float u = x * inv * 2f - 1f, v = y * inv * 2f - 1f;
                    float r = Mathf.Sqrt(u * u + v * v);
                    float a = Mathf.Clamp01(1f - r);
                    a = a * a * (3f - 2f * a);
                    float n = Mathf.PerlinNoise(x * 0.07f + 3.1f, y * 0.07f + 7.7f);
                    a *= Mathf.Lerp(0.65f, 1f, n);
                    px[y * size + x] = new Color32(255, 255, 255, (byte)Mathf.RoundToInt(a * 255f));
                }
            tex.SetPixels32(px);
            tex.Apply(true);
            return tex;
        }

        public static Material CreateParticleMaterial(Texture tex, float softFar, float camNear, float camFar)
        {
            var sh = ParticleShader();
            if (sh == null) return null;
            var m = new Material(sh);
            ConfigureParticleMaterial(m, tex, softFar, camNear, camFar);
            return m;
        }

        /// <summary>URP Particles/Unlit, alpha-blended transparent, soft particles + camera near fade.</summary>
        public static void ConfigureParticleMaterial(Material m, Texture tex, float softFar, float camNear, float camFar)
        {
            if (m == null) return;
            if (tex != null)
            {
                if (m.HasProperty("_BaseMap")) m.SetTexture("_BaseMap", tex);
                if (m.HasProperty("_MainTex")) m.SetTexture("_MainTex", tex);
            }
            if (m.HasProperty("_BaseColor")) m.SetColor("_BaseColor", Color.white);
            if (m.HasProperty("_Color")) m.SetColor("_Color", Color.white);

            SetF(m, "_Surface", 1f);
            SetF(m, "_Blend", 0f);
            SetF(m, "_SrcBlend", (float)UnityEngine.Rendering.BlendMode.SrcAlpha);
            SetF(m, "_DstBlend", (float)UnityEngine.Rendering.BlendMode.OneMinusSrcAlpha);
            SetF(m, "_SrcBlendAlpha", (float)UnityEngine.Rendering.BlendMode.One);
            SetF(m, "_DstBlendAlpha", (float)UnityEngine.Rendering.BlendMode.OneMinusSrcAlpha);
            SetF(m, "_ZWrite", 0f);
            SetF(m, "_Cull", 0f);
            m.SetOverrideTag("RenderType", "Transparent");
            m.EnableKeyword("_SURFACE_TYPE_TRANSPARENT");
            m.DisableKeyword("_ALPHATEST_ON");
            m.DisableKeyword("_ALPHAPREMULTIPLY_ON");
            m.renderQueue = (int)UnityEngine.Rendering.RenderQueue.Transparent;

            if (softFar > 0f)
            {
                SetF(m, "_SoftParticlesEnabled", 1f);
                SetF(m, "_SoftParticlesNearFadeDistance", 0f);
                SetF(m, "_SoftParticlesFarFadeDistance", softFar);
                if (m.HasProperty("_SoftParticleFadeParams")) m.SetVector("_SoftParticleFadeParams", new Vector4(0f, 1f / softFar, 0f, 0f));
                m.EnableKeyword("_SOFTPARTICLES_ON");
            }
            else
            {
                SetF(m, "_SoftParticlesEnabled", 0f);
                m.DisableKeyword("_SOFTPARTICLES_ON");
            }

            if (camFar > camNear)
            {
                SetF(m, "_CameraFadingEnabled", 1f);
                SetF(m, "_CameraNearFadeDistance", camNear);
                SetF(m, "_CameraFarFadeDistance", camFar);
                if (m.HasProperty("_CameraFadeParams")) m.SetVector("_CameraFadeParams", new Vector4(camNear, 1f / (camFar - camNear), 0f, 0f));
                m.EnableKeyword("_FADING_ON");
            }
        }

        private static void SetF(Material m, string prop, float v)
        {
            if (m.HasProperty(prop)) m.SetFloat(prop, v);
        }

        private static Material runtimeDust;

        /// <summary>Fallback material (created once) when no generated material is assigned.</summary>
        public static Material RuntimeDustMaterial()
        {
            if (runtimeDust == null)
            {
                runtimeDust = CreateParticleMaterial(CreateSoftDotTexture(64), 2f, 0.5f, 3f);
                if (runtimeDust != null) runtimeDust.hideFlags = HideFlags.DontSave;
            }
            return runtimeDust;
        }

        public static float Damp(float smoothing, float dt)
        {
            return 1f - Mathf.Exp(-smoothing * dt);
        }
    }

    /// <summary>Writes MineLookSettings into URP VolumeProfiles. Cheap set only: uber-pass effects + low-quality bloom.</summary>
    public static class MineLookPost
    {
        public static T Get<T>(VolumeProfile p) where T : VolumeComponent
        {
            T c;
            if (!p.TryGet(out c)) c = p.Add<T>(false);
            c.active = true;
            return c;
        }

        public static void ApplyMain(VolumeProfile p, MineLookSettings s)
        {
            if (p == null || s == null) return;
            var tm = Get<Tonemapping>(p);
            tm.mode.Override(TonemappingMode.ACES);

            var ca = Get<ColorAdjustments>(p);
            ca.postExposure.Override(s.postExposure);
            ca.contrast.Override(s.contrast);
            ca.saturation.Override(s.saturation);
            ca.colorFilter.Override(s.colorFilter);

            var wb = Get<WhiteBalance>(p);
            wb.temperature.Override(s.temperature);
            wb.tint.Override(s.tint);

            var st = Get<SplitToning>(p);
            st.shadows.Override(s.splitShadows);
            st.highlights.Override(s.splitHighlights);
            st.balance.Override(s.splitBalance);

            var bl = Get<Bloom>(p);
            bl.threshold.Override(s.bloomThreshold);
            bl.intensity.Override(s.bloomIntensity);
            bl.scatter.Override(s.bloomScatter);
            bl.tint.Override(s.bloomTint);
            bl.highQualityFiltering.Override(false);

            var vg = Get<Vignette>(p);
            vg.color.Override(Color.black);
            vg.intensity.Override(s.vignetteIntensity);
            vg.smoothness.Override(s.vignetteSmoothness);

            var fg = Get<FilmGrain>(p);
            fg.type.Override(FilmGrainLookup.Thin1);
            fg.intensity.Override(s.grainIntensity);
            fg.response.Override(s.grainResponse);

            // keep the overview (and anything outside the cab layer) sharp
            var dof = Get<DepthOfField>(p);
            dof.mode.Override(DepthOfFieldMode.Off);
        }

        public static void ApplyCabDof(VolumeProfile p, MineLookSettings s)
        {
            if (p == null || s == null) return;
            var dof = Get<DepthOfField>(p);
            dof.mode.Override(s.cabDepthOfField ? DepthOfFieldMode.Gaussian : DepthOfFieldMode.Off);
            dof.gaussianStart.Override(s.cabDofStart);
            dof.gaussianEnd.Override(Mathf.Max(s.cabDofStart + 1f, s.cabDofEnd));
            dof.gaussianMaxRadius.Override(s.cabDofMaxRadius);
            dof.highQualitySampling.Override(false);
        }
    }
}

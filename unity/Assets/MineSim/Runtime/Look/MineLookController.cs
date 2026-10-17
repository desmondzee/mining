using System.Collections.Generic;
using UnityEngine;
using UnityEngine.Rendering;

namespace MineSim
{
    /// <summary>
    /// Sits on the "MineLook" object made by Tools/Mine Sim/Apply Cinematic Look. Applies MineLookSettings (sun, sky,
    /// ambient, fog, post profiles, ambient dust) on enable and whenever the asset changes, in edit and play mode.
    /// In play mode it also attaches MineOverviewCamera / MineCabCamera / MineDustTrail to the team's runtime-spawned
    /// cameras and trucks (by SensorCamera.cameraName "overview" / "truck-N" and TruckAgent), see settings "Auto attach".
    /// Runtime-safe for IL2CPP players (editor calls are behind UNITY_EDITOR).
    /// </summary>
    [ExecuteAlways]
    [DisallowMultipleComponent]
    public class MineLookController : MonoBehaviour
    {
        public MineLookSettings settings;
        public Light sun;
        public MineWorldRoot world;
        public Volume postVolume;
        public Volume cabDofVolume;

        private bool dirty = true;
        private float nextAttach;
        private readonly Dictionary<MonoBehaviour, string> sensorNames = new Dictionary<MonoBehaviour, string>();

        private void OnEnable()
        {
            if (settings != null) MineLookSettings.Active = settings;
            MineLookSettings.Changed += OnSettingsChanged;
            dirty = true;
            nextAttach = 0f;
        }

        private void OnDisable()
        {
            MineLookSettings.Changed -= OnSettingsChanged;
            if (MineLookSettings.Active == settings) MineLookSettings.Active = null;
        }

        private void OnSettingsChanged(MineLookSettings s)
        {
            if (s != settings) return;
            dirty = true;
#if UNITY_EDITOR
            if (!Application.isPlaying) UnityEditor.EditorApplication.delayCall += DelayedApply;
#endif
        }

#if UNITY_EDITOR
        private void DelayedApply()
        {
            if (this != null && isActiveAndEnabled && dirty) Apply();
        }
#endif

        private void Update()
        {
            if (settings != null && MineLookSettings.Active != settings) MineLookSettings.Active = settings;
            if (dirty) Apply();
            if (Application.isPlaying && settings != null && Time.unscaledTime >= nextAttach)
            {
                nextAttach = Time.unscaledTime + Mathf.Max(0.25f, settings.autoAttachInterval);
                AutoAttach();
            }
        }

        /// <summary>Applies everything from <see cref="settings"/> now.</summary>
        public void Apply()
        {
            dirty = false;
            var s = settings;
            if (s == null) return;
            MineLookSettings.Active = s;
            if (world == null) world = Object.FindFirstObjectByType<MineWorldRoot>();
            MineLookUtil.WorldBounds(world, out Vector3 center, out float extent, out Vector2 size);

            ApplySun(s);
            ApplySky(s);
            ApplyFog(s, extent);
            ApplyPost(s);
            foreach (var d in GetComponentsInChildren<MineAmbientDust>(true))
                if (d.mode != MineAmbientDust.Mode.CabMotes) d.Configure(s, center, size);
        }

        private void ApplySun(MineLookSettings s)
        {
            if (sun == null) return;
            sun.type = UnityEngine.LightType.Directional;
            sun.transform.rotation = Quaternion.Euler(s.sunElevationDeg, s.sunAzimuthDeg, 0f);
            sun.color = s.sunColor;
            sun.intensity = s.sunIntensity;
            sun.shadows = s.softShadows ? LightShadows.Soft : LightShadows.Hard;
            sun.shadowStrength = s.shadowStrength;
            RenderSettings.sun = sun;
        }

        private void ApplySky(MineLookSettings s)
        {
            var mat = s.skyboxMaterial;
            if (mat != null)
            {
                if (mat.HasProperty("_Exposure")) mat.SetFloat("_Exposure", s.skyExposure);
                if (mat.HasProperty("_Rotation")) mat.SetFloat("_Rotation", s.panoRotation);
                if (mat.HasProperty("_SkyTint")) mat.SetColor("_SkyTint", s.skyTint);
                if (mat.HasProperty("_GroundColor")) mat.SetColor("_GroundColor", s.skyGround);
                if (mat.HasProperty("_AtmosphereThickness")) mat.SetFloat("_AtmosphereThickness", s.atmosphereThickness);
                if (mat.HasProperty("_SunSize")) mat.SetFloat("_SunSize", s.sunDiskSize);
                if (RenderSettings.skybox != mat) RenderSettings.skybox = mat;
#if UNITY_EDITOR
                UnityEditor.EditorUtility.SetDirty(mat);
#endif
            }
            if (s.ambientFromSkybox && RenderSettings.skybox != null)
            {
                RenderSettings.ambientMode = AmbientMode.Skybox;
                RenderSettings.ambientIntensity = s.ambientIntensity;
            }
            else
            {
                RenderSettings.ambientMode = AmbientMode.Trilight;
                RenderSettings.ambientSkyColor = s.ambientSky * s.ambientIntensity;
                RenderSettings.ambientEquatorColor = s.ambientEquator * s.ambientIntensity;
                RenderSettings.ambientGroundColor = s.ambientGround * s.ambientIntensity;
            }
            RenderSettings.defaultReflectionMode = DefaultReflectionMode.Skybox;
            RenderSettings.reflectionIntensity = s.reflectionIntensity;
            DynamicGI.UpdateEnvironment();
        }

        private static void ApplyFog(MineLookSettings s, float extent)
        {
            RenderSettings.fog = s.fog;
            RenderSettings.fogMode = s.fogMode;
            RenderSettings.fogColor = s.fogColor;
            float e = Mathf.Max(100f, extent);
            RenderSettings.fogDensity = s.fogFromWorldSize ? s.fogDensityScale / e : s.fogDensity;
            RenderSettings.fogStartDistance = s.fogStartFactor * e;
            RenderSettings.fogEndDistance = Mathf.Max(s.fogStartFactor * e + 1f, s.fogEndFactor * e);
        }

        private void ApplyPost(MineLookSettings s)
        {
            if (s.postProfile != null)
            {
                MineLookPost.ApplyMain(s.postProfile, s);
                if (postVolume != null)
                {
                    postVolume.isGlobal = true;
                    postVolume.priority = 50f;
                    postVolume.sharedProfile = s.postProfile;
                }
#if UNITY_EDITOR
                UnityEditor.EditorUtility.SetDirty(s.postProfile);
#endif
            }
            if (s.cabDofProfile != null)
            {
                MineLookPost.ApplyCabDof(s.cabDofProfile, s);
                if (cabDofVolume != null)
                {
                    cabDofVolume.isGlobal = true;
                    cabDofVolume.priority = 60f;
                    cabDofVolume.sharedProfile = s.cabDofProfile;
                    cabDofVolume.gameObject.layer = Mathf.Clamp(s.cabVolumeLayer, 0, 31);
                    cabDofVolume.enabled = s.cabDepthOfField && s.cabVolumeLayer > 0;
                }
#if UNITY_EDITOR
                UnityEditor.EditorUtility.SetDirty(s.cabDofProfile);
#endif
            }
        }

        // ------------------------------------------------------------------ auto attach (play mode)
        private string CameraId(Camera cam)
        {
            var sensor = MineLookUtil.FindComponentByName(cam.gameObject, settings.sensorComponentName);
            if (sensor != null)
            {
                if (!sensorNames.TryGetValue(sensor, out string id))
                {
                    id = MineLookUtil.ReadStringField(sensor, "cameraName");
                    sensorNames[sensor] = id;
                }
                if (!string.IsNullOrEmpty(id)) return id;
            }
            return cam.gameObject.name;
        }

        private void AutoAttach()
        {
            var s = settings;
            if (!s.autoAttachOverview && !s.autoAttachCabCameras && !s.autoAttachDustTrails) return;
            foreach (var cam in Object.FindObjectsByType<Camera>(FindObjectsSortMode.None))
            {
                if (cam == null || cam.cameraType != CameraType.Game) continue;
                if (cam.GetComponent<MineOverviewCamera>() != null) continue;
                string id = CameraId(cam);
                if (string.IsNullOrEmpty(id)) continue;

                if (s.autoAttachOverview && string.Equals(id, s.overviewCameraName, System.StringComparison.OrdinalIgnoreCase))
                {
                    if (Object.FindFirstObjectByType<MineOverviewCamera>() == null)
                    {
                        cam.gameObject.AddComponent<MineOverviewCamera>();
                        Debug.Log("[MineSim] MineOverviewCamera attached to '" + cam.name + "'");
                    }
                    continue;
                }

                if (string.IsNullOrEmpty(s.truckCameraPrefix) ||
                    !id.StartsWith(s.truckCameraPrefix, System.StringComparison.OrdinalIgnoreCase)) continue;
                Transform truck = MineLookUtil.FindTruck(cam.transform, s.truckComponentName);
                if (truck == null || truck == cam.transform) continue;

                if (s.autoAttachCabCameras && cam.GetComponent<MineCabCamera>() == null && truck.GetComponent<MineCabCamera>() == null)
                {
                    var cab = cam.gameObject.AddComponent<MineCabCamera>();
                    cab.target = truck;
                    cab.displayName = id;
                    cab.CaptureMountFromCurrentPose();
                    Debug.Log("[MineSim] MineCabCamera attached to '" + cam.name + "' (truck '" + truck.name + "')");
                }
                if (s.autoAttachDustTrails && truck.GetComponent<MineDustTrail>() == null)
                    truck.gameObject.AddComponent<MineDustTrail>();
            }

            // trucks without cameras
            if (s.autoAttachDustTrails && !string.IsNullOrEmpty(s.truckComponentName))
                foreach (var mb in Object.FindObjectsByType<MonoBehaviour>(FindObjectsSortMode.None))
                    if (mb != null && mb.GetType().Name == s.truckComponentName && mb.GetComponent<MineDustTrail>() == null)
                        mb.gameObject.AddComponent<MineDustTrail>();
        }
    }
}

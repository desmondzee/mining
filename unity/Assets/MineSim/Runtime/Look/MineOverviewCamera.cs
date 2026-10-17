using System.Collections.Generic;
using UnityEngine;

namespace MineSim
{
    /// <summary>
    /// Slow cinematic orbit + dolly around the pit, sized from the world. The look-at point eases toward each site in turn.
    /// It only drives the transform. FOV is set only when <see cref="controlFov"/> is on AND there is no SensorCamera on
    /// the object. targetTexture and rect are never touched (MineSplitScreenLayout, if you add it, sets the rect).
    /// Add it to the team's "overview" SensorCamera (MineLookController does that automatically) or to any camera.
    /// </summary>
    [RequireComponent(typeof(Camera))]
    [DisallowMultipleComponent]
    public class MineOverviewCamera : MonoBehaviour
    {
        public MineLookSettings settingsOverride;
        [Tooltip("Defaults to the MineWorldRoot in the scene")]
        public MineWorldRoot world;
        [Tooltip("Optional fixed look target (overrides the site tour)")]
        public Transform lookTargetOverride;
        [Tooltip("Set fieldOfView from settings (ignored when a SensorCamera is present)")]
        public bool controlFov = true;
        public float startAngleDeg = 35f;

        private Camera cam;
        private Vector3 center;
        private float extent = 800f;
        private readonly List<Vector3> sites = new List<Vector3>();
        private float angle;
        private float siteTimer;
        private int siteIndex;
        private Vector3 lookPoint;
        private bool hasLook;
        private bool sensor;
        private float clock;

        private void OnEnable()
        {
            cam = GetComponent<Camera>();
            var s = MineLookSettings.Resolve(settingsOverride);
            sensor = MineLookUtil.HasSensor(cam, s);
            Recompute();
            angle = startAngleDeg;
            clock = 0f;
            hasLook = false;
            MineLookUtil.ConfigureCamera(cam, s, false, extent * 3f);
            SnapToStart();
        }

        /// <summary>Re-reads world size and site positions.</summary>
        public void Recompute()
        {
            if (world == null) world = Object.FindFirstObjectByType<MineWorldRoot>();
            MineLookUtil.WorldBounds(world, out center, out extent, out Vector2 _);
            sites.Clear();
            if (world != null)
            {
                var list = new List<MineSiteMarker>(world.GetComponentsInChildren<MineSiteMarker>(true));
                list.Sort((a, b) => string.CompareOrdinal(a.siteId, b.siteId));
                foreach (var m in list) sites.Add(m.Position);
            }
        }

        /// <summary>Places the camera at the start pose (also used by the editor menu).</summary>
        public void SnapToStart()
        {
            if (cam == null) cam = GetComponent<Camera>();
            var s = MineLookSettings.Resolve(settingsOverride);
            sensor = MineLookUtil.HasSensor(cam, s);
            hasLook = false;
            Step(0f, s);
        }

        private void LateUpdate()
        {
            Step(Time.deltaTime, MineLookSettings.Resolve(settingsOverride));
        }

        private void Step(float dt, MineLookSettings s)
        {
            clock += dt;
            angle += s.overviewOrbitDegPerSec * dt;
            float period = Mathf.Max(5f, s.overviewDollyPeriod);
            float r = extent * s.overviewRadiusFactor * (1f + s.overviewDollyAmount * Mathf.Sin(clock * Mathf.PI * 2f / period));
            float h = extent * s.overviewHeightFactor * (1f + s.overviewHeightBob * Mathf.Sin(clock * Mathf.PI * 2f / (period * 1.37f) + 1f));
            Vector3 pos = center + Quaternion.Euler(0f, angle, 0f) * new Vector3(0f, 0f, -r) + Vector3.up * h;
            if (world != null)
            {
                float g = world.GroundHeight(pos.x, pos.z) + Mathf.Max(15f, extent * 0.04f);
                if (pos.y < g) pos.y = g;
            }

            Vector3 desired = center;
            if (lookTargetOverride != null) desired = lookTargetOverride.position;
            else if (s.overviewFocusSites && sites.Count > 0)
            {
                siteTimer += dt;
                if (siteTimer > Mathf.Max(1f, s.overviewSiteDwell))
                {
                    siteTimer = 0f;
                    siteIndex = (siteIndex + 1) % sites.Count;
                }
                if (siteIndex >= sites.Count) siteIndex = 0;
                desired = Vector3.Lerp(center, sites[siteIndex], s.overviewSiteBias);
            }
            if (!hasLook)
            {
                lookPoint = desired;
                hasLook = true;
            }
            else lookPoint = Vector3.Lerp(lookPoint, desired, 1f - Mathf.Exp(-dt / Mathf.Max(0.05f, s.overviewLookSmoothing)));

            Vector3 dir = lookPoint - pos;
            if (dir.sqrMagnitude < 1e-4f) dir = Vector3.forward;
            Quaternion rot = Quaternion.LookRotation(dir, Vector3.up);
            float sw = s.overviewSwayDeg;
            if (sw > 0f)
            {
                float px = (Mathf.PerlinNoise(clock * 0.07f, 0.31f) - 0.5f) * 2f * sw;
                float py = (Mathf.PerlinNoise(0.73f, clock * 0.05f) - 0.5f) * 2f * sw;
                rot *= Quaternion.Euler(px, py, 0f);
            }
            transform.SetPositionAndRotation(pos, rot);

            if (controlFov && !sensor && cam != null) cam.fieldOfView = s.overviewFov;
        }
    }
}

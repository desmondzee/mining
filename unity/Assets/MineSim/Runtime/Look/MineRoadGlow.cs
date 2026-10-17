using System.Collections.Generic;
using UnityEngine;

namespace MineSim
{
    /// <summary>
    /// Amber emissive road lines without a custom shader: every MineRoad LineRenderer gets the URP Unlit material and an
    /// HDR base colour (above 1, so it blooms) through a MaterialPropertyBlock. A pulse wave travels outward from the pit centre.
    /// Re-scans roads every few seconds, so it keeps working after a reimport.
    /// </summary>
    public class MineRoadGlow : MonoBehaviour
    {
        public MineLookSettings settingsOverride;
        public float rescanInterval = 3f;

        private struct Line
        {
            public LineRenderer lr;
            public float distance;
            public float typeGain;
        }

        private static readonly int BaseColorId = Shader.PropertyToID("_BaseColor");
        private static readonly int ColorId = Shader.PropertyToID("_Color");

        private readonly List<Line> lines = new List<Line>();
        private readonly Dictionary<LineRenderer, float> baseWidths = new Dictionary<LineRenderer, float>();
        private MaterialPropertyBlock mpb;
        private float nextScan;
        private float appliedWidthMul = -1f;

        private void OnEnable()
        {
            mpb = new MaterialPropertyBlock();
            nextScan = 0f;
        }

        private void OnDisable()
        {
            // restore widths and clear blocks
            foreach (var l in lines)
            {
                if (l.lr == null) continue;
                l.lr.SetPropertyBlock(null);
                if (baseWidths.TryGetValue(l.lr, out float w)) l.lr.widthMultiplier = w;
            }
            appliedWidthMul = -1f;
        }

        private void Scan(MineLookSettings s)
        {
            lines.Clear();
            var world = Object.FindFirstObjectByType<MineWorldRoot>();
            MineLookUtil.WorldBounds(world, out Vector3 center, out float _, out Vector2 _);
            foreach (var r in Object.FindObjectsByType<MineRoad>(FindObjectsInactive.Include, FindObjectsSortMode.None))
            {
                if (r.line == null) continue;
                if (!baseWidths.ContainsKey(r.line)) baseWidths[r.line] = r.line.widthMultiplier;
                if (s.roadGlowMaterial != null && r.line.sharedMaterial != s.roadGlowMaterial) r.line.sharedMaterial = s.roadGlowMaterial;
                Vector3 mid = r.Midpoint;
                lines.Add(new Line
                {
                    lr = r.line,
                    distance = new Vector2(mid.x - center.x, mid.z - center.z).magnitude,
                    typeGain = r.roadType == "ramp" ? 1.15f : r.roadType == "spur" ? 0.85f : 1f,
                });
            }
            appliedWidthMul = -1f;
        }

        private void Update()
        {
            var s = MineLookSettings.Resolve(settingsOverride);
            if (Time.unscaledTime >= nextScan)
            {
                nextScan = Time.unscaledTime + Mathf.Max(0.5f, rescanInterval);
                Scan(s);
            }

            if (!Mathf.Approximately(appliedWidthMul, s.roadWidthMultiplier))
            {
                appliedWidthMul = s.roadWidthMultiplier;
                foreach (var l in lines)
                    if (l.lr != null && baseWidths.TryGetValue(l.lr, out float w))
                        l.lr.widthMultiplier = Mathf.Max(0.3f, w * s.roadWidthMultiplier);
            }

            float t = Time.time;
            float wl = Mathf.Max(1f, s.roadPulseWavelength);
            Color baseCol = s.roadGlowColor.linear;
            foreach (var l in lines)
            {
                if (l.lr == null || !l.lr.enabled) continue;
                float phase = (t * s.roadPulseSpeed - l.distance / wl) * Mathf.PI * 2f;
                float k = s.roadGlowIntensity * l.typeGain * (1f + s.roadPulseAmount * Mathf.Sin(phase));
                // SetVector passes the value raw (linear, HDR); SetColor would gamma-convert and over-brighten values > 1
                var v = new Vector4(baseCol.r * k, baseCol.g * k, baseCol.b * k, 1f);
                l.lr.GetPropertyBlock(mpb);
                mpb.SetVector(BaseColorId, v);
                mpb.SetVector(ColorId, v);
                l.lr.SetPropertyBlock(mpb);
            }
        }
    }
}

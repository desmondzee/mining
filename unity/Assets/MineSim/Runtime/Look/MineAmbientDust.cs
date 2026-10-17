using UnityEngine;

namespace MineSim
{
    /// <summary>
    /// Slow drifting dust over the whole pit (motes or large haze puffs), configured from MineLookSettings by
    /// MineLookController. Also used for the small motes box that rides with each cab camera.
    /// </summary>
    [RequireComponent(typeof(ParticleSystem))]
    public class MineAmbientDust : MonoBehaviour
    {
        public enum Mode { WorldMotes, WorldHaze, CabMotes }

        public Mode mode = Mode.WorldMotes;

        /// <summary>Configure for the pit. <paramref name="center"/> is pit-floor centre, <paramref name="size"/> the world XZ size.</summary>
        public void Configure(MineLookSettings s, Vector3 center, Vector2 size)
        {
            var ps = GetComponent<ParticleSystem>();
            if (ps == null || s == null) return;
            bool on = s.ambientDust && (mode != Mode.WorldHaze || s.hazePuffs) && (mode != Mode.CabMotes || s.cabDustMotes);
            if (!on)
            {
                ps.Stop(true, ParticleSystemStopBehavior.StopEmittingAndClear);
                var em0 = ps.emission;
                em0.enabled = false;
                return;
            }

            switch (mode)
            {
                case Mode.WorldMotes:
                {
                    transform.position = center + Vector3.up * (s.dustLayerHeight * 0.5f);
                    float life = (s.moteLifetime.x + s.moteLifetime.y) * 0.5f;
                    int max = Mathf.Max(10, s.moteMaxParticles);
                    Setup(ps, s.dustMaterial, max, Mathf.Min(s.moteRate, max / Mathf.Max(1f, life)), s.moteLifetime, s.moteSize, s.dustColor,
                        new Vector3(size.x, s.dustLayerHeight, size.y), s.wind, s.turbulence, 0.05f, true);
                    break;
                }
                case Mode.WorldHaze:
                {
                    transform.position = center + Vector3.up * (s.dustLayerHeight * 0.35f);
                    float life = (s.hazeLifetime.x + s.hazeLifetime.y) * 0.5f;
                    int max = Mathf.Max(4, s.hazeMaxParticles);
                    Setup(ps, s.hazeMaterial, max, max / Mathf.Max(1f, life), s.hazeLifetime, s.hazeSize, s.hazeColor,
                        new Vector3(size.x * 1.1f, s.dustLayerHeight * 0.7f, size.y * 1.1f), s.wind * 0.6f, s.turbulence * 0.5f, 0.01f, true);
                    break;
                }
                default:
                {
                    // small box in front of a cab camera; local position is set by MineCabCamera
                    var c = s.dustColor;
                    c.a = Mathf.Clamp01(c.a * 1.8f);
                    Setup(ps, s.dustMaterial, 400, 70f, new Vector2(3f, 6f), new Vector2(0.03f, 0.14f), c,
                        new Vector3(28f, 10f, 30f), s.wind, s.turbulence * 1.5f, 0.3f, true);
                    break;
                }
            }
        }

        public static void Setup(ParticleSystem ps, Material mat, int maxParticles, float rate, Vector2 lifetime, Vector2 size,
            Color color, Vector3 box, Vector3 wind, float turbulence, float noiseFrequency, bool prewarm)
        {
            ps.Stop(true, ParticleSystemStopBehavior.StopEmittingAndClear);

            var main = ps.main;
            main.duration = 10f;
            main.loop = true;
            main.prewarm = prewarm;
            main.playOnAwake = true;
            main.startLifetime = new ParticleSystem.MinMaxCurve(lifetime.x, Mathf.Max(lifetime.x, lifetime.y));
            main.startSpeed = 0f;
            main.startSize = new ParticleSystem.MinMaxCurve(size.x, Mathf.Max(size.x, size.y));
            main.startRotation = new ParticleSystem.MinMaxCurve(0f, Mathf.PI * 2f);
            main.startColor = color;
            main.maxParticles = maxParticles;
            main.simulationSpace = ParticleSystemSimulationSpace.World;
            main.scalingMode = ParticleSystemScalingMode.Local;
            main.gravityModifier = 0f;

            var em = ps.emission;
            em.enabled = true;
            em.rateOverTime = Mathf.Max(0f, rate);

            var sh = ps.shape;
            sh.enabled = true;
            sh.shapeType = ParticleSystemShapeType.Box;
            sh.scale = box;
            sh.position = Vector3.zero;
            sh.rotation = Vector3.zero;

            var vel = ps.velocityOverLifetime;
            vel.enabled = true;
            vel.space = ParticleSystemSimulationSpace.World;
            vel.x = new ParticleSystem.MinMaxCurve(wind.x * 0.6f, wind.x * 1.4f);
            vel.y = new ParticleSystem.MinMaxCurve(wind.y * 0.6f - 0.05f, wind.y * 1.4f + 0.05f);
            vel.z = new ParticleSystem.MinMaxCurve(wind.z * 0.6f, wind.z * 1.4f);

            var noise = ps.noise;
            noise.enabled = turbulence > 0f;
            noise.strength = turbulence;
            noise.frequency = noiseFrequency;
            noise.scrollSpeed = 0.05f;
            noise.damping = true;
            noise.quality = ParticleSystemNoiseQuality.Low;

            var col = ps.colorOverLifetime;
            col.enabled = true;
            var g = new Gradient();
            g.SetKeys(
                new[] { new GradientColorKey(Color.white, 0f), new GradientColorKey(Color.white, 1f) },
                new[] { new GradientAlphaKey(0f, 0f), new GradientAlphaKey(1f, 0.2f), new GradientAlphaKey(1f, 0.75f), new GradientAlphaKey(0f, 1f) });
            col.color = new ParticleSystem.MinMaxGradient(g);

            var rot = ps.rotationOverLifetime;
            rot.enabled = true;
            rot.z = new ParticleSystem.MinMaxCurve(-0.15f, 0.15f);

            var r = ps.GetComponent<ParticleSystemRenderer>();
            if (r != null)
            {
                r.renderMode = ParticleSystemRenderMode.Billboard;
                r.sharedMaterial = mat != null ? mat : MineLookUtil.RuntimeDustMaterial();
                r.shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.Off;
                r.receiveShadows = false;
                r.maxParticleSize = 1f;
                r.sortMode = ParticleSystemSortMode.None;
            }

            ps.Play(true);
        }
    }
}

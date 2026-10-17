using UnityEngine;

namespace MineSim
{
    /// <summary>
    /// Drop on any moving object (truck root): emits dust behind it, proportional to its speed.
    /// Speed comes from an optional Rigidbody or from the position delta. Creates a child "MineDustTrail_FX".
    /// Offsets are metres in the object's rotation frame (object scale is ignored).
    /// </summary>
    [DisallowMultipleComponent]
    public class MineDustTrail : MonoBehaviour
    {
        [Tooltip("Emitter position behind the rear wheels (m, object rotation frame). Default fits a ~9 m haul truck")]
        public Vector3 emitterOffset = new Vector3(0f, 0.6f, -4.2f);
        [Tooltip("Emitter width across both rear wheels (m)")]
        public float emitterWidth = 4f;
        public float rateMultiplier = 1f;
        public float sizeMultiplier = 1f;
        [Tooltip("Optional; otherwise speed comes from the position delta")]
        public Rigidbody body;
        public MineLookSettings settingsOverride;
        [Tooltip("Optional material; else MineLookSettings.trailMaterial, else a runtime fallback")]
        public Material material;

        /// <summary>Smoothed speed (m/s).</summary>
        public float Speed { get; private set; }

        private ParticleSystem ps;
        private Transform fx;
        private Vector3 lastPos;
        private bool hasLast;

        private void OnEnable()
        {
            Build();
            hasLast = false;
        }

        private void OnDisable()
        {
            if (ps != null)
            {
                var em = ps.emission;
                em.rateOverTime = 0f;
            }
        }

        private void Build()
        {
            var s = MineLookSettings.Resolve(settingsOverride);
            if (fx == null)
            {
                fx = transform.Find("MineDustTrail_FX");
                if (fx == null)
                {
                    var go = new GameObject("MineDustTrail_FX");
                    fx = go.transform;
                    fx.SetParent(transform, false);
                }
            }
            ps = fx.GetComponent<ParticleSystem>();
            if (ps == null) ps = fx.gameObject.AddComponent<ParticleSystem>();
            ps.Stop(true, ParticleSystemStopBehavior.StopEmittingAndClear);

            var main = ps.main;
            main.duration = 5f;
            main.loop = true;
            main.prewarm = false;
            main.playOnAwake = true;
            main.startLifetime = new ParticleSystem.MinMaxCurve(s.trailLifetime.x, Mathf.Max(s.trailLifetime.x, s.trailLifetime.y));
            main.startSpeed = new ParticleSystem.MinMaxCurve(0.2f, 1.2f);
            main.startSize = new ParticleSystem.MinMaxCurve(s.trailSize.x * sizeMultiplier, Mathf.Max(s.trailSize.x, s.trailSize.y) * sizeMultiplier);
            main.startRotation = new ParticleSystem.MinMaxCurve(0f, Mathf.PI * 2f);
            main.startColor = s.trailColor;
            main.maxParticles = 1500;
            main.simulationSpace = ParticleSystemSimulationSpace.World;
            main.scalingMode = ParticleSystemScalingMode.Local;
            main.gravityModifier = -0.02f;

            var em = ps.emission;
            em.enabled = true;
            em.rateOverTime = 0f;

            var sh = ps.shape;
            sh.enabled = true;
            sh.shapeType = ParticleSystemShapeType.Box;
            sh.scale = new Vector3(Mathf.Max(0.5f, emitterWidth), 0.4f, 1.2f);
            sh.position = Vector3.zero;
            sh.rotation = new Vector3(-60f, 0f, 0f);   // box emits along +Z: tilt it upward

            var vel = ps.velocityOverLifetime;
            vel.enabled = true;
            vel.space = ParticleSystemSimulationSpace.World;
            vel.x = new ParticleSystem.MinMaxCurve(-0.5f, 0.5f);
            vel.y = new ParticleSystem.MinMaxCurve(0.3f, 1.4f);
            vel.z = new ParticleSystem.MinMaxCurve(-0.5f, 0.5f);

            var limit = ps.limitVelocityOverLifetime;
            limit.enabled = true;
            limit.limit = 3f;
            limit.dampen = 0.15f;

            var noise = ps.noise;
            noise.enabled = true;
            noise.strength = 0.9f;
            noise.frequency = 0.25f;
            noise.scrollSpeed = 0.2f;
            noise.damping = true;
            noise.quality = ParticleSystemNoiseQuality.Low;

            var col = ps.colorOverLifetime;
            col.enabled = true;
            var g = new Gradient();
            g.SetKeys(
                new[] { new GradientColorKey(Color.white, 0f), new GradientColorKey(new Color(0.92f, 0.88f, 0.82f), 1f) },
                new[] { new GradientAlphaKey(0f, 0f), new GradientAlphaKey(1f, 0.08f), new GradientAlphaKey(0.55f, 0.5f), new GradientAlphaKey(0f, 1f) });
            col.color = new ParticleSystem.MinMaxGradient(g);

            var sz = ps.sizeOverLifetime;
            sz.enabled = true;
            sz.size = new ParticleSystem.MinMaxCurve(1f, AnimationCurve.EaseInOut(0f, 0.35f, 1f, 1f));

            var rot = ps.rotationOverLifetime;
            rot.enabled = true;
            rot.z = new ParticleSystem.MinMaxCurve(-0.4f, 0.4f);

            var r = ps.GetComponent<ParticleSystemRenderer>();
            if (r != null)
            {
                Material m = material != null ? material : s.trailMaterial;
                r.sharedMaterial = m != null ? m : MineLookUtil.RuntimeDustMaterial();
                r.renderMode = ParticleSystemRenderMode.Billboard;
                r.shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.Off;
                r.receiveShadows = false;
                r.maxParticleSize = 1f;
            }
            ps.Play(true);
        }

        private void LateUpdate()
        {
            if (ps == null || fx == null) return;
            float dt = Time.deltaTime;
            if (dt <= 0f) return;
            var s = MineLookSettings.Resolve(settingsOverride);

            // keep the emitter at the offset in metres regardless of object scale
            fx.position = transform.position + transform.rotation * emitterOffset;
            fx.rotation = transform.rotation;
            // scalingMode Local ignores parent scale, so a scaled truck does not scale the dust

            Vector3 p = transform.position;
            float v;
            if (body != null)
            {
#if UNITY_6000_0_OR_NEWER
                v = body.linearVelocity.magnitude;
#else
                v = body.velocity.magnitude;
#endif
            }
            else
            {
                v = hasLast ? (p - lastPos).magnitude / dt : 0f;
                if (v > 120f) v = 0f;   // teleport
            }
            lastPos = p;
            hasLast = true;
            Speed = Mathf.Lerp(Speed, v, MineLookUtil.Damp(5f, dt));

            float k = Mathf.Max(0f, Speed - s.trailMinSpeed);
            var em = ps.emission;
            em.rateOverTime = Mathf.Min(s.trailMaxRate, k * s.trailRatePerMps) * rateMultiplier;
            var main = ps.main;
            Color c = s.trailColor;
            c.a *= Mathf.Clamp01(0.35f + k / 8f);
            main.startColor = c;
            float grow = 1f + Mathf.Clamp01(k / 15f) * 0.8f;
            float sMin = s.trailSize.x * sizeMultiplier * grow;
            main.startSize = new ParticleSystem.MinMaxCurve(sMin, Mathf.Max(sMin, s.trailSize.y * sizeMultiplier * grow));
        }
    }
}

using UnityEngine;

namespace MineSim
{
    /// <summary>
    /// Excavator animation. Articulated mode: if the model has child transforms named <c>house</c>, <c>boom</c>,
    /// <c>stick</c> and <c>bucket</c> (assets/models/excavator/excavator_rigged.glb; glTFast keeps node names), only the
    /// house yaws and boom/stick/bucket pitch about their local X through an eased dig cycle (same keyframes as the
    /// browser preview): reach at the face, dig (stick in + curl), boom up, swing to the truck side, dump, swing back.
    /// Holding eases every joint back to the modelled rest pose. Otherwise falls back to the whole-model behaviour below.
    /// Whole-model fallback (single-mesh excavator, the whole transform swings).
    /// <c>digging</c>: yaw cycle face side -> swing -> truck side (dump) -> swing back, about the base heading
    /// (+-<see cref="swingAmplitude"/>, <see cref="cyclePeriod"/> s, eased), plus a small dig bob on a child pivot.
    /// <c>!digging</c> (holding): eases back to the base heading and stops.
    /// Set by <see cref="MineRadioLink"/> from the radio service; purely visual, no physics (the static BoxCollider on
    /// this transform rotates with it). Never touches trucks, goals or the brain.
    /// </summary>
    [DisallowMultipleComponent]
    public class MineExcavatorRig : MonoBehaviour
    {
        [Tooltip("Radio / world.json equipment id, e.g. exc-0")]
        public string excavatorId = "";
        [Tooltip("True while the radio says 'digging'; false = 'holding'")]
        public bool digging;

        [Header("Swing")]
        [Tooltip("Degrees either side of the base heading (face side = -amp, truck side = +amp)")]
        public float swingAmplitude = 60f;
        [Tooltip("Extra offset of the swing centre from the base heading (deg)")]
        public float swingCenterOffset = 0f;
        [Tooltip("Seconds for one full dig cycle (dig, swing, dump, swing back)")]
        public float cyclePeriod = 8f;
        [Tooltip("Smoothing (s) when starting/stopping or changing state")]
        public float smoothTime = 0.45f;

        [Header("Dig bob (child pivot)")]
        [Tooltip("Child to bob while digging at the face. Auto: child named dig_pivot/DigPivot/pivot/boom, else 'model'")]
        public Transform bobPivot;
        public float bobHeight = 0.12f;
        public float bobPitchDeg = 2f;

        [Header("Articulated rig (auto: children named house/boom/stick/bucket)")]
        public Transform house, boom, stick, bucket;
        [Tooltip("House yaw at the truck (dump) side, deg. Negative = toward the cab side after glTFast's X mirror, matching the browser preview")]
        public float truckSideYawDeg = -97f;
        [Tooltip("Joint tracking rate while digging / easing rate back to rest (1/s)")]
        public float rigTrackRate = 8f, rigRestRate = 2.5f;

        public bool Digging
        {
            get => digging;
            set => digging = value;
        }

        // cycle segments (fractions of the period): dig dwell at face, swing to truck, dump dwell, swing back
        const float DigEnd = 0.18f, SwingOutEnd = 0.5f, DumpEnd = 0.62f;
        const float ZeroCrossPhase = (DumpEnd + 1f) * 0.5f; // middle of the swing back: offset == 0

        Quaternion baseRotation;
        bool baseCaptured;
        float phase = ZeroCrossPhase;
        float offset, offsetVel;
        float bobWeight, bobVel;
        bool wasDigging;
        Vector3 pivotRestPos;
        Quaternion pivotRestRot;
        bool pivotCaptured;

        // dig cycle keyframes: phase 0..1, house yaw (fraction of truckSideYawDeg), boom, stick, bucket (deg about local X;
        // + = boom down / stick in / bucket curl; all 0 = modelled rest pose). Mirrors EXC_KEYS in envgen/preview/index.html.
        static readonly float[][] RigKeys =
        {
            new[] { 0.00f, 0f, 12f, -42f, -60f },   // at the face, reaching out
            new[] { 0.12f, 0f, 20f, -50f, -85f },   // boom down, stick out, bucket open
            new[] { 0.30f, 0f, 14f, -12f, -10f },   // dig: stick in + bucket curl
            new[] { 0.42f, 0f, -14f, -5f, 0f },     // boom up, full bucket
            new[] { 0.60f, 1f, -14f, -15f, 0f },    // swing to the truck side
            new[] { 0.70f, 1f, -12f, -35f, -110f }, // dump: stick out, bucket open
            new[] { 0.78f, 1f, -12f, -35f, -100f },
            new[] { 1.00f, 0f, 12f, -42f, -60f },   // swing back to the face
        };

        bool rigMode;
        Quaternion houseRest, boomRest, stickRest, bucketRest;
        readonly float[] rigPose = new float[4];
        float rigPhase;

        void Awake()
        {
            CaptureBase();
            if (house == null) house = FindDeep(transform, "house");
            if (boom == null) boom = FindDeep(transform, "boom");
            if (stick == null) stick = FindDeep(transform, "stick");
            if (bucket == null) bucket = FindDeep(transform, "bucket");
            rigMode = house != null && boom != null && stick != null && bucket != null;
            if (rigMode)
            {
                houseRest = house.localRotation; boomRest = boom.localRotation;
                stickRest = stick.localRotation; bucketRest = bucket.localRotation;
                rigPhase = Mathf.Abs(excavatorId.GetHashCode() % 1000) / 1000f; // de-sync several excavators
                return;
            }
            if (bobPivot == null) bobPivot = FindPivot();
            CapturePivot();
        }

        void UpdateRig(float dt)
        {
            float[] goal = { 0f, 0f, 0f, 0f };
            if (digging)
            {
                rigPhase = Mathf.Repeat(rigPhase + dt / Mathf.Max(1f, cyclePeriod), 1f);
                int i = 0;
                while (i < RigKeys.Length - 2 && rigPhase > RigKeys[i + 1][0]) i++;
                float[] a = RigKeys[i], b = RigKeys[i + 1];
                float u = Smoother(Mathf.InverseLerp(a[0], b[0], rigPhase));
                for (int j = 0; j < 4; j++) goal[j] = Mathf.Lerp(a[j + 1], b[j + 1], u);
                goal[0] *= truckSideYawDeg;
            }
            float k = 1f - Mathf.Exp(-dt * (digging ? rigTrackRate : rigRestRate));
            for (int j = 0; j < 4; j++) rigPose[j] += (goal[j] - rigPose[j]) * k;
            house.localRotation = houseRest * Quaternion.Euler(0f, rigPose[0], 0f);
            boom.localRotation = boomRest * Quaternion.Euler(rigPose[1], 0f, 0f);
            stick.localRotation = stickRest * Quaternion.Euler(rigPose[2], 0f, 0f);
            bucket.localRotation = bucketRest * Quaternion.Euler(rigPose[3], 0f, 0f);
        }

        static float Smoother(float t)
        {
            t = Mathf.Clamp01(t);
            return t * t * t * (t * (t * 6f - 15f) + 10f);
        }

        void OnEnable()
        {
            if (!baseCaptured) CaptureBase();
        }

        /// <summary>Re-reads the current rotation as the base heading (call after moving the excavator at runtime).</summary>
        public void CaptureBase()
        {
            baseRotation = transform.rotation;
            baseCaptured = true;
            offset = 0f;
            offsetVel = 0f;
        }

        void CapturePivot()
        {
            pivotCaptured = bobPivot != null && bobPivot != transform;
            if (!pivotCaptured) return;
            pivotRestPos = bobPivot.localPosition;
            pivotRestRot = bobPivot.localRotation;
        }

        Transform FindPivot()
        {
            string[] names = { "dig_pivot", "DigPivot", "pivot", "Pivot", "boom", "Boom" };
            foreach (var n in names)
            {
                var t = FindDeep(transform, n);
                if (t != null) return t;
            }
            return transform.Find("model");
        }

        static Transform FindDeep(Transform t, string name)
        {
            for (int i = 0; i < t.childCount; i++)
            {
                var c = t.GetChild(i);
                if (c.name == name) return c;
                var r = FindDeep(c, name);
                if (r != null) return r;
            }
            return null;
        }

        void Update()
        {
            float dt = Time.deltaTime;
            if (dt <= 0f) return;
            if (rigMode) { UpdateRig(dt); return; }
            float period = Mathf.Max(1f, cyclePeriod);

            if (digging && !wasDigging) phase = ZeroCrossPhase; // start where the cycle passes the base heading
            wasDigging = digging;

            float target = 0f, bobTarget = 0f, bobWave = 0f;
            if (digging)
            {
                phase = Mathf.Repeat(phase + dt / period, 1f);
                target = CycleOffset(phase) * swingAmplitude + swingCenterOffset;
                if (phase < DigEnd)
                {
                    bobTarget = 1f;
                    bobWave = Mathf.Sin(phase / DigEnd * Mathf.PI * 2f); // one scoop down/up at the face
                }
            }

            offset = Mathf.SmoothDamp(offset, target, ref offsetVel, Mathf.Max(0.01f, smoothTime), Mathf.Infinity, dt);
            if (!digging && Mathf.Abs(offset) < 0.01f && Mathf.Abs(offsetVel) < 0.01f) { offset = 0f; offsetVel = 0f; }
            transform.rotation = baseRotation * Quaternion.Euler(0f, offset, 0f);

            if (pivotCaptured && bobPivot != null)
            {
                bobWeight = Mathf.SmoothDamp(bobWeight, bobTarget, ref bobVel, 0.25f, Mathf.Infinity, dt);
                float w = bobWeight * bobWave;
                bobPivot.localPosition = pivotRestPos + Vector3.down * (bobHeight * Mathf.Max(0f, w));
                bobPivot.localRotation = pivotRestRot * Quaternion.Euler(bobPitchDeg * w, 0f, 0f);
            }
        }

        /// <summary>-1 at the face side, +1 at the truck side, eased swings, dwell at both ends.</summary>
        static float CycleOffset(float p)
        {
            if (p < DigEnd) return -1f;
            if (p < SwingOutEnd) return Mathf.Lerp(-1f, 1f, Ease((p - DigEnd) / (SwingOutEnd - DigEnd)));
            if (p < DumpEnd) return 1f;
            return Mathf.Lerp(1f, -1f, Ease((p - DumpEnd) / (1f - DumpEnd)));
        }

        static float Ease(float t)
        {
            t = Mathf.Clamp01(t);
            return t * t * (3f - 2f * t);
        }

        void OnDisable()
        {
            if (rigMode)
            {
                house.localRotation = houseRest; boom.localRotation = boomRest;
                stick.localRotation = stickRest; bucket.localRotation = bucketRest;
                for (int j = 0; j < 4; j++) rigPose[j] = 0f;
            }
            if (baseCaptured) transform.rotation = baseRotation;
            if (pivotCaptured && bobPivot != null)
            {
                bobPivot.localPosition = pivotRestPos;
                bobPivot.localRotation = pivotRestRot;
            }
            offset = offsetVel = 0f;
            bobWeight = bobVel = 0f;
            wasDigging = false;
        }
    }
}

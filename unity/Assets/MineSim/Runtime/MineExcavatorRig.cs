using UnityEngine;

namespace MineSim
{
    /// <summary>
    /// Whole-model excavator animation (the imported excavator is a single mesh, so the whole transform swings).
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

        void Awake()
        {
            CaptureBase();
            if (bobPivot == null) bobPivot = FindPivot();
            CapturePivot();
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

using System.Collections.Generic;
using UnityEngine;

namespace MineSim
{
    /// <summary>
    /// Keeps a kinematic truck (the team's <c>TruckAgent</c>: bicycle model, x/z only, constant y, yaw-only rotation)
    /// glued to uneven ground such as the scanned quarry.
    ///
    /// Ordering (why this does not fight TruckAgent):
    ///   1. This runs first in every FixedUpdate (<c>DefaultExecutionOrder(-100)</c>, TruckAgent is at 0).
    ///   2. It writes <c>rb.position</c> with the corrected y. On a kinematic body that teleports the PhysX pose at once,
    ///      so it is what <c>body.position</c> returns from now on.
    ///   3. TruckAgent.FixedUpdate then reads <c>body.position</c> (already at the ground height) and calls
    ///      <c>MovePosition(body.position + Euler(0,yaw,0) * forward * v * dt)</c>: its forward vector is horizontal, so the
    ///      kinematic target keeps our y. The target is applied in the physics step that follows all FixedUpdates.
    ///   4. Next step, <c>rb.position</c> is that target and we correct y again. The height lags by one step of travel
    ///      (at most 8 m/s * 0.02 s = 0.16 m along the ground, about 1.6 cm of height on a 10 % grade).
    /// Yaw, <c>rb.rotation</c> and the root transform rotation are never touched (TruckAgent derives yaw from
    /// <c>body.rotation.eulerAngles.y</c> and reports <c>transform.eulerAngles.y</c> as the pose). Tilt is applied to child
    /// transforms only (the model, and the sensor mounts when <see cref="tiltSensors"/> is on), pivoting about the root origin
    /// (bottom centre of the truck, per spec).
    /// </summary>
    [DefaultExecutionOrder(-100)]
    [DisallowMultipleComponent]
    [RequireComponent(typeof(Rigidbody))]
    public class MineGroundFollow : MonoBehaviour
    {
        [Header("Ground")]
        [Tooltip("Raycast only the imported world's ground (MineWorldRoot.groundCollider or Terrain) with Collider.Raycast. " +
                 "Ignores layers, trucks, pedestrians and props. Falls back to the physics query below when no world is found.")]
        public bool useWorldGround = true;
        [Tooltip("When the scan ground misses (holes, beyond the pit edge), also try MineWorldRoot.surroundCollider (assets.surround)")]
        public bool surroundFallback = true;
        [Tooltip("Physics query mask used when useWorldGround is off or no MineWorldRoot exists")]
        public LayerMask groundMask = ~0;
        [Tooltip("Physics query: ignore colliders that belong to a Rigidbody (trucks, pedestrians)")]
        public bool ignoreRigidbodyColliders = true;
        [Tooltip("Optional explicit ground colliders (overrides the world lookup when set)")]
        public Collider[] groundColliders;
        [Tooltip("Metres added to the ground height (0 = model origin on the ground; the team's trucks have their origin at the bottom centre)")]
        public float heightOffset = 0f;
        [Tooltip("Rays start this far above the current height (lets the truck climb steps without snapping onto ledges far above)")]
        public float probeUp = 8f;
        [Tooltip("Ray length below the current height")]
        public float probeDown = 150f;
        [Tooltip("If the local probe misses, retry from this high above (finds the top surface after a big drop/teleport)")]
        public float highProbe = 1000f;

        [Header("Height smoothing")]
        [Tooltip("Exponential smoothing time (s) of the height; 0 = exact. Upward motion is never slower than this so the truck doesn't sink into rises.")]
        public float heightSmoothTime = 0.06f;
        [Tooltip("Jumps larger than this (m) snap instantly (teleports, spawn)")]
        public float snapDistance = 4f;

        [Header("Wheel contact points (truck local, metres)")]
        [Tooltip("Derive axle/track from the root BoxCollider (the team's truck collider = renderer bounds)")]
        public bool autoFromCollider = true;
        public float frontAxleZ = 2.6f;
        public float rearAxleZ = -1.7f;
        public float halfTrack = 1.6f;
        [Tooltip("Height = average of the four wheel samples (true) or the centre sample (false)")]
        public bool averageWheelHeights = true;

        [Header("Visual tilt")]
        public bool tiltVisual = true;
        [Tooltip("Also tilt sensor mounts (children with a Camera, SensorCamera or LidarSensor). Recommended on slopes: " +
                 "a level depth camera/lidar sees an up-ramp as a wall. MineCabCamera follows this frame automatically.")]
        public bool tiltSensors = false;
        [Tooltip("Explicit transforms to tilt (children of this truck). Empty = auto: every direct child with a Renderer below it, sensors excluded.")]
        public Transform[] visuals;
        [Range(0f, 45f)] public float maxTiltDeg = 25f;
        [Tooltip("Tilt smoothing time (s)")]
        public float tiltSmoothTime = 0.15f;

        [Header("Debug")]
        public bool drawGizmos = true;

        /// <summary>Current tilt relative to the yaw-only root (applied about the root origin).</summary>
        public Quaternion TiltLocal { get; private set; } = Quaternion.identity;
        /// <summary>True when the last FixedUpdate found ground.</summary>
        public bool Grounded { get; private set; }
        /// <summary>Last ground height under the truck (world y).</summary>
        public float GroundY { get; private set; }

        struct Tilted { public Transform t; public Vector3 pos; public Quaternion rot; }

        Rigidbody rb;
        readonly List<Collider> ground = new List<Collider>();
        readonly List<Collider> fallbackGround = new List<Collider>();
        readonly List<Tilted> tilted = new List<Tilted>();
        Collider[] own;
        readonly RaycastHit[] hits = new RaycastHit[16];
        bool hasY;
        float smoothY;
        bool tiltInit;

        void Awake()
        {
            rb = GetComponent<Rigidbody>();
            own = GetComponentsInChildren<Collider>(true);
            if (autoFromCollider) DeriveAxles();
            ResolveGround();
            CaptureTilted();
        }

        void OnEnable()
        {
            hasY = false;
            tiltInit = false;
        }

        void OnDisable()
        {
            RestoreTilted();
        }

        /// <summary>Re-reads the world ground (call after reimporting the world at runtime).</summary>
        public void ResolveGround()
        {
            ground.Clear();
            fallbackGround.Clear();
            if (groundColliders != null)
                foreach (var c in groundColliders) if (c != null) ground.Add(c);
            if (ground.Count > 0 || !useWorldGround) return;
            var world = FindFirstObjectByType<MineWorldRoot>();
            if (world == null) return;
            if (world.groundCollider != null) ground.Add(world.groundCollider);
            if (world.terrain != null)
            {
                var tc = world.terrain.GetComponent<TerrainCollider>();
                if (tc != null) ground.Add(tc);
            }
            if (surroundFallback && world.surroundCollider != null) fallbackGround.Add(world.surroundCollider);
        }

        void DeriveAxles()
        {
            var box = GetComponent<BoxCollider>();
            if (box == null) return;
            Vector3 c = box.center, s = box.size;
            if (s.z < 1f || s.x < 0.5f) return;
            frontAxleZ = c.z + s.z * 0.30f;
            rearAxleZ = c.z - s.z * 0.28f;
            halfTrack = s.x * 0.38f;
        }

        void CaptureTilted()
        {
            tilted.Clear();
            var list = new List<Transform>();
            if (visuals != null && visuals.Length > 0)
            {
                foreach (var v in visuals) if (v != null && v != transform) list.Add(v);
            }
            else
            {
                for (int i = 0; i < transform.childCount; i++)
                {
                    var ch = transform.GetChild(i);
                    if (IsSensor(ch)) continue;
                    if (ch.GetComponentInChildren<Renderer>(true) != null) list.Add(ch);
                }
            }
            if (tiltSensors)
                for (int i = 0; i < transform.childCount; i++)
                {
                    var ch = transform.GetChild(i);
                    if (IsSensor(ch) && !list.Contains(ch)) list.Add(ch);
                }
            foreach (var t in list) tilted.Add(new Tilted { t = t, pos = t.localPosition, rot = t.localRotation });
        }

        void RestoreTilted()
        {
            foreach (var x in tilted)
                if (x.t != null) x.t.SetLocalPositionAndRotation(x.pos, x.rot);
            TiltLocal = Quaternion.identity;
        }

        static bool IsSensor(Transform t)
        {
            if (t.GetComponent<Camera>() != null) return true;
            var mbs = t.GetComponents<MonoBehaviour>();
            foreach (var mb in mbs)
            {
                if (mb == null) continue;
                string n = mb.GetType().Name;
                if (n == "SensorCamera" || n == "LidarSensor" || n == "MineCabCamera") return true;
            }
            return false;
        }

        void FixedUpdate()
        {
            Vector3 p = rb.position;
            Quaternion yaw = Quaternion.Euler(0f, rb.rotation.eulerAngles.y, 0f);
            float refY = hasY ? smoothY - heightOffset : p.y - heightOffset;

            bool okC = Sample(p, refY, out float hC);
            bool okFL = Sample(p + yaw * new Vector3(-halfTrack, 0f, frontAxleZ), refY, out float hFL);
            bool okFR = Sample(p + yaw * new Vector3(halfTrack, 0f, frontAxleZ), refY, out float hFR);
            bool okRL = Sample(p + yaw * new Vector3(-halfTrack, 0f, rearAxleZ), refY, out float hRL);
            bool okRR = Sample(p + yaw * new Vector3(halfTrack, 0f, rearAxleZ), refY, out float hRR);

            int nWheels = (okFL ? 1 : 0) + (okFR ? 1 : 0) + (okRL ? 1 : 0) + (okRR ? 1 : 0);
            Grounded = okC || nWheels > 0;
            if (!Grounded) return;   // no hit: keep the last height (and tilt)

            float centre = okC ? hC : ((okFL ? hFL : 0f) + (okFR ? hFR : 0f) + (okRL ? hRL : 0f) + (okRR ? hRR : 0f)) / nWheels;
            if (!okFL) hFL = centre;
            if (!okFR) hFR = centre;
            if (!okRL) hRL = centre;
            if (!okRR) hRR = centre;
            float target = (averageWheelHeights && nWheels >= 2 ? (hFL + hFR + hRL + hRR) * 0.25f : centre);
            GroundY = target;
            target += heightOffset;

            float dt = Time.fixedDeltaTime;
            if (!hasY || Mathf.Abs(target - smoothY) > snapDistance || heightSmoothTime <= 0f)
                smoothY = target;
            else
            {
                float k = 1f - Mathf.Exp(-dt / heightSmoothTime);
                float next = Mathf.Lerp(smoothY, target, k);
                if (target > smoothY) next = Mathf.Max(next, target - 0.25f);   // never lag far below a rise
                smoothY = next;
            }
            hasY = true;

            if (Mathf.Abs(p.y - smoothY) > 1e-5f)
            {
                p.y = smoothY;
                rb.position = p;   // teleport the kinematic body now; TruckAgent's MovePosition continues from here
            }

            if (tiltVisual || tiltSensors) UpdateTilt(hFL, hFR, hRL, hRR, dt);
        }

        void UpdateTilt(float hFL, float hFR, float hRL, float hRR, float dt)
        {
            float wheelbase = Mathf.Max(0.5f, frontAxleZ - rearAxleZ);
            float track = Mathf.Max(0.5f, halfTrack * 2f);
            var f = new Vector3(0f, (hFL + hFR - hRL - hRR) * 0.5f, wheelbase);
            var r = new Vector3(track, (hFR + hRR - hFL - hRL) * 0.5f, 0f);
            var up = Vector3.Cross(f, r);
            Quaternion target = up.sqrMagnitude > 1e-8f ? Quaternion.LookRotation(f.normalized, up.normalized) : Quaternion.identity;
            float ang = Quaternion.Angle(Quaternion.identity, target);
            if (ang > maxTiltDeg) target = Quaternion.Slerp(Quaternion.identity, target, maxTiltDeg / ang);

            if (!tiltInit || tiltSmoothTime <= 0f) TiltLocal = target;
            else TiltLocal = Quaternion.Slerp(TiltLocal, target, 1f - Mathf.Exp(-dt / tiltSmoothTime));
            tiltInit = true;

            for (int i = 0; i < tilted.Count; i++)
            {
                var x = tilted[i];
                if (x.t == null) continue;
                x.t.SetLocalPositionAndRotation(TiltLocal * x.pos, TiltLocal * x.rot);
            }
        }

        /// <summary>World-space rotation of the tilted frame (root yaw * tilt). Sensor rigs use this when tiltSensors is on.</summary>
        public Quaternion TiltedFrameRotation => transform.rotation * TiltLocal;

        /// <summary>Ground height under world (x, z) near <paramref name="refY"/>. False when nothing is hit.</summary>
        public bool Sample(Vector3 at, float refY, out float y)
        {
            float top = refY + probeUp;
            if (Probe(at.x, at.z, top, probeUp + probeDown, out y)) return true;
            return Probe(at.x, at.z, refY + highProbe, highProbe + probeDown, out y);
        }

        bool Probe(float x, float z, float top, float length, out float y)
        {
            y = 0f;
            var ray = new Ray(new Vector3(x, top, z), Vector3.down);
            if (ground.Count > 0)
            {
                float best = Nearest(ground, ray, length);
                if (float.IsPositiveInfinity(best) && fallbackGround.Count > 0) best = Nearest(fallbackGround, ray, length);
                if (float.IsPositiveInfinity(best)) return false;
                y = top - best;
                return true;
            }

            int n = Physics.RaycastNonAlloc(ray, hits, length, groundMask, QueryTriggerInteraction.Ignore);
            float bestD = float.PositiveInfinity;
            for (int i = 0; i < n; i++)
            {
                var c = hits[i].collider;
                if (c == null || IsOwn(c)) continue;
                if (ignoreRigidbodyColliders && c.attachedRigidbody != null) continue;
                if (hits[i].distance < bestD) bestD = hits[i].distance;
            }
            if (float.IsPositiveInfinity(bestD)) return false;
            y = top - bestD;
            return true;
        }

        static float Nearest(List<Collider> cols, Ray ray, float length)
        {
            float best = float.PositiveInfinity;
            for (int i = 0; i < cols.Count; i++)
            {
                var g = cols[i];
                if (g == null || !g.enabled || !g.gameObject.activeInHierarchy) continue;
                if (g.Raycast(ray, out RaycastHit h, length) && h.distance < best) best = h.distance;
            }
            return best;
        }

        bool IsOwn(Collider c)
        {
            if (own == null) return false;
            for (int i = 0; i < own.Length; i++) if (own[i] == c) return true;
            return false;
        }

        /// <summary>Teleport to (x, z) with the given heading, snapped to the ground. Works in edit mode too.</summary>
        public void PlaceAt(Vector3 xz, float headingDeg)
        {
            if (rb == null) rb = GetComponent<Rigidbody>();
            if (ground.Count == 0) ResolveGround();
            var p = new Vector3(xz.x, xz.y, xz.z);
            if (Sample(p, p.y, out float gy)) p.y = gy + heightOffset;
            var rot = Quaternion.Euler(0f, headingDeg, 0f);
            transform.SetPositionAndRotation(p, rot);
            if (Application.isPlaying && rb != null)
            {
                rb.position = p;
                rb.rotation = rot;
            }
            hasY = false;
        }

        void OnDrawGizmosSelected()
        {
            if (!drawGizmos) return;
            Gizmos.color = Grounded ? Color.green : Color.red;
            var yaw = Quaternion.Euler(0f, transform.eulerAngles.y, 0f);
            var p = transform.position;
            foreach (var l in new[]
                     {
                         new Vector3(-halfTrack, 0f, frontAxleZ), new Vector3(halfTrack, 0f, frontAxleZ),
                         new Vector3(-halfTrack, 0f, rearAxleZ), new Vector3(halfTrack, 0f, rearAxleZ),
                     })
                Gizmos.DrawWireSphere(p + yaw * l, 0.4f);
        }
    }
}

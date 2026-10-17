using System.Collections.Generic;
using UnityEngine;

namespace MineSim
{
    /// <summary>
    /// Cab-mounted camera rig for a truck: holds the camera at a mount pose in the truck's frame (metres, truck scale
    /// ignored), stabilises the head against jitter, adds light head-bob/engine vibration and a little roll in turns.
    ///
    /// Three ways to use it:
    ///  * On the team's truck camera (has SensorCamera): positions the transform only. FOV, targetTexture and rect are never touched.
    ///    MineLookController does this automatically and keeps the existing mount pose (CaptureMountFromCurrentPose).
    ///  * On any camera: set <see cref="target"/> (defaults to the parent).
    ///  * On a truck without a camera: creates its own "CabCam_&lt;name&gt;" camera (hFOV from settings, default 90).
    ///    That path is for editor previews; it adds a full extra render per truck.
    /// </summary>
    [DisallowMultipleComponent]
    public class MineCabCamera : MonoBehaviour
    {
        /// <summary>All enabled cab rigs (used by MineSplitScreenLayout and MineMultiViewLabels).</summary>
        public static readonly List<MineCabCamera> All = new List<MineCabCamera>();

        [Tooltip("Truck transform. Empty: the parent when this sits on a camera, else this object")]
        public Transform target;
        [Tooltip("Shown in the split-screen preview. Empty: SensorCamera.cameraName or the truck name")]
        public string displayName;
        public int order;
        [Tooltip("Mount position in the truck frame (m). Default = driver seat of a ~4.4 m tall haul truck, left side")]
        public Vector3 mountOffset = new Vector3(-1.1f, 3.6f, 2.2f);
        [Tooltip("Mount rotation in the truck frame (deg). X > 0 looks down over the hood")]
        public Vector3 mountEuler = new Vector3(8f, 0f, 0f);
        public bool headMotion = true;
        [Tooltip("When the driven camera has a SensorCamera, keep its current pose as the mount (only stabilisation + vibration are added)")]
        public bool keepSensorMountPose = true;
        [Tooltip("Set fieldOfView for an existing camera without SensorCamera. Cameras created by this component always get it")]
        public bool controlFov = false;
        [Tooltip("Camera driven by this rig. Found or created on enable")]
        public Camera cam;
        public MineLookSettings settingsOverride;

        public float SpeedKmh => speed * 3.6f;
        public bool HasSensor => sensor;

        public string DisplayName
        {
            get
            {
                if (!string.IsNullOrEmpty(displayName)) return displayName;
                if (target != null) return target.name;
                return name;
            }
        }

        private GameObject ownedCam;
        private MineAmbientDust motes;
        private bool sensor;
        private bool captured;
        private bool init;
        private Vector3 lastPos;
        private float lastYaw;
        private float speed;
        private float yawRate;
        private Quaternion smoothRot = Quaternion.identity;
        private float bobPhase;
        private float seed;

        /// <summary>Adds (or returns) a rig on <paramref name="truck"/>; it creates its own camera unless one is found.</summary>
        public static MineCabCamera Attach(Transform truck, string label = null)
        {
            if (truck == null) return null;
            var c = truck.GetComponent<MineCabCamera>();
            if (c == null) c = truck.gameObject.AddComponent<MineCabCamera>();
            c.target = truck;
            if (!string.IsNullOrEmpty(label)) c.displayName = label;
            return c;
        }

        public static bool IsCabCamera(Camera c)
        {
            if (c == null) return false;
            for (int i = 0; i < All.Count; i++)
                if (All[i] != null && All[i].cam == c) return true;
            return false;
        }

        private void OnEnable()
        {
            if (!All.Contains(this)) All.Add(this);
            seed = Random.Range(0f, 100f);
            init = false;
            EnsureCamera();
        }

        private void OnDisable()
        {
            All.Remove(this);
            if (ownedCam != null) ownedCam.SetActive(false);
            if (motes != null) motes.gameObject.SetActive(false);
        }

        private void OnDestroy()
        {
            if (ownedCam != null) Destroy(ownedCam);
            if (motes != null) Destroy(motes.gameObject);
        }

        private void EnsureCamera()
        {
            var s = MineLookSettings.Resolve(settingsOverride);
            if (cam == null) cam = GetComponent<Camera>();
            if (cam == null)
            {
                // on a truck: look for the team's sensor camera under it first
                foreach (var c in GetComponentsInChildren<Camera>(true))
                    if (MineLookUtil.HasSensor(c, s)) { cam = c; break; }
            }
            if (cam == null)
            {
                if (ownedCam == null)
                {
                    ownedCam = new GameObject("CabCam_" + DisplayName);
                    var c = ownedCam.AddComponent<Camera>();
                    c.nearClipPlane = 0.1f;
                    c.depth = 1f;
                }
                ownedCam.SetActive(true);
                cam = ownedCam.GetComponent<Camera>();
            }
            if (target == null) target = cam.transform == transform ? transform.parent : transform;
            sensor = MineLookUtil.HasSensor(cam, s);
            if (string.IsNullOrEmpty(displayName) && sensor)
                displayName = MineLookUtil.ReadStringField(MineLookUtil.FindComponentByName(cam.gameObject, s.sensorComponentName), "cameraName");

            if (sensor && keepSensorMountPose && !captured && target != null && cam.transform != target)
                CaptureMountFromCurrentPose();

            MineLookUtil.ConfigureCamera(cam, s, true, 2500f);
            ApplyFov(s);
            EnsureMotes(s);
        }

        private void ApplyFov(MineLookSettings s)
        {
            if (cam == null || sensor) return;
            if (ownedCam == null && !controlFov) return;
            cam.fieldOfView = s.cabFovIsHorizontal ? Camera.HorizontalToVerticalFieldOfView(s.cabFov, Mathf.Max(0.1f, cam.aspect)) : s.cabFov;
        }

        private void EnsureMotes(MineLookSettings s)
        {
            if (!s.cabDustMotes || !s.ambientDust || cam == null) return;
            if (motes == null)
            {
                var go = new GameObject("CabMotes");
                go.AddComponent<ParticleSystem>();
                motes = go.AddComponent<MineAmbientDust>();
                motes.mode = MineAmbientDust.Mode.CabMotes;
            }
            motes.gameObject.SetActive(true);
            motes.Configure(s, Vector3.zero, Vector2.one);
        }

        /// <summary>Keep the camera exactly where it is now relative to the truck (used for auto-attach to existing cameras).</summary>
        public void CaptureMountFromCurrentPose()
        {
            if (cam == null || target == null) return;
            Quaternion inv = Quaternion.Inverse(TargetFrame());
            mountOffset = inv * (cam.transform.position - target.position);
            mountEuler = (inv * cam.transform.rotation).eulerAngles;
            captured = true;
            init = false;
        }

        private MineGroundFollow groundFollow;
        private Transform groundFollowFor;

        /// <summary>Truck frame for the mount: the root rotation, tilted by MineGroundFollow when it tilts the sensors.</summary>
        private Quaternion TargetFrame()
        {
            if (groundFollowFor != target)
            {
                groundFollow = target.GetComponent<MineGroundFollow>();
                groundFollowFor = target;
            }
            if (groundFollow != null && groundFollow.isActiveAndEnabled && groundFollow.tiltSensors)
                return groundFollow.TiltedFrameRotation;
            return target.rotation;
        }

        private void LateUpdate()
        {
            if (cam == null || target == null || cam.transform == target) return;
            float dt = Time.deltaTime;
            if (dt <= 0f) return;
            var s = MineLookSettings.Resolve(settingsOverride);

            Vector3 tp = target.position;
            float yaw = target.rotation.eulerAngles.y;
            Quaternion tr = TargetFrame();
            Quaternion mountRot = tr * Quaternion.Euler(mountEuler);
            if (!init)
            {
                lastPos = tp;
                lastYaw = yaw;
                smoothRot = mountRot;
                speed = 0f;
                yawRate = 0f;
                init = true;
            }
            float v = (tp - lastPos).magnitude / dt;
            if (v > 150f) v = 0f;
            speed = Mathf.Lerp(speed, v, MineLookUtil.Damp(4f, dt));
            yawRate = Mathf.Lerp(yawRate, Mathf.DeltaAngle(lastYaw, yaw) / dt, MineLookUtil.Damp(3f, dt));
            lastPos = tp;
            lastYaw = yaw;

            Vector3 pos = tp + tr * mountOffset;
            Quaternion rot;
            if (headMotion)
            {
                smoothRot = Quaternion.Slerp(smoothRot, mountRot, MineLookUtil.Damp(Mathf.Max(0.5f, s.cabRotationSmoothing), dt));
                float sp01 = Mathf.Clamp01(speed / 12f);
                bobPhase += dt * s.cabBobFrequency * Mathf.PI * 2f * (0.4f + sp01);
                Vector3 bob = new Vector3(Mathf.Sin(bobPhase * 0.5f) * 0.5f, Mathf.Sin(bobPhase), 0f) * (s.cabBobAmplitude * (0.25f + sp01));
                float t = Time.time * s.cabVibrationFrequency + seed;
                float vib = s.cabVibrationDeg * (0.5f + sp01);
                float vx = (Mathf.PerlinNoise(t, seed) - 0.5f) * 2f * vib;
                float vy = (Mathf.PerlinNoise(seed, t) - 0.5f) * 2f * vib;
                float lean = Mathf.Clamp(-yawRate * Mathf.Deg2Rad * s.cabLeanDeg * sp01, -6f, 6f);
                rot = smoothRot * Quaternion.Euler(vx, vy, lean);
                pos += smoothRot * bob;
            }
            else rot = mountRot;

            cam.transform.SetPositionAndRotation(pos, rot);
            ApplyFov(s);

            if (motes != null && motes.gameObject.activeSelf)
                motes.transform.SetPositionAndRotation(pos + rot * new Vector3(0f, 0f, 14f), rot);
        }
    }
}

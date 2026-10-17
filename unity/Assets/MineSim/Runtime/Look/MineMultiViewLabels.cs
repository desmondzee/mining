using System.Collections.Generic;
using UnityEngine;
using UnityEngine.Rendering;

namespace MineSim
{
    /// <summary>
    /// MineBillboard faces Camera.main only. With several cameras (overview + truck SensorCameras rendering to
    /// textures) this re-orients every label right before each camera renders, so labels read correctly in every view.
    /// Labels are hidden in non-overview game cameras (truck cams) unless MineLookSettings.labelsInCabViews is on.
    /// </summary>
    public class MineMultiViewLabels : MonoBehaviour
    {
        public MineLookSettings settingsOverride;
        public float rescanInterval = 2f;

        private readonly List<MineBillboard> labels = new List<MineBillboard>();
        private readonly HashSet<Camera> overviewCams = new HashSet<Camera>();
        private float nextScan;

        private void OnEnable()
        {
            RenderPipelineManager.beginCameraRendering += OnBeginCamera;
            nextScan = 0f;
        }

        private void OnDisable()
        {
            RenderPipelineManager.beginCameraRendering -= OnBeginCamera;
        }

        private void Update()
        {
            if (Time.unscaledTime < nextScan) return;
            nextScan = Time.unscaledTime + Mathf.Max(0.25f, rescanInterval);
            labels.Clear();
            labels.AddRange(Object.FindObjectsByType<MineBillboard>(FindObjectsSortMode.None));
            overviewCams.Clear();
            foreach (var o in Object.FindObjectsByType<MineOverviewCamera>(FindObjectsSortMode.None))
            {
                var c = o.GetComponent<Camera>();
                if (c != null) overviewCams.Add(c);
            }
        }

        private void OnBeginCamera(ScriptableRenderContext ctx, Camera cam)
        {
            if (cam == null) return;
            if (cam.cameraType != CameraType.Game && cam.cameraType != CameraType.SceneView) return;
            var s = MineLookSettings.Resolve(settingsOverride);
            bool hide = cam.cameraType == CameraType.Game && !s.labelsInCabViews && overviewCams.Count > 0 && !overviewCams.Contains(cam);
            Vector3 cp = cam.transform.position;
            Vector3 up = cam.transform.up;
            for (int i = 0; i < labels.Count; i++)
            {
                var b = labels[i];
                if (b == null || !b.isActiveAndEnabled) continue;
                var t = b.transform;
                if (hide)
                {
                    t.localScale = Vector3.zero;
                    continue;
                }
                Vector3 dir = t.position - cp;
                if (dir.sqrMagnitude < 1e-6f) continue;
                t.rotation = Quaternion.LookRotation(dir, up);
                float k = 1f;
                if (b.scaleWithDistance && b.referenceDistance > 0f)
                    k = Mathf.Clamp(dir.magnitude / b.referenceDistance, b.minScale, b.maxScale);
                t.localScale = b.BaseScale * k;
            }
        }
    }
}

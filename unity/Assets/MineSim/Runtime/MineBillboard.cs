using UnityEngine;

namespace MineSim
{
    /// <summary>Keeps a label facing the active camera; optionally keeps a roughly constant on-screen size.</summary>
    public class MineBillboard : MonoBehaviour
    {
        public bool scaleWithDistance = true;
        [Tooltip("Distance (m) at which the label has its authored scale")]
        public float referenceDistance = 150f;
        public float minScale = 0.4f;
        public float maxScale = 6f;

        private Vector3 baseScale;
        private Camera cam;

        private void Awake() { baseScale = transform.localScale; }

        private void LateUpdate()
        {
            if (cam == null || !cam.isActiveAndEnabled) cam = Camera.main;
            if (cam == null) return;
            Vector3 dir = transform.position - cam.transform.position;
            if (dir.sqrMagnitude < 1e-6f) return;
            // TextMesh / TextMeshPro read correctly when their +Z points away from the viewer.
            transform.rotation = Quaternion.LookRotation(dir, cam.transform.up);
            if (scaleWithDistance && referenceDistance > 0f)
            {
                float k = Mathf.Clamp(dir.magnitude / referenceDistance, minScale, maxScale);
                transform.localScale = baseScale * k;
            }
        }
    }
}

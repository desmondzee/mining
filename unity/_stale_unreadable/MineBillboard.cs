using UnityEngine;

namespace MineSim
{
    /// <summary>Rotates labels to face the main camera.</summary>
    public class MineBillboard : MonoBehaviour
    {
        void LateUpdate()
        {
            var cam = Camera.main;
            if (cam == null) return;
            transform.rotation = Quaternion.LookRotation(transform.position - cam.transform.position, Vector3.up);
        }
    }
}

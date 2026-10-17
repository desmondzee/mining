using UnityEngine;

namespace MineSim
{
    /// <summary>
    /// Runtime hotkeys: L toggles all MineLabel objects, R toggles road LineRenderers.
    /// Uses MineInput: the Input System (optional MineSim.InputSystem assembly) or the legacy Input Manager.
    /// </summary>
    public class MineLabelToggle : MonoBehaviour
    {
        public KeyCode labelsKey = KeyCode.L;
        public KeyCode roadsKey = KeyCode.R;
        public bool labelsVisible = true;
        public bool roadsVisible = true;

        private void Update()
        {
            if (Pressed(labelsKey)) SetLabelsVisible(!labelsVisible);
            if (Pressed(roadsKey)) SetRoadsVisible(!roadsVisible);
        }

        public void SetLabelsVisible(bool visible)
        {
            labelsVisible = visible;
            foreach (var l in Object.FindObjectsByType<MineLabel>(FindObjectsInactive.Include, FindObjectsSortMode.None))
                l.gameObject.SetActive(visible);
        }

        public void SetRoadsVisible(bool visible)
        {
            roadsVisible = visible;
            foreach (var r in Object.FindObjectsByType<MineRoad>(FindObjectsInactive.Include, FindObjectsSortMode.None))
                r.SetLineVisible(visible);
        }

        private static bool Pressed(KeyCode key)
        {
            return MineInput.GetKeyDown(key);
        }
    }
}

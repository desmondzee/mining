using UnityEngine;
#if MINESIM_INPUTSYSTEM && ENABLE_INPUT_SYSTEM
using UnityEngine.InputSystem;
#endif

namespace MineSim
{
    /// <summary>
    /// Runtime hotkeys: L toggles all MineLabel objects, R toggles road LineRenderers.
    /// Works with the Input System package (if installed + active) or the legacy Input Manager.
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
            if (key == KeyCode.None) return false;
#if MINESIM_INPUTSYSTEM && ENABLE_INPUT_SYSTEM
            var kb = Keyboard.current;
            if (kb != null && System.Enum.TryParse(key.ToString(), out Key k) && k != Key.None)
            {
                try { return kb[k].wasPressedThisFrame; }
                catch (System.ArgumentOutOfRangeException) { return false; }
            }
            return false;
#elif ENABLE_LEGACY_INPUT_MANAGER
            return Input.GetKeyDown(key);
#else
            return false;
#endif
        }
    }
}

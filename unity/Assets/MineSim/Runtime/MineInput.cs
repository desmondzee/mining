using UnityEngine;

namespace MineSim
{
    /// <summary>
    /// Keyboard access for MineSim without a hard dependency on the Input System package.
    /// The optional MineSim.InputSystem assembly sets <see cref="KeyDownHook"/> at startup when com.unity.inputsystem
    /// is installed. Otherwise the legacy Input Manager is used (when enabled in Player settings).
    /// </summary>
    public static class MineInput
    {
        /// <summary>Returns true on the frame the key went down. Set by MineSim.InputSystem.</summary>
        public static System.Func<KeyCode, bool> KeyDownHook;

        public static bool GetKeyDown(KeyCode key)
        {
            if (key == KeyCode.None) return false;
            if (KeyDownHook != null) return KeyDownHook(key);
#if ENABLE_LEGACY_INPUT_MANAGER
            return Input.GetKeyDown(key);
#else
            return false;
#endif
        }
    }
}

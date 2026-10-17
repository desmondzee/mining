using UnityEngine;
#if ENABLE_INPUT_SYSTEM
using UnityEngine.InputSystem;
#endif

namespace MineSim
{
    /// <summary>Only compiled when com.unity.inputsystem is installed (defineConstraints MINESIM_INPUTSYSTEM).</summary>
    internal static class MineInputSystemHook
    {
        [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.SubsystemRegistration)]
        private static void Register()
        {
#if ENABLE_INPUT_SYSTEM
            MineInput.KeyDownHook = KeyDown;
#endif
        }

#if ENABLE_INPUT_SYSTEM
        private static bool KeyDown(KeyCode key)
        {
            var kb = Keyboard.current;
            if (kb == null) return false;
            if (!System.Enum.TryParse(key.ToString(), out Key k) || k == Key.None) return false;
            try { return kb[k].wasPressedThisFrame; }
            catch (System.ArgumentOutOfRangeException) { return false; }
        }
#endif
    }
}

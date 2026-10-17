using System.Collections.Generic;
using UnityEngine;

namespace MineSim
{
    /// <summary>
    /// OPTIONAL editor/desktop preview of the demo layout: overview on the left, up to 4 cab views on the right with an
    /// IMGUI tag per view. The real demo layout lives in the web UI, so this is not added by default
    /// (Tools/Mine Sim/Add Split-Screen Preview).
    /// Cameras that render to a texture (the team's SensorCameras) are left alone: their targetTexture is drawn into the
    /// slot. Cameras that render to the screen get their viewport rect set.
    /// </summary>
    public class MineSplitScreenLayout : MonoBehaviour
    {
        public Camera overview;
        [Range(1, 4)] public int maxCabViews = 4;
        public bool showLabels = true;
        public MineLookSettings settingsOverride;

        private readonly List<MineCabCamera> cabs = new List<MineCabCamera>();
        private readonly List<Rect> cabRects = new List<Rect>();
        private Rect overviewRect = new Rect(0f, 0f, 1f, 1f);
        private Texture2D white;
        private GUIStyle title, sub;

        private void LateUpdate()
        {
            var s = MineLookSettings.Resolve(settingsOverride);
            if (overview == null)
            {
                var o = Object.FindFirstObjectByType<MineOverviewCamera>();
                if (o != null) overview = o.GetComponent<Camera>();
            }

            cabs.Clear();
            foreach (var c in MineCabCamera.All)
                if (c != null && c.isActiveAndEnabled && c.cam != null) cabs.Add(c);
            cabs.Sort((a, b) => a.order != b.order ? a.order.CompareTo(b.order) : string.CompareOrdinal(a.DisplayName, b.DisplayName));
            int n = Mathf.Min(cabs.Count, maxCabViews);
            float split = s.splitX;
            bool full = n == 0 && s.overviewFullscreenWhenNoCabs;

            overviewRect = full ? new Rect(0f, 0f, 1f, 1f) : new Rect(0f, 0f, split, 1f);
            if (overview != null && overview.targetTexture == null) overview.rect = overviewRect;

            cabRects.Clear();
            for (int i = 0; i < cabs.Count; i++)
            {
                var cam = cabs[i].cam;
                bool toScreen = cam.targetTexture == null;
                if (i >= n)
                {
                    if (toScreen && !cabs[i].HasSensor) cam.enabled = false;
                    continue;
                }
                Rect r = CabRect(i, n, split);
                cabRects.Add(r);
                if (toScreen)
                {
                    cam.enabled = true;
                    cam.rect = r;
                    cam.depth = 1 + i;
                }
            }
        }

        private static Rect CabRect(int i, int n, float x0)
        {
            float w = 1f - x0;
            switch (n)
            {
                case 1: return new Rect(x0, 0f, w, 1f);
                case 2: return new Rect(x0, i == 0 ? 0.5f : 0f, w, 0.5f);
                case 3:
                    if (i == 0) return new Rect(x0, 0.5f, w, 0.5f);
                    return new Rect(x0 + (i - 1) * w * 0.5f, 0f, w * 0.5f, 0.5f);
                default:
                    int col = i % 2, row = i / 2;
                    return new Rect(x0 + col * w * 0.5f, row == 0 ? 0.5f : 0f, w * 0.5f, 0.5f);
            }
        }

        // viewport rect (bottom-left origin, normalised) -> GUI pixels (top-left origin)
        private static Rect ToGui(Rect v)
        {
            float W = Screen.width, H = Screen.height;
            return new Rect(v.x * W, (1f - v.yMax) * H, v.width * W, v.height * H);
        }

        private void OnGUI()
        {
            if (Event.current.type != EventType.Repaint) return;
            var s = MineLookSettings.Resolve(settingsOverride);
            if (white == null)
            {
                white = new Texture2D(1, 1, TextureFormat.RGBA32, false);
                white.SetPixel(0, 0, Color.white);
                white.Apply();
                white.hideFlags = HideFlags.DontSave;
            }
            float ui = Mathf.Max(0.5f, Screen.height / 1080f) * s.uiScale;
            if (title == null)
            {
                title = new GUIStyle(GUI.skin.label) { fontStyle = FontStyle.Bold, alignment = TextAnchor.MiddleLeft, wordWrap = false };
                sub = new GUIStyle(GUI.skin.label) { alignment = TextAnchor.MiddleLeft, wordWrap = false };
            }
            title.fontSize = Mathf.RoundToInt(17 * ui);
            sub.fontSize = Mathf.RoundToInt(12 * ui);
            title.normal.textColor = Color.white;
            sub.normal.textColor = new Color(0.86f, 0.86f, 0.86f);

            bool anyTexture = (overview != null && overview.targetTexture != null);
            foreach (var c in cabs) if (c != null && c.cam != null && c.cam.targetTexture != null) anyTexture = true;
            if (anyTexture) Fill(new Rect(0, 0, Screen.width, Screen.height), Color.black);

            // overview
            Rect og = ToGui(overviewRect);
            if (overview != null && overview.targetTexture != null)
                GUI.DrawTexture(og, overview.targetTexture, ScaleMode.ScaleAndCrop, false);
            if (showLabels)
            {
                var world = Object.FindFirstObjectByType<MineWorldRoot>();
                string subline = (world != null && !string.IsNullOrEmpty(world.worldName) ? world.worldName.ToUpperInvariant() + "  ·  " : "") +
                                 MineCabCamera.All.Count + " TRUCKS";
                Tag(og, s.overviewTitle, subline, s, ui, true);
                Corners(og, s, ui);
            }

            for (int i = 0; i < cabRects.Count && i < cabs.Count; i++)
            {
                var c = cabs[i];
                Rect g = ToGui(cabRects[i]);
                if (c.cam.targetTexture != null) GUI.DrawTexture(g, c.cam.targetTexture, ScaleMode.ScaleAndCrop, false);
                if (!showLabels) continue;
                string line = "CAB CAM" + (s.uiShowSpeed ? "  ·  " + Mathf.RoundToInt(c.SpeedKmh) + " km/h" : "");
                Tag(g, c.DisplayName.ToUpperInvariant(), line, s, ui, false);
                Corners(g, s, ui);
            }

            // dividers
            if (cabRects.Count > 0)
            {
                Color div = new Color(0f, 0f, 0f, 0.9f);
                float d = Mathf.Max(2f, 3f * ui);
                foreach (var r in cabRects)
                {
                    Rect g = ToGui(r);
                    Fill(new Rect(g.x - d * 0.5f, g.y, d, g.height), div);
                    Fill(new Rect(g.x, g.y - d * 0.5f, g.width, d), div);
                }
            }
        }

        private void Tag(Rect view, string head, string line, MineLookSettings s, float ui, bool live)
        {
            float pad = 10f * ui, margin = 16f * ui;
            Vector2 hs = title.CalcSize(new GUIContent(head));
            Vector2 ls = sub.CalcSize(new GUIContent(line));
            float liveW = 0f;
            string liveText = "●  LIVE   " + System.DateTime.Now.ToString("HH:mm:ss");
            if (live && s.uiShowLive) liveW = sub.CalcSize(new GUIContent(liveText)).x + pad;
            float w = Mathf.Max(hs.x, ls.x + liveW) + pad * 2f + 4f * ui;
            float h = hs.y + ls.y + pad * 1.4f;
            Rect plate = new Rect(view.x + margin, view.y + margin, w, h);
            Fill(plate, s.uiPlate);
            Fill(new Rect(plate.x, plate.y, 4f * ui, plate.height), s.uiAccent);
            float x = plate.x + pad + 4f * ui;
            GUI.Label(new Rect(x, plate.y + pad * 0.5f, hs.x + 4, hs.y), head, title);
            GUI.Label(new Rect(x, plate.y + pad * 0.5f + hs.y, ls.x + 4, ls.y), line, sub);
            if (live && s.uiShowLive)
            {
                var old = sub.normal.textColor;
                float blink = 0.55f + 0.45f * Mathf.Sin(Time.unscaledTime * 4f);
                sub.normal.textColor = new Color(1f, 0.25f, 0.2f, blink);
                GUI.Label(new Rect(x + ls.x + pad, plate.y + pad * 0.5f + hs.y, liveW, ls.y), liveText, sub);
                sub.normal.textColor = old;
            }
        }

        private void Corners(Rect v, MineLookSettings s, float ui)
        {
            if (!s.uiViewfinderCorners) return;
            Color c = s.uiAccent;
            c.a = 0.55f;
            float L = 26f * ui, T = Mathf.Max(1f, 2f * ui), m = 10f * ui;
            float x0 = v.x + m, y0 = v.y + m, x1 = v.xMax - m, y1 = v.yMax - m;
            Fill(new Rect(x1 - L, y0, L, T), c); Fill(new Rect(x1 - T, y0, T, L), c);
            Fill(new Rect(x0, y1 - T, L, T), c); Fill(new Rect(x0, y1 - L, T, L), c);
            Fill(new Rect(x1 - L, y1 - T, L, T), c); Fill(new Rect(x1 - T, y1 - L, T, L), c);
        }

        private void Fill(Rect r, Color c)
        {
            var old = GUI.color;
            GUI.color = c;
            GUI.DrawTexture(r, white);
            GUI.color = old;
        }
    }
}

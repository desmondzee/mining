const THEME_KEY = "quarry-theme";
const STALE_MS = 2500;
const root = document.documentElement;
const darkQuery = matchMedia("(prefers-color-scheme: dark)");

const PALETTES = {
  light: { grid: "rgba(26, 26, 26, 0.05)", road: "rgba(26, 26, 26, 0.16)", junction: "rgba(26, 26, 26, 0.35)", ink: "#1a1a1a", inkMuted: "#5b5b58", halo: "#f4f2ec", obstacle: "#b7b3a8", path: "rgba(168, 85, 69, 0.55)", pedestrian: "#a85545", outline: "#f4f2ec", selected: "#1a1a1a" },
  dark: { grid: "rgba(255, 255, 255, 0.04)", road: "rgba(236, 235, 230, 0.16)", junction: "rgba(236, 235, 230, 0.4)", ink: "#ecebe6", inkMuted: "#a19f98", halo: "#121211", obstacle: "#4a4945", path: "rgba(214, 140, 110, 0.5)", pedestrian: "#d68c6e", outline: "#121211", selected: "#ecebe6" },
};

export const palette = { ...PALETTES.dark };
export const THEMES = ["dark", "light", "auto"];

const el = {
  toast: document.getElementById("toast"),
  summary: document.getElementById("summary"),
};

let lastState = 0;
let simConnected = false;
let onThemeChange = null;

export function storedTheme() {
  try { return localStorage.getItem(THEME_KEY) || "dark"; } catch { return "dark"; }
}

export function applyTheme(choice) {
  root.dataset.theme = choice;
  try { localStorage.setItem(THEME_KEY, choice); } catch {}
  const dark = choice === "dark" || (choice === "auto" && darkQuery.matches);
  Object.assign(palette, dark ? PALETTES.dark : PALETTES.light);
  onThemeChange?.();
}

export function onTheme(fn) {
  onThemeChange = fn;
}

darkQuery.addEventListener("change", () => applyTheme(storedTheme()));
applyTheme(storedTheme());

function showToast(title, body) {
  if (!title) {
    el.toast.hidden = true;
    return;
  }
  if (el.toast.querySelector(".toast-title").textContent !== title) {
    el.toast.querySelector(".toast-title").textContent = title;
    el.toast.querySelector(".toast-body").textContent = body;
  }
  el.toast.hidden = false;
}

function renderConnection() {
  if (!lastState) {
    showToast("Connecting", "Opening a connection to the control server.");
    el.summary.textContent = "connecting";
  } else if (Date.now() - lastState > STALE_MS) {
    showToast("Reconnecting", "The connection to the control server dropped. Retrying.");
    el.summary.textContent = "reconnecting";
  } else if (!simConnected) {
    showToast("Simulator offline", "Feeds and telemetry resume when it connects.");
  } else {
    showToast(null);
  }
}

setInterval(renderConnection, 1000);
renderConnection();

export function renderChrome(state, { tileFor, tiles }) {
  lastState = Date.now();
  simConnected = state.sim_connected;
  for (const name of state.cameras || []) tileFor(name);
  const live = [...tiles.values()].filter((t) => t.el.classList.contains("live")).length;
  const moving = state.trucks.filter((t) => t.status !== "idle" && t.status !== "arrived").length;
  el.summary.textContent = simConnected
    ? `live · ${moving}/${state.trucks.length} moving · ${live}/${tiles.size} cams`
    : "sim offline";
  renderConnection();
}

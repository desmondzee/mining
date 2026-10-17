const THEME_KEY = "quarry-theme";
const STALE_MS = 2500;
const root = document.documentElement;
const darkQuery = matchMedia("(prefers-color-scheme: dark)");

const PALETTES = {
  light: { grid: "rgba(0, 0, 0, 0.06)", obstacle: "#9a9aa0", path: "rgba(230, 100, 10, 0.6)", pedestrian: "#f06a0a", outline: "#efeee9", selected: "#111214" },
  dark: { grid: "rgba(255, 255, 255, 0.05)", obstacle: "#5f6168", path: "rgba(255, 120, 20, 0.5)", pedestrian: "#ff7814", outline: "#111316", selected: "#ffffff" },
};

export const palette = { ...PALETTES.light };

const el = {
  empty: document.getElementById("feeds-empty"),
  feedsMeta: document.getElementById("feeds-meta"),
  fleetMeta: document.getElementById("fleet-meta"),
  lidarMeta: document.getElementById("lidar-meta"),
  simStatus: document.getElementById("sim-status"),
};

let lastState = 0;
let simConnected = false;

function storedTheme() {
  try { return localStorage.getItem(THEME_KEY) || "auto"; } catch { return "auto"; }
}

function applyTheme(choice) {
  if (choice === "auto") delete root.dataset.theme;
  else root.dataset.theme = choice;
  try {
    if (choice === "auto") localStorage.removeItem(THEME_KEY);
    else localStorage.setItem(THEME_KEY, choice);
  } catch {}
  for (const b of document.querySelectorAll("[data-theme-choice]")) b.setAttribute("aria-pressed", String(b.dataset.themeChoice === choice));
  const dark = choice === "dark" || (choice === "auto" && darkQuery.matches);
  Object.assign(palette, dark ? PALETTES.dark : PALETTES.light);
}

for (const b of document.querySelectorAll("[data-theme-choice]")) b.addEventListener("click", () => applyTheme(b.dataset.themeChoice));
darkQuery.addEventListener("change", () => applyTheme(storedTheme()));
applyTheme(storedTheme());

function showEmpty(kind, title, body) {
  if (!kind) {
    el.empty.hidden = true;
    return;
  }
  el.empty.hidden = false;
  el.empty.className = `empty-state ${kind}`;
  el.empty.querySelector(".empty-title").textContent = title;
  el.empty.querySelector(".empty-body").textContent = body;
}

function renderConnection() {
  if (!lastState) {
    showEmpty("reconnecting", "Connecting to brain", "Opening a connection to the control server.");
  } else if (Date.now() - lastState > STALE_MS) {
    el.simStatus.textContent = "reconnecting";
    el.simStatus.className = "pill off";
    showEmpty("reconnecting", "Reconnecting to brain", "The dashboard lost its connection to the control server. Retrying automatically.");
  } else if (!simConnected) {
    showEmpty("offline", "Simulator offline", "Waiting for the simulator to connect. Feeds and telemetry resume on their own.");
  } else {
    showEmpty(null);
  }
}

setInterval(renderConnection, 1000);
renderConnection();

export function renderChrome(state, { tileFor, tiles, truckColour, selected }) {
  lastState = Date.now();
  simConnected = state.sim_connected;
  for (const name of state.cameras || []) tileFor(name);
  for (const t of state.trucks) {
    const tile = tiles.get(t.id);
    if (!tile) continue;
    tile.el.style.setProperty("--swatch", truckColour(t.id));
    tile.statusEl.textContent = t.status;
    tile.statusEl.className = `chip status status-${t.status}`;
    tile.speedEl.textContent = `${t.speed.toFixed(1)} m/s`;
  }
  const live = [...tiles.values()].filter((t) => t.el.classList.contains("live")).length;
  el.feedsMeta.textContent = !tiles.size ? "" : simConnected ? `${live}/${tiles.size} live` : "paused";
  const moving = state.trucks.filter((t) => t.status !== "idle" && t.status !== "arrived").length;
  el.fleetMeta.textContent = state.trucks.length ? `${moving}/${state.trucks.length} active` : "";
  el.lidarMeta.textContent = selected || "no truck selected";
  renderConnection();
}

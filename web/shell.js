const VIEW_KEY = "quarry-view";
const SPLIT_KEY = "quarry-split";
const CAMERA_KEY = "quarry-camera";
const IDLE_MS = 3200;
const root = document.documentElement;
const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)");

export const VIEWS = ["video", "lidar", "map", "split"];
export const SPLITS = ["video map", "video lidar", "map lidar", "video map lidar"];

function load(key, fallback, valid) {
  try {
    const v = localStorage.getItem(key);
    return v && (!valid || valid.includes(v)) ? v : fallback;
  } catch { return fallback; }
}

function save(key, value) {
  try { localStorage.setItem(key, value); } catch {}
}

function springEasing(stiffness = 320, damping = 32, mass = 1) {
  const w0 = Math.sqrt(stiffness / mass);
  const zeta = damping / (2 * Math.sqrt(stiffness * mass));
  const wd = w0 * Math.sqrt(Math.max(1 - zeta * zeta, 1e-6));
  const at = (t) => zeta < 1
    ? 1 - Math.exp(-zeta * w0 * t) * (Math.cos(wd * t) + (zeta * w0 / wd) * Math.sin(wd * t))
    : 1 - Math.exp(-w0 * t) * (1 + w0 * t);
  const duration = 0.5;
  const pts = [];
  for (let i = 0; i <= 40; i++) pts.push(at((i / 40) * duration).toFixed(4));
  pts[40] = "1";
  return `linear(${pts.join(", ")})`;
}

if (CSS.supports("transition-timing-function", "linear(0, 1)")) root.style.setProperty("--ease-spring", springEasing());

export function createShell({ onView, onCamera }) {
  const views = document.getElementById("views");
  const tabs = [...document.querySelectorAll("[data-tab]")];
  const tabRule = document.querySelector(".tab-rule");
  let view = load(VIEW_KEY, "video", VIEWS);
  let split = load(SPLIT_KEY, "video map lidar", SPLITS);
  let camera = load(CAMERA_KEY, null);
  let switching = 0;

  function placeRule() {
    const t = tabs.find((x) => x.dataset.tab === view);
    if (!t) return;
    tabRule.style.transform = `translateX(${t.offsetLeft}px) scaleX(${t.offsetWidth})`;
  }

  function applyView(silent) {
    root.dataset.view = view;
    root.dataset.split = split;
    for (const t of tabs) t.classList.toggle("active", t.dataset.tab === view);
    placeRule();
    if (!silent) onView?.(view);
  }

  function setView(next, nextSplit = split) {
    if (next === view && nextSplit === split) return;
    view = next;
    split = nextSplit;
    save(VIEW_KEY, view);
    save(SPLIT_KEY, split);
    if (reduceMotion.matches) { applyView(); return; }
    clearTimeout(switching);
    views.classList.add("leaving");
    switching = setTimeout(() => {
      views.classList.remove("leaving");
      views.classList.add("entering");
      applyView();
      requestAnimationFrame(() => requestAnimationFrame(() => views.classList.remove("entering")));
    }, 120);
  }

  function setCamera(name, tiles) {
    camera = name;
    save(CAMERA_KEY, name);
    for (const [n, tile] of tiles) tile.el.classList.toggle("active", n === name);
    onCamera?.(name);
  }

  let idleTimer = 0;
  function wake() {
    root.classList.remove("idle");
    clearTimeout(idleTimer);
    idleTimer = setTimeout(() => {
      if (root.classList.contains("palette-open") || document.querySelector(".chrome:hover, .chrome :focus-visible")) return wake();
      if (view === "video") root.classList.add("idle");
    }, IDLE_MS);
  }
  for (const ev of ["pointermove", "pointerdown", "keydown", "wheel"]) addEventListener(ev, wake, { passive: true });
  wake();

  addEventListener("resize", placeRule);
  document.fonts?.ready.then(placeRule);
  applyView(true);

  return {
    get view() { return view; },
    get split() { return split; },
    get camera() { return camera; },
    setView,
    setSplit: (s) => setView("split", s),
    setCamera,
    wake,
  };
}

export function createDialog(root) {
  let restore = null;
  const sheet = root.querySelector('[role="dialog"]');
  function open() {
    restore = document.activeElement;
    root.hidden = false;
    requestAnimationFrame(() => { root.classList.add("open"); sheet.focus({ preventScroll: true }); });
  }
  function close() {
    if (root.hidden) return;
    root.classList.remove("open");
    root.classList.add("closing");
    setTimeout(() => { root.hidden = true; root.classList.remove("closing"); }, 140);
    restore?.focus?.({ preventScroll: true });
  }
  root.querySelector("[data-close]").addEventListener("click", close);
  root.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(); }
    if (e.key === "Tab") e.preventDefault();
  });
  return { open, close, isOpen: () => !root.hidden && !root.classList.contains("closing") };
}

export function isTyping(target) {
  if (!(target instanceof HTMLElement)) return false;
  const tag = target.tagName.toLowerCase();
  return target.isContentEditable || tag === "input" || tag === "textarea" || tag === "select";
}

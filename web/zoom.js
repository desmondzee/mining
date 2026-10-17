const MIN_DRAG = 8;
const HOLD_MS = 4000;
const MOVE_MS = 360;

const round = (v) => Math.round(v * 1e4) / 1e4;
const sameBox = (a, b) => (a == null || b == null ? a == b : a.every((v, i) => Math.abs(v - b[i]) < 1e-3));

export function createZoom({ camera = "overview" } = {}) {
  let tile = null;
  let box = null;
  let server = null;
  let drag = null;
  let pending = null;
  let rect = null;

  const url = `/api/view/${camera}`;

  function cover() {
    const r = tile.el.getBoundingClientRect();
    const vw = tile.canvas.width, vh = tile.canvas.height;
    const s = Math.max(r.width / vw, r.height / vh);
    return { r, vw, vh, s, ox: (r.width - vw * s) / 2, oy: (r.height - vh * s) / 2 };
  }

  function local(e) {
    const r = tile.el.getBoundingClientRect();
    return [Math.min(Math.max(e.clientX - r.left, 0), r.width), Math.min(Math.max(e.clientY - r.top, 0), r.height)];
  }

  function drawRect([ax, ay], [bx, by]) {
    rect.style.left = `${Math.min(ax, bx)}px`;
    rect.style.top = `${Math.min(ay, by)}px`;
    rect.style.width = `${Math.abs(bx - ax)}px`;
    rect.style.height = `${Math.abs(by - ay)}px`;
  }

  function toImage([ax, ay], [bx, by]) {
    const c = cover();
    const { width: W, height: H } = c.r;
    const k = Math.max(Math.abs(bx - ax) / W, Math.abs(by - ay) / H);
    const w = W * k, h = H * k;
    const x = Math.min(Math.max((ax + bx) / 2 - w / 2, 0), W - w);
    const y = Math.min(Math.max((ay + by) / 2 - h / 2, 0), H - h);
    const u = (px) => Math.min(Math.max((px - c.ox) / (c.vw * c.s), 0), 1);
    const v = (py) => Math.min(Math.max((py - c.oy) / (c.vh * c.s), 0), 1);
    return [u(x), v(y), u(x + w), v(y + h)].map(round);
  }

  function preview(target) {
    const c = cover();
    const [x0, y0, x1, y1] = target;
    const bw = (x1 - x0) * c.vw * c.s, bh = (y1 - y0) * c.vh * c.s;
    const k = Math.max(c.r.width / bw, c.r.height / bh);
    const cx = c.ox + ((x0 + x1) / 2) * c.vw * c.s, cy = c.oy + ((y0 + y1) / 2) * c.vh * c.s;
    tile.canvas.style.transformOrigin = "0 0";
    tile.canvas.style.transform = `translate(${c.r.width / 2 - cx * k}px, ${c.r.height / 2 - cy * k}px) scale(${k})`;
  }

  function release() {
    if (!pending) return;
    clearTimeout(pending.timer);
    pending = null;
    tile.el.classList.remove("zoom-moving", "zoom-wait");
    tile.canvas.style.transform = "";
    tile.el.classList.add("zoom-arrive");
    tile.canvas.addEventListener("animationend", () => tile.el.classList.remove("zoom-arrive"), { once: true });
    sync();
  }

  function sync() {
    box = server;
    tile.el.classList.toggle("zoomed", !!box);
  }

  function hold(target) {
    if (pending) clearTimeout(pending.timer);
    pending = { box: target, since: performance.now(), timer: setTimeout(release, HOLD_MS) };
  }

  function accept(meta) {
    if (!pending) return true;
    if (performance.now() - pending.since < MOVE_MS) return false;
    if (!meta?.view?.settled || !sameBox(meta.view.box, pending.box)) return false;
    release();
    return true;
  }

  async function request(target) {
    hold(target);
    tile.el.classList.add("zoomed", "zoom-moving");
    rect.classList.add("leaving");
    preview(target);
    try {
      const res = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ box: target }) });
      if (!res.ok) throw new Error(res.status);
    } catch {
      release();
    }
  }

  function onDown(e) {
    if (e.button !== 0 || box || pending || tile.canvas.width < 2) return;
    tile.el.setPointerCapture(e.pointerId);
    drag = { id: e.pointerId, from: local(e) };
    rect.classList.remove("leaving");
    drawRect(drag.from, drag.from);
  }

  function onMove(e) {
    if (!drag || e.pointerId !== drag.id) return;
    const to = local(e);
    drawRect(drag.from, to);
    rect.hidden = Math.abs(to[0] - drag.from[0]) < MIN_DRAG && Math.abs(to[1] - drag.from[1]) < MIN_DRAG;
  }

  function onUp(e) {
    if (!drag || e.pointerId !== drag.id) return;
    const { from } = drag;
    const to = local(e);
    drag = null;
    if (Math.abs(to[0] - from[0]) < MIN_DRAG || Math.abs(to[1] - from[1]) < MIN_DRAG) { rect.hidden = true; return; }
    e.stopPropagation();
    drawRect(from, to);
    request(toImage(from, to));
  }

  function onCancel() {
    drag = null;
    rect.hidden = true;
  }

  return {
    attach(t) {
      tile = t;
      rect = document.createElement("div");
      rect.className = "zoom-box";
      rect.hidden = true;
      rect.setAttribute("aria-hidden", "true");
      rect.addEventListener("transitionend", () => { if (rect.classList.contains("leaving")) rect.hidden = true; });
      tile.el.append(rect);
      tile.el.classList.add("zoomable");
      tile.el.addEventListener("pointerdown", onDown);
      tile.el.addEventListener("pointermove", onMove);
      tile.el.addEventListener("pointerup", onUp);
      tile.el.addEventListener("pointercancel", onCancel);
      tile.accept = accept;
    },

    get zoomed() {
      return !!(box || pending);
    },

    get visible() {
      return !!tile && (tile.el.checkVisibility?.({ visibilityProperty: true }) ?? true);
    },

    setBox(next) {
      server = next ?? null;
      if (tile && !pending && !sameBox(server, box)) sync();
    },

    async reset() {
      if (!tile || !(box || pending)) return;
      hold(null);
      tile.el.classList.add("zoom-wait");
      try {
        await fetch(url, { method: "DELETE" });
      } catch {
        release();
      }
    },
  };
}

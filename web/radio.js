const BASE = "/radio";
const STALE_MS = 2500;
const MAX_LINES = 60;

export function createRadio({ onEvent, onState, onHealth }) {
  const radio = {
    open: false,
    lastState: 0,
    health: null,
    state: { trucks: [], excavators: [] },
    log: [],
    trucks: new Map(),
    get online() { return radio.open && Date.now() - radio.lastState < STALE_MS; },
    get mock() { return radio.online && (radio.state.mode === "mock" || radio.health?.mock === true); },
    command,
    release,
    audioUrl: (url) => (url && url.startsWith("/") ? BASE + url : url),
  };

  function push(ev) {
    if (radio.log.some((e) => e.id && e.id === ev.id)) return;
    radio.log.push(ev);
    if (radio.log.length > MAX_LINES) radio.log.splice(0, radio.log.length - MAX_LINES);
    onEvent?.(ev);
  }

  function handle(msg) {
    if (msg.type === "hello") {
      radio.health = msg.health || null;
      radio.log = [];
      for (const ev of (msg.log || []).slice(-MAX_LINES)) push({ ...ev, backfill: true });
      onHealth?.(radio.health);
    } else if (msg.type === "state") {
      radio.state = msg;
      radio.lastState = Date.now();
      radio.trucks = new Map((msg.trucks || []).map((t) => [t.id, t]));
      onState?.(msg);
    } else if (msg.type === "radio") {
      push(msg);
    }
  }

  let delay = 1000;
  function connect() {
    const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}${BASE}/ws/radio`);
    ws.onopen = () => { radio.open = true; delay = 1000; };
    ws.onmessage = (e) => { try { handle(JSON.parse(e.data)); } catch {} };
    ws.onclose = () => {
      radio.open = false;
      setTimeout(connect, delay);
      delay = Math.min(delay * 2, 10000);
    };
  }
  connect();

  async function post(path, body) {
    const r = await fetch(`${BASE}${path}`, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : undefined,
    });
    if (!r.ok) throw new Error(`radio ${r.status}`);
    return r.json();
  }

  function command(text) {
    return post("/api/radio/command", { text });
  }

  function release() {
    return post("/api/radio/release");
  }

  return radio;
}

export function createKpis(capacity = 55) {
  const k = { loads: 0, cycles: [], idle: 0, active: 0, since: null, capacity, last: new Map(), t: null };

  k.update = (msg) => {
    const t = msg.t;
    const dt = k.t === null ? 0 : Math.max(0, Math.min(t - k.t, 2));
    k.t = t;
    for (const tr of msg.trucks || []) {
      const prev = k.last.get(tr.id) || { phase: tr.phase, loadingAt: null };
      if (!k.since && tr.phase && tr.phase !== "idle") k.since = Date.now();
      if (prev.phase === "dumping" && tr.phase !== "dumping") k.loads++;
      if (tr.phase === "loading" && prev.phase !== "loading") {
        if (prev.loadingAt !== null) k.cycles.push(t - prev.loadingAt);
        prev.loadingAt = t;
      }
      if (k.since) {
        k.active += dt;
        const parked = tr.phase === "idle" || ((tr.speed ?? 0) < 0.3 && tr.phase !== "loading" && tr.phase !== "dumping");
        if (parked) k.idle += dt;
      }
      k.last.set(tr.id, { phase: tr.phase, loadingAt: prev.loadingAt });
    }
  };

  k.reset = () => {
    k.loads = 0;
    k.cycles = [];
    k.idle = 0;
    k.active = 0;
    k.since = null;
    k.last.clear();
  };

  k.view = () => {
    const avg = k.cycles.length ? k.cycles.reduce((a, b) => a + b, 0) / k.cycles.length : null;
    return {
      loads: k.loads,
      tonnes: k.loads * k.capacity,
      cycle: avg === null ? "—" : `${Math.floor(Math.round(avg) / 60)}:${String(Math.round(avg) % 60).padStart(2, "0")}`,
      idle: k.active ? `${Math.round((100 * k.idle) / k.active)}%` : "—",
      since: k.since ? new Date(k.since).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : null,
    };
  };

  return k;
}

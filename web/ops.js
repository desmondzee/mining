const MAX_LINES = 60;
const WARN_M = 15;
const CRIT_M = 6;
const CLEAR_MS = 2000;
const MOVING = 0.3;
const AUDIO_KEY = "quarry-audio";
const JUDGE_KEY = "quarry-judge";
const QR_SRC = "https://cdnjs.cloudflare.com/ajax/libs/qrcodejs/1.0.0/qrcode.min.js";
const PHASES = { idle: "standing by", to_site: "en route", to_load: "to load", loading: "loading", to_dump: "hauling", dumping: "dumping", returning: "returning" };

function load(key, fallback) {
  try { return JSON.parse(localStorage.getItem(key)) ?? fallback; } catch { return fallback; }
}

function save(key, value) {
  try { localStorage.setItem(key, JSON.stringify(value)); } catch {}
}

function clock(t) {
  return new Date(t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function kmh(speed) {
  return `${Math.round((speed || 0) * 3.6)} km/h`;
}

export function createOps({ radio, kpis }) {
  const transcripts = [document.getElementById("transcript"), document.getElementById("judge-transcript")];
  const alertEl = document.getElementById("alert");
  const noticeEl = document.getElementById("notice");
  const roster = document.getElementById("roster");
  const params = new URLSearchParams(location.search);
  const judge = { ...load(JUDGE_KEY, {}), ...(params.get("radio") ? { url: params.get("radio") } : {}), ...(params.get("pin") ? { pin: params.get("pin") } : {}) };
  let audio = load(AUDIO_KEY, false);
  let hazard = null;
  let hazardSeen = 0;
  let noticeTimer = 0;
  let qrLoading = null;
  const rows = new Map();

  function line(ev) {
    const li = document.createElement("li");
    li.className = `k-${ev.kind || "truck"}${ev.backfill ? " backfill" : ""}`;
    const who = document.createElement("span");
    who.className = "who";
    who.textContent = `${clock(ev.t || Date.now() / 1000)}  ${ev.from || ""}`;
    const text = document.createElement("span");
    text.className = "text";
    text.textContent = ev.text;
    li.append(who, text);
    return li;
  }

  function addLine(ev) {
    for (const ol of transcripts) {
      ol.append(line(ev));
      while (ol.children.length > MAX_LINES) ol.firstElementChild.remove();
    }
    if (audio && !ev.backfill && ev.audio_url) new Audio(radio.audioUrl(ev.audio_url)).play().catch(() => {});
  }

  function notice(text, armed = false) {
    noticeEl.textContent = text;
    noticeEl.classList.toggle("armed", armed);
    noticeEl.hidden = false;
    noticeEl.style.animation = "none";
    void noticeEl.offsetWidth;
    noticeEl.style.animation = "";
    clearTimeout(noticeTimer);
    noticeTimer = setTimeout(() => { noticeEl.hidden = true; }, armed ? 2000 : 2600);
  }

  function nearest(state) {
    let best = null;
    for (const t of state.trucks) {
      const moving = (t.speed || 0) > MOVING || t.status === "driving" || t.status === "avoiding";
      if (!moving) continue;
      for (const p of state.pedestrians) {
        const d = Math.hypot(p.x - t.x, p.z - t.z);
        if (d < WARN_M && (!best || d < best.d)) best = { d, truck: t.id, ped: p, level: d < CRIT_M ? "critical" : "warn" };
      }
    }
    return best;
  }

  function safety(state) {
    const now = Date.now();
    const hit = nearest(state);
    if (hit) {
      if (!hazard) addLine({ kind: "safety", from: "Safety", text: `Pedestrian ${Math.round(hit.d)} m from ${hit.truck}`, t: now / 1000 });
      hazard = hit;
      hazardSeen = now;
    } else if (hazard && now - hazardSeen > CLEAR_MS) {
      hazard = null;
    }
    if (hazard) {
      alertEl.textContent = `pedestrian  ${Math.round(hazard.d)} m  ·  ${hazard.truck}`;
      alertEl.className = `alert ${hazard.level}`;
      alertEl.hidden = false;
    } else {
      alertEl.hidden = true;
    }
    return hazard;
  }

  function truckTask(id) {
    const r = radio.online ? radio.trucks.get(id) : null;
    if (!r) return null;
    return r.task && r.task !== "standing by" ? r.task : PHASES[r.phase] || r.phase || "";
  }

  function truckLine(t) {
    const task = truckTask(t.id) || t.status;
    return `${task} · ${kmh(t.speed)}`;
  }

  function row(key) {
    let li = rows.get(key);
    if (!li) {
      li = document.createElement("li");
      li.innerHTML = `<span class="id"></span><span class="task"></span><span class="num"></span><span class="st"></span>`;
      rows.set(key, li);
      roster.append(li);
    }
    return li;
  }

  function set(li, sel, text) {
    const el = li.querySelector(sel);
    if (el.textContent !== text) el.textContent = text;
  }

  function renderRoster(state, siteName) {
    for (const t of state.trucks) {
      const li = row(t.id);
      const r = radio.online ? radio.trucks.get(t.id) : null;
      set(li, ".id", t.id);
      set(li, ".task", truckTask(t.id) || (t.goal_site ? `to ${siteName(t.goal_site)}` : "—"));
      set(li, ".num", kmh(t.speed));
      set(li, ".st", r?.remaining_m != null ? `${r.remaining_m} m` : t.status);
    }
    const excs = radio.online ? radio.state.excavators || [] : [];
    excs.forEach((e, i) => {
      const li = row(e.id);
      li.classList.toggle("gap", i === 0);
      set(li, ".id", e.id);
      set(li, ".task", e.face_name || e.face || "");
      set(li, ".num", "");
      set(li, ".st", e.state || "");
    });
  }

  function renderKpis(hintEl) {
    const v = kpis.view();
    for (const dd of document.querySelectorAll("[data-kpi]")) {
      const text = String(v[dd.dataset.kpi]);
      if (dd.textContent !== text) dd.textContent = text;
    }
    document.getElementById("kpi-since").textContent = v.since ? `since ${v.since}` : "";
    const summary = `${v.loads} loads · ${v.tonnes} t · cycle ${v.cycle} · idle ${v.idle}`;
    document.getElementById("judge-kpis").textContent = summary;
    return v.loads || v.since ? summary : "";
  }

  async function renderQr() {
    const box = document.getElementById("qr");
    document.getElementById("judge-url").textContent = judge.url || "";
    const pin = document.getElementById("judge-pin");
    pin.hidden = !judge.pin;
    pin.innerHTML = "";
    if (judge.pin) {
      const b = document.createElement("b");
      b.textContent = judge.pin;
      pin.append("access code", b);
    }
    box.replaceChildren();
    if (!judge.url) return;
    qrLoading ??= new Promise((resolve, reject) => {
      const s = document.createElement("script");
      s.src = QR_SRC;
      s.onload = resolve;
      s.onerror = reject;
      document.head.append(s);
    });
    try {
      await qrLoading;
      new window.QRCode(box, { text: judge.url, width: 200, height: 200, colorDark: "#1a1a1a", colorLight: "#f4f2ec", correctLevel: window.QRCode.CorrectLevel.M });
    } catch {
      qrLoading = null;
    }
  }

  function status() {
    if (!radio.open && !radio.lastState) return { text: "radio", tone: "warn" };
    if (!radio.online) return { text: "radio", tone: "warn" };
    if (radio.mock) return { text: "mock", tone: "mock" };
    if (radio.health && radio.health.brain_ok === false) return { text: "radio · brain", tone: "warn" };
    return null;
  }

  return {
    addLine,
    notice,
    safety,
    truckLine,
    renderRoster,
    renderKpis,
    renderQr,
    status,
    get hazard() { return hazard; },
    get audio() { return audio; },
    setAudio(on) { audio = on; save(AUDIO_KEY, on); notice(on ? "audio replies on" : "audio replies off"); },
    get judge() { return judge; },
    setJudge(patch) { Object.assign(judge, patch); save(JUDGE_KEY, judge); renderQr(); },
    thresholds: { WARN_M, CRIT_M },
  };
}

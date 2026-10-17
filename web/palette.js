const RECENT_KEY = "quarry-recent";
const RECENT_MAX = 4;
const CLOSE_MS = 140;

function words(text) {
  return text.toLowerCase().replace(/[-_·→]/g, " ").split(/\s+/).filter(Boolean);
}

function tokenScore(token, hay, hayWords) {
  let best = 0;
  for (const w of hayWords) {
    if (w === token) return 3;
    if (w.startsWith(token)) best = Math.max(best, 2.2);
    else if (token.length > 2 && w.includes(token)) best = Math.max(best, 1.2);
  }
  if (best || token.length < 2) return best;
  for (const w of hayWords) {
    let i = 0;
    for (const ch of w) if (ch === token[i]) i++;
    if (i === token.length) return 0.4;
  }
  return 0;
}

export function score(query, cmd) {
  const tokens = words(query);
  if (!tokens.length) return 1;
  const hay = `${cmd.title} ${cmd.keywords || ""}`.toLowerCase();
  const hayWords = words(hay);
  let total = 0;
  for (const t of tokens) {
    const s = tokenScore(t, hay, hayWords);
    if (!s) return 0;
    total += s;
  }
  const title = cmd.title.toLowerCase();
  if (title.startsWith(query.toLowerCase())) total += 2;
  return total / tokens.length + total * 0.05;
}

function highlight(el, title, query) {
  const tokens = words(query);
  if (!tokens.length) { el.textContent = title; return; }
  const parts = title.split(/(\s+|-)/);
  el.replaceChildren(...parts.map((p) => {
    const lw = p.toLowerCase();
    const t = tokens.find((tk) => lw.startsWith(tk));
    if (!t || !p.trim()) return document.createTextNode(p);
    const frag = document.createDocumentFragment();
    const mark = document.createElement("mark");
    mark.textContent = p.slice(0, t.length);
    frag.append(mark, p.slice(t.length));
    return frag;
  }));
}

function loadRecent() {
  try { return JSON.parse(localStorage.getItem(RECENT_KEY)) || []; } catch { return []; }
}

export function createPalette({ root, commands, recents = true }) {
  const pre = root.id;
  const input = root.querySelector("input");
  const list = root.querySelector('[role="listbox"]');
  const body = root.querySelector(".palette-body");
  const cursor = root.querySelector(".palette-cursor");
  const empty = root.querySelector(".palette-empty");
  let items = [];
  let active = 0;
  let restore = null;
  let closeTimer = 0;
  let recent = recents ? loadRecent() : [];
  let armed = null;
  let armTimer = 0;

  function remember(id) {
    if (!recents) return;
    recent = [id, ...recent.filter((r) => r !== id)].slice(0, RECENT_MAX);
    try { localStorage.setItem(RECENT_KEY, JSON.stringify(recent)); } catch {}
  }

  function results(query) {
    const q = query.trim();
    const all = commands(q);
    if (!q) {
      const byId = new Map(all.map((c) => [c.id, c]));
      const rec = recent.map((id) => byId.get(id)).filter(Boolean).map((c) => ({ ...c, group: "recent" }));
      const taken = new Set(rec.map((c) => c.id));
      return [...rec, ...all.filter((c) => !c.searchOnly && !c.always && !taken.has(c.id))];
    }
    const recentRank = new Map(recent.map((id, i) => [id, (RECENT_MAX - i) * 0.08]));
    const scored = all.map((c) => {
      const base = c.always ? 0.001 : score(q, c);
      return { c, s: base && base + (recentRank.get(c.id) || 0) };
    }).filter((r) => r.s > 0);
    scored.sort((a, b) => b.s - a.s);
    const top = scored.slice(0, 40);
    const order = [];
    for (const r of top) if (!order.includes(r.c.group)) order.push(r.c.group);
    return order.flatMap((g) => top.filter((r) => r.c.group === g).map((r) => r.c));
  }

  function disarm() {
    clearTimeout(armTimer);
    if (!armed) return;
    const opt = list.querySelector(`#${pre}-o${armed.index}`);
    if (opt) { opt.classList.remove("armed"); highlight(opt.querySelector(".label"), armed.cmd.title, input.value); }
    armed = null;
  }

  function render(keep) {
    const keepId = keep ? items[active]?.id : null;
    if (!keep) disarm();
    const query = input.value;
    items = results(query);
    active = 0;
    const frag = document.createDocumentFragment();
    let group = null;
    let groupEl = null;
    items.forEach((cmd, i) => {
      if (cmd.group !== group) {
        group = cmd.group;
        groupEl = document.createElement("div");
        groupEl.setAttribute("role", "group");
        const label = document.createElement("div");
        label.className = "palette-group-label";
        label.id = `${pre}-g${i}`;
        label.textContent = group;
        groupEl.setAttribute("aria-labelledby", label.id);
        groupEl.append(label);
        frag.append(groupEl);
      }
      const opt = document.createElement("div");
      opt.className = "palette-option";
      opt.id = `${pre}-o${i}`;
      opt.setAttribute("role", "option");
      opt.dataset.index = i;
      const label = document.createElement("span");
      label.className = "label";
      highlight(label, cmd.title, query);
      opt.append(label);
      if (cmd.detail) {
        const d = document.createElement("span");
        d.className = "detail";
        d.textContent = cmd.detail;
        opt.append(d);
      }
      if (cmd.keys) {
        const k = document.createElement("span");
        k.className = "keys";
        k.setAttribute("aria-hidden", "true");
        for (const key of cmd.keys) {
          const kbd = document.createElement("kbd");
          kbd.textContent = key;
          k.append(kbd);
        }
        opt.append(k);
      }
      groupEl.append(opt);
    });
    list.replaceChildren(frag);
    empty.hidden = items.length > 0;
    body.hidden = !items.length;
    const keepIndex = keepId ? items.findIndex((c) => c.id === keepId) : -1;
    if (keepIndex >= 0) {
      setActive(keepIndex, true);
      return;
    }
    cursor.classList.add("jump");
    setActive(0, true);
    requestAnimationFrame(() => cursor.classList.remove("jump"));
  }

  function setActive(i, instant) {
    if (!items.length) {
      cursor.classList.remove("on");
      input.removeAttribute("aria-activedescendant");
      return;
    }
    active = (i + items.length) % items.length;
    for (const o of list.querySelectorAll('[aria-selected="true"]')) o.setAttribute("aria-selected", "false");
    const opt = list.querySelector(`#${pre}-o${active}`);
    opt.setAttribute("aria-selected", "true");
    input.setAttribute("aria-activedescendant", opt.id);
    cursor.style.transform = `translateY(${opt.offsetTop + list.offsetTop}px)`;
    cursor.style.height = `${opt.offsetHeight}px`;
    cursor.classList.add("on");
    const top = opt.offsetTop - (opt.previousElementSibling?.classList.contains("palette-group-label") ? 32 : 4);
    if (top < body.scrollTop) body.scrollTo({ top, behavior: instant ? "auto" : "smooth" });
    else if (opt.offsetTop + opt.offsetHeight > body.scrollTop + body.clientHeight) body.scrollTo({ top: opt.offsetTop + opt.offsetHeight - body.clientHeight + 8, behavior: instant ? "auto" : "smooth" });
  }

  function run(i) {
    const cmd = items[i];
    if (!cmd) return;
    if (cmd.confirm && armed?.cmd.id !== cmd.id) {
      disarm();
      armed = { cmd, index: i };
      const opt = list.querySelector(`#${pre}-o${i}`);
      opt.classList.add("armed");
      opt.querySelector(".label").textContent = cmd.confirm;
      armTimer = setTimeout(disarm, 2000);
      return;
    }
    disarm();
    remember(cmd.id);
    close(cmd.keepFocus);
    cmd.run();
  }

  function open(query = "") {
    if (isOpen()) { input.select(); return; }
    clearTimeout(closeTimer);
    restore = document.activeElement;
    root.hidden = false;
    root.classList.remove("closing");
    input.value = query;
    render();
    document.documentElement.classList.add("palette-open");
    input.focus({ preventScroll: true });
    requestAnimationFrame(() => root.classList.add("open"));
  }

  function close(keepFocus) {
    if (!isOpen()) return;
    root.classList.remove("open");
    root.classList.add("closing");
    document.documentElement.classList.remove("palette-open");
    closeTimer = setTimeout(() => { root.hidden = true; root.classList.remove("closing"); }, CLOSE_MS);
    if (!keepFocus && restore && restore.focus && restore !== document.body) restore.focus({ preventScroll: true });
    else input.blur();
  }

  function isOpen() {
    return !root.hidden && !root.classList.contains("closing");
  }

  input.addEventListener("input", render);
  input.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown" || (e.ctrlKey && e.key === "n")) { e.preventDefault(); disarm(); setActive(active + 1); }
    else if (e.key === "ArrowUp" || (e.ctrlKey && e.key === "p")) { e.preventDefault(); disarm(); setActive(active - 1); }
    else if (e.key === "Home") { e.preventDefault(); setActive(0); }
    else if (e.key === "End") { e.preventDefault(); setActive(items.length - 1); }
    else if (e.key === "Enter") { e.preventDefault(); run(active); }
    else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(); }
    else if (e.key === "Tab") { e.preventDefault(); setActive(active + (e.shiftKey ? -1 : 1)); }
  });
  list.addEventListener("pointermove", (e) => {
    const opt = e.target.closest(".palette-option");
    if (opt && Number(opt.dataset.index) !== active) { if (armed) disarm(); setActive(Number(opt.dataset.index), true); }
  });
  list.addEventListener("click", (e) => {
    const opt = e.target.closest(".palette-option");
    if (opt) run(Number(opt.dataset.index));
  });
  list.addEventListener("mousedown", (e) => e.preventDefault());
  root.querySelector("[data-close]").addEventListener("click", () => close());

  return { open, close, isOpen, toggle: () => (isOpen() ? close() : open()), refresh: () => isOpen() && render(true) };
}

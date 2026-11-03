// Daash app: Today and the session screen, over /app/api.
// No AI in here. Ask Claude opens Claude with the context filled in.

const API = "/app/api/";
const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode: signed in for this tab only */ } },
  drop(k) { try { localStorage.removeItem(k); } catch { /* nothing to drop */ } },
};
const memory = {};
const token = {
  get access() { return memory.access || store.get("daash.access"); },
  get refresh() { return memory.refresh || store.get("daash.refresh"); },
  save(t) {
    memory.access = t.access_token; store.set("daash.access", t.access_token);
    if (t.refresh_token) { memory.refresh = t.refresh_token; store.set("daash.refresh", t.refresh_token); }
  },
  clear() { delete memory.access; delete memory.refresh; store.drop("daash.access"); store.drop("daash.refresh"); },
};

// -- small helpers ------------------------------------------------------------

const $ = (id) => document.getElementById(id);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const svg = (body, size = 18, extra = "") => `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" ${extra}>${body}</svg>`;
const ICON = {
  right: (s = 18, c = "") => svg('<path d="m9 18 6-6-6-6"/>', s, c ? `style="color:${c}"` : ""),
  left: () => svg('<path d="m15 18-6-6 6-6"/>'),
  down: () => svg('<path d="m6 9 6 6 6-6"/>', 16),
  out: () => svg('<path d="M7 17 17 7"/><path d="M7 7h10v10"/>', 14, 'stroke-width="2.2"'),
  check: (s = 12) => `<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="none" stroke="#161614" stroke-width="3.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 6 9 17l-5-5"/></svg>`,
  watch: (s = 18) => svg('<circle cx="12" cy="12" r="6"/><polyline points="12 10 12 12 13 13"/><path d="m16.13 7.66-.81-4.05a2 2 0 0 0-2-1.61h-2.68a2 2 0 0 0-2 1.61l-.78 4.05"/><path d="m7.88 16.36.8 4a2 2 0 0 0 2 1.61h2.72a2 2 0 0 0 2-1.61l.81-4.05"/>', s),
  share: () => svg('<path d="M4 12v8a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-8"/><polyline points="16 6 12 2 8 6"/><line x1="12" x2="12" y1="2" y2="15"/>', 16),
  today: () => svg('<circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>', 20),
  plan: () => svg('<rect width="18" height="18" x="3" y="4" rx="2"/><path d="M16 2v4"/><path d="M8 2v4"/><path d="M3 10h18"/>', 20),
  crew: () => svg('<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>', 20),
  you: () => svg('<circle cx="12" cy="8" r="5"/><path d="M20 21a8 8 0 0 0-16 0"/>', 20),
};
const FACE = ["var(--long)", "var(--easy)", "var(--hard)", "var(--strength)"];

let toastTimer;
function toast(message) {
  const el = $("toast");
  el.textContent = message;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, 2800);
}

function isoToday() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

// -- sign-in: Daash's own OAuth 2.1, the same account the connector uses ------

const CALLBACK = `${location.origin}/app/callback`;

function b64url(bytes) {
  return btoa(String.fromCharCode(...new Uint8Array(bytes))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

// Daash's own app is a client of its own on the server (oauth.APP_CLIENT_ID),
// so its sign-ins skip the consent page that third-party apps get.
const CLIENT_ID = "daash-app";

async function signIn() {
  const id = CLIENT_ID;
  const verifier = b64url(crypto.getRandomValues(new Uint8Array(48)));
  const challenge = b64url(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier)));
  const state = b64url(crypto.getRandomValues(new Uint8Array(16)));
  sessionStorage.setItem("daash.pkce", JSON.stringify({ verifier, state, back: location.hash || "#/today" }));
  const q = new URLSearchParams({ response_type: "code", client_id: id, redirect_uri: CALLBACK, code_challenge: challenge,
    code_challenge_method: "S256", state, resource: `${location.origin}/mcp` });
  location.assign(`/authorize?${q}`);
}

async function finishSignIn() {
  const q = new URLSearchParams(location.search);
  const saved = JSON.parse(sessionStorage.getItem("daash.pkce") || "{}");
  sessionStorage.removeItem("daash.pkce");
  if (q.get("error") || !q.get("code") || q.get("state") !== saved.state) {
    history.replaceState(null, "", "/app/#/today");
    return q.get("error") === "access_denied" ? "Sign-in cancelled." : "That sign-in didn't complete. Try again.";
  }
  const res = await fetch("/token", {
    method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ grant_type: "authorization_code", code: q.get("code"), redirect_uri: CALLBACK,
      client_id: CLIENT_ID, code_verifier: saved.verifier }),
  });
  history.replaceState(null, "", `/app/${saved.back || "#/today"}`);
  if (!res.ok) return "That sign-in didn't complete. Try again.";
  token.save(await res.json());
  return null;
}

async function refreshed() {
  if (!token.refresh) return false;
  const res = await fetch("/token", {
    method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ grant_type: "refresh_token", refresh_token: token.refresh, client_id: CLIENT_ID }),
  });
  if (!res.ok) return false;
  token.save(await res.json());
  return true;
}

class SignedOut extends Error {}

async function api(path, options = {}, retry = true) {
  const res = await fetch(API + path, {
    ...options,
    headers: { Authorization: `Bearer ${token.access}`, ...(options.body ? { "Content-Type": "application/json" } : {}) },
  });
  if (res.status === 401) {
    if (retry && await refreshed()) return api(path, options, false);
    token.clear();
    throw new SignedOut();
  }
  const body = await res.json().catch(() => ({ error: "Daash didn't answer. Try again in a moment." }));
  if (!res.ok || body.error) throw new Error(body.error || "Something went wrong.");
  return body;
}

// -- shared parts (also what the chat cards draw) -----------------------------

function shapeBar(segments, height = 22, labels = true) {
  if (!segments || !segments.length) return "";
  const total = segments.reduce((a, s) => a + s.w, 0);
  const bars = segments.map((s) => `<span class="bg-${esc(s.colour)}" style="flex-grow:${s.w};height:${Math.round(s.h * height / 20)}px"></span>`).join("");
  const text = labels ? `<div class="shape-labels" aria-hidden="true">${segments.map((s) => `<span style="flex-grow:${s.w}">${s.w / total > 0.09 ? esc(s.label) : ""}</span>`).join("")}</div>` : "";
  return `<div class="stack-tight"><div class="shape" style="height:${height}px" role="img" aria-label="Shape of the session">${bars}</div>${text}</div>`;
}

function askLink(data, label) {
  const app = data.assistant === "ChatGPT" ? "chatgpt" : "claude";
  const name = data.assistant === "ChatGPT" ? "ChatGPT" : "Claude";
  const inApp = app === "claude" && data.ask?.claude_app ? ` data-app="${esc(data.ask.claude_app)}"` : "";
  return { href: data.ask?.[app], name, html: `<a class="pill red tall" href="${esc(data.ask?.[app])}" target="_blank" rel="noopener"${inApp}>${esc(label || `Ask ${name}`)}${ICON.out()}</a>` };
}

// Ask Claude opens Claude's desktop app when it's there. A page can't ask
// whether an app is installed, so it tries the app's claude:// link and
// watches for the page losing focus, which is what opening an app does. If
// nothing takes it within a second, the web link opens instead, and that
// answer is remembered: You lets the person switch it back.
const OPENS = "daash.opens";
const onComputer = () => !/Android|iPhone|iPad|iPod/i.test(navigator.userAgent) && !window.matchMedia("(pointer: coarse)").matches;

function openAssistant(appUrl, webUrl) {
  if (!appUrl || !onComputer() || store.get(OPENS) === "web") {
    window.open(webUrl, "_blank", "noopener");
    return;
  }
  let left = false;
  const away = () => { left = true; };
  window.addEventListener("blur", away);
  document.addEventListener("visibilitychange", away);
  location.href = appUrl;
  setTimeout(() => {
    window.removeEventListener("blur", away);
    document.removeEventListener("visibilitychange", away);
    if (left) { store.set(OPENS, "app"); return; }
    store.set(OPENS, "web");
    const opened = window.open(webUrl, "_blank", "noopener");
    if (!opened) toast("The Claude app didn't open. Ask Claude again to open it in the browser.");
  }, 1000);
}

document.addEventListener("click", (e) => {
  const link = e.target.closest && e.target.closest("a[data-app]");
  if (!link || e.metaKey || e.ctrlKey || e.shiftKey || e.button > 0) return;
  e.preventDefault();
  openAssistant(link.dataset.app, link.href);
});

// At 1024px and wider the app is laid out for a desktop: a top bar instead
// of tabs, Today as a calendar, and sessions in a side panel.
const WIDE = window.matchMedia("(min-width: 1024px)");
const desktop = () => WIDE.matches;
WIDE.addEventListener("change", () => { base = null; route(); });

// What the top bar shows, kept from the last answers that said it.
const me = { initial: store.get("daash.initial") || "", assistant: store.get("daash.assistant") || "Claude" };
function remember(d) {
  if (d.initial) { me.initial = d.initial; store.set("daash.initial", d.initial); }
  if (d.assistant) { me.assistant = d.assistant; store.set("daash.assistant", d.assistant); }
}

function topbar(current) {
  const bar = $("topbar");
  bar.hidden = false;
  const name = me.assistant === "ChatGPT" ? "ChatGPT" : "Claude";
  const prompt = "Use Daash to look at my training this week. How is it going, and is anything worth changing?";
  const href = (name === "ChatGPT" ? "https://chatgpt.com/?q=" : "https://claude.ai/new?q=") + encodeURIComponent(prompt);
  const inApp = name === "Claude" ? ` data-app="claude://claude.ai/new?q=${encodeURIComponent(prompt)}"` : "";
  bar.innerHTML = `<span class="topbar-mark">Daash</span>
    <nav class="topbar-tabs" aria-label="Sections">${[["today", "Today"], ["plan", "Plan"], ["crew", "Crew"], ["you", "You"]]
      .map(([id, label]) => `<a href="#/${id}" ${id === current ? 'aria-current="page"' : ""}>${label}</a>`).join("")}</nav>
    <span style="flex:1"></span>
    <a class="pill red" href="${esc(href)}" target="_blank" rel="noopener" style="height:40px"${inApp}>Ask ${name}${ICON.out()}</a>
    <a class="avatar small" href="#/you" aria-label="You">${esc(me.initial || "Y")}</a>`;
}

function tabs(current) {
  if (desktop()) {
    $("tabs").hidden = true;
    return topbar(current);
  }
  $("topbar").hidden = true;
  const nav = $("tabs");
  nav.hidden = false;
  nav.innerHTML = [["today", "Today"], ["plan", "Plan"], ["crew", "Crew"], ["you", "You"]]
    .map(([id, label]) => `<a href="#/${id}" ${id === current ? 'aria-current="page"' : ""}>${ICON[id]()}${label}</a>`).join("");
}

function loading() {
  $("screen").innerHTML = `<div class="skeleton" style="min-height:60px;max-width:70%"></div><div class="skeleton" style="min-height:260px"></div><div class="skeleton"></div>`;
}

function failed(message, retry) {
  $("screen").innerHTML = `<div class="empty"><span class="strong">${esc(message)}</span><button class="pill" id="retry">Try again</button></div>`;
  $("retry").onclick = retry;
}

// -- Today --------------------------------------------------------------------

function todayParts(d) {
  const p = { head: `<header class="stack-tight"><span class="kicker">${esc(d.kicker)}</span><h1 class="headline">${esc(d.headline)}</h1></header>` };
  if (d.done_today) {
    const r = d.done_today;
    p.done = `<a class="row-button" href="#/run/${r.activity_id}?from=today">
      <span class="tick bg-${esc(r.colour)}">${ICON.check()}</span>
      <span class="grow"><span class="kicker" style="font-size:11.5px">DONE TODAY${r.at ? ` · ${esc(r.at)}` : ""}</span><span class="strong">${esc(r.line)}</span></span>
      ${ICON.right(18, "var(--muted)")}</a>`;
  }
  if (d.next) {
    const n = d.next;
    const open = `#/session/${n.date}?from=today`;
    p.next = `<section class="card red" aria-label="Next session">
      <div class="next-top"><span class="kicker red" style="font-weight:500;letter-spacing:0.08em">${esc(n.kicker)}</span>
        ${n.on_watch ? `<span class="watch-tag">${ICON.watch(13)}on your watch</span>` : ""}</div>
      <a class="next-name" href="${open}"><span class="stack-tight" style="gap:2px;min-width:0">
        <span class="type-label c-${esc(n.colour)}">${esc(n.type_label)}</span><span class="next-title">${esc(n.name)}</span></span>
        ${ICON.right(22, "var(--red-text)")}</a>
      <div class="stats3">${(n.stats || []).map((s) => `<div class="stack-tight" style="gap:1px"><span class="stat-v">${esc(s.v)}${s.u ? `<span class="stat-u"> ${esc(s.u)}</span>` : ""}</span><span class="stat-l">${esc(s.l)}</span></div>`).join("")}</div>
      ${shapeBar(n.segments)}
      <div class="pills"><a class="pill light" href="${open}" style="flex:1.3">See the session</a>
        ${n.easier ? `<button class="pill" data-act="${n.easier.applied ? "undo" : "easier"}" data-day="${n.date}" data-sid="${n.schedule_id || ""}">${esc(n.easier.applied ? "Undo easier" : "Easier")}</button>` : ""}
        <button class="pill" data-act="move" data-day="${n.date}" data-sid="${n.schedule_id || ""}" data-name="${esc(n.name)}">Move</button></div>
    </section>`;
  } else if (!d.empty) {
    p.next = `<div class="card pad stack-tight"><span class="strong">Nothing else planned</span><span class="meta">The plan on your Garmin calendar has no more sessions.</span></div>`;
  }
  if (d.empty) {
    const ask = askLink(d, `Build a plan with ${d.assistant === "ChatGPT" ? "ChatGPT" : "Claude"}`);
    p.next = `<div class="card pad stack"><span class="strong">No sessions on your Garmin calendar yet.</span>
      <span class="meta">Ask ${esc(ask.name)} to build a plan with Daash. It lands here and on your watch.</span>${ask.html}</div>`;
  }
  if (d.crew) {
    p.crew = `<div class="card crew-line"><div class="faces">${d.crew.faces.map((f, i) => `<span class="face" style="background:${FACE[i % 4]}">${esc(f)}</span>`).join("")}</div>
      <span class="grow" style="gap:1px"><span style="font-size:14.5px;font-weight:600;line-height:1.3">${esc(d.crew.line)}</span><span class="meta" style="font-size:12.5px">${esc(d.crew.sub)}</span></span>
      <a class="pill" href="#/crew" style="flex-shrink:0;padding:0 14px">Line up</a></div>`;
  }
  p.ready = d.readiness
    ? `<section class="stack" aria-label="Readiness"><div class="section-head"><span class="section-title">Readiness</span><span class="mono-label">FROM GARMIN</span></div>
      <div class="card trio">${d.readiness.tiles.map((t) => `<div><span class="l">${esc(t.l)}</span><span class="v">${esc(t.v)}</span><span class="n">${esc(t.note)}</span></div>`).join("")}</div></section>`
    : `<section class="stack"><span class="section-title">Readiness</span><div class="card pad meta">Readiness needs a night of sleep data. Wear the watch overnight and it shows here.</div></section>`;
  if (d.week) {
    const w = d.week;
    const total = w.planned ? `<span class="meta"><strong style="color:var(--ink);font-weight:600">${esc(w.done ?? 0)}</strong> of ${esc(w.planned)} ${esc(w.unit)}</span>` : "";
    const mark = (day) => day.state === "done" ? `<span class="tick bg-${esc(day.colour)}" style="width:18px;height:18px">${ICON.check(11)}</span>`
      : day.state === "next" ? `<span class="ring"></span>` : day.state === "rest" ? `<span class="dash"></span>`
      : day.state === "missed" ? `<span class="miss-ring" title="Missed"></span>`
      : day.dots.map((c) => `<span class="dot bg-${esc(c)}"></span>`).join("");
    const label = (day) => `${day.n}, ${day.state === "rest" ? "rest day" : day.state}`;
    p.week = `<section class="stack" aria-label="This week"><div class="section-head"><span class="section-title">This week</span>${total}</div>
      <div class="card strip-card"><div class="strip">${w.days.map((day) => `<a href="${day.state === "rest" ? "#/today" : `#/session/${day.date}?from=today`}" aria-label="${esc(label(day))}" ${day.state === "rest" ? 'tabindex="-1" style="pointer-events:none"' : ""}>
        <span class="d">${day.letter}</span><span class="n ${day.today ? "today" : ""}">${day.n}</span><span class="mark">${mark(day)}</span></a>`).join("")}</div>
        ${w.verdict ? `<span class="strip-verdict">${esc(w.verdict)}</span>` : ""}</div></section>`;
    p.verdict = w.verdict ? `<p class="meta" style="font-size:13.5px;line-height:1.45">${esc(w.verdict)}</p>` : "";
  }
  return p;
}

function todayView(d) {
  const p = todayParts(d);
  return [p.head, p.done, p.next, p.crew, p.ready, p.week].filter(Boolean).join("");
}

// Desktop: three weeks as a calendar, the phone's cards on the right.
function calendarView(d) {
  const dows = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"];
  const chip = (c, day) => {
    const href = c.state === "done" && c.activity_id ? `#/run/${c.activity_id}?from=today` : `#/session/${day.date}?sid=${c.schedule_id}&from=today`;
    const now = day.today && c.state !== "done";
    return `<a class="cal-chip tint-${esc(c.colour)} ${now ? "now" : ""}" href="${href}" ${c.movable ? `draggable="true" data-sid="${c.schedule_id}" data-from="${day.date}"` : ""}>
      <span class="cal-name c-${esc(c.colour)}">${c.state === "done" ? `<span class="tick bg-${esc(c.colour)}" style="width:13px;height:13px;margin-top:2px">${ICON.check(8)}</span>` : ""}
        ${c.missed ? `<span class="miss-ring" style="width:10px;height:10px;margin-top:3px" title="Not recorded"></span>` : ""}<span>${esc(c.name)}</span></span>
      ${c.v ? `<span class="cal-v">${esc(c.v)}</span>` : ""}</a>`;
  };
  return `<div class="cal-top"><div class="stack-tight"><span class="kicker">${esc(d.kicker)}</span><h1 class="headline" style="font-size:28px">${esc(d.headline)}</h1></div>
      <span class="meta">Drag a session to move it. The watch follows.</span></div>
    <div class="cal" id="cal"><div class="cal-row cal-head"><span></span>${dows.map((x) => `<span class="mono-label" style="font-size:11.5px;letter-spacing:0.06em;padding-left:4px">${x}</span>`).join("")}</div>
    ${(d.calendar || []).map((w) => `<div class="cal-row"><div class="cal-week"><span class="strong" style="font-size:13px;${w.current ? "color:var(--red-text)" : ""}">${esc(w.label)}</span>
        ${w.km ? `<span class="meta" style="font-size:12.5px">${esc(w.km)}</span>` : ""}</div>
      ${w.days.map((day) => `<div class="cal-day ${day.today ? "today" : ""} ${w.past ? "done-week" : ""}" data-date="${day.date}" ${day.past ? 'data-past="1"' : ""}>
        <span class="cal-n">${day.n}</span>
        ${day.chips.length ? day.chips.map((c) => chip(c, day)).join("") : `<span class="cal-chip rest">Rest</span>`}
        ${day.crew ? `<span class="cal-crew">${esc(day.crew)}</span>` : ""}</div>`).join("")}</div>`).join("")}</div>`;
}

function todayDesk(d) {
  const p = todayParts(d);
  return `<div class="desk-today"><div class="desk-main">${d.empty ? p.head + p.next : calendarView(d)}</div>
    <aside class="desk-side">${[p.done, d.empty ? "" : p.next, p.ready, p.crew, p.verdict].filter(Boolean).join("")}</aside></div>`;
}

// Drag a session's chip to another day: the same move as the Move button.
// Done runs can't be dragged, and nothing drops on a day already past.
function dragToMove(cal) {
  if (!cal) return;
  let from = null;
  cal.addEventListener("dragstart", (e) => {
    const chip = e.target.closest(".cal-chip[draggable]");
    if (!chip) return;
    from = { sid: chip.dataset.sid, date: chip.dataset.from };
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", from.sid);
    cal.classList.add("dragging");
  });
  const target = (e) => {
    const day = e.target.closest(".cal-day");
    return day && from && !day.dataset.past && day.dataset.date !== from.date ? day : null;
  };
  cal.addEventListener("dragover", (e) => {
    const day = target(e);
    cal.querySelectorAll(".cal-day.drop").forEach((x) => { if (x !== day) x.classList.remove("drop"); });
    if (!day) return;
    e.preventDefault();
    day.classList.add("drop");
  });
  cal.addEventListener("dragend", () => {
    cal.classList.remove("dragging");
    cal.querySelectorAll(".cal-day.drop").forEach((x) => x.classList.remove("drop"));
    from = null;
  });
  cal.addEventListener("drop", async (e) => {
    const day = target(e);
    if (!day) return;
    e.preventDefault();
    const moving = from;
    try {
      const r = await api(`session/${moving.date}/move`, { method: "POST", body: JSON.stringify({ to: day.dataset.date, schedule_id: Number(moving.sid) || null }) });
      toast(r.message);
      showToday();
    } catch (err) {
      if (err instanceof SignedOut) return route();
      toast(err.message);
    }
  });
}

let base = null;  // the desktop screen under the side panel

async function showToday() {
  tabs("today");
  if (base !== "today") loading();
  try {
    const d = await api("today");
    remember(d);
    if (desktop()) {
      $("screen").innerHTML = todayDesk(d);
      dragToMove($("cal"));
    } else {
      $("screen").innerHTML = todayView(d);
    }
    base = "today";
    wire($("screen"));
  } catch (e) {
    if (e instanceof SignedOut) return route();
    failed(e.message, showToday);
  }
}

// -- the session: preview before the run --------------------------------------

function stepRow(s) {
  const right = s.right || "";
  const line = s.target ? `<span class="target">${esc(s.target)}</span>` : s.sub ? `<span class="sub">${esc(s.sub)}</span>` : "";
  return `<div class="step ${s.head ? "head" : ""}" style="padding-left:${s.indent || 0}px">
    <span class="bar ${s.colour ? `bg-${esc(s.colour)}` : ""}"></span>
    <div class="stack-tight" style="gap:2px;min-width:0"><span class="strong">${esc(s.label)}</span>${line}</div>
    <span class="right">${esc(right)}</span></div>`;
}

function previewView(d, from, panel = false) {
  const back = from === "plan" ? "Plan" : "Today";
  const ask = askLink(d);
  const r = d.readiness;
  const parts = [panel ? "" : `<a class="back" href="#/${from === "plan" ? "plan" : "today"}">${ICON.left()}${back}</a>`,
    `<header class="stack-8">${panel ? "" : `<span class="kicker ${d.today ? "red" : ""}">${esc(d.kicker)}</span>`}<h1 class="title-xl">${esc(d.name)}</h1>
      ${d.km ? `<span class="size">${esc(d.km)} km<span style="color:var(--muted);font-weight:500"> · ~${esc(d.minutes)} min</span></span>`
        : d.minutes ? `<span class="size">~${esc(d.minutes)} min</span>` : ""}
      ${d.why ? `<p class="why">${esc(d.why)}</p>` : ""}</header>`];
  if (d.missed) parts.push(`<div class="card pad amber stack-tight"><span class="strong">Not recorded</span><span class="meta">Garmin has no run for this day. Move it if you still want to run it.</span></div>`);
  if (d.segments) parts.push(shapeBar(d.segments, 24, false));
  if (d.steps && d.steps.length) {
    parts.push(`<section class="stack"><span class="section-title">${esc(d.structure_title)}</span><div class="card steps">${d.steps.map(stepRow).join("")}</div></section>`);
  }
  if (r && r.score !== null && r.score !== undefined) {
    parts.push(`<div class="card ready-card ${r.low ? "amber" : ""}"><div class="ready-top"><span class="ready-score">${esc(r.score)}</span>
      <span class="grow"><span class="strong">${esc(r.head)}</span><span class="meta" style="line-height:1.4">${esc(r.sub)}${r.low && d.easier && !d.easier.applied ? ` Consider Easier: ${esc(d.easier.preview)} at the same pace.` : ""}</span></span></div>
      ${r.low && d.easier ? `<button class="pill amber" data-act="${d.easier.applied ? "undo" : "easier"}" data-day="${d.date}" data-sid="${d.schedule_id || ""}">${d.easier.applied ? "Back to the original" : "Make it easier"}</button>` : ""}</div>`);
  } else if (r) {
    parts.push(`<div class="card pad meta">${esc(r.head)}</div>`);
  }
  parts.push(`<div class="card watch-row"><span class="watch-icon">${ICON.watch()}</span>
    <div class="grow" style="gap:1px"><span class="strong">${esc(d.watch.head)}</span><span class="meta" style="${d.watch.changed ? "color:var(--red-text)" : ""}">${esc(d.watch.sub)}</span></div>
    <button class="pill" data-act="resend" data-day="${d.date}" data-sid="${d.schedule_id || ""}" style="flex-shrink:0;padding:0 14px">Send again</button></div>`);
  parts.push(`<div class="actions3"><button class="pill tall" data-act="move" data-day="${d.date}" data-sid="${d.schedule_id || ""}" data-name="${esc(d.name)}">Move</button>
    ${d.easier ? `<button class="pill tall" data-act="${d.easier.applied ? "undo" : "easier"}" data-day="${d.date}" data-sid="${d.schedule_id || ""}">${esc(d.easier.label)}</button>` : `<button class="pill tall" disabled title="Nothing to shorten">Easier</button>`}
    ${ask.html}</div>
    <span class="footnote">Ask ${esc(ask.name)} opens ${esc(ask.name)} with this session filled in.</span>`);
  if (d.also) {
    parts.push(`<div class="also"><span class="meta">Also this day:</span>${d.also.map((a) => `<a class="pill" href="#/session/${d.date}?sid=${a.schedule_id}&from=${from || "today"}"><span class="c-${esc(a.colour)}">●</span>${esc(a.name)}</a>`).join("")}</div>`);
  }
  return parts.join("");
}

function restView(d, from) {
  return `<a class="back" href="#/${from === "plan" ? "plan" : "today"}">${ICON.left()}${from === "plan" ? "Plan" : "Today"}</a>
    <header class="stack-8"><span class="kicker">${esc(d.kicker)}</span><h1 class="title-xl">Rest day</h1></header>
    ${d.next ? `<a class="row-button" href="#/session/${d.next.date}?from=${from || "today"}"><span class="grow"><span class="meta">Next</span><span class="strong">${esc(d.next.name)}</span></span>${ICON.right(18, "var(--muted)")}</a>` : ""}`;
}

async function showSession(day, params) {
  tabs(params.get("from") === "plan" ? "plan" : "today");
  loading();
  try {
    const sid = params.get("sid");
    const d = await api(`session/${day}${sid ? `?schedule_id=${encodeURIComponent(sid)}` : ""}`);
    if (d.done && d.activity_id) {
      location.replace(`#/run/${d.activity_id}?from=${params.get("from") || "today"}`);
      return;
    }
    $("screen").innerHTML = d.rest ? restView(d, params.get("from")) : previewView(d, params.get("from"));
    wire($("screen"));
  } catch (e) {
    if (e instanceof SignedOut) return route();
    failed(e.message, () => showSession(day, params));
  }
}

// -- the session: analysis after the run --------------------------------------

function analysisView(d, from, panel = false) {
  const back = from === "plan" ? "Plan" : "Today";
  const ask = askLink(d, `Ask ${d.assistant === "ChatGPT" ? "ChatGPT" : "Claude"} about it`);
  if (panel) return analysisPanel(d, ask);
  const parts = [`<a class="back" href="#/${from === "plan" ? "plan" : "today"}">${ICON.left()}${back}</a>`,
    `<header class="stack-8"><span class="kicker">${esc(d.kicker)}</span><h1 class="title-xl">${esc(d.name)}</h1></header>`,
    `<p class="verdict">${esc(d.verdict)}</p>`,
    `<div class="stats4">${d.stats.map((s) => `<div class="stack-tight" style="gap:2px;min-width:0"><span class="v">${esc(s.v)}</span><span class="l">${esc(s.l)}</span></div>`).join("")}</div>`];
  if (d.planned_and_run) {
    parts.push(`<section class="stack"><span class="section-title">Planned and run</span><div class="card table">
      <div class="cmp head"><span>STEP</span><span>TARGET</span><span style="text-align:right">PACE</span><span style="text-align:right">HR</span></div>
      ${d.planned_and_run.map((c) => `<div class="cmp"><span class="lab"><span class="bar bg-${esc(c.colour)}"></span>
        <span class="stack-tight" style="gap:1px;min-width:0"><span style="font-size:14.5px;font-weight:600">${esc(c.label)}</span><span class="meta ${c.soft ? "soft" : ""}" style="font-size:12.5px">${esc(c.note)}</span></span></span>
        <span class="tgt">${esc(c.target || "–")}</span><span class="num ${c.soft && c.miss !== "high" ? "soft" : ""}">${esc(c.pace || "–")}</span><span class="num ${c.miss === "high" ? "soft" : ""}">${esc(c.hr || "–")}</span></div>`).join("")}
    </div></section>`);
  }
  if (d.splits) {
    parts.push(`<div class="card table"><button class="splits-toggle" aria-expanded="false" aria-controls="splits" id="splits-toggle">
      <span class="strong">Splits<span style="font-weight:500;color:var(--muted)"> · ${esc(d.km)} km</span></span>
      <span class="meta" style="display:flex;align-items:center;gap:6px;font-size:13.5px"><span id="splits-word">Show</span>${ICON.down()}</span></button>
      <div id="splits" hidden><div class="splits-row head"><span>KM</span><span>PACE</span><span>HR</span></div>
      ${d.splits.map((s) => `<div class="splits-row"><span class="meta" style="font-size:14px">${esc(s.rest ? "Jogs" : s.km)}</span><span style="font-weight:600">${esc(s.rest ? s.pace : s.partial ? `${s.time} (${s.pace} /km)` : s.pace)}</span><span class="meta" style="font-size:14px">${esc(s.hr || "–")}</span></div>`).join("")}
      </div></div>`);
  }
  if (d.effect) {
    parts.push(`<div class="card effect"><span class="tick bg-${esc(d.colour)}">${ICON.check()}</span>
      <span class="stack-tight" style="gap:2px"><span class="strong">${esc(d.effect.head)}</span><span class="meta" style="font-size:13.5px;line-height:1.45">${esc(d.effect.sub)}</span></span></div>`);
  }
  parts.push(`<div class="actions2">${d.share_url ? `<a class="pill tall" href="${esc(d.share_url)}" target="_blank" rel="noopener">${ICON.share()}Share this run</a>` : `<span></span>`}${ask.html}</div>`);
  return parts.join("");
}

// The panel's analysis: planned and run beside the splits, the splits open.
function analysisPanel(d, ask) {
  const rows = (d.planned_and_run || []).map((c) => `<div class="pcmp"><span class="bar bg-${esc(c.colour)}"></span>
      <span class="stack-tight" style="gap:1px;min-width:0"><span style="font-size:14px;font-weight:600">${esc(c.label)}</span>
        <span class="meta ${c.soft ? "soft" : ""}" style="font-size:12px">${esc(c.soft ? c.note : [c.note, c.target].filter(Boolean).join(" · "))}</span></span>
      <span class="stack-tight" style="gap:1px;align-items:flex-end"><span class="${c.soft && c.miss !== "high" ? "soft" : ""}" style="font-size:14px;font-weight:600">${esc(c.pace || "–")}</span>
        <span class="meta ${c.miss === "high" ? "soft" : ""}" style="font-size:12px">${c.hr ? `${esc(c.hr)} bpm` : ""}</span></span></div>`).join("");
  const splits = (d.splits || []).map((s) => `<div class="psplit"><span class="meta">${esc(s.rest ? "Jogs" : s.km)}</span>
      <span style="font-weight:600;white-space:nowrap">${esc(s.rest ? s.pace.replace(" for ", " · ") : s.partial ? `${s.time} (${s.pace})` : s.pace)}</span><span class="meta" style="text-align:right">${esc(s.hr || "–")}</span></div>`).join("");
  return `<h2 class="title-xl" style="font-size:32px">${esc(d.name)}</h2>
    <p class="verdict" style="font-size:19px">${esc(d.verdict)}</p>
    <div class="stats4">${d.stats.map((s) => `<div class="stack-tight" style="gap:2px;min-width:0"><span class="v">${esc(s.v)}</span><span class="l">${esc(s.l)}</span></div>`).join("")}</div>
    <div class="panel-two">${rows ? `<section class="stack-8"><span class="strong">Planned and run</span><div class="card table">${rows}</div></section>` : ""}
      ${splits ? `<section class="stack-8"><span class="strong">Splits<span style="font-weight:500;color:var(--muted)"> · ${esc(d.km)} km</span></span>
        <div class="card table"><div class="psplit head"><span>KM</span><span>PACE</span><span style="text-align:right">HR</span></div>${splits}</div></section>` : ""}</div>
    ${d.effect ? `<div class="card effect"><span class="tick bg-${esc(d.colour)}">${ICON.check()}</span>
      <span class="stack-tight" style="gap:2px"><span class="strong">${esc(d.effect.head)}</span><span class="meta" style="font-size:13.5px;line-height:1.45">${esc(d.effect.sub)}</span></span></div>` : ""}
    <div class="actions2">${d.share_url ? `<a class="pill tall" href="${esc(d.share_url)}" target="_blank" rel="noopener">${ICON.share()}Share this run</a>` : `<span></span>`}${ask.html}</div>`;
}

// Desktop: a session or run opens in a panel over the calendar, and closing
// it leaves you where you were.
async function showPanel(kind, id, params) {
  if (base !== "today" && base !== "plan") await showToday();
  const holder = $("panel");
  holder.innerHTML = `<div class="panel" role="dialog" aria-modal="false" aria-labelledby="panel-kicker">
    <div class="panel-head"><span class="kicker" id="panel-kicker"></span>
      <button class="pill icon" id="panel-close" aria-label="Close" style="border:0">${svg('<path d="M18 6 6 18"/><path d="m6 6 12 12"/>')}</button></div>
    <div class="panel-body" id="panel-body"><div class="skeleton" style="min-height:300px"></div></div></div>`;
  holder.hidden = false;
  $("panel-close").onclick = closePanel;
  try {
    let d;
    if (kind === "session") {
      const sid = params.get("sid");
      d = await api(`session/${id}${sid ? `?schedule_id=${encodeURIComponent(sid)}` : ""}`);
      if (d.done && d.activity_id) { location.replace(`#/run/${d.activity_id}?from=${params.get("from") || "today"}`); return; }
    } else {
      d = await api(`run/${encodeURIComponent(id)}`);
    }
    $("panel-kicker").textContent = d.kicker || "";
    $("panel-kicker").classList.toggle("red", !!d.today);
    $("panel-body").innerHTML = kind === "run" ? analysisView(d, "today", true) : d.rest ? restView(d, "today").replace(/<a class="back"[^]*?<\/a>/, "") : previewView(d, "today", true);
    wire($("panel-body"));
    $("panel-close").focus();
  } catch (e) {
    if (e instanceof SignedOut) return route();
    $("panel-body").innerHTML = `<div class="empty"><span class="strong">${esc(e.message)}</span></div>`;
  }
}

// An edit made in the panel shows on the calendar under it straight away.
function refreshUnderPanel() {
  if (!desktop() || $("panel").hidden) return;
  if (base === "today") showToday();
  else if (base === "plan") showPlan("week", new URLSearchParams());
}

function closePanel() {
  $("panel").hidden = true;
  $("panel").innerHTML = "";
  location.hash = base === "plan" ? "#/plan" : "#/today";
}

document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("panel").hidden) closePanel(); });

async function showRun(id, params) {
  tabs(params.get("from") === "plan" ? "plan" : "today");
  loading();
  try {
    const d = await api(`run/${encodeURIComponent(id)}`);
    $("screen").innerHTML = analysisView(d, params.get("from"));
    const t = $("splits-toggle");
    if (t) t.onclick = () => {
      const open = t.getAttribute("aria-expanded") !== "true";
      t.setAttribute("aria-expanded", String(open));
      $("splits").hidden = !open;
      $("splits-word").textContent = open ? "Hide" : "Show";
    };
  } catch (e) {
    if (e instanceof SignedOut) return route();
    failed(e.message, () => showRun(id, params));
  }
}

// -- edits: the same writes Claude makes --------------------------------------

function wire(root) {
  root.querySelectorAll("[data-act]").forEach((b) => {
    b.onclick = () => act(b.dataset.act, b.dataset.day, b.dataset.sid, b);
  });
}

async function act(action, day, sid, button) {
  if (action === "move") return moveSheet(day, sid, button.dataset.name);
  button.disabled = true;
  try {
    const r = await api(`session/${day}/${action}`, { method: "POST", body: JSON.stringify({ schedule_id: sid ? Number(sid) : null }) });
    toast(r.message);
    refreshUnderPanel();
    route();
  } catch (e) {
    if (e instanceof SignedOut) return route();
    toast(e.message);
    button.disabled = false;
  }
}

function moveSheet(day, sid, name) {
  const holder = $("sheet");
  const start = new Date(`${isoToday()}T12:00:00`);
  const days = [];
  for (let i = 0; days.length < 8 && i < 15; i++) {
    const d = new Date(start); d.setDate(start.getDate() + i);
    const iso = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
    if (iso !== day) days.push({ iso, wd: d.toLocaleDateString("en-GB", { weekday: "short" }), n: d.getDate(), today: i === 0 });
  }
  holder.innerHTML = `<div class="scrim" id="scrim"><div class="sheet" role="dialog" aria-modal="true" aria-labelledby="sheet-title">
    <div class="stack-tight"><span class="strong" id="sheet-title">Move ${esc(name || "this session")}</span><span class="meta">Pick a day. The watch follows.</span></div>
    <div class="days-grid">${days.map((d) => `<button class="pill" data-to="${d.iso}"><b>${d.today ? "Today" : esc(d.wd)}</b>${d.n}</button>`).join("")}</div>
    <button class="pill" id="sheet-cancel">Cancel</button></div></div>`;
  const close = () => { holder.innerHTML = ""; };
  $("sheet-cancel").onclick = close;
  $("scrim").onclick = (e) => { if (e.target.id === "scrim") close(); };
  holder.querySelector("[data-to]").focus();
  holder.querySelectorAll("[data-to]").forEach((b) => {
    b.onclick = async () => {
      holder.querySelectorAll("button").forEach((x) => { x.disabled = true; });
      try {
        const r = await api(`session/${day}/move`, { method: "POST", body: JSON.stringify({ to: b.dataset.to, schedule_id: sid ? Number(sid) : null }) });
        close();
        toast(r.message);
        refreshUnderPanel();
        location.hash = `#/session/${r.date}?from=today`;
      } catch (e) {
        close();
        if (e instanceof SignedOut) return route();
        toast(e.message);
      }
    };
  });
}

// -- Plan: the week, with hold to move, and the block ---------------------------

function planRow(r, day, first) {
  const href = r.state === "done" && r.activity_id ? `#/run/${r.activity_id}?from=plan` : `#/session/${day.date}?sid=${r.schedule_id}&from=plan`;
  const mark = r.state === "done" ? `<span class="tick bg-${esc(r.colour)}" style="width:15px;height:15px">${ICON.check(9)}</span>`
    : r.state === "next" ? `<span class="ring" style="width:14px;height:14px"></span>`
    : r.missed ? `<span class="miss-ring" style="width:12px;height:12px"></span>` : "";
  return `<a class="plan-row ${first ? "" : "same-day"} ${r.state === "next" ? "is-next" : ""}" href="${href}" data-date="${day.date}" draggable="false"
      data-sid="${r.schedule_id || ""}" ${r.movable ? 'data-movable="1"' : ""} data-name="${esc(r.name)}">
    <span class="day ${first ? "" : "hide"}"><span class="d">${esc(day.dow)}</span><span class="n ${day.today ? "today" : ""}">${day.n}</span></span>
    <span class="body"><span class="type c-${esc(r.colour)}">${mark}${esc(r.type_label)}</span>
      <span class="name ${r.state === "done" ? "dim" : ""}">${esc(r.name)}</span>
      ${r.meta ? `<span class="meta ${r.state === "next" ? "red" : r.missed ? "soft" : ""}">${esc(r.meta)}</span>` : ""}
      ${r.segments ? `<span class="mini-shape">${r.segments.map((g) => `<i class="bg-${esc(g.colour)}" style="flex-grow:${g.w};height:${Math.max(3, Math.round(g.h * 0.45))}px"></i>`).join("")}</span>` : ""}</span>
    <span class="val ${r.state === "done" ? "dim" : ""}">${esc(r.v)}${r.u ? `<span class="u"> ${esc(r.u)}</span>` : ""}</span></a>`;
}

function weekView(d) {
  const w = d.week;
  const ask = askLink({ ...d, ask: w.ask });
  const askNext = askLink({ ...d, ask: w.ask_next }, "Plan next week");
  const rows = w.days.map((day) => day.sessions.length
    ? day.sessions.map((r, i) => planRow(r, day, i === 0)).join("")
    : `<div class="plan-row rest" data-date="${day.date}"><span class="day"><span class="d">${esc(day.dow)}</span><span class="n ${day.today ? "today" : ""}">${day.n}</span></span>
        <span class="body"><span class="type c-rec">Rest</span><span class="name dim">Rest day</span></span><span></span></div>`).join("");
  const nav = (to, label, icon) => to ? `<a class="pill icon" href="#/plan?week=${to}" aria-label="${label}">${icon}</a>` : `<span class="pill icon" aria-hidden="true" style="visibility:hidden">${icon}</span>`;
  return `<div class="week-head"><div class="stack-tight" style="gap:2px;min-width:0"><span class="section-title">${esc(w.title)}</span>
      <span class="meta">${w.planned ? `<strong style="color:var(--ink);font-weight:600">${esc(w.done ?? 0)}</strong> of ${esc(w.planned)} km · ` : ""}${esc(w.range)}</span></div>
      <div class="pills" style="flex:0 0 auto">${nav(w.previous, "Week before", ICON.left())}${nav(w.next, "Week after", ICON.right())}</div></div>
    <div class="card plan-list" id="plan-list">${rows}</div>
    <div class="week-foot"><span class="meta" style="flex:1;font-size:13.5px;line-height:1.4">Change anything here and the watch follows. Hold a row to move it.</span>${ask.html.replace("pill red tall", "pill red")}</div>
    ${w.current ? `<div style="display:flex">${askNext.html.replace("pill red tall", "pill")}</div>` : ""}`;
}

function blockView(d) {
  const b = d.block;
  if (!b) {
    const ask = askLink(d, `Build a block with ${d.assistant === "ChatGPT" ? "ChatGPT" : "Claude"}`);
    return `<div class="card pad stack"><span class="strong">No training block on the calendar.</span><span class="meta">A block built with Daash shows here week by week.</span>${ask.html}</div>`;
  }
  const cols = `repeat(${b.bars.length}, minmax(0, 1fr))`;
  const bars = b.bars.map((x) => `<div class="bar-col"><span class="bar-label ${x.current ? "red" : ""}">${esc(x.label)}</span>
      <div class="bar ${x.current ? "now" : x.past ? "past" : ""}" style="height:${Math.max(4, Math.round(x.h * 120))}px">
        <span style="height:${Math.round(x.fill * 100)}%"></span></div></div>`).join("");
  const phases = b.phases ? `<div class="phases" style="grid-template-columns:${b.phases.map((p) => `${p.weeks}fr`).join(" ")}">
      ${b.phases.map((p) => p.label
        ? `<span class="${p.current ? "now" : ""}" title="${esc(p.label)}">${esc((p.weeks * 3 < b.bars.length / 4 ? p.label.slice(0, 2) : p.label).toUpperCase())}</span>`
        : `<span class="gap"></span>`).join("")}</div>` : "";
  const ask = askLink(d, "Edit the plan");
  return `<div class="card block-card"><div class="section-head"><span class="section-title">${esc(b.title)}</span><span class="meta">km per week</span></div>
      <div class="bars" style="grid-template-columns:${cols}" role="img" aria-label="Planned and run distance for each week of the block">${bars}</div>
      <div class="bar-nums" style="grid-template-columns:${cols}">${b.bars.map((x) => `<span class="${x.current ? "red" : ""}">${x.show_n ? x.n : ""}</span>`).join("")}</div>
      ${phases}
      <div class="legend"><span><i style="background:var(--muted)"></i>Run</span><span><i style="background:var(--red)"></i>This week</span><span><i class="planned"></i>Planned</span></div>
      <span class="block-verdict">${esc(b.verdict)}</span></div>
    <div class="week-foot"><div class="stack-tight" style="gap:2px;flex:1;min-width:0"><span class="strong">${esc(b.name)}</span><span class="meta">${esc(b.dates)}</span>
      ${(b.blocks || []).map((x) => `<span class="meta" style="font-size:12.5px">${esc(x.name)}${x.dates ? ` · ${esc(x.dates)}` : ""}</span>`).join("")}</div>
      ${ask.html.replace("pill red tall", "pill")}</div>`;
}

async function showPlan(view, params) {
  tabs("plan");
  if (base !== "plan") loading();
  try {
    const week = params.get("week");
    const d = await api(`plan${week ? `?week=${encodeURIComponent(week)}` : ""}`);
    const seg = (id, label) => `<a href="#/plan${id === "block" ? "/block" : ""}" class="${view === id ? "on" : ""}" ${view === id ? 'aria-current="page"' : ""}>${label}</a>`;
    const head = `<header class="stack" style="gap:12px"><h1 class="headline">Plan</h1><nav class="segmented" aria-label="Plan view">${seg("week", "Week")}${seg("block", "Block")}</nav></header>`;
    if (d.empty) {
      const ask = askLink(d, `Build a plan with ${d.assistant === "ChatGPT" ? "ChatGPT" : "Claude"}`);
      $("screen").innerHTML = head + `<div class="card pad stack"><span class="strong">No sessions on your Garmin calendar yet.</span>${ask.html}</div>`;
      return;
    }
    remember(d);
    base = "plan";
    if (desktop()) {
      $("screen").innerHTML = `<header><h1 class="headline" style="font-size:28px">Plan</h1></header>
        <div class="desk-plan"><div class="stack">${weekView(d)}</div><div class="stack">${blockView(d)}</div></div>`;
    } else {
      $("screen").innerHTML = head + (view === "block" ? blockView(d) : weekView(d));
    }
    if (desktop() || view !== "block") holdToMove($("plan-list"), d.today);
  } catch (e) {
    if (e instanceof SignedOut) return route();
    failed(e.message, () => showPlan(view, params));
  }
}

// Hold a row, drag it to another day, let go: the same move as the session's
// Move button. A short tap still opens the session.
const HOLD_MS = 450;
let dragging = null;
document.addEventListener("touchmove", (e) => { if (dragging) e.preventDefault(); }, { passive: false });

function holdToMove(list, today) {
  if (!list) return;
  let timer = null, start = null, justDropped = false, raf = 0, last = null;
  const rows = () => [...list.querySelectorAll(".plan-row")];
  const scroller = (() => {
    for (let el = list.parentElement; el; el = el.parentElement) {
      const o = getComputedStyle(el).overflowY;
      if ((o === "auto" || o === "scroll") && el.scrollHeight > el.clientHeight) return el;
    }
    return document.scrollingElement || document.documentElement;
  })();
  const DAY_NAMES = { Mon: "Monday", Tue: "Tuesday", Wed: "Wednesday", Thu: "Thursday", Fri: "Friday", Sat: "Saturday", Sun: "Sunday" };

  // The week as seven days, each holding its sessions in order. A run moves
  // into a day of its own: onto an empty day it just goes; onto a day with a
  // run, that run makes way one day along, towards the day the dragged one
  // left, into the first free day (Nov 3: people training a few days a week
  // rarely want two runs on one day). Done sessions and past days don't move.
  const plan = (target) => {
    const { from, row, week } = dragging;
    const fresh = () => week.dates.map((d) => ({ date: d, items: week.items[d].filter((r) => r !== row) }));
    let days = fresh();
    const at = (date) => days.findIndex((d) => d.date === date);
    const t = at(target), f = at(from);
    if (t === f || !days[t].items.length) { days[t].items.push(row); return days; }
    // The run there makes way one day at a time, in whichever direction
    // moves fewest sessions (the gap the dragged one left wins a tie), until
    // a free day takes it; with no room either way, the day takes both.
    const push = (step) => {
      const ds = fresh(), can = (d) => d.date >= today && d.items.every((r) => r.dataset.movable);
      let i = t, carry = ds[t].items, moved = carry.length;
      ds[t].items = [row];
      while (true) {
        const j = i + step;
        if (j < 0 || j >= ds.length || !can(ds[j])) return null;
        const next = ds[j].items;
        ds[j].items = carry;
        if (!next.length) return { ds, moved };
        moved += next.length; carry = next; i = j;
      }
    };
    const back = push(f > t ? 1 : -1), on = push(f > t ? -1 : 1);
    const best = !back ? on : !on ? back : on.moved < back.moved ? on : back;
    if (best) return best.ds;
    days[t].items = [row].concat(days[t].items);
    return days;
  };
  // Draw a plan: each day's rows in order, a rest row on an empty day, and
  // the day's name on its first row.
  const draw = (days) => {
    const { week } = dragging;
    days.forEach((d) => {
      const rest = week.rest[d.date];
      if (d.items.length) { if (rest) rest.classList.add("covered"); }
      else if (rest) rest.classList.remove("covered");
      (d.items.length ? d.items : rest ? [rest] : []).forEach((r, k) => {
        list.appendChild(r);
        if (r.classList.contains("rest")) return;
        const day = r.querySelector(".day");
        if (day) { day.innerHTML = week.label[d.date]; day.classList.toggle("hide", k > 0); }
        r.classList.toggle("same-day", k > 0);
        r.dataset.at = d.date;
      });
      if (rest) list.appendChild(rest);
    });
  };
  const glide = (change) => {
    const all = rows().filter((r) => r !== dragging.row);
    const before = new Map(all.map((r) => [r, r.getBoundingClientRect().top]));
    change();
    all.forEach((r) => {
      const was = before.get(r);
      if (was == null || r.classList.contains("covered")) return;
      const by = was - r.getBoundingClientRect().top;
      if (!by) return;
      r.style.transition = "none";
      r.style.transform = `translateY(${by}px)`;
      r.getBoundingClientRect();
      r.style.transition = "transform 200ms cubic-bezier(.2,.7,.3,1)";
      r.style.transform = "";
    });
  };
  // Which day the finger is over: the day of the row under it, or the
  // nearest end of the week.
  // Read against where each day sat when the run was picked up, not where
  // the rows are now: rows making way would otherwise put the day before
  // back under the finger, and the target flicked between the two (Nov 3).
  const dayAt = (x, y) => {
    const { bands } = dragging, at = y - list.getBoundingClientRect().top;
    const hit = bands.find((b) => at >= b.top && at < b.bottom);
    return hit ? hit.date : at < bands[0].top ? bands[0].date : bands[bands.length - 1].date;
  };
  const follow = (x, y) => {
    last = { x, y };
    const { row } = dragging;
    const date = dayAt(x, y);
    const ok = date && date >= today;
    row.classList.toggle("no-drop", !!date && !ok);
    if (ok && date !== dragging.target) {
      const days = plan(date);
      glide(() => draw(days));
      dragging.target = date; dragging.days = days;
      if (navigator.vibrate) navigator.vibrate(5);
    }
    row.classList.toggle("elsewhere", dragging.target !== dragging.from);
    row.style.transform = "none";
    const top = row.getBoundingClientRect().top;
    row.style.transform = `translateY(${y - dragging.grab - top}px) scale(1.02)`;
  };
  const edge = () => {
    raf = 0;
    if (!dragging || !last) return;
    const view = scroller === document.scrollingElement || scroller === document.documentElement
      ? { top: 0, bottom: window.innerHeight } : scroller.getBoundingClientRect();
    const zone = 70, speed = last.y < view.top + zone ? -(view.top + zone - last.y) / 4
      : last.y > view.bottom - zone ? (last.y - (view.bottom - zone)) / 4 : 0;
    if (speed) { scroller.scrollBy(0, Math.max(-18, Math.min(18, speed))); follow(last.x, last.y); }
    raf = requestAnimationFrame(edge);
  };
  const settle = (then) => {
    const { row } = dragging;
    row.style.transition = "transform 170ms cubic-bezier(.2,.7,.3,1)";
    row.style.transform = "";
    setTimeout(() => { row.style.transition = ""; then && then(); }, 180);
  };
  const clear = () => {
    clearTimeout(timer); timer = null;
    cancelAnimationFrame(raf); raf = 0;
    if (dragging) dragging.row.classList.remove("lifted", "no-drop", "elsewhere");
    rows().forEach((r) => { r.classList.remove("drop", "no-drop"); r.style.transform = ""; r.style.transition = ""; });
    list.classList.remove("dragging");
    dragging = null; last = null;
  };
  // The week as it is, read off the list once a row is lifted.
  const readWeek = () => {
    const dates = [], items = {}, rest = {}, label = {};
    rows().forEach((r) => {
      const d = r.dataset.date;
      if (!items[d]) { dates.push(d); items[d] = []; }
      const day = r.querySelector(".day");
      if (day && !day.classList.contains("hide") && !label[d]) label[d] = day.innerHTML;
      if (r.classList.contains("rest")) rest[d] = r; else items[d].push(r);
    });
    // An empty day needs a rest row to show while it's empty.
    dates.forEach((d) => {
      if (rest[d]) return;
      const ghost = document.createElement("div");
      ghost.className = "plan-row rest covered"; ghost.dataset.date = d;
      ghost.innerHTML = `<span class="day">${label[d] || ""}</span><span class="body"><span class="type c-rec">Rest</span><span class="name dim">Rest day</span></span><span></span>`;
      rest[d] = ghost;
    });
    return { dates, items, rest, label };
  };
  list.addEventListener("contextmenu", (e) => { if (e.target.closest("[data-movable]")) e.preventDefault(); });
  // A held link is something the phone drags itself (Android shows the link
  // as a bubble), which cancels this drag, so the row lifted but never
  // dropped (Nov 3). The browser's own drag of a row is stopped here.
  list.addEventListener("dragstart", (e) => { if (e.target.closest && e.target.closest(".plan-row")) e.preventDefault(); });
  list.addEventListener("pointerdown", (e) => {
    const row = e.target.closest(".plan-row[data-movable]");
    if (!row || e.button > 0) return;
    start = { x: e.clientX, y: e.clientY };
    timer = setTimeout(() => {
      dragging = { row, from: row.dataset.date, target: row.dataset.date, pointer: e.pointerId,
        grab: start.y - row.getBoundingClientRect().top };
      dragging.week = readWeek();
      const top = list.getBoundingClientRect().top;
      dragging.bands = dragging.week.dates.map((d) => {
        const own = rows().filter((r) => r.dataset.date === d && !r.classList.contains("covered")).map((r) => r.getBoundingClientRect());
        return { date: d, top: Math.min(...own.map((b) => b.top)) - top, bottom: Math.max(...own.map((b) => b.bottom)) - top };
      });
      dragging.days = null;
      try { row.setPointerCapture(e.pointerId); } catch { /* the move still tracks without it */ }
      row.classList.add("lifted");
      list.classList.add("dragging");
      if (navigator.vibrate) navigator.vibrate(10);
      follow(start.x, start.y);
      raf = requestAnimationFrame(edge);
    }, HOLD_MS);
  });
  list.addEventListener("pointermove", (e) => {
    if (!dragging) {
      if (timer && start && Math.hypot(e.clientX - start.x, e.clientY - start.y) > 8) { clearTimeout(timer); timer = null; }
      return;
    }
    e.preventDefault();
    follow(e.clientX, e.clientY);
  });
  // A phone can still cancel the pointer once a row is lifted (Nov 3: it
  // lifted but never dropped). The touch carries on regardless, so a lifted
  // row follows the finger and drops on the touch's own events too.
  // A row moved in the page stops getting the pointer's events on some
  // phones, so the drop never came (Nov 3); the touch's own events carry on.
  list.addEventListener("touchmove", (e) => {
    if (dragging && e.touches[0]) follow(e.touches[0].clientX, e.touches[0].clientY);
  });
  list.addEventListener("touchend", () => { if (dragging) finish(); });
  const backHome = () => {
    const days = dragging.week.dates.map((d) => ({ date: d, items: dragging.week.items[d] }));
    glide(() => draw(days));
  };
  const finish = async () => {
    if (!dragging) { clearTimeout(timer); timer = null; return; }
    if (dragging.done) return;
    dragging.done = true;
    const { row, from, target, days, week } = dragging;
    justDropped = true;
    setTimeout(() => { justDropped = false; }, 400);
    cancelAnimationFrame(raf); raf = 0;
    if (!days || target === from) { backHome(); settle(clear); return; }
    // Every session whose day changed, the dragged one first.
    const was = new Map(); week.dates.forEach((d) => week.items[d].forEach((r) => was.set(r, d)));
    const moves = [];
    days.forEach((d) => d.items.forEach((r) => { if (was.get(r) !== d.date) moves.push({ r, from: was.get(r), to: d.date }); }));
    moves.sort((a, b) => (b.r === row) - (a.r === row));
    settle(() => { clear(); moves.forEach((m) => m.r.classList.add("moving")); });
    const said = [];
    try {
      for (const m of moves) {
        await api(`session/${m.from}/move`, { method: "POST", body: JSON.stringify({ to: m.to, schedule_id: Number(m.r.dataset.sid) || null }) });
        const dow = (week.label[m.to] || "").match(/>(\w{3})</);
        const key = dow ? dow[1].slice(0, 1).toUpperCase() + dow[1].slice(1).toLowerCase() : "";
        said.push(`${m.r.dataset.name} to ${DAY_NAMES[key] || key || m.to}`);
      }
      toast(`Moved ${said.join(", and ")}. The watch follows at its next sync.`);
    } catch (err) {
      if (err instanceof SignedOut) return route();
      toast(said.length ? `Moved ${said.join(", and ")}, then: ${err.message}` : err.message);
    }
    route();
  };
  list.addEventListener("pointerup", finish);
  list.addEventListener("pointercancel", (e) => {
    if (dragging && e.pointerType === "touch") { dragging.cancelled = true; return; }
    if (dragging) backHome();
    clear();
  });
  list.addEventListener("click", (e) => { if (justDropped) { e.preventDefault(); e.stopPropagation(); } }, true);
}

// -- Crew: runs to join, and who runs when (view only) --------------------------

function face(f, size = 30, ring = "var(--surface)") {
  const bg = f.you ? "var(--red)" : FACE[f.c ?? 0];
  return `<span class="face" style="width:${size}px;height:${size}px;background:${bg};color:${f.you ? "#FFFFFF" : "var(--ground)"};border-color:${ring};font-size:${Math.round(size * 0.4)}px">${esc(f.i)}</span>`;
}

function crewParts(d, desk = false) {
  const p = { head: `<header class="stack-tight"><span class="kicker">${esc(d.kicker)}</span><h1 class="headline" ${desk ? 'style="font-size:28px"' : ""}>${esc(d.headline)}</h1></header>` };
  if (d.hero) {
    const h = d.hero;
    p.hero = `<section class="card red" style="gap:12px" aria-label="Your best match this week">
      <div class="next-top"><span class="kicker red" style="font-weight:500;letter-spacing:0.08em">${esc(h.kicker)}</span><span class="chip chip-${esc(h.colour)}">${esc(h.type_label)}</span></div>
      <div style="display:flex;align-items:center;gap:12px"><div class="faces">${h.faces.map((f) => face(f, desk ? 38 : 36, "var(--red-soft)")).join("")}</div>
        <span style="font-size:22px;font-weight:700;letter-spacing:-0.025em;line-height:1.15">${esc(h.names)}</span></div>
      <span style="font-size:14px;line-height:1.45;color:var(--ink-2)">${esc(h.line)}</span>
      <a class="pill red" href="${esc(h.whatsapp)}" target="_blank" rel="noopener">Ask the group on WhatsApp</a></section>`;
  } else {
    p.hero = `<div class="card pad stack-tight"><span class="strong">Nothing lines up yet</span><span class="meta" style="line-height:1.45">When two of you have an easy or long run at the same time of day, it shows here.</span></div>`;
  }
  if (d.also) {
    p.also = `<section class="stack"><span class="section-title">Also lining up</span><div class="card plan-list">${d.also.map((m) => `
      <div class="crew-match"><span class="day"><span class="d">${esc(m.dow)}</span><span class="n">${m.n}</span></span>
        <span class="stack-tight" style="gap:2px;min-width:0"><span style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
          ${m.slot ? `<span class="strong">${esc(m.slot)}</span>` : ""}<span class="c-${esc(m.colour)}" style="font-size:12px;font-weight:600">${esc(m.type_label)}</span>
          ${m.tag ? `<span style="font-size:12px;font-weight:600;color:var(--red-text)">${esc(m.tag)}</span>` : ""}</span>
          <span class="meta ellipsis">${esc(m.names)}</span></span>
        <div class="faces">${m.faces.map((f) => face(f, 28)).join("")}</div></div>`).join("")}</div></section>`;
  }
  const eye = svg('<path d="M2.06 12.35a1 1 0 0 1 0-.7 10.75 10.75 0 0 1 19.88 0 1 1 0 0 1 0 .7 10.75 10.75 0 0 1-19.88 0"/><circle cx="12" cy="12" r="3"/>', 13);
  const seg = (x) => {
    if (!x.kind) return `<i></i>`;
    if (x.kind === "hard") return `<i class="hard"></i>`;
    return `<i class="bg-${x.kind === "long" ? "long" : "easy"} ${x.match ? "match" : ""}"></i>`;
  };
  const key = `<span class="slot-key" aria-hidden="true"><span>AM</span><span>PM</span><span>EVE</span></span>`;
  const who = (person) => desk
    ? `<span class="who desk" role="rowheader">${face({ i: person.name[0], you: person.you, c: person.c }, 26, "transparent")}
        <span class="stack-tight" style="gap:0;flex:1;min-width:0"><span class="name ${person.you ? "you" : ""}">${esc(person.name)}</span>${person.by_hand ? `<span class="hand">by hand</span>` : ""}</span>${key}</span>`
    : `<span class="who" role="rowheader"><span class="name ${person.you ? "you" : ""}">${esc(person.name)}</span>${person.by_hand ? `<span class="hand">by hand</span>` : ""}</span>`;
  p.grid = `<section class="stack"><div class="section-head" style="align-items:center${desk ? ";padding-top:30px" : ""}"><span class="section-title" ${desk ? 'style="font-size:17px"' : ""}>Who runs when</span><span class="view-only">${eye}View only</span></div>
    <div class="crew-grid ${desk ? "desk" : ""}" role="table" aria-label="Crew calendar, view only">
      <div class="grid-row head" role="row">${desk ? "<span></span>" : `<span class="slots" aria-hidden="true"><span>AM</span><span>PM</span><span>EVE</span></span>`}
        ${d.days.map((x) => `<span class="grid-day ${x.past ? "past" : ""}" role="columnheader"><span class="d">${desk ? ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"][d.days.indexOf(x)] : x.letter}</span><span class="n ${x.today ? "today" : ""}">${x.n}</span></span>`).join("")}</div>
      ${d.people.map((person) => `<div class="grid-row" role="row">${who(person)}
        ${person.cells.map((c) => `<span class="cell ${c.past ? "past" : ""}" role="cell">${c.segs.map(seg).join("")}</span>`).join("")}</div>`).join("")}
      <div class="legend" style="padding-top:8px;border-top:1px solid var(--hairline);font-size:12px"><span><i class="lg bg-easy"></i>Easy</span><span><i class="lg bg-long"></i>Long</span>
        <span><i class="lg hard"></i>Hard, runs alone</span><span><i class="lg bg-easy match"></i>Same time</span></div></div>
    <span class="meta" style="line-height:1.45;text-wrap:pretty">Runs fill in from each person's watch or plan. Yours follow your plan; <a href="#/plan">change them in Plan</a>.</span></section>`;
  const inv = d.invite || {};
  const value = inv.url || inv.code || "";
  p.invite = `<div class="card pad stack" style="gap:10px"><span class="strong">Invite a friend</span>
    <span class="meta" style="font-size:13.5px;line-height:1.45">${inv.url ? "They see days and times of easy and long runs. Never pace, heart rate or routes. No watch needed."
      : "They tell their Daash the crew's code to join. They see days and times of easy and long runs, never pace, heart rate or routes."}</span>
    <div style="display:flex;gap:8px;align-items:center"><span class="copy-field">${esc(inv.shown || inv.code || "")}</span>
      <button class="pill" id="copy" data-value="${esc(value)}" style="flex-shrink:0">${inv.url ? "Copy link" : "Copy code"}</button></div></div>`;
  return p;
}

function crewView(d) {
  if (desktop()) {
    const p = crewParts(d, true);
    return `<div class="desk-crew"><div class="stack" style="gap:16px">${[p.head, p.hero, p.also, p.invite].filter(Boolean).join("")}</div><div>${p.grid}</div></div>`;
  }
  const p = crewParts(d);
  return [p.head, p.hero, p.also, p.grid, p.invite].filter(Boolean).join("");
}

async function showCrew() {
  tabs("crew");
  loading();
  base = "crew";
  try {
    const d = await api("crew");
    remember(d);
    if (d.none) {
      const ask = askLink(d, `Start a crew with ${d.assistant === "ChatGPT" ? "ChatGPT" : "Claude"}`);
      $("screen").innerHTML = `<header class="stack-tight"><span class="kicker">CREW</span><h1 class="headline">Run with friends</h1></header>
        <div class="card pad stack"><span class="strong">You're not in a crew yet.</span>
        <span class="meta" style="line-height:1.5">A crew is a few friends who see each other's easy and long runs, by day and time of day, and line them up. Never pace, heart rate or routes. Have a code from a friend? Ask your assistant to join with it.</span>${ask.html}</div>`;
      return;
    }
    $("screen").innerHTML = crewView(d);
    const copy = $("copy");
    if (copy) copy.onclick = async () => {
      try { await navigator.clipboard.writeText(copy.dataset.value); toast(copy.textContent.includes("link") ? "Link copied." : "Code copied."); }
      catch { toast("Couldn't copy. Press and hold the link to copy it."); }
    };
  } catch (e) {
    if (e instanceof SignedOut) return route();
    failed(e.message, showCrew);
  }
}

// -- You: the watch, the doors, settings and data -------------------------------

function youRow(r, d, i) {
  const tone = r.tone === "ok" ? "var(--easy)" : r.tone === "red" ? "var(--red-text)" : "var(--muted)";
  const href = r.href || (r.ask ? askLink(d).href : null);
  const inner = `<span class="grow"><span style="font-size:15px;font-weight:500;${r.danger ? "color:var(--red-text)" : ""}">${esc(r.l)}</span>
      ${r.sub ? `<span class="meta" style="font-size:12.5px;line-height:1.4">${esc(r.sub)}</span>` : ""}</span>
    ${r.v ? `<span class="you-v" style="color:${tone}">${esc(r.v)}</span>` : ""}
    ${(href || r.action) && !r.danger && r.action !== "signout" ? ICON.right(16, "var(--muted)") : ""}`;
  const cls = `you-row ${i ? "" : "first"}`;
  if (href) {
    const outside = /^https?:/.test(href);
    return `<a class="${cls}" href="${esc(href)}" ${outside ? 'target="_blank" rel="noopener"' : ""}>${inner}</a>`;
  }
  if (r.action) return `<button class="${cls}" data-you="${esc(r.action)}">${inner}</button>`;
  return `<div class="${cls}">${inner}</div>`;
}

function youView(d) {
  return `<header style="display:flex;align-items:center;gap:12px"><span class="avatar">${esc(d.initial)}</span>
      <span class="stack-tight" style="gap:2px;min-width:0"><h1 class="headline">${esc(d.name)}</h1>${d.since ? `<span class="meta">${esc(d.since)}</span>` : ""}</span></header>
    <div class="card pad stack" style="gap:12px"><div style="display:flex;align-items:center;gap:12px">
      <span class="watch-icon" style="width:40px;height:40px;color:var(--ink)">${ICON.watch(20)}</span>
      <span class="grow"><span style="font-size:16px;font-weight:600">${esc(d.watch.name)}</span>
        ${d.watch.synced ? `<span style="display:flex;align-items:center;gap:6px;font-size:13px;color:var(--easy)"><span class="dot bg-easy" style="width:8px;height:8px"></span>${esc(d.watch.synced)}</span>`
          : `<span class="meta">Sync time not available</span>`}</span>
      <button class="pill" id="refresh" style="flex-shrink:0;padding:0 14px">Refresh</button></div>
      <span class="meta" style="font-size:13.5px;line-height:1.45;padding-top:10px;border-top:1px solid var(--hairline)">${esc(d.watch.line)}</span></div>
    ${d.groups.map((g) => g.title === "Assistants" && onComputer() ? { ...g, rows: [...g.rows,
      { l: "Ask Claude opens in", v: store.get(OPENS) === "web" ? "The browser" : "The Claude app", sub: "Tap to switch", action: "opens" }] } : g)
      .map((g) => `<section class="stack-8"><span class="kicker" style="font-size:11.5px;letter-spacing:0.08em;padding-left:2px">${esc(g.title.toUpperCase())}</span>
      <div class="card" style="overflow:hidden">${g.rows.map((r, i) => youRow(r, d, i)).join("")}</div></section>`).join("")}
    <p class="small" style="text-align:center">Daash 0.1 · <a href="${esc(d.privacy)}" target="_blank" rel="noopener">Privacy</a><br>Daash is independent and not made or endorsed by Garmin.</p>`;
}

function sheet(html, wire) {
  const holder = $("sheet");
  holder.innerHTML = `<div class="scrim" id="scrim"><div class="sheet" role="dialog" aria-modal="true" aria-labelledby="sheet-title">${html}</div></div>`;
  const close = () => { holder.innerHTML = ""; };
  $("scrim").onclick = (e) => { if (e.target.id === "scrim") close(); };
  holder.querySelectorAll("[data-close]").forEach((b) => { b.onclick = close; });
  wire(holder, close);
  const first = holder.querySelector("button");
  if (first) first.focus();
}

function timesSheet(d) {
  const chosen = new Set(d.times.chosen);
  sheet(`<div class="stack-tight"><span class="strong" id="sheet-title">Usual run time</span>
      <span class="meta">When you usually run. The crew uses it to line runs up.</span></div>
    <div class="times-grid">${d.times.options.map((o) => `<button class="pill" data-code="${o.code}" aria-pressed="${chosen.has(o.code)}">${esc(o.label)}</button>`).join("")}</div>
    <div class="pills"><button class="pill" data-close>Cancel</button><button class="pill light" id="times-save">Save</button></div>`, (holder, close) => {
    holder.querySelectorAll("[data-code]").forEach((b) => {
      b.onclick = () => { b.setAttribute("aria-pressed", String(b.getAttribute("aria-pressed") !== "true")); };
    });
    $("times-save").onclick = async () => {
      const times = [...holder.querySelectorAll('[data-code][aria-pressed="true"]')].map((b) => b.dataset.code);
      try {
        const r = await api("you/times", { method: "POST", body: JSON.stringify({ times }) });
        close(); toast(r.message); route();
      } catch (e) { toast(e.message); }
    };
  });
}

function deleteSheet() {
  sheet(`<div class="stack-tight"><span class="strong" id="sheet-title">Delete your Daash account?</span>
      <span class="meta" style="line-height:1.5">This disconnects Garmin and deletes your stored session, what Daash keeps for you and your place in your crew. Claude and ChatGPT lose access too. Your Garmin account and the workouts on your watch stay as they are. It can't be undone.</span></div>
    <div class="pills"><button class="pill" data-close>Cancel</button><button class="pill red" id="delete-go">Delete account</button></div>`, (holder, close) => {
    $("delete-go").onclick = async () => {
      $("delete-go").disabled = true;
      try {
        await api("you/delete", { method: "POST", body: JSON.stringify({ confirm: "delete" }) });
        close(); token.clear();
        signInView("Your Daash account is deleted.");
      } catch (e) { close(); toast(e.message); }
    };
  });
}

async function signOut() {
  const access = token.access, client = CLIENT_ID;
  token.clear();
  if (access) {
    // Revoked as well as forgotten, so the token can't be used again from anywhere.
    fetch("/revoke", { method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({ token: access, client_id: client }) }).catch(() => {});
  }
  signInView("Signed out.");
}

async function showYou() {
  tabs("you");
  loading();
  base = "you";
  try {
    const d = await api("you");
    $("screen").innerHTML = youView(d);
    $("refresh").onclick = () => { toast("Checked Garmin again."); showYou(); };
    $("screen").querySelectorAll("[data-you]").forEach((b) => {
      b.onclick = () => {
          if (b.dataset.you === "opens") {
            store.set(OPENS, store.get(OPENS) === "web" ? "app" : "web");
            toast(store.get(OPENS) === "web" ? "Ask Claude opens in the browser." : "Ask Claude opens the Claude app, or the browser without it.");
            return showYou();
          }
          return b.dataset.you === "times" ? timesSheet(d) : b.dataset.you === "delete" ? deleteSheet() : signOut();
        };
    });
  } catch (e) {
    if (e instanceof SignedOut) return route();
    failed(e.message, showYou);
  }
}

async function signInPost(path, body) {
  const res = await fetch(API + "sign-in/" + path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const out = await res.json().catch(() => ({ error: "Daash didn't answer. Try again in a moment." }));
  if (!res.ok || out.error) throw new Error(out.error || "Something went wrong.");
  return out;
}

function signInView(message, step = "start", email = "") {
  $("tabs").hidden = true;
  $("topbar").hidden = true;
  base = null;
  const head = `<div class="wordmark"><img src="/app/icons/favicon.svg" width="32" height="32" alt="">Daash</div>`;
  const note = message ? `<div class="card pad meta" role="status">${esc(message)}</div>` : "";
  const foot = `<p class="small">Daash is independent and not made or endorsed by Garmin.</p>`;
  if (step === "code") {
    $("screen").innerHTML = `<div class="signin">${head}<h1 class="headline">Check your email</h1>
      <p class="why">If ${esc(email)} has a Daash account, a 6-digit code is on its way to it. It works for 10 minutes.</p>${note}
      <form id="code-form" class="stack"><label class="field-label" for="code">Code</label>
        <input id="code" class="field" inputmode="numeric" autocomplete="one-time-code" maxlength="7" required>
        <button class="pill red tall" type="submit">Sign in</button></form>
      <div class="pills"><button class="pill" id="resend">Send a new code</button><button class="pill" id="other">Use another email</button></div>${foot}</div>`;
    $("code").focus();
    $("code-form").onsubmit = async (e) => {
      e.preventDefault();
      const button = e.target.querySelector("button"); button.disabled = true;
      try {
        token.save(await signInPost("verify", { email, code: $("code").value }));
        history.replaceState(null, "", "/app/#/today");
        route();
      } catch (err) { signInView(err.message, "code", email); }
    };
    $("resend").onclick = async () => {
      try { await signInPost("email", { email }); signInView("Sent a new code. Use the latest one.", "code", email); }
      catch (err) { signInView(err.message, "code", email); }
    };
    $("other").onclick = () => signInView(null, "start");
    return;
  }
  $("screen").innerHTML = `<div class="signin">${head}
    <h1 class="headline">Your plan, from Claude or ChatGPT, on your watch.</h1>
    <p class="why">See today's session, check how a run went, and move or ease a session. The changes reach your watch and the chat.</p>
    ${note}
    <form id="email-form" class="stack"><label class="field-label" for="email">Already use Daash? Your email</label>
      <input id="email" class="field" type="email" autocomplete="email" value="${esc(email)}" required>
      <button class="pill red tall" type="submit">Email me a code</button></form>
    <p class="small">The email your Daash account is under, usually your Garmin one. Your plan, crew and settings are waiting.</p>
    <div class="or"><span>New to Daash?</span></div>
    <button class="pill tall" id="garmin">Sign in with Garmin</button>
    <p class="small">Connects your Garmin account. You can add Claude or ChatGPT later.</p>${foot}</div>`;
  $("email-form").onsubmit = async (e) => {
    e.preventDefault();
    const address = $("email").value.trim();
    e.target.querySelector("button").disabled = true;
    try { await signInPost("email", { email: address }); signInView(null, "code", address); }
    catch (err) { signInView(err.message, "start", address); }
  };
  $("garmin").onclick = async () => {
    $("garmin").disabled = true;
    try { await signIn(); } catch (err) { signInView(err.message); }
  };
}

// Open in Daash from a chat card carries a single-use code (login=…): it
// signs the app in as the person who tapped it, with nothing to type.
async function handOff() {
  const m = location.hash.match(/[?&]login=([\w-]+)/);
  if (!m) return null;
  const clean = location.hash.replace(/[?&]login=[\w-]+/, "").replace(/\?$/, "");
  history.replaceState(null, "", `/app/${clean || "#/today"}`);
  if (token.access) return null;
  try {
    token.save(await signInPost("handoff", { code: m[1] }));
    return null;
  } catch (err) {
    return `${err.message} Sign in once here and the app remembers you.`;
  }
}

// -- routing ------------------------------------------------------------------

async function route() {
  $("sheet").innerHTML = "";
  // A link from scripts/app_dev.py carries a local token, so the demo opens signed in.
  const dev = location.hash.match(/dev-token=([\w-]+)/);
  if (dev) {
    token.save({ access_token: dev[1] });
    history.replaceState(null, "", "/app/#/today");
  }
  const handed = await handOff();
  if (handed) return signInView(handed);
  if (!token.access) return signInView();
  const [path, query] = (location.hash.replace(/^#\/?/, "") || "today").split("?");
  const params = new URLSearchParams(query || "");
  const [screen, arg] = path.split("/");
  const wide = desktop();
  document.body.classList.toggle("desk", wide);
  document.body.dataset.screen = screen || "today";
  const panelled = wide && (screen === "session" || screen === "run");
  if (!panelled) { $("panel").hidden = true; $("panel").innerHTML = ""; window.scrollTo(0, 0); }
  if (screen === "session" && /^\d{4}-\d{2}-\d{2}$/.test(arg || "")) return wide ? showPanel("session", arg, params) : showSession(arg, params);
  if (screen === "run" && /^\d+$/.test(arg || "")) return wide ? showPanel("run", arg, params) : showRun(arg, params);
  if (screen === "plan") return showPlan(arg === "block" ? "block" : "week", params);
  if (screen === "crew") return showCrew();
  if (screen === "you") return showYou();
  return showToday();
}

async function start() {
  let message = null;
  if (location.pathname.startsWith("/app/callback")) message = await finishSignIn();
  if (message) return signInView(message);
  window.addEventListener("hashchange", route);
  route();
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/app/sw.js", { scope: "/app/" }).catch(() => {});
}

start();

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

async function clientId() {
  const known = store.get("daash.client");
  if (known) return known;
  const res = await fetch("/register", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ client_name: "Daash app", redirect_uris: [CALLBACK], grant_types: ["authorization_code", "refresh_token"],
      response_types: ["code"], token_endpoint_auth_method: "none" }),
  });
  if (!res.ok) throw new Error("Daash couldn't start the sign-in.");
  const id = (await res.json()).client_id;
  store.set("daash.client", id);
  return id;
}

async function signIn() {
  const id = await clientId();
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
      client_id: store.get("daash.client") || "", code_verifier: saved.verifier }),
  });
  history.replaceState(null, "", `/app/${saved.back || "#/today"}`);
  if (!res.ok) return "That sign-in didn't complete. Try again.";
  token.save(await res.json());
  return null;
}

async function refreshed() {
  if (!token.refresh || !store.get("daash.client")) return false;
  const res = await fetch("/token", {
    method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ grant_type: "refresh_token", refresh_token: token.refresh, client_id: store.get("daash.client") }),
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
  return { href: data.ask?.[app], name, html: `<a class="pill red tall" href="${esc(data.ask?.[app])}" target="_blank" rel="noopener">${esc(label || `Ask ${name}`)}${ICON.out()}</a>` };
}

function tabs(current) {
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

function todayView(d) {
  const parts = [`<header class="stack-tight"><span class="kicker">${esc(d.kicker)}</span><h1 class="headline">${esc(d.headline)}</h1></header>`];
  if (d.done_today) {
    const r = d.done_today;
    parts.push(`<a class="row-button" href="#/run/${r.activity_id}?from=today">
      <span class="tick bg-${esc(r.colour)}">${ICON.check()}</span>
      <span class="grow"><span class="kicker" style="font-size:11.5px">DONE TODAY${r.at ? ` · ${esc(r.at)}` : ""}</span><span class="strong">${esc(r.line)}</span></span>
      ${ICON.right(18, "var(--muted)")}</a>`);
  }
  if (d.next) {
    const n = d.next;
    const open = `#/session/${n.date}?from=today`;
    parts.push(`<section class="card red" aria-label="Next session">
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
    </section>`);
  } else if (!d.empty) {
    parts.push(`<div class="card pad stack-tight"><span class="strong">Nothing else planned</span><span class="meta">The plan on your Garmin calendar has no more sessions.</span></div>`);
  }
  if (d.empty) {
    const ask = askLink(d, `Build a plan with ${d.assistant === "ChatGPT" ? "ChatGPT" : "Claude"}`);
    parts.push(`<div class="card pad stack"><span class="strong">No sessions on your Garmin calendar yet.</span>
      <span class="meta">Ask ${esc(ask.name)} to build a plan with Daash. It lands here and on your watch.</span>${ask.html}</div>`);
  }
  if (d.crew) {
    parts.push(`<div class="card crew-line"><div class="faces">${d.crew.faces.map((f, i) => `<span class="face" style="background:${FACE[i % 4]}">${esc(f)}</span>`).join("")}</div>
      <span class="grow" style="gap:1px"><span style="font-size:14.5px;font-weight:600;line-height:1.3">${esc(d.crew.line)}</span><span class="meta" style="font-size:12.5px">${esc(d.crew.sub)}</span></span>
      <a class="pill" href="#/crew" style="flex-shrink:0;padding:0 14px">Line up</a></div>`);
  }
  if (d.readiness) {
    parts.push(`<section class="stack" aria-label="Readiness"><div class="section-head"><span class="section-title">Readiness</span><span class="mono-label">FROM GARMIN</span></div>
      <div class="card trio">${d.readiness.tiles.map((t) => `<div><span class="l">${esc(t.l)}</span><span class="v">${esc(t.v)}</span><span class="n">${esc(t.note)}</span></div>`).join("")}</div></section>`);
  } else {
    parts.push(`<section class="stack"><span class="section-title">Readiness</span><div class="card pad meta">Readiness needs a night of sleep data. Wear the watch overnight and it shows here.</div></section>`);
  }
  if (d.week) {
    const w = d.week;
    const total = w.planned ? `<span class="meta"><strong style="color:var(--ink);font-weight:600">${esc(w.done ?? 0)}</strong> of ${esc(w.planned)} ${esc(w.unit)}</span>` : "";
    const mark = (day) => day.state === "done" ? `<span class="tick bg-${esc(day.colour)}" style="width:18px;height:18px">${ICON.check(11)}</span>`
      : day.state === "next" ? `<span class="ring"></span>` : day.state === "rest" ? `<span class="dash"></span>`
      : day.state === "missed" ? `<span class="miss-ring" title="Missed"></span>`
      : day.dots.map((c) => `<span class="dot bg-${esc(c)}"></span>`).join("");
    const label = (day) => `${day.n}, ${day.state === "rest" ? "rest day" : day.state}`;
    parts.push(`<section class="stack" aria-label="This week"><div class="section-head"><span class="section-title">This week</span>${total}</div>
      <div class="card strip-card"><div class="strip">${w.days.map((day) => `<a href="${day.state === "rest" ? "#/today" : `#/session/${day.date}?from=today`}" aria-label="${esc(label(day))}" ${day.state === "rest" ? 'tabindex="-1" style="pointer-events:none"' : ""}>
        <span class="d">${day.letter}</span><span class="n ${day.today ? "today" : ""}">${day.n}</span><span class="mark">${mark(day)}</span></a>`).join("")}</div>
        ${w.verdict ? `<span class="strip-verdict">${esc(w.verdict)}</span>` : ""}</div></section>`);
  }
  return parts.join("");
}

async function showToday() {
  tabs("today");
  loading();
  try {
    const d = await api("today");
    $("screen").innerHTML = todayView(d);
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

function previewView(d, from) {
  const back = from === "plan" ? "Plan" : "Today";
  const ask = askLink(d);
  const r = d.readiness;
  const parts = [`<a class="back" href="#/${from === "plan" ? "plan" : "today"}">${ICON.left()}${back}</a>`,
    `<header class="stack-8"><span class="kicker ${d.today ? "red" : ""}">${esc(d.kicker)}</span><h1 class="title-xl">${esc(d.name)}</h1>
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

function analysisView(d, from) {
  const back = from === "plan" ? "Plan" : "Today";
  const ask = askLink(d, `Ask ${d.assistant === "ChatGPT" ? "ChatGPT" : "Claude"} about it`);
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
  return `<a class="plan-row ${first ? "" : "same-day"} ${r.state === "next" ? "is-next" : ""}" href="${href}" data-date="${day.date}"
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
      ${b.phases.map((p) => `<span class="${p.current ? "now" : ""}">${esc(p.label.toUpperCase())}</span>`).join("")}</div>` : "";
  const ask = askLink(d, "Edit the plan");
  return `<div class="card block-card"><div class="section-head"><span class="section-title">${esc(b.title)}</span><span class="meta">km per week</span></div>
      <div class="bars" style="grid-template-columns:${cols}" role="img" aria-label="Planned and run distance for each week of the block">${bars}</div>
      <div class="bar-nums" style="grid-template-columns:${cols}">${b.bars.map((x) => `<span class="${x.current ? "red" : ""}">${x.n}</span>`).join("")}</div>
      ${phases}
      <div class="legend"><span><i style="background:var(--muted)"></i>Run</span><span><i style="background:var(--red)"></i>This week</span><span><i class="planned"></i>Planned</span></div>
      <span class="block-verdict">${esc(b.verdict)}</span></div>
    <div class="week-foot"><div class="stack-tight" style="gap:2px;flex:1;min-width:0"><span class="strong">${esc(b.name)}</span><span class="meta">${esc(b.dates)}</span></div>
      ${ask.html.replace("pill red tall", "pill")}</div>`;
}

async function showPlan(view, params) {
  tabs("plan");
  loading();
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
    $("screen").innerHTML = head + (view === "block" ? blockView(d) : weekView(d));
    if (view !== "block") holdToMove($("plan-list"), d.today);
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
  let timer = null, start = null, justDropped = false;
  const rows = () => [...list.querySelectorAll(".plan-row")];
  const clear = () => {
    clearTimeout(timer); timer = null;
    if (dragging) {
      dragging.row.classList.remove("lifted");
      dragging.row.style.transform = "";
    }
    rows().forEach((r) => r.classList.remove("drop", "no-drop"));
    list.classList.remove("dragging");
    dragging = null;
  };
  const dayRows = (date) => rows().filter((r) => r.dataset.date === date);
  list.addEventListener("contextmenu", (e) => { if (e.target.closest("[data-movable]")) e.preventDefault(); });
  list.addEventListener("pointerdown", (e) => {
    const row = e.target.closest(".plan-row[data-movable]");
    if (!row || e.button > 0) return;
    start = { x: e.clientX, y: e.clientY };
    timer = setTimeout(() => {
      dragging = { row, from: row.dataset.date, to: null, pointer: e.pointerId };
      try { row.setPointerCapture(e.pointerId); } catch { /* the move still tracks without it */ }
      row.classList.add("lifted");
      list.classList.add("dragging");
      rows().forEach((r) => { if (r.dataset.date < today) r.classList.add("no-drop"); });
      if (navigator.vibrate) navigator.vibrate(10);
    }, HOLD_MS);
  });
  list.addEventListener("pointermove", (e) => {
    if (!dragging) {
      if (timer && start && Math.hypot(e.clientX - start.x, e.clientY - start.y) > 8) { clearTimeout(timer); timer = null; }
      return;
    }
    e.preventDefault();
    dragging.row.style.transform = `translateY(${e.clientY - start.y}px)`;
    const under = document.elementsFromPoint(e.clientX, e.clientY).map((el) => el.closest && el.closest(".plan-row"))
      .find((el) => el && el !== dragging.row && list.contains(el));
    rows().forEach((r) => r.classList.remove("drop"));
    const to = under && under.dataset.date;
    dragging.to = to && to !== dragging.from && to >= today ? to : null;
    if (dragging.to) dayRows(dragging.to).forEach((r) => r.classList.add("drop"));
  });
  const finish = async () => {
    if (!dragging) { clearTimeout(timer); timer = null; return; }
    const { row, from, to } = dragging;
    justDropped = true;
    setTimeout(() => { justDropped = false; }, 400);
    clear();
    if (!to) return;
    try {
      const r = await api(`session/${from}/move`, { method: "POST", body: JSON.stringify({ to, schedule_id: Number(row.dataset.sid) || null }) });
      toast(r.message);
      route();
    } catch (err) {
      if (err instanceof SignedOut) return route();
      toast(err.message);
    }
  };
  list.addEventListener("pointerup", finish);
  list.addEventListener("pointercancel", clear);
  list.addEventListener("click", (e) => { if (justDropped) { e.preventDefault(); e.stopPropagation(); } }, true);
}

// -- Crew: runs to join, and who runs when (view only) --------------------------

function face(f, size = 30, ring = "var(--surface)") {
  const bg = f.you ? "var(--red)" : FACE[f.c ?? 0];
  return `<span class="face" style="width:${size}px;height:${size}px;background:${bg};color:${f.you ? "#FFFFFF" : "var(--ground)"};border-color:${ring};font-size:${Math.round(size * 0.4)}px">${esc(f.i)}</span>`;
}

function crewView(d) {
  const parts = [`<header class="stack-tight"><span class="kicker">${esc(d.kicker)}</span><h1 class="headline">${esc(d.headline)}</h1></header>`];
  if (d.hero) {
    const h = d.hero;
    parts.push(`<section class="card red" style="gap:12px" aria-label="Your best match this week">
      <div class="next-top"><span class="kicker red" style="font-weight:500;letter-spacing:0.08em">${esc(h.kicker)}</span><span class="chip chip-${esc(h.colour)}">${esc(h.type_label)}</span></div>
      <div style="display:flex;align-items:center;gap:12px"><div class="faces">${h.faces.map((f) => face(f, 36, "var(--red-soft)")).join("")}</div>
        <span style="font-size:22px;font-weight:700;letter-spacing:-0.025em;line-height:1.15">${esc(h.names)}</span></div>
      <span style="font-size:14px;line-height:1.45;color:var(--ink-2)">${esc(h.line)}</span>
      <a class="pill red" href="${esc(h.whatsapp)}" target="_blank" rel="noopener">Ask the group on WhatsApp</a></section>`);
  } else {
    parts.push(`<div class="card pad stack-tight"><span class="strong">Nothing lines up yet</span><span class="meta" style="line-height:1.45">When two of you have an easy or long run at the same time of day, it shows here.</span></div>`);
  }
  if (d.also) {
    parts.push(`<section class="stack"><span class="section-title">Also lining up</span><div class="card plan-list">${d.also.map((m) => `
      <div class="crew-match"><span class="day"><span class="d">${esc(m.dow)}</span><span class="n">${m.n}</span></span>
        <span class="stack-tight" style="gap:2px;min-width:0"><span style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
          ${m.slot ? `<span class="strong">${esc(m.slot)}</span>` : ""}<span class="c-${esc(m.colour)}" style="font-size:12px;font-weight:600">${esc(m.type_label)}</span>
          ${m.tag ? `<span style="font-size:12px;font-weight:600;color:var(--red-text)">${esc(m.tag)}</span>` : ""}</span>
          <span class="meta ellipsis">${esc(m.names)}</span></span>
        <div class="faces">${m.faces.map((f) => face(f, 28)).join("")}</div></div>`).join("")}</div></section>`);
  }
  const eye = svg('<path d="M2.06 12.35a1 1 0 0 1 0-.7 10.75 10.75 0 0 1 19.88 0 1 1 0 0 1 0 .7 10.75 10.75 0 0 1-19.88 0"/><circle cx="12" cy="12" r="3"/>', 13);
  const seg = (x) => {
    if (!x.kind) return `<i></i>`;
    if (x.kind === "hard") return `<i class="hard"></i>`;
    return `<i class="bg-${x.kind === "long" ? "long" : "easy"} ${x.match ? "match" : ""}"></i>`;
  };
  parts.push(`<section class="stack"><div class="section-head" style="align-items:center"><span class="section-title">Who runs when</span><span class="view-only">${eye}View only</span></div>
    <div class="crew-grid" role="table" aria-label="Crew calendar, view only">
      <div class="grid-row head" role="row"><span class="slots" aria-hidden="true"><span>AM</span><span>PM</span><span>EVE</span></span>
        ${d.days.map((x) => `<span class="grid-day ${x.past ? "past" : ""}" role="columnheader"><span class="d">${x.letter}</span><span class="n ${x.today ? "today" : ""}">${x.n}</span></span>`).join("")}</div>
      ${d.people.map((p) => `<div class="grid-row" role="row"><span class="who" role="rowheader"><span class="name ${p.you ? "you" : ""}">${esc(p.name)}</span>${p.by_hand ? `<span class="hand">by hand</span>` : ""}</span>
        ${p.cells.map((c) => `<span class="cell ${c.past ? "past" : ""}" role="cell">${c.segs.map(seg).join("")}</span>`).join("")}</div>`).join("")}
      <div class="legend" style="padding-top:8px;border-top:1px solid var(--hairline);font-size:12px"><span><i class="lg bg-easy"></i>Easy</span><span><i class="lg bg-long"></i>Long</span>
        <span><i class="lg hard"></i>Hard, runs alone</span><span><i class="lg bg-easy match"></i>Same time</span></div></div>
    <span class="meta" style="line-height:1.45;text-wrap:pretty">Runs fill in from each person's watch or plan. Yours follow your plan; <a href="#/plan">change them in Plan</a>.</span></section>`);
  const inv = d.invite || {};
  const value = inv.url || inv.code || "";
  parts.push(`<div class="card pad stack" style="gap:10px"><span class="strong">Invite a friend</span>
    <span class="meta" style="font-size:13.5px;line-height:1.45">${inv.url ? "They see days and times of easy and long runs. Never pace, heart rate or routes. No watch needed."
      : "They tell their Daash the crew's code to join. They see days and times of easy and long runs, never pace, heart rate or routes."}</span>
    <div style="display:flex;gap:8px;align-items:center"><span class="copy-field">${esc(inv.shown || inv.code || "")}</span>
      <button class="pill" id="copy" data-value="${esc(value)}" style="flex-shrink:0">${inv.url ? "Copy link" : "Copy code"}</button></div></div>`);
  return parts.join("");
}

async function showCrew() {
  tabs("crew");
  loading();
  try {
    const d = await api("crew");
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
    ${d.groups.map((g) => `<section class="stack-8"><span class="kicker" style="font-size:11.5px;letter-spacing:0.08em;padding-left:2px">${esc(g.title.toUpperCase())}</span>
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
        close(); token.clear(); store.drop("daash.client");
        signInView("Your Daash account is deleted.");
      } catch (e) { close(); toast(e.message); }
    };
  });
}

async function signOut() {
  const access = token.access, client = store.get("daash.client");
  token.clear();
  if (access && client) {
    // Revoked as well as forgotten, so the token can't be used again from anywhere.
    fetch("/revoke", { method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({ token: access, client_id: client }) }).catch(() => {});
  }
  signInView("Signed out.");
}

async function showYou() {
  tabs("you");
  loading();
  try {
    const d = await api("you");
    $("screen").innerHTML = youView(d);
    $("refresh").onclick = () => { toast("Checked Garmin again."); showYou(); };
    $("screen").querySelectorAll("[data-you]").forEach((b) => {
      b.onclick = () => (b.dataset.you === "times" ? timesSheet(d) : b.dataset.you === "delete" ? deleteSheet() : signOut());
    });
  } catch (e) {
    if (e instanceof SignedOut) return route();
    failed(e.message, showYou);
  }
}

function signInView(message) {
  $("tabs").hidden = true;
  $("screen").innerHTML = `<div class="signin">
    <div class="wordmark"><img src="/app/icons/favicon.svg" width="32" height="32" alt="">Daash</div>
    <h1 class="headline">Your plan, from Claude or ChatGPT, on your watch.</h1>
    <p class="why">See today's session, check how a run went, and move or ease a session. The changes reach your watch and the chat.</p>
    ${message ? `<div class="card pad amber meta" role="alert">${esc(message)}</div>` : ""}
    <button class="pill red tall" id="signin">Sign in</button>
    <p class="small">Already use Daash in Claude or ChatGPT? It's the same account, so your plan is waiting. New to Daash? Connect Garmin here and add Claude or ChatGPT later.</p>
    <p class="small">Daash is independent and not made or endorsed by Garmin.</p></div>`;
  $("signin").onclick = async () => {
    $("signin").disabled = true;
    try { await signIn(); } catch (e) { signInView(e.message); }
  };
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
  if (!token.access) return signInView();
  const [path, query] = (location.hash.replace(/^#\/?/, "") || "today").split("?");
  const params = new URLSearchParams(query || "");
  const [screen, arg] = path.split("/");
  window.scrollTo(0, 0);
  if (screen === "session" && /^\d{4}-\d{2}-\d{2}$/.test(arg || "")) return showSession(arg, params);
  if (screen === "run" && /^\d+$/.test(arg || "")) return showRun(arg, params);
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

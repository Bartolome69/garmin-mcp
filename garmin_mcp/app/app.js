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
  clear() { delete memory.access; delete memory.refresh; store.drop("daash.access"); store.drop("daash.refresh"); cache.clear(); },
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
  grip: () => svg('<circle cx="9" cy="6" r="1.4"/><circle cx="15" cy="6" r="1.4"/><circle cx="9" cy="12" r="1.4"/><circle cx="15" cy="12" r="1.4"/><circle cx="9" cy="18" r="1.4"/><circle cx="15" cy="18" r="1.4"/>', 14),
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
  track("signed_in", { method: "garmin" });
  return null;
}

// What only the page sees, for the app's analytics: never what a screen showed.
function track(name, properties = {}) {
  if (!token.access) return;
  fetch(API + "event", {
    method: "POST", keepalive: true,
    headers: { Authorization: `Bearer ${token.access}`, "Content-Type": "application/json", "X-Daash-Layout": desktop() ? "desktop" : "phone",
      ...(homeScreen() ? { "X-Daash-Home": "1" } : {}) },
    body: JSON.stringify({ name, properties }),
  }).catch(() => {});
}

// Ask Claude or ChatGPT, and Share this run, wherever they appear.
document.addEventListener("click", (e) => {
  const link = e.target.closest && e.target.closest("a[href]");
  // The Ask sheet's own links are counted as its choice (ask_chosen), not as another Ask.
  if (!link || link.closest(".sheet")) return;
  const where = (location.hash.replace(/^#\/?/, "").split(/[/?]/)[0]) || "today";
  if (/^https:\/\/(claude\.ai|chatgpt\.com)\//.test(link.href)) {
    track("ask_clicked", { to: link.href.includes("chatgpt") ? "chatgpt" : "claude", where,
      opens: link.dataset.app ? (store.get("daash.opens") || "app") : "web" });
  } else if (link.href.includes("/share/#")) {
    track("share_clicked", { where });
  }
}, true);
window.addEventListener("appinstalled", () => {
  track("installed");
  store.set(INSTALLED, "1");
  const card = document.getElementById("nudge");
  if (card) card.remove();
});

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
// The screen a pull to refresh is fetching afresh (below), until its request goes.
let pullFresh = null;

async function api(path, options = {}, retry = true) {
  const from = options.ahead ? null : cameFrom;
  if (from) cameFrom = null;
  // One request per pull goes past the server's memory: the pulled screen's own.
  const pulled = !!pullFresh && !options.ahead && path.split(/[/?]/)[0] === pullFresh;
  if (pulled) pullFresh = null;
  const res = await fetch(API + path, {
    ...options,
    headers: { Authorization: `Bearer ${token.access}`, "X-Daash-Layout": desktop() ? "desktop" : "phone",
      // Opened from the home screen rather than a browser tab (Nov 10: who keeps it).
      ...(homeScreen() ? { "X-Daash-Home": "1" } : {}),
      // Fetched ahead of a tap: not counted as a screen opened.
      ...(options.ahead ? { "X-Daash-Ahead": "1" } : {}),
      ...(from ? { "X-Daash-From": from } : {}),
      // Pulled down to refresh: Garmin's latest, past the server's memory.
      ...(pulled ? { "X-Daash-Fresh": "1" } : {}),
      ...(options.body ? { "Content-Type": "application/json" } : {}) },
  });
  if (res.status === 401) {
    if (retry && await refreshed()) return api(path, options, false);
    token.clear();
    throw new SignedOut();
  }
  const body = await res.json().catch(() => ({ error: "Daash didn't answer. Try again in a moment." }));
  if (!res.ok || body.error) throw new Error(body.error || "Something went wrong.");
  // A change on Garmin: what the screens showed of the plan is out of date.
  // (A read sent as a POST, like the plan builder's answers, changes nothing.)
  if (options.method === "POST" && !options.read) { epoch++; inflight.clear(); cache.clear(); }
  return body;
}

// -- snappy: every screen opens on its last copy, then catches up ---------------
// Each answer is kept, in memory and on the phone, and a screen draws from it
// at once while the fresh one is fetched; it redraws only if something
// changed. Asked twice at once (a prefetch, then a tap), one request serves
// both. An edit forgets what it could have changed.
const CACHE_PREFIX = "daash.c.";
const CACHE_MAX = 40;
const cache = {
  mem: new Map(),
  get(path) {
    if (this.mem.has(path)) return this.mem.get(path);
    const raw = store.get(CACHE_PREFIX + path);
    if (!raw) return null;
    try { const hit = JSON.parse(raw); this.mem.set(path, hit); return hit; } catch { return null; }
  },
  put(path, value) {
    this.mem.set(path, value);
    store.set(CACHE_PREFIX + path, JSON.stringify(value));
    const keys = (store.get(CACHE_PREFIX + "#") || "").split("\n").filter((k) => k && k !== path);
    keys.push(path);
    while (keys.length > CACHE_MAX) store.drop(CACHE_PREFIX + keys.shift());
    store.set(CACHE_PREFIX + "#", keys.join("\n"));
  },
  drop(test) {
    const keys = (store.get(CACHE_PREFIX + "#") || "").split("\n").filter(Boolean);
    const keep = keys.filter((k) => !test(k));
    keys.filter((k) => test(k)).forEach((k) => { store.drop(CACHE_PREFIX + k); this.mem.delete(k); });
    [...this.mem.keys()].filter(test).forEach((k) => this.mem.delete(k));
    store.set(CACHE_PREFIX + "#", keep.join("\n"));
  },
  clear() { this.drop(() => true); },
};
const inflight = new Map();
// Bumped by every edit, so an answer fetched before it isn't kept after it.
let epoch = 0;
function fresh(path, ahead = false) {
  const going = inflight.get(path);
  if (going) {
    // A tap that lands on a fetch started ahead of it (a finger on the row, the
    // tabs warmed): the server didn't count that fetch as a screen opened, so
    // the page says it was, once.
    if (!ahead && going.ahead && !going.seen) {
      going.seen = true;
      const screen = path.split(/[/?]/)[0];
      going.p.then(() => track("viewed", { screen, ok: true }), () => track("viewed", { screen, ok: false }));
    }
    return going.p;
  }
  const asked = epoch;
  const p = api(path, { ahead }).then((d) => { if (asked === epoch) cache.put(path, d); return d; })
    .finally(() => { if (inflight.get(path)?.p === p) inflight.delete(path); });
  inflight.set(path, { p, ahead });
  return p;
}
let showing = 0;
// Set by route(true), after an edit: the screen stays put while it catches up.
let calm = false;
// Draw a screen from its last copy, then from Garmin's answer if it differs.
// quiet: with no copy, keep what's on screen rather than the skeleton (after
// an edit, whose result is already drawn).
async function show(path, render, onFail, quiet = false) {
  const mine = ++showing;
  const hit = cache.get(path);
  if (hit) render(hit, true); else if (!quiet && !calm) loading();
  try {
    const d = await fresh(path);
    if (mine !== showing) return;
    if (hit && JSON.stringify(hit) === JSON.stringify(d)) return;
    // Not under a finger mid-drag: the redraw waits for the drop.
    if (dragging) return;
    const y = window.scrollY;
    render(d, false);
    if (hit) window.scrollTo(0, y);
  } catch (e) {
    if (e instanceof SignedOut) return route();
    if (mine !== showing) return;
    if (hit) toast("Couldn't reach Garmin just now. This is the last copy.");
    else onFail(e);
  }
}
// The other tabs, fetched once the first screen is up, so a tap is instant.
let warmed = false;
function warm() {
  if (warmed) return;
  warmed = true;
  const go = () => {
    ["today", "plan", "crew", "you"].forEach((p) => { if (!inflight.has(p) && !cache.get(p)) fresh(p, true).catch(() => {}); });
    // And the next few sessions on screen, the ones most likely tapped.
    [...new Set([...document.querySelectorAll('#screen a[href^="#/session/"]')].map((a) => apiPathFor(a.getAttribute("href"))))]
      .filter((p) => p && !cache.get(p)).slice(0, 3).forEach((p) => fresh(p, true).catch(() => {}));
  };
  (window.requestIdleCallback || ((f) => setTimeout(f, 600)))(go);
}
// A finger on a session or a run starts its fetch before the tap lands.
function apiPathFor(href) {
  const m = (href || "").match(/#\/(session|run)\/([^?]+)(?:\?(.*))?/);
  if (!m) return null;
  if (m[1] === "run") return `run/${encodeURIComponent(m[2])}`;
  const sid = new URLSearchParams(m[3] || "").get("sid");
  return `session/${m[2]}${sid ? `?schedule_id=${encodeURIComponent(sid)}` : ""}`;
}
document.addEventListener("pointerdown", (e) => {
  const a = e.target.closest && e.target.closest('a[href^="#/session/"], a[href^="#/run/"]');
  const path = a && apiPathFor(a.getAttribute("href"));
  if (path && !cache.get(path)) fresh(path, true).catch(() => {});
}, { passive: true });

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
  // No question to ask, no button: an empty link reopens the app on Today.
  if (!data.ask?.[app]) return { href: null, name, html: "" };
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

// Ask Claude or ChatGPT: back to the chat they already have, or a new one.
// Every Ask used to open a new chat, and people ended up with dozens of
// threads (Nov 6). Daash can't see anyone's chats, so Back to my chat copies
// the question and opens either the chat they saved here or their list of
// chats, to paste it in.
const ASK_LINK = /^https:\/\/(claude\.ai\/new|chatgpt\.com\/)\?q=/;
const CHAT_KEY = (to) => `daash.chat.${to}`;

// A chat's own address, not a share link (that opens a public copy).
function chatAddress(to, text) {
  let u;
  try { u = new URL(String(text || "").trim()); } catch { return { error: "That isn't a link. Paste the chat's address." }; }
  if (/\/share\//.test(u.pathname)) return { error: "That's a share link, which opens a public copy. Paste the chat's own address." };
  const ok = to === "claude"
    ? u.hostname === "claude.ai" && /^\/chat\/[\w-]{8,}$/.test(u.pathname)
    : /^(www\.)?chatgpt\.com$/.test(u.hostname) && /^(\/g\/[\w-]+)?\/c\/[\w-]{8,}$/.test(u.pathname);
  if (!ok) return { error: to === "claude" ? "That isn't a Claude chat. Its address starts claude.ai/chat/." : "That isn't a ChatGPT chat. Its address starts chatgpt.com/c/." };
  return { url: `https://${u.hostname}${u.pathname}` };
}

function copyQuestion(text, then) {
  const done = (ok) => toast(ok ? then : "Couldn't copy the question. Press and hold it above to copy it.");
  try { navigator.clipboard.writeText(text).then(() => done(true), () => done(false)); } catch { done(false); }
}

// Which address opens the assistant's phone app, not the browser. The Claude
// iPhone app claims only some claude.ai addresses (its
// apple-app-site-association, Nov 6): /new is one; /recents and /chat/… are
// not, so those open in Safari. On an iPhone, Back to my chat goes through
// /new: the app opens, and the person picks their chat from its menu.
const iPhone = () => /iPhone|iPad|iPod/i.test(navigator.userAgent)
  || (/Macintosh/.test(navigator.userAgent) && navigator.maxTouchPoints > 1);

function backTo(to, saved) {
  if (to === "claude" && !onComputer() && iPhone()) return { href: "https://claude.ai/new", inApp: true, saved: false };
  const href = saved || (to === "claude" ? "https://claude.ai/recents" : "https://chatgpt.com/");
  return { href, saved: !!saved };
}

function askSheet(link) {
  const to = link.href.includes("chatgpt") ? "chatgpt" : "claude";
  const name = to === "chatgpt" ? "ChatGPT" : "Claude";
  const question = new URL(link.href).searchParams.get("q") || "";
  const where = (location.hash.replace(/^#\/?/, "").split(/[/?]/)[0]) || "today";
  const saved = store.get(CHAT_KEY(to));
  const back = backTo(to, saved);
  // A saved chat can't open in the iPhone app, so it isn't offered there.
  const canKeep = !(to === "claude" && !onComputer() && iPhone());
  const chose = (choice) => track("ask_chosen", { to, where, choice, saved: back.saved ? "yes" : "no" });
  const how = back.inApp ? `Opens the Claude app with the question copied: pick your Daash chat from the menu and paste it.`
    : back.saved ? `Opens your Daash chat with the question copied: paste it there.`
    : `Copies the question and opens your ${name} chats: pick your Daash chat and paste it.`;
  // Real links, tapped: a phone hands a link to an app only on a tap, not
  // when a page opens a window itself.
  sheet(`<div class="stack-tight"><span class="strong" id="sheet-title">Ask ${name}</span>
      <span class="meta" style="line-height:1.5;user-select:text">“${esc(question)}”</span></div>
    <div class="stack-tight"><a class="pill red tall" id="ask-mine" href="${esc(back.href)}" target="_blank" rel="noopener">Back to my chat</a>
      <span class="small">${how}</span></div>
    <a class="pill tall" id="ask-new" href="${esc(link.href)}" target="_blank" rel="noopener">New chat</a>
    ${canKeep ? `<div class="stack-tight" id="ask-keep">${saved
      ? `<span class="small">Back to my chat opens <b>${esc(saved.replace(/^https:\/\//, "").slice(0, 34))}…</b> · <a href="#" id="ask-change">Change</a> · <a href="#" id="ask-forget">Forget</a></span>`
      : `<a href="#" class="small" id="ask-change">Use one chat every time</a>`}</div>` : ""}`, (holder, close) => {
    $("ask-mine").onclick = (e) => {
      chose("my_chat");
      copyQuestion(question, back.saved ? `Question copied. Paste it into your Daash chat.` : `Question copied. Open your Daash chat and paste it.`);
      // Claude's desktop app on a computer, as Ask Claude has always done.
      if (to === "claude" && onComputer()) { e.preventDefault(); openAssistant(back.href.replace(/^https:\/\//, "claude://"), back.href); }
      setTimeout(close, 0);
    };
    $("ask-new").onclick = (e) => {
      chose("new_chat");
      if (link.dataset.app && onComputer()) { e.preventDefault(); openAssistant(link.dataset.app, link.href); }
      setTimeout(close, 0);
    };
    const forget = $("ask-forget");
    if (forget) forget.onclick = (e) => { e.preventDefault(); store.drop(CHAT_KEY(to)); close(); toast("Forgotten. Back to my chat opens your list of chats."); };
    const change = $("ask-change");
    if (change) change.onclick = (e) => {
      e.preventDefault();
      $("ask-keep").innerHTML = `<label class="field-label" for="ask-url">Your Daash chat's address</label>
        <input id="ask-url" class="field" type="url" inputmode="url" autocomplete="off" autocapitalize="off" spellcheck="false"
          placeholder="${to === "claude" ? "https://claude.ai/chat/…" : "https://chatgpt.com/c/…"}">
        <span class="small">Open the chat in a browser and copy the address bar.</span>
        <div class="pills"><button class="pill" id="ask-cancel">Cancel</button><button class="pill light" id="ask-save">Save</button></div>`;
      const field = $("ask-url");
      field.focus();
      $("ask-cancel").onclick = () => { close(); askSheet(link); };
      $("ask-save").onclick = () => {
        const got = chatAddress(to, field.value);
        if (got.error) { toast(got.error); field.focus(); return; }
        store.set(CHAT_KEY(to), got.url);
        track("ask_chat_saved", { to, where });
        close(); toast("Saved. Back to my chat opens it."); askSheet(link);
      };
    };
  });
}

// Registered after the ask_clicked count above, so that still counts the tap.
document.addEventListener("click", (e) => {
  const link = e.target.closest && e.target.closest("a[href]");
  if (!link || link.closest(".sheet") || !ASK_LINK.test(link.href) || e.metaKey || e.ctrlKey || e.shiftKey || e.button > 0) return;
  e.preventDefault();
  e.stopPropagation();
  askSheet(link);
}, true);

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
  const p = { head: `<header class="stack-tight"><span class="kicker">${esc(d.kicker)}</span><h1 class="headline">${esc(d.headline)}</h1>${credit(d)}</header>` };
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
      <div class="pills next-pills"><a class="pill light" href="${open}">See the session</a>
        ${n.easier ? `<button class="pill" data-act="${n.easier.applied ? "undo" : "easier"}" data-day="${n.date}" data-sid="${n.schedule_id || ""}">${esc(n.easier.applied ? "Undo easier" : "Easier")}</button>` : ""}
        <button class="pill" data-act="move" data-day="${n.date}" data-sid="${n.schedule_id || ""}" data-name="${esc(n.name)}">Move</button></div>
    </section>`;
  } else if (!d.empty) {
    p.next = `<div class="card pad stack-tight"><span class="strong">Nothing else planned</span><span class="meta">The plan on your Garmin calendar has no more sessions.</span></div>`;
  }
  if (d.empty && d.build) {
    // The builder makes a plan here, with no chat; the assistant stays a way to do it too.
    const ask = askLink(d, `Or ask ${d.assistant === "ChatGPT" ? "ChatGPT" : "Claude"}`);
    p.next = `<div class="card pad stack"><span class="strong">No sessions on your Garmin calendar yet.</span>
      <span class="meta">Build a running plan here in a couple of minutes. It goes on your Garmin calendar and your watch.</span>
      <a class="pill light tall" href="#/build?from=today">Build a plan</a>${ask.html}</div>`;
  } else if (d.empty) {
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
  return [p.head, `<div id="race-slot" hidden></div>`, p.done, p.next, nudge(), p.crew, p.ready, p.week].filter(Boolean).join("");
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
  return `<div class="cal-top"><div class="stack-tight"><span class="kicker">${esc(d.kicker)}</span><h1 class="headline" style="font-size:28px">${esc(d.headline)}</h1>${credit(d)}</div>
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
    <aside class="desk-side">${[`<div id="race-slot" hidden></div>`, p.done, d.empty ? "" : p.next, p.ready, p.crew, p.verdict].filter(Boolean).join("")}</aside></div>`;
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
      const r = await api(`session/${moving.date}/move`, { method: "POST", body: JSON.stringify({ to: day.dataset.date, schedule_id: Number(moving.sid) || null, via: "drag" }) });
      store.set(DRAGGED, "1");
      toast(r.message);
      showToday();
    } catch (err) {
      if (err instanceof SignedOut) return route();
      toast(err.message);
    }
  });
}

let base = null;  // the desktop screen under the side panel

// The server's day, which Today's answer is for (Fly's clock is UTC).
const serverDay = () => new Date().toISOString().slice(0, 10);

async function showToday(quiet = false) {
  tabs("today");
  return show("today", (d, saved) => {
    remember(d);
    if (desktop()) {
      $("screen").innerHTML = todayDesk(d);
      dragToMove($("cal"));
    } else {
      $("screen").innerHTML = todayView(d);
      wireNudge($("screen"));
    }
    // Yesterday's copy, drawn while today's is fetched: said so, and dimmed,
    // rather than passed off as today (Nov 13).
    const old = saved && d.today && d.today !== serverDay();
    $("screen").classList.toggle("stale", !!old);
    if (old) {
      // Its date line is yesterday's: say what's happening in its place.
      const line = $("screen").querySelector(".kicker");
      if (line) { line.textContent = "Updating to today…"; line.classList.add("updating"); line.setAttribute("role", "status"); }
    }
    base = "today";
    wire($("screen"));
    racePrompt();
    warm();
  }, (e) => failed(e.message, showToday), quiet || base === "today");
}

// -- a new race on the Garmin calendar: a card offering a plan for it (preview) --

async function racePrompt() {
  const slot = $("race-slot");
  if (!slot) return;
  // The last answer at once, then the fresh one: no flash of nothing on every visit.
  const draw = (r) => {
    const here = $("race-slot");
    if (!here) return;
    if (!r || !r.key) { here.hidden = true; here.innerHTML = ""; return; }
    here.hidden = false;
    here.innerHTML = r.mode === "row"
      ? `<a class="race-row" href="#/build?race=${esc(r.key)}&from=race" data-race="cta"><i></i><span class="stack-tight" style="gap:1px;min-width:0"><span class="race-row-t">${esc(r.name)} · ${esc(r.when)}</span><span class="meta" style="font-size:12.5px">${esc(r.row_sub)}</span></span><span class="race-row-cta">${esc(r.cta)}</span></a>`
      : `<div class="card race-card"><div class="race-kick"><i></i><span>NEW ON YOUR GARMIN CALENDAR</span></div>
          <div class="stack-tight" style="gap:6px"><span class="race-title">${esc(r.title)}</span><span class="race-sub">${esc(r.sub)}</span></div>
          <div class="race-facts">${r.facts.map((f) => `<div><span class="race-fact-v">${esc(f.v)}</span><span class="meta" style="font-size:12px">${esc(f.l)}</span></div>`).join("")}</div>
          <div class="pills"><a class="pill tall light" style="flex:1.4" href="#/build?race=${esc(r.key)}&from=race" data-race="cta">${esc(r.cta)}</a><button type="button" class="pill tall" data-race="snooze">Not now</button></div>
          <button type="button" class="race-not" data-race="not_goal">It's not a goal race</button></div>`;
    here.querySelectorAll("[data-race]").forEach((el) => { el.onclick = (e) => raceAction(r, el.dataset.race, e); });
  };
  const hit = cache.get("race-prompt");
  if (hit) draw(hit);
  try {
    const r = await fresh("race-prompt");
    if (!hit || JSON.stringify(hit) !== JSON.stringify(r)) draw(r);
  } catch (e) {
    if (!(e instanceof SignedOut) && !hit) draw(null);
  }
}

async function raceAction(r, action, e) {
  if (action !== "cta") e.preventDefault();
  const said = api("race-prompt", { method: "POST", body: JSON.stringify({ key: r.key, action, case: r.case }), read: true });
  if (action === "cta") { said.catch(() => {}); return; }
  try {
    const d = await said;
    cache.drop((k) => k === "race-prompt");
    toast(d.message);
    racePrompt();
  } catch (err) {
    if (err instanceof SignedOut) return route();
    toast(err.message);
  }
}

// -- the session: preview before the run --------------------------------------

// Back from a session or run: the tab it was opened from, and in Plan the
// week that was showing.
function backHref(from) {
  if (from !== "plan") return "#/today";
  const week = new URLSearchParams(location.hash.split("?")[1] || "").get("week");
  return week ? `#/plan?week=${encodeURIComponent(week)}` : "#/plan";
}

// Garmin's credit, "Garmin [device model]", under each screen's title: Garmin's
// API brand guidelines ask for it by the title of every view of its data (Nov 10).
function credit(d) {
  return `<span class="credit">${esc((d && d.credit) || "Garmin")}</span>`;
}

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
  const parts = [panel ? "" : `<a class="back" href="${backHref(from)}">${ICON.left()}${back}</a>`,
    `<header class="stack-8">${panel ? "" : `<span class="kicker ${d.today ? "red" : ""}">${esc(d.kicker)}</span>`}<h1 class="title-xl">${esc(d.name)}</h1>${credit(d)}
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
  parts.push(editHint());
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
  return `<a class="back" href="${backHref(from)}">${ICON.left()}${from === "plan" ? "Plan" : "Today"}</a>
    <header class="stack-8"><span class="kicker">${esc(d.kicker)}</span><h1 class="title-xl">Rest day</h1></header>
    ${d.next ? `<a class="row-button" href="#/session/${d.next.date}?from=${from || "today"}"><span class="grow"><span class="meta">Next</span><span class="strong">${esc(d.next.name)}</span></span>${ICON.right(18, "var(--muted)")}</a>` : ""}`;
}

async function showSession(day, params) {
  tabs(params.get("from") === "plan" ? "plan" : "today");
  const sid = params.get("sid");
  return show(`session/${day}${sid ? `?schedule_id=${encodeURIComponent(sid)}` : ""}`, (d) => {
    if (d.done && d.activity_id) {
      location.replace(`#/run/${d.activity_id}?from=${params.get("from") || "today"}${params.get("week") ? `&week=${encodeURIComponent(params.get("week"))}` : ""}`);
      return;
    }
    $("screen").innerHTML = d.rest ? restView(d, params.get("from")) : previewView(d, params.get("from"));
    if (!d.rest) store.set(LOOKED, "1");
    wireHint($("screen"));
    wire($("screen"));
  }, (e) => failed(e.message, () => showSession(day, params)));
}

// -- the session: analysis after the run --------------------------------------

function analysisView(d, from, panel = false) {
  const back = from === "plan" ? "Plan" : "Today";
  const ask = askLink(d, `Ask ${d.assistant === "ChatGPT" ? "ChatGPT" : "Claude"} about it`);
  if (panel) return analysisPanel(d, ask);
  const parts = [`<a class="back" href="${backHref(from)}">${ICON.left()}${back}</a>`,
    `<header class="stack-8"><span class="kicker">${esc(d.kicker)}</span><h1 class="title-xl">${esc(d.name)}</h1>${credit(d)}</header>`,
    `<p class="verdict">${esc(d.verdict)}</p>`,
    `<div class="stats4">${d.stats.map((s) => `<div class="stack-tight" style="gap:2px;min-width:0"><span class="v">${esc(s.v)}</span><span class="l">${esc(s.l)}</span></div>`).join("")}</div>`];
  const chart = runChart(d.chart, d.colour);
  if (chart && !d.planned_and_run) parts.push(`<section class="stack"><span class="section-title">Km by km</span>${chart}</section>`);
  if (d.planned_and_run) {
    parts.push(`<section class="stack"><span class="section-title">Planned and run</span>${chart}<div class="card table">
      <div class="cmp head"><span>STEP</span><span>TARGET</span><span style="text-align:right">PACE</span><span style="text-align:right">HR</span></div>
      ${d.planned_and_run.map((c) => `<div class="cmp"><span class="lab"><span class="bar bg-${esc(c.colour)}"></span>
        <span class="stack-tight" style="gap:1px;min-width:0"><span style="font-size:14.5px;font-weight:600">${esc(c.label)}</span><span class="meta ${c.soft ? "soft" : ""}" style="font-size:12.5px">${esc(c.note)}${spark(c)}</span></span></span>
        <span class="tgt">${esc(c.target || "–")}</span><span class="num ${c.soft && c.miss !== "high" ? "soft" : ""}">${esc(c.pace || "–")}</span><span class="num ${c.miss === "high" ? "soft" : ""}">${esc(c.hr || "–")}</span></div>`).join("")}
    </div></section>`);
  }
  const told = insightsView(d.insights);
  if (told) parts.push(told);
  if (d.splits) {
    parts.push(`<div class="card table"><button class="splits-toggle" aria-expanded="false" aria-controls="splits" id="splits-toggle">
      <span class="strong">Splits<span style="font-weight:500;color:var(--muted)"> · ${esc(d.km)} km</span></span>
      <span class="meta" style="display:flex;align-items:center;gap:6px;font-size:13.5px"><span id="splits-word">Show</span>${ICON.down()}</span></button>
      <div id="splits" hidden><div class="splits-row head"><span>KM</span><span>PACE</span><span>HR</span></div>
      ${d.splits.map((s) => `<div class="splits-row"><span class="meta" style="font-size:14px">${esc(s.rest ? "Jogs" : s.km)}</span><span style="font-weight:600">${esc(s.rest ? s.pace : s.partial ? `${s.time} (${s.pace} /km)` : s.pace)}</span><span class="meta" style="font-size:14px">${esc(s.hr || "–")}</span></div>`).join("")}
      </div></div>`);
  }
  if (d.easy_check) parts.push(easyCheck(d.easy_check));
  if (d.effect) {
    parts.push(`<div class="card effect"><span class="tick bg-${esc(d.colour)}">${ICON.check()}</span>
      <span class="stack-tight" style="gap:2px"><span class="strong">${esc(d.effect.head)}</span><span class="meta" style="font-size:13.5px;line-height:1.45">${esc(d.effect.sub)}</span></span></div>`);
  }
  parts.push(`<div class="actions2">${d.share_url ? `<a class="pill tall" href="${esc(d.share_url)}" target="_blank" rel="noopener">${ICON.share()}Share this run</a>` : `<span></span>`}${ask.html}</div>`);
  return parts.join("");
}

// After a run (Nov 11): the session as planned with what was run inside it,
// and a few lines on what it shows. Worked out on the server (run_view.py);
// this only draws them.
const clockOf = (s) => `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, "0")}`;

function runChart(c, colour) {
  if (!c || !c.parts || c.parts.length < 2) return "";
  const W = 340;
  const tone = (k) => k === "work" || k === "km" ? `var(--${colour || "easy"})` : k === "stride" ? "var(--hard)" : "var(--rec)";
  const parts = c.parts;
  let body = "";
  let key = "";
  if (c.mode === "steps") {
    const gap = 2, total = parts.reduce((a, p) => a + p.s, 0);
    const room = W - gap * (parts.length - 1);
    const band = c.band && c.band.kind === "pace" ? c.band : null;
    // The target band is drawn tall enough to see where each rep landed in it;
    // the rest of the session sits below it, quicker taller.
    const bandTop = 34, bandBot = 78, floor = 150;
    const others = parts.filter((p) => p.k !== "work" && p.k !== "stride" && p.p);
    const slowest = Math.max(...others.map((p) => p.p), (band ? band.hi : 0) + 60);
    const work = parts.filter((p) => p.k === "work" && p.p);
    const quickest = Math.min(...work.map((p) => p.p), band ? band.lo : 9999);
    const yWork = (p) => band ? Math.max(14, Math.min(floor - 12, bandTop + (p - band.lo) / Math.max(band.hi - band.lo, 1) * (bandBot - bandTop)))
      : 24 + (p - quickest) / Math.max(slowest - quickest, 1) * (floor - 40);
    const yOther = (p) => {
      const top = band ? bandBot + 8 : 40;
      const lo = band ? band.hi : quickest;
      return Math.min(floor - 8, top + Math.max(0, p - lo) / Math.max(slowest - lo, 1) * (floor - 8 - top));
    };
    let x = 0;
    parts.forEach((p) => {
      const w = Math.max(3, p.s / total * room);
      const mid = x + w / 2;
      if (p.k === "work") {
        const y = p.p ? yWork(p.p) : floor - 10;
        if (band) {
          body += `<rect x="${x.toFixed(1)}" y="${bandTop}" width="${w.toFixed(1)}" height="${floor - bandTop}" rx="3" fill="none" stroke="var(--button)" stroke-dasharray="3 3"/>`;
          body += `<rect x="${x.toFixed(1)}" y="${bandTop}" width="${w.toFixed(1)}" height="${bandBot - bandTop}" fill="var(--band-${colour === "hard" ? "hard" : "easy"})"/>`;
        }
        body += `<rect x="${(x + Math.min(5, w / 6)).toFixed(1)}" y="${y.toFixed(1)}" width="${Math.max(2, w - 2 * Math.min(5, w / 6)).toFixed(1)}" height="${(floor - y).toFixed(1)}" rx="3" fill="${tone("work")}" ${p.on === false ? 'opacity="0.55"' : ""}/>`;
        if (w >= 26 && p.p) body += `<text class="v" x="${mid.toFixed(1)}" y="${((band ? bandTop : y) - 6).toFixed(1)}" text-anchor="middle">${clockOf(p.p)}</text>`;
        if (w >= 40 && p.hr) body += `<text x="${mid.toFixed(1)}" y="${floor + 16}" text-anchor="middle">${p.hr} bpm</text>`;
      } else if (p.k === "stride") {
        const y = 20 + Math.max(0, (p.p - 170)) / 120 * 80;
        body += `<rect x="${(x + 0.5).toFixed(1)}" y="${y.toFixed(1)}" width="${Math.max(2, w - 1).toFixed(1)}" height="${(floor - y).toFixed(1)}" rx="1.5" fill="${tone("stride")}"/>`;
      } else {
        const y = p.p ? yOther(p.p) : floor - 8;
        body += `<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${w.toFixed(1)}" height="${(floor - y).toFixed(1)}" rx="2" fill="${tone("rec")}"/>`;
        if (p.label && w >= 56) body += `<text x="${(x + 2).toFixed(1)}" y="${floor + 16}">${esc(p.label)}</text>`;
      }
      x += w + gap;
    });
    body = `<svg viewBox="0 -4 ${W} ${floor + 26}" role="img" aria-label="${esc(c.caption || "The session as planned and as run")}">${body}</svg>`;
    key = band ? `<span><i style="border:1px dashed var(--button)"></i>Planned</span><span><i style="background:var(--band-${colour === "hard" ? "hard" : "easy"})"></i>Target ${esc(band.text)}</span><span><i style="background:${tone("work")}"></i>Your rep</span>`
      : `<span><i style="background:${tone("work")}"></i>Reps</span><span><i style="background:var(--rec)"></i>Easy and recoveries</span>`;
  } else {
    const kms = parts.filter((p) => p.k === "km"), strides = parts.filter((p) => p.k === "stride");
    const strideW = strides.length ? Math.min(72, strides.length * 12) : 0;
    const roomKm = W - (strideW ? strideW + 10 : 0);
    const kmW = (roomKm - 3 * (kms.length - 1)) / kms.length;
    const quick = Math.min(...kms.map((k) => k.p)), slow = Math.max(...kms.map((k) => k.p));
    const lo = quick - 20, hi = slow + 30;
    const top = 26, bot = 110;
    const yKm = (p) => bot - (hi - p) / (hi - lo) * (bot - top);
    kms.forEach((k, i) => {
      const x = i * (kmW + 3), y = yKm(k.p);
      body += `<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${kmW.toFixed(1)}" height="${(bot - y).toFixed(1)}" rx="3" fill="${tone("km")}"/>`;
      if (kmW >= 24) body += `<text class="v" x="${(x + kmW / 2).toFixed(1)}" y="${(y - 5).toFixed(1)}" text-anchor="middle" style="font-size:9.5px">${clockOf(k.p)}</text>`;
    });
    const sx = roomKm + 10;
    strides.forEach((p, j) => {
      const sw = strideW / strides.length, x = sx + j * sw;
      const y = 18 + Math.max(0, p.p - 170) / 150 * 80;
      body += `<rect x="${(x + 1).toFixed(1)}" y="${y.toFixed(1)}" width="${Math.max(2, sw - 4).toFixed(1)}" height="${(bot - y).toFixed(1)}" rx="1.5" fill="${tone("stride")}"/>`;
    });
    // Heart rate straight under the bars, each km's point under its own bar
    // with a faint guide down to it, and the km named once, under both (Nov 12).
    const hrs = kms.filter((k) => k.hr);
    let labelY = 124, height = 130;
    if (hrs.length >= 2) {
      const band = c.band && c.band.kind === "hr" ? c.band : null;
      const vals = hrs.map((k) => k.hr).concat(band ? [band.lo, band.hi] : []);
      const hLo = Math.min(...vals) - 6, hHi = Math.max(...vals) + 4, hTop = bot + 20, hBot = bot + 64;
      const yHr = (h) => hBot - (h - hLo) / (hHi - hLo) * (hBot - hTop);
      const midOf = (i) => i * (kmW + 3) + kmW / 2;
      const peak = Math.max(...hrs.map((k) => k.hr));
      const named = (k, i) => i === 0 || i === kms.length - 1 || (k.hr === peak && hrs.findIndex((h) => h.hr === peak) === hrs.indexOf(k));
      kms.forEach((k, i) => {
        if (!k.hr) return;
        const end = yHr(k.hr) - (named(k, i) ? 17 : 3);
        if (end > bot + 4) body += `<line class="guide" x1="${midOf(i).toFixed(1)}" y1="${(bot + 3).toFixed(1)}" x2="${midOf(i).toFixed(1)}" y2="${end.toFixed(1)}" stroke="var(--hairline)" stroke-width="1" stroke-dasharray="2 3"/>`;
      });
      if (band) body += `<rect x="0" y="${yHr(band.hi).toFixed(1)}" width="${roomKm.toFixed(1)}" height="${(yHr(band.lo) - yHr(band.hi)).toFixed(1)}" rx="4" fill="var(--band-easy)" stroke="var(--band-easy-line)" stroke-width="0.8"/>`;
      const pts = kms.map((k, i) => k.hr ? `${midOf(i).toFixed(1)},${yHr(k.hr).toFixed(1)}` : "").filter(Boolean).join(" ");
      body += `<polyline points="${pts}" fill="none" stroke="var(--red-text)" stroke-width="1.8" stroke-linejoin="round" stroke-linecap="round"/>`;
      kms.forEach((k, i) => {
        if (k.hr && named(k, i)) body += `<text x="${midOf(i).toFixed(1)}" y="${(yHr(k.hr) - 5).toFixed(1)}" text-anchor="middle" style="fill:var(--red-text)">${k.hr}</text>`;
      });
      labelY = hBot + 16;
      height = labelY + 4;
      key = `<span><i style="background:${tone("km")}"></i>Km pace</span>${strides.length ? `<span><i style="background:var(--hard)"></i>Stride</span>` : ""}<span><i style="background:var(--red-text);height:2px"></i>Heart rate</span>${band ? `<span><i style="background:var(--band-easy)"></i>Target ${esc(band.text)}</span>` : ""}`;
    }
    body += `<text x="0" y="${labelY}">Km ${esc(kms[0].label)}</text><text x="${(roomKm).toFixed(1)}" y="${labelY}" text-anchor="end">Km ${esc(kms[kms.length - 1].label)}</text>`;
    if (strides.length) body += `<text x="${(sx + strideW / 2).toFixed(1)}" y="${labelY}" text-anchor="middle">${strides.length} strides</text>`;
    body = `<svg viewBox="0 0 ${W} ${height}" role="img" aria-label="${esc(c.caption || "Each km's pace and heart rate")}">${body}</svg>`;
  }
  return `<div class="card run-chart">${body}${key ? `<div class="chart-key">${key}</div>` : ""}${c.caption ? `<span class="chart-caption">${esc(c.caption)}</span>` : ""}</div>`;
}

function insightsView(list) {
  if (!list || !list.length) return "";
  return `<section class="stack"><span class="section-title">What it shows</span><div class="card insights">${list.map((x) =>
    `<div class="insight"><span class="ic" style="background:var(--${esc(x.tone)})" aria-hidden="true">${esc(x.icon)}</span><span><b>${esc(x.head)}</b> ${esc(x.text)}</span></div>`).join("")}</div></section>`;
}

// Strides as one row (Nov 11): each one's pace as a little bar, quicker taller.
function spark(c) {
  if (!c.spark || c.spark.length < 2) return "";
  const lo = Math.min(...c.spark), hi = Math.max(...c.spark), span = Math.max(hi - lo, 1);
  return `<span class="spark" aria-hidden="true">${c.spark.map((p) => `<i class="bg-${esc(c.colour)}" style="height:${Math.round(35 + 65 * (hi - p) / span)}%"></i>`).join("")}</span>`;
}

// An easy session run harder than its target (Nov 9).
function easyCheck(c) {
  return `<div class="card amber" style="padding:14px"><span class="stack-tight" style="gap:2px"><span class="strong">${esc(c.head)}</span>
    <span class="meta" style="font-size:13.5px;line-height:1.45">${esc(c.sub)}</span></span></div>`;
}

// The panel's analysis: planned and run beside the splits, the splits open.
function analysisPanel(d, ask) {
  const rows = (d.planned_and_run || []).map((c) => `<div class="pcmp"><span class="bar bg-${esc(c.colour)}"></span>
      <span class="stack-tight" style="gap:1px;min-width:0"><span style="font-size:14px;font-weight:600">${esc(c.label)}</span>
        <span class="meta ${c.soft ? "soft" : ""}" style="font-size:12px">${esc(c.soft ? c.note : [c.note, c.target].filter(Boolean).join(" · "))}${spark(c)}</span></span>
      <span class="stack-tight" style="gap:1px;align-items:flex-end"><span class="${c.soft && c.miss !== "high" ? "soft" : ""}" style="font-size:14px;font-weight:600">${esc(c.pace || "–")}</span>
        <span class="meta ${c.miss === "high" ? "soft" : ""}" style="font-size:12px">${c.hr ? `${esc(c.hr)} bpm` : ""}</span></span></div>`).join("");
  const splits = (d.splits || []).map((s) => `<div class="psplit"><span class="meta">${esc(s.rest ? "Jogs" : s.km)}</span>
      <span style="font-weight:600;white-space:nowrap">${esc(s.rest ? s.pace.replace(" for ", " · ") : s.partial ? `${s.time} (${s.pace})` : s.pace)}</span><span class="meta" style="text-align:right">${esc(s.hr || "–")}</span></div>`).join("");
  return `<h2 class="title-xl" style="font-size:32px">${esc(d.name)}</h2>${credit(d)}
    <p class="verdict" style="font-size:19px">${esc(d.verdict)}</p>
    <div class="stats4">${d.stats.map((s) => `<div class="stack-tight" style="gap:2px;min-width:0"><span class="v">${esc(s.v)}</span><span class="l">${esc(s.l)}</span></div>`).join("")}</div>
    ${runChart(d.chart, d.colour)}
    ${insightsView(d.insights)}
    <div class="panel-two">${rows ? `<section class="stack-8"><span class="strong">Planned and run</span><div class="card table">${rows}</div></section>` : ""}
      ${splits ? `<section class="stack-8"><span class="strong">Splits<span style="font-weight:500;color:var(--muted)"> · ${esc(d.km)} km</span></span>
        <div class="card table"><div class="psplit head"><span>KM</span><span>PACE</span><span style="text-align:right">HR</span></div>${splits}</div></section>` : ""}</div>
    ${d.easy_check ? easyCheck(d.easy_check) : ""}
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
  const sid = params.get("sid");
  const path = kind === "session" ? `session/${id}${sid ? `?schedule_id=${encodeURIComponent(sid)}` : ""}` : `run/${encodeURIComponent(id)}`;
  const draw = (d) => {
    if (kind === "session" && d.done && d.activity_id) { location.replace(`#/run/${d.activity_id}?from=${params.get("from") || "today"}${params.get("week") ? `&week=${encodeURIComponent(params.get("week"))}` : ""}`); return; }
    if (!$("panel-body")) return;
    $("panel-kicker").textContent = d.kicker || "";
    $("panel-kicker").classList.toggle("red", !!d.today);
    $("panel-body").innerHTML = kind === "run" ? analysisView(d, "today", true) : d.rest ? restView(d, "today").replace(/<a class="back"[^]*?<\/a>/, "") : previewView(d, "today", true);
    wire($("panel-body"));
  };
  const hit = cache.get(path);
  if (hit) { draw(hit); $("panel-close").focus(); }
  try {
    const d = await fresh(path);
    if (!hit || JSON.stringify(hit) !== JSON.stringify(d)) draw(d);
    if (!hit) $("panel-close").focus();
  } catch (e) {
    if (e instanceof SignedOut) return route();
    if (!hit && $("panel-body")) $("panel-body").innerHTML = `<div class="empty"><span class="strong">${esc(e.message)}</span></div>`;
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
  location.hash = base === "plan" ? backHref("plan") : "#/today";
}

document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("panel").hidden) closePanel(); });

async function showRun(id, params) {
  tabs(params.get("from") === "plan" ? "plan" : "today");
  return show(`run/${encodeURIComponent(id)}`, (d) => {
    $("screen").innerHTML = analysisView(d, params.get("from"));
    store.set(LOOKED, "1");
    const t = $("splits-toggle");
    if (t) t.onclick = () => {
      const open = t.getAttribute("aria-expanded") !== "true";
      t.setAttribute("aria-expanded", String(open));
      $("splits").hidden = !open;
      $("splits-word").textContent = open ? "Hide" : "Show";
    };
  }, (e) => failed(e.message, () => showRun(id, params)));
}

// -- edits: the same writes Claude makes --------------------------------------

function wire(root) {
  root.querySelectorAll("[data-act]").forEach((b) => {
    b.onclick = () => act(b.dataset.act, b.dataset.day, b.dataset.sid, b);
  });
}

// Plans change: once, above Move and Easier, say what they do. Gone once
// either is used or it's dismissed (Nov 11: 4 of 25 app users had changed a session).
function editHint() {
  if (desktop() || store.get(EDIT_HINT)) return "";
  shownOnce("edit_hint_shown");
  return `<div class="card pad stack-tight" id="edit-hint" style="gap:8px"><span class="strong">Plans change. Yours can too.</span>
    <span class="meta" style="font-size:13.5px;line-height:1.45"><b>Move</b> it to another day or make it <b>Easier</b>. Your watch updates.</span>
    <div class="pills"><button class="pill" data-hint="done">Got it</button></div></div>`;
}

function wireHint(root) {
  const b = root.querySelector('[data-hint="done"]');
  if (b) b.onclick = () => { store.set(EDIT_HINT, "dismissed"); track("edit_hint_dismissed"); const h = $("edit-hint"); if (h) h.remove(); };
}

async function act(action, day, sid, button) {
  if (action === "move" || action === "easier") store.set(EDIT_HINT, "used");
  if (action === "move") return moveSheet(day, sid, button.dataset.name);
  button.disabled = true;
  try {
    const r = await api(`session/${day}/${action}`, { method: "POST", body: JSON.stringify({ schedule_id: sid ? Number(sid) : null }) });
    toast(r.message);
    refreshUnderPanel();
    route(true);
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

function planRow(r, day, first, week) {
  // A run not on the plan opens as a run; it has no session of its own to open.
  const wk = week ? `&week=${encodeURIComponent(week)}` : "";
  const href = (r.state === "done" || r.state === "extra") && r.activity_id ? `#/run/${r.activity_id}?from=plan${wk}`
    : r.state === "extra" ? `#/plan` : `#/session/${day.date}?sid=${r.schedule_id}&from=plan${wk}`;
  const mark = r.state === "done" || r.state === "extra" ? `<span class="tick bg-${esc(r.colour)}" style="width:15px;height:15px">${ICON.check(9)}</span>`
    : r.state === "next" ? `<span class="ring" style="width:14px;height:14px"></span>`
    : r.missed ? `<span class="miss-ring" style="width:12px;height:12px"></span>` : "";
  return `<a class="plan-row ${first ? "" : "same-day"} ${r.state === "next" ? "is-next" : ""}" href="${href}" data-date="${day.date}" draggable="false"
      data-sid="${r.schedule_id || ""}" ${r.movable ? 'data-movable="1"' : ""} data-name="${esc(r.name)}">
    <span class="day ${first ? "" : "hide"}"><span class="d">${esc(day.dow)}</span><span class="n ${day.today ? "today" : ""}">${day.n}</span></span>
    <span class="body"><span class="type c-${esc(r.colour)}">${mark}${esc(r.type_label)}</span>
      <span class="name ${r.state === "done" || r.state === "extra" ? "dim" : ""}">${esc(r.name)}</span>
      ${r.meta ? `<span class="meta ${r.state === "next" ? "red" : r.missed ? "soft" : ""}">${esc(r.meta)}</span>` : ""}
      ${r.segments ? `<span class="mini-shape">${r.segments.map((g) => `<i class="bg-${esc(g.colour)}" style="flex-grow:${g.w};height:${Math.max(3, Math.round(g.h * 0.45))}px"></i>`).join("")}</span>` : ""}</span>
    <span class="val ${r.state === "done" || r.state === "extra" ? "dim" : ""}">${esc(r.v)}${r.u ? `<span class="u"> ${esc(r.u)}</span>` : ""}</span>
    <span class="grip" aria-hidden="true">${r.movable ? ICON.grip() : ""}</span></a>`;
}

// Until someone has dragged a session once, the week says it can be done (Nov 11).
const DRAGGED = "daash.dragged";
function dragHint(w) {
  return !store.get(DRAGGED) && w.days.some((day) => day.sessions.some((r) => r.movable));
}

function weekView(d) {
  const w = d.week;
  const ask = askLink({ ...d, ask: w.ask });
  const askNext = askLink({ ...d, ask: w.ask_next }, "Plan next week");
  const rows = w.days.map((day) => day.sessions.length
    ? day.sessions.map((r, i) => planRow(r, day, i === 0, w.current ? null : w.days[0].date)).join("")
    : `<div class="plan-row rest" data-date="${day.date}"><span class="day"><span class="d">${esc(day.dow)}</span><span class="n ${day.today ? "today" : ""}">${day.n}</span></span>
        <span class="body"><span class="type c-rec">Rest</span><span class="name dim">Rest day</span></span><span></span></div>`).join("");
  const nav = (to, label, icon) => to ? `<a class="pill icon" href="#/plan?week=${to}" aria-label="${label}">${icon}</a>` : `<span class="pill icon" aria-hidden="true" style="visibility:hidden">${icon}</span>`;
  return `<div class="week-head"><div class="stack-tight" style="gap:2px;min-width:0"><span class="section-title">${esc(w.title)}</span>
      <span class="meta">${w.planned ? `<strong style="color:var(--ink);font-weight:600">${esc(w.done ?? 0)}</strong> of ${esc(w.planned)} km · ` : ""}${esc(w.range)}</span></div>
      <div class="pills" style="flex:0 0 auto">${nav(w.previous, "Week before", ICON.left())}${nav(w.next, "Week after", ICON.right())}</div></div>
    ${dragHint(w) ? `<div class="drag-hint">${ICON.grip()}<span>Hold a session, then drag it to another day.</span></div>` : ""}
    <div class="card plan-list" id="plan-list">${rows}</div>
    <div class="week-foot"><span class="meta" style="flex:1;font-size:13.5px;line-height:1.4">Change anything here and the watch follows.${dragHint(w) ? "" : " Hold a row to move it."}</span>${ask.html.replace("pill red tall", "pill red")}</div>
    ${w.current ? `<div style="display:flex">${askNext.html.replace("pill red tall", "pill")}</div>` : ""}
    ${d.manage && !desktop() ? `<a class="link-plain" href="#/plan/block" style="align-self:flex-start">Change or remove this plan</a>` : ""}`;
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
  // The phases sit on the bars' own columns, each spanning its weeks, so a
  // phase lines up with its bars (Nov 11: Base ran a bar past its last week).
  const phases = b.phases ? `<div class="phases" style="grid-template-columns:${cols}">
      ${b.phases.map((p) => p.label
        ? `<span class="${p.current ? "now" : ""}" style="grid-column:span ${p.weeks}" title="${esc(p.label)}" data-label="${esc(p.label.toUpperCase())}">${esc(p.label.toUpperCase())}</span>`
        : `<span class="gap" style="grid-column:span ${p.weeks}"></span>`).join("")}</div>` : "";
  // The block's own ask: the screen has none at its top (Nov 6: with none,
  // the link was empty and opened the app again, on Today).
  const ask = askLink({ ...d, ask: b.ask }, "Edit the plan");
  const manage = d.manage ? `<div class="card plan-manage"><a class="pill tall light" href="#/build?replace=1&from=change">Change this plan</a>
      <div class="plan-manage-row"><a class="link-plain" href="${esc(ask.href)}" target="_blank" rel="noopener">Ask ${esc(ask.name)} to change it</a>
      <button type="button" class="link-plain red" data-remove-plan>Remove this plan</button></div></div>` : "";
  return `<div class="card block-card"><div class="section-head"><span class="section-title">${esc(b.title)}</span><span class="meta">km per week</span></div>
      <div class="bars" style="grid-template-columns:${cols}" role="img" aria-label="Planned and run distance for each week of the block">${bars}</div>
      <div class="bar-nums" style="grid-template-columns:${cols}">${b.bars.map((x) => `<span class="${x.current ? "red" : ""}">${x.show_n ? x.n : ""}</span>`).join("")}</div>
      ${phases}
      <div class="legend"><span><i style="background:var(--muted)"></i>Run</span><span><i style="background:var(--red)"></i>This week</span><span><i class="planned"></i>Planned</span></div>
      <span class="block-verdict">${esc(b.verdict)}</span></div>
    <div class="week-foot"><div class="stack-tight" style="gap:2px;flex:1;min-width:0"><span class="strong">${esc(b.name)}</span><span class="meta">${esc(b.dates)}</span>
      ${(b.blocks || []).map((x) => `<span class="meta" style="font-size:12.5px">${esc(x.name)}${x.dates ? ` · ${esc(x.dates)}` : ""}</span>`).join("")}</div>
      ${d.manage || d.next_block?.none ? "" : ask.html.replace("pill red tall", "pill")}</div>${manage}`;
}

// A plan left part way, kept on the phone: one row on Plan to carry on, or to start again.
function resumeRow() {
  const d = buildDraft();
  if (!d) return "";
  const n = STEP_OF[d.step] || d.answers.reached || 1;
  const days = Math.floor((Date.now() - d.at) / 864e5);
  const when = days === 0 ? "today" : days === 1 ? "yesterday" : `${days} days ago`;
  return `<div class="b-resume"><a class="b-resume-go" href="#/build${n > 1 ? `/${d.step}` : ""}${d.replace ? "?replace=1" : "?replace=0"}&from=resume">
      <span class="stack-tight" style="gap:1px;min-width:0"><span class="strong">Pick up your plan</span><span class="meta" style="font-size:12.5px">Step ${n} of 7 · last changed ${when}</span></span>
      <span class="b-resume-cta">Carry on</span></a><button type="button" class="link-plain" data-drop-draft>Start again</button></div>`;
}
function wireResume() {
  const b = document.querySelector("[data-drop-draft]");
  if (b) b.onclick = () => { track("build_action", { action: "restart" }); store.drop(BUILD_KEY); B.inputs = null; B.answers = null; b.closest(".b-resume").remove(); toast("Cleared. Build one fresh whenever you like."); };
}

function removePlanSheet(m) {
  sheet(`<div class="stack-tight"><span class="strong" id="sheet-title" style="font-size:18px;font-weight:700">Remove this plan?</span>
      <span class="meta" style="font-size:14px;line-height:1.5">Takes ${m.count} session${m.count === 1 ? "" : "s"}, ${esc(m.from)} to ${esc(m.to)}, off your Garmin calendar and watch. Runs you've already done stay.</span></div>
    <div class="pills"><button type="button" class="pill tall" data-close>Keep it</button><button type="button" class="pill tall red" data-go>Remove ${m.count} session${m.count === 1 ? "" : "s"}</button></div>`,
  (holder, close) => {
    const go = holder.querySelector("[data-go]");
    go.onclick = async () => {
      go.disabled = true; go.textContent = "Removing…";
      try {
        const r = await api("plan/remove", { method: "POST", body: JSON.stringify({ confirm: "remove" }) });
        close(); toast(r.message); B.inputs = null; route(true);
      } catch (e) {
        if (e instanceof SignedOut) return route();
        close(); toast(e.message);
      }
    };
  });
}

// A phase name too long for its weeks is shortened until it fits: ENDURANCE,
// then ENDUR, then EN, then E (Nov 11: on a phone, Endurance was cut at both ends).
function fitPhases(root) {
  (root || document).querySelectorAll(".phases span[data-label]").forEach((el) => {
    const full = el.dataset.label;
    for (const text of [full, full.slice(0, 5), full.slice(0, 2), full.slice(0, 1)]) {
      el.textContent = text;
      if (el.scrollWidth <= el.clientWidth) break;
    }
  });
}
window.addEventListener("resize", () => fitPhases());

async function showPlan(view, params, quiet = false) {
  tabs("plan");
  const week = params.get("week");
  return show(`plan${week ? `?week=${encodeURIComponent(week)}` : ""}`, (d) => {
    const seg = (id, label) => `<a href="#/plan${id === "block" ? "/block" : ""}" class="${view === id ? "on" : ""}" ${view === id ? 'aria-current="page"' : ""}>${label}</a>`;
    const head = `<header class="stack" style="gap:12px"><span class="stack-tight"><h1 class="headline">Plan</h1>${credit(d)}</span><nav class="segmented" aria-label="Plan view">${seg("week", "Week")}${seg("block", "Block")}</nav></header>`;
    if (d.empty) {
      const ask = askLink(d, `Build a plan with ${d.assistant === "ChatGPT" ? "ChatGPT" : "Claude"}`);
      if (d.build) {
        $("screen").innerHTML = `<header class="stack-tight" style="padding-top:4px"><span class="kicker">Plan</span><h1 class="headline">No plan yet</h1></header>
          ${resumeRow()}
          <div class="card b-entry"><div class="stack-tight" style="gap:6px"><span class="b-entry-t">Build one here</span>
            <span class="b-entry-d">${d.fresh ? "About two minutes. Tell it your usual week and it does the rest." : "About two minutes. Most answers come from Garmin, so most steps are one tap."}</span></div>
            <a class="pill tall light" href="#/build?from=plan">Build one here</a></div>
          <div class="card b-entry"><div class="stack-tight" style="gap:6px"><span class="b-entry-t">Or talk it through</span>
            <span class="b-entry-d">${esc(d.assistant === "ChatGPT" ? "ChatGPT" : "Claude")} asks the same questions and can handle anything unusual.</span></div>
            ${ask.html.replace("pill red tall", "pill tall")}</div>`;
        wireResume();
        return;
      }
      $("screen").innerHTML = head + `<div class="card pad stack"><span class="strong">No sessions on your Garmin calendar yet.</span>${ask.html}</div>`;
      return;
    }
    // The plan ends within three weeks: the next block can be planned now.
    const nb = d.next_block?.none ? `<div class="card b-entry"><div class="stack-tight" style="gap:6px"><span class="b-entry-t">Nothing planned from here</span>
        <span class="b-entry-d">Build the next plan here, or talk it through with ${esc(d.assistant === "ChatGPT" ? "ChatGPT" : "Claude")}.</span></div>
        <a class="pill tall light" href="#/build?from=nothing_ahead">Build a plan</a></div>`
      : d.next_block ? `<div class="card b-entry"><div class="stack-tight" style="gap:6px"><span class="b-entry-t">This plan ends ${esc(d.next_block.ends)}</span>
        <span class="b-entry-d">Plan the next block now and it starts the Monday after, with an easy week first.</span></div>
        <div class="pills"><a class="pill tall light" href="#/build?from=next_block" style="flex:1.2">Plan the next block</a>${askLink({ ...d, ask: d.next_block.ask }, "With " + (d.assistant === "ChatGPT" ? "ChatGPT" : "Claude")).html.replace("pill red tall", "pill tall")}</div></div>` : "";
    remember(d);
    base = "plan";
    if (desktop()) {
      $("screen").innerHTML = `<header><h1 class="headline" style="font-size:28px">Plan</h1></header>${resumeRow()}${nb}
        <div class="desk-plan"><div class="stack">${weekView(d)}</div><div class="stack">${blockView(d)}</div></div>`;
    } else {
      $("screen").innerHTML = head + resumeRow() + nb + (view === "block" ? blockView(d) : nudge() + weekView(d));
      wireNudge($("screen"));
    }
    fitPhases($("screen"));
    if (desktop() || view !== "block") holdToMove($("plan-list"), d.today);
    const rm = document.querySelector("[data-remove-plan]");
    if (rm) rm.onclick = () => removePlanSheet(d.manage);
    wireResume();
    warm();
  }, (e) => failed(e.message, () => showPlan(view, params)), quiet || base === "plan");
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
        await api(`session/${m.from}/move`, { method: "POST", body: JSON.stringify({ to: m.to, schedule_id: Number(m.r.dataset.sid) || null, via: "hold" }) });
        store.set(DRAGGED, "1");
        const dow = (week.label[m.to] || "").match(/>(\w{3})</);
        const key = dow ? dow[1].slice(0, 1).toUpperCase() + dow[1].slice(1).toLowerCase() : "";
        said.push(`${m.r.dataset.name} to ${DAY_NAMES[key] || key || m.to}`);
      }
      toast(`Moved ${said.join(", and ")}. The watch follows at its next sync.`);
    } catch (err) {
      if (err instanceof SignedOut) return route();
      toast(said.length ? `Moved ${said.join(", and ")}, then: ${err.message}` : err.message);
    }
    route(true);
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
  // This week or next: the two weeks a crew keeps.
  const next = d.showing === "next";
  const weeks = `<nav class="segmented" aria-label="Crew week"><a href="#/crew" class="${next ? "" : "on"}" ${next ? "" : 'aria-current="page"'}>This week</a>
    <a href="#/crew?week=next" class="${next ? "on" : ""}" ${next ? 'aria-current="page"' : ""}>Next week</a></nav>`;
  const p = { head: `<header class="stack" style="gap:12px"><span class="stack-tight"><span class="kicker">${esc(d.kicker)}</span><h1 class="headline" ${desk ? 'style="font-size:28px"' : ""}>${esc(d.headline)}</h1>${credit(d)}</span>${weeks}</header>` };
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

async function showCrew(params = new URLSearchParams()) {
  tabs("crew");
  base = "crew";
  const next = params.get("week") === "next";
  return show(next ? "crew?week=next" : "crew", (d) => {
    remember(d);
    if (d.none) {
      const ask = askLink(d, `Start a crew with ${d.assistant === "ChatGPT" ? "ChatGPT" : "Claude"}`);
      $("screen").innerHTML = `<header class="stack-tight"><span class="kicker">CREW</span><h1 class="headline">Run with friends</h1></header>
        <div class="card pad stack" style="max-width:560px"><span class="strong">You're not in a crew yet.</span>
        <span class="meta" style="line-height:1.5">A crew is a few friends who see each other's easy and long runs, by day and time of day, and line them up. Never pace, heart rate or routes. Have a code from a friend? Ask your assistant to join with it.</span>${ask.html}
        <a class="meta" href="https://daash.run/crews?utm_source=app&utm_medium=link&utm_campaign=crews" target="_blank" rel="noopener" style="font-weight:600;color:var(--red-text);text-decoration:none">How crews work</a></div>`;
      return;
    }
    $("screen").innerHTML = crewView(d);
    const copy = $("copy");
    if (copy) copy.onclick = async () => {
      try { await navigator.clipboard.writeText(copy.dataset.value); toast(copy.textContent.includes("link") ? "Link copied." : "Code copied."); }
      catch { toast("Couldn't copy. Press and hold the link to copy it."); }
    };
  }, (e) => failed(e.message, () => showCrew(params)));
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
    ${d.groups.map((g) => g.title === "Assistants" && onComputer() && d.assistant !== "ChatGPT" ? { ...g, rows: [...g.rows,
      { l: "Ask Claude opens in", v: store.get(OPENS) === "web" ? "The browser" : "The Claude app", sub: "Tap to switch", action: "opens" }] } : g)
      // Notifications sit just above Account (Nov 11), for everyone.
      .map((g) => `${g.title === "Account" ? pushRow(d) : ""}<section class="stack-8"><span class="kicker" style="font-size:11.5px;letter-spacing:0.08em;padding-left:2px">${esc(g.title.toUpperCase())}</span>
      <div class="card" style="overflow:hidden">${g.rows.map((r, i) => youRow(r, d, i)).join("")}</div></section>`).join("")}
    ${d.groups.some((g) => g.title === "Account") ? "" : pushRow(d)}
    <p class="small" style="text-align:center">Daash 0.1 · <a href="${esc(d.privacy)}" target="_blank" rel="noopener">Privacy</a><br>Daash is independent and not made or endorsed by Garmin.</p>`;
}

function sheet(html, wire) {
  const holder = $("sheet");
  holder.innerHTML = `<div class="scrim" id="scrim"><div class="sheet" role="dialog" aria-modal="true" aria-labelledby="sheet-title">${html}</div></div>`;
  const close = () => { holder.innerHTML = ""; };
  $("scrim").onclick = (e) => { if (e.target.id === "scrim") close(); };
  holder.querySelectorAll("[data-close]").forEach((b) => { b.onclick = close; });
  holder.querySelector(".scrim").onkeydown = (e) => {
    if (e.key === "Escape") return close();
    if (e.key !== "Tab") return;
    // Tab goes round the dialog, not to the page behind it.
    const items = [...holder.querySelectorAll('button:not([disabled]), a[href], input, select, textarea')].filter((x) => !x.closest("[hidden]"));
    if (!items.length) return;
    const first = items[0], last = items[items.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  };
  wire(holder, close);
  // The sheet's own choice of where to start (a name to type) wins over its first button.
  const first = holder.querySelector("button");
  if (first && !holder.contains(document.activeElement)) first.focus();
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

// Deleting the account can't be undone, so it takes typing the word, not a tap:
// the button stays off until "delete" is typed, and the server checks the word too.
function deleteSheet() {
  sheet(`<div class="stack-tight"><span class="strong" id="sheet-title">Delete your Daash account?</span>
      <span class="meta" style="line-height:1.5">This disconnects Garmin and deletes your stored session, what Daash keeps for you and your place in your crew. Claude and ChatGPT lose access too. Your Garmin account and the workouts on your watch stay as they are. It can't be undone.</span></div>
    <label class="field-label" for="delete-word">Type <b>delete</b> to confirm</label>
    <input id="delete-word" class="field" autocomplete="off" autocapitalize="off" spellcheck="false">
    <div class="pills"><button class="pill" data-close>Cancel</button><button class="pill red" id="delete-go" disabled>Delete account</button></div>`, (holder, close) => {
    const word = $("delete-word"), go = $("delete-go");
    const said = () => word.value.trim().toLowerCase() === "delete";
    word.oninput = () => { go.disabled = !said(); };
    holder.querySelector("[data-close]").focus();
    go.onclick = async () => {
      if (!said()) return;
      go.disabled = true;
      try {
        await api("you/delete", { method: "POST", body: JSON.stringify({ confirm: word.value.trim().toLowerCase() }) });
        close(); token.clear();
        signInView("Your Daash account is deleted.");
      } catch (e) { close(); toast(e.message); }
    };
  });
}

async function signOut() {
  const access = token.access, client = CLIENT_ID;
  token.clear();
  store.drop(BUILD_KEY);
  if (access) {
    // Revoked as well as forgotten, so the token can't be used again from anywhere.
    fetch("/revoke", { method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({ token: access, client_id: client }) }).catch(() => {});
  }
  signInView("Signed out.");
}

async function showYou() {
  tabs("you");
  base = "you";
  return show("you", (d) => {
    $("screen").innerHTML = youView(d);
    $("refresh").onclick = () => { cache.drop((k) => k === "you"); toast("Checked Garmin again."); showYou(); };
    $("screen").querySelectorAll("[data-you]").forEach((b) => {
      b.onclick = () => {
          if (b.dataset.you === "opens") {
            store.set(OPENS, store.get(OPENS) === "web" ? "app" : "web");
            toast(store.get(OPENS) === "web" ? "Ask Claude opens in the browser." : "Ask Claude opens the Claude app, or the browser without it.");
            return showYou();
          }
          if (b.dataset.you === "push") return (pushIsOn() ? turnOffPush() : turnOnPush("you")).then(() => { cache.drop((k) => k === "you"); showYou(); });
          if (b.dataset.you === "headsup") return headsUpSheet(d);
          return b.dataset.you === "times" ? timesSheet(d) : b.dataset.you === "delete" ? deleteSheet() : signOut();
        };
    });
  }, (e) => failed(e.message, showYou));
}

async function signInPost(path, body) {
  const res = await fetch(API + "sign-in/" + path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const out = await res.json().catch(() => ({ error: "Daash didn't answer. Try again in a moment." }));
  if (!res.ok || out.error) throw new Error(out.error || "Something went wrong.");
  return out;
}

// -- Home screen and notifications (Nov 10) -----------------------------------
// The step 2 test: after a second day with the app, a card asks to put it on
// the home screen; opened from there, a card offers a session heads-up and a
// run's verdict once it syncs. "Not now" quiets either for two weeks.
const DAYS_SEEN = "daash.days";
const HOME_LATER = "daash.home.later";
const PUSH_LATER = "daash.push.later";
// Set once a session or run has been opened: the home-screen prompt waits for it.
const LOOKED = "daash.looked";
// Added to the home screen from this browser: it isn't asked again here.
const INSTALLED = "daash.installed";
// The hint that a session can be moved or made easier, until it's used or dismissed (Nov 11).
const EDIT_HINT = "daash.edit.hint";
const PUSH_ON = "daash.push.on";
const QUIET_DAYS = 14;
let installEvent = null;
// Chrome on Android says it can install the app some time after the page has
// drawn: the prompt card, already showing the menu steps, swaps to the button.
window.addEventListener("beforeinstallprompt", (e) => {
  e.preventDefault();
  installEvent = e;
  const card = document.getElementById("nudge");
  if (card) { card.outerHTML = nudge(); wireNudge(document); }
});

function daysSeen() {
  let days = [];
  try { days = JSON.parse(store.get(DAYS_SEEN) || "[]"); } catch { days = []; }
  const today = isoToday();
  if (!days.includes(today)) { days = [...days, today].slice(-30); store.set(DAYS_SEEN, JSON.stringify(days)); }
  return days.length;
}
const quiet = (key) => { const t = Number(store.get(key) || 0); return t && Date.now() - t < QUIET_DAYS * 86400000; };
const iphone = () => phoneKind() === "ios";
// Which phone, by its own words: iPadOS says it's a Mac, but with touch.
// Anything else (a computer, a narrow window on one) gets no home-screen prompt.
function phoneKind() {
  const ua = navigator.userAgent;
  if (/iPhone|iPad|iPod/.test(ua) || (/Macintosh/.test(ua) && navigator.maxTouchPoints > 1)) return "ios";
  if (/Android/.test(ua)) return "android";
  return null;
}

// How to add the app on this phone and browser (Nov 11). Apps that open links
// inside themselves (Instagram, Facebook, Google) can't add to the home screen.
function homeSteps() {
  const ua = navigator.userAgent, kind = phoneKind();
  const share = `${ICON.share()} Share`;
  if (/FBAN|FBAV|Instagram|Line\/|GSA\/|; wv\)/.test(ua)) {
    return kind === "ios" ? "Open this page in Safari first, then tap Share and <b>Add to Home Screen</b>."
      : "Open this page in Chrome first, then tap <b>⋮</b> and <b>Add to home screen</b>.";
  }
  if (kind === "ios") {
    if (/CriOS/.test(ua)) return `Tap ${share} in the address bar, then <b>Add to Home Screen</b>.`;
    if (/FxiOS|EdgiOS|OPiOS/.test(ua)) return `Open this page in Safari, tap ${share}, then <b>Add to Home Screen</b>.`;
    return `Tap ${share} at the bottom of Safari, then <b>Add to Home Screen</b>. Don't see it? Open this page in Safari first.`;
  }
  if (/SamsungBrowser/.test(ua)) return "Tap <b>☰</b> at the bottom, then <b>Add page to</b> and <b>Home screen</b>.";
  if (/Firefox/.test(ua)) return "Tap <b>⋮</b>, then <b>Install</b>.";
  return "Tap <b>⋮</b> at the top right, then <b>Add to home screen</b>.";
}
const canPush = () => "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
const pushIsOn = () => store.get(PUSH_ON) === "1" && canPush() && Notification.permission === "granted";

// Counted once a day per card, not every time Today redraws.
function shownOnce(name) {
  const key = `daash.${name}.shown`;
  if (store.get(key) === isoToday()) return;
  store.set(key, isoToday());
  track(name);
}

function nudge() {
  const days = daysSeen();
  // Phones only: never on a computer, however narrow its window.
  if (desktop() || !phoneKind()) return "";
  if (!homeScreen()) {
    // From the first day once they've opened a session or a run (Nov 11: on
    // day 2 only, and on Today only, it reached nobody in its first day).
    if ((days < 2 && !store.get(LOOKED)) || quiet(HOME_LATER) || store.get(INSTALLED)) return "";
    shownOnce("home_prompt_shown");
    const how = installEvent ? "" : `<span class="meta" style="font-size:13.5px;line-height:1.45">${homeSteps()}</span>`;
    return `<div class="card pad stack" id="nudge" style="gap:12px"><span class="stack-tight" style="gap:4px">
        <span class="strong">Put Daash on your home screen</span>
        <span class="meta" style="font-size:13.5px;line-height:1.45">Your plan in one tap. From there, Daash can tell you the evening before what's on, and how your run went once your watch syncs.</span>${how}</span>
      <div class="pills"><button class="pill" data-nudge="home-later">Not now</button>
        ${installEvent ? `<button class="pill light" data-nudge="install">Add to home screen</button>` : ""}</div></div>`;
  }
  if (!canPush() || Notification.permission === "denied" || pushIsOn() || quiet(PUSH_LATER)) return "";
  shownOnce("push_prompt_shown");
  return `<div class="card pad stack" id="nudge" style="gap:12px"><span class="stack-tight" style="gap:4px">
      <span class="strong">A heads-up before each session</span>
      <span class="meta" style="font-size:13.5px;line-height:1.45">Tomorrow's session at 8pm the evening before, and how your run went once your watch syncs. Change the time or turn them off in You.</span></span>
    <div class="pills"><button class="pill" data-nudge="push-later">Not now</button><button class="pill light" data-nudge="push">Turn on</button></div></div>`;
}

function wireNudge(root) {
  root.querySelectorAll("[data-nudge]").forEach((b) => {
    b.onclick = async () => {
      const what = b.dataset.nudge;
      if (what === "home-later" || what === "push-later") {
        store.set(what === "home-later" ? HOME_LATER : PUSH_LATER, String(Date.now()));
        track(what === "home-later" ? "home_prompt_dismissed" : "push_prompt_dismissed");
      } else if (what === "install" && installEvent) {
        track("home_prompt_tapped");
        installEvent.prompt();
        const choice = await installEvent.userChoice.catch(() => null);
        installEvent = null;
        if (choice && choice.outcome === "accepted") store.set(INSTALLED, "1");
        else store.set(HOME_LATER, String(Date.now()));
      } else if (what === "push") {
        b.disabled = true;
        await turnOnPush("today");
      }
      const card = $("nudge");
      if (card) card.outerHTML = nudge();
      wireNudge(root);
    };
  });
}

function keyBytes(key) {
  const raw = atob(key.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - key.length % 4) % 4));
  return Uint8Array.from(raw, (c) => c.charCodeAt(0));
}

async function turnOnPush(where) {
  try {
    const answer = await Notification.requestPermission();
    if (answer !== "granted") {
      track("push_denied", { where });
      if (answer === "denied") toast("Notifications are blocked for Daash. Allow them in your phone's settings.");
      else store.set(PUSH_LATER, String(Date.now()));
      return;
    }
    const reg = await navigator.serviceWorker.ready;
    const { key } = await api("push");
    const sub = (await reg.pushManager.getSubscription())
      || await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(key) });
    let tz = null;
    try { tz = Intl.DateTimeFormat().resolvedOptions().timeZone; } catch { tz = null; }
    await api("push", { method: "POST", body: JSON.stringify({ subscription: sub.toJSON(), tz }) });
    store.set(PUSH_ON, "1");
    toast("Notifications on. You'll hear about each session at 8pm the evening before. Change it in You.");
  } catch (e) {
    if (e instanceof SignedOut) return route();
    toast(e.message || "Notifications couldn't be turned on.");
  }
}

async function turnOffPush() {
  try {
    const reg = await navigator.serviceWorker.ready;
    const sub = await reg.pushManager.getSubscription();
    if (sub) {
      await api("push", { method: "POST", body: JSON.stringify({ off: true, endpoint: sub.endpoint }) });
      await sub.unsubscribe();
    }
    store.drop(PUSH_ON);
    toast("Notifications off.");
  } catch (e) {
    if (e instanceof SignedOut) return route();
    toast(e.message || "Notifications couldn't be turned off.");
  }
}

// When the heads-up about a session goes (Nov 11). The evening before is the default.
const HEADS_UP = [["evening", "Evening before", "8pm the night before"], ["6", "6am", "That morning"],
  ["7", "7am", "That morning"], ["8", "8am", "That morning"]];

// In You: the switch, on a phone that can have notifications at all, and when they go.
function pushRow(d = {}) {
  // Shown to everyone (Nov 11). iPhone only allows notifications from the
  // home-screen app, so in Safari the row says how to get there.
  const iosBrowser = phoneKind() === "ios" && !homeScreen();
  const blocked = canPush() && Notification.permission === "denied";
  const on = !iosBrowser && pushIsOn();
  const r = iosBrowser ? { l: "Notifications", v: "Home screen first",
      sub: "On iPhone they work once Daash is on your home screen: in Safari, tap Share, then Add to Home Screen." }
    : !canPush() ? { l: "Notifications", v: "Not here", sub: "This browser can't show notifications. Try Chrome, Safari or the home-screen app." }
    : blocked ? { l: "Notifications", v: "Blocked", sub: "Allow them for Daash in your browser or phone settings." }
    : { l: "Notifications", v: on ? "On" : "Off", sub: "Session heads-up and run verdicts. Tap to switch", action: "push" };
  const when = HEADS_UP.find(([code]) => code === (d.heads_up || "evening"));
  const time = on && !blocked && !iosBrowser ? youRow({ l: "Session heads-up", v: when[1], sub: "When you hear about a planned session", action: "headsup" }, {}, 1) : "";
  return `<section class="stack-8"><span class="kicker" style="font-size:11.5px;letter-spacing:0.08em;padding-left:2px">NOTIFICATIONS</span>
    <div class="card" style="overflow:hidden">${youRow({ ...r, tone: on ? "ok" : undefined }, {}, 0)}${time}</div></section>`;
}

function headsUpSheet(d) {
  const chosen = d.heads_up || "evening";
  sheet(`<div class="stack-tight"><span class="strong" id="sheet-title">Session heads-up</span>
      <span class="meta">When you hear what's planned. Out the door early? The evening before gives you time to get kit ready.</span></div>
    <div class="times-grid">${HEADS_UP.map(([code, label, sub]) => `<button class="pill" data-when="${code}" aria-pressed="${code === chosen}" style="flex-direction:column;gap:2px;height:auto;padding:10px 12px"><span>${esc(label)}</span><span style="font-size:12px;opacity:0.72">${esc(sub)}</span></button>`).join("")}</div>
    <div class="pills"><button class="pill" data-close>Cancel</button></div>`, (holder, close) => {
    holder.querySelectorAll("[data-when]").forEach((b) => {
      b.onclick = async () => {
        try {
          const r = await api("push", { method: "POST", body: JSON.stringify({ heads_up: b.dataset.when }) });
          close(); toast(r.message); cache.drop((k) => k === "you"); showYou();
        } catch (e) { if (e instanceof SignedOut) return route(); toast(e.message); }
      };
    });
  });
}

// Opened from the home screen: on iPhone that keeps its own storage, so
// someone signed in in Safari is asked again (Nov 4: two sign-ins a minute apart).
function homeScreen() {
  return window.navigator.standalone === true
    || !!(window.matchMedia && window.matchMedia("(display-mode: standalone)").matches);
}

function signInView(message, step = "start", email = "") {
  $("tabs").hidden = true;
  $("topbar").hidden = true;
  base = null;
  const head = `<div class="wordmark"><img src="/app/icons/favicon.svg" width="32" height="32" alt="">Daash</div>`;
  const note = message ? `<div class="card pad meta" role="status">${esc(message)}</div>` : "";
  const foot = `<p class="small">Daash is independent and not made or endorsed by Garmin.</p>`;
  // Sent here to build a plan (daash.run's "Just the app"): say that, not the chat.
  const building = location.hash.startsWith("#/build");
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
        // Back to the screen a chat's link was opening, if it was opening one.
        if (!/^#\/\w/.test(location.hash)) history.replaceState(null, "", "/app/#/today");
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
    ${building ? `<h1 class="headline">A running plan for your watch, built in two minutes.</h1>
    <p class="why">Sign in with Garmin Connect™, answer six questions, and Daash puts the plan on your Garmin calendar. No chat needed.</p>`
    : `<h1 class="headline">Your plan, from Claude or ChatGPT, on your watch.</h1>
    <p class="why">See today's session, check how a run went, and move or ease a session. The changes reach your watch and the chat.</p>`}
    ${note}${homeScreen() ? `<div class="card pad meta" style="line-height:1.5">On your home screen, Daash keeps its own sign-in, apart from your browser's. Sign in once here and it stays signed in.</div>` : ""}
    ${building ? `<button class="pill red tall" id="garmin">Sign in with Garmin Connect™</button>
    <p class="small">Connects your Garmin account, so the plan can go on your calendar. You can add Claude or ChatGPT later.</p>
    <div class="or"><span>Already use Daash?</span></div>` : ""}
    <form id="email-form" class="stack"><label class="field-label" for="email">${building ? "Your email" : "Already use Daash? Your email"}</label>
      <input id="email" class="field" type="email" autocomplete="email" value="${esc(email)}" required>
      <button class="pill ${building ? "" : "red "}tall" type="submit">Email me a code</button></form>
    <p class="small">The email your Daash account is under, usually your Garmin one. Your plan, crew and settings are waiting.</p>
    ${building ? "" : `<div class="or"><span>New to Daash?</span></div>
    <button class="pill tall" id="garmin">Sign in with Garmin Connect™</button>
    <p class="small">Connects your Garmin account. You can add Claude or ChatGPT later.</p>`}${foot}</div>`;
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
// signs the app in as the person who tapped it, with nothing to type. It also
// says where it was given (from=…), sent once with the first screen it opens.
let cameFrom = null;
async function handOff() {
  // The app's own links say which tab Back returns to with from=today or
  // from=plan: those stay. Nov 9: stripping them too sent Back from a session
  // opened in Plan to Today.
  const from = location.hash.match(/[?&]from=(?!(?:today|plan)(?:&|$))([a-z_]{1,24})/);
  const m = location.hash.match(/[?&]login=([\w-]+)/);
  if (!m && !from) return null;
  if (from) cameFrom = from[1];
  const clean = location.hash.replace(/[?&]login=[\w-]+/g, "")
    .replace(/[?&]from=(?!(?:today|plan)(?:&|$))[\w-]+/g, "").replace(/^([^?]*)&/, "$1?").replace(/\?$/, "");
  history.replaceState(null, "", `/app/${clean || "#/today"}`);
  if (!m || token.access) return null;
  try {
    token.save(await signInPost("handoff", { code: m[1] }));
    return null;
  } catch (err) {
    return `${err.message} Sign in once here and the app remembers you.`;
  }
}

// -- Build a plan: six steps, a preview, then onto the watch (preview accounts) ---
// The answers live here and in this tab's storage; every plan the preview shows
// is made on the server from them, by rule, the same way each time.

const BUILD_KEY = "daash.build";
const DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const DAY_LONG = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const STEP_OF = { 1: 1, 2: 2, "2c": 2, 3: 3, 4: 4, 5: 5, 6: 6, 7: 7 };
const PREV_OF = { 1: null, 2: "1", "2c": "2", 3: null, 4: "3", 5: "4", 6: "5", 7: "6", hurt: "5", error: "7" };
const LEVELS = [["new", "Under 6 months"], ["regular", "Up to 2 years"], ["exp", "2 years +"]];
const INJURIES = [["none", "None", "Running feels fine."], ["niggle", "A niggle now", "Something's a bit sore but you can run easy."],
  ["back", "Coming back from injury", "You've had time off and are running again."], ["hurts", "Something hurts now", "It hurts to run, or it gets worse as you go."]];
const GOALS = [["faster", "Get faster at a distance", "Sharper sessions aimed at one distance."],
  ["base", "Build a base", "More easy running, ready for a race plan later."], ["fit", "General fitness", "A steady mix, about the same each week."]];
const DISTS = [["5k", "5K", 5], ["10k", "10K", 10], ["half", "Half", 21.1], ["marathon", "Marathon", 42.2]];
const STYLE_OPTS = [
  { id: "steady", t: "Steady", d: "Mostly easy, with one harder day a week.", based: "BASED ON 80/20 TRAINING" },
  { id: "balanced", t: "Balanced", d: "Two harder days a week, paced from your race times.", based: "BASED ON JACK DANIELS STYLE PACES" },
  { id: "faster", t: "Faster", d: "Several controlled threshold sessions, kept in check by heart rate.", based: "BASED ON NORWEGIAN SINGLES STYLE" },
  { id: "pick", t: "Pick for me", d: "Chosen from your days, races and history.", based: "" },
];
const VERDICT = { realistic: ["#22352A", "#8CC08F"], stretch: ["#3A2A1A", "#E8A15F"], ambitious: ["#2A2520", "#E8A15F"] };
const WARN = svg('<circle cx="12" cy="12" r="10"/><path d="M12 8v4"/><path d="M12 16h.01"/>', 18, 'style="color:var(--hard);flex-shrink:0;margin-top:1px"');

const B = { inputs: null, answers: null, plan: null, sel: null, job: null, poll: null, asked: 0, from: null, did: new Set() };

// The builder's own events beyond the steps: what people did and what stopped them.
// once: counted the first time in a run through, for things tapped many times over.
function buildDid(action, props = {}, once = false) {
  if (once) { const k = `${action}|${props.reason || ""}`; if (B.did.has(k)) return; B.did.add(k); }
  track("build_action", { action, step: B.step, ...props });
}
// The goal race's distance as a word, never its name or date.
function distOf(km) { return (DISTS.find((x) => x[2] === km) || [km > 42.2 ? "ultra" : "other"])[0]; }

function day(iso) { const [y, m, d] = iso.split("-").map(Number); return new Date(y, m - 1, d); }
// The answers so far are kept on the phone, so leaving part way loses nothing: Plan offers to pick them up.
const DRAFT_DAYS = 14;
function saveAnswers() {
  store.set(BUILD_KEY, JSON.stringify({ starts: B.inputs.starts, replace: !!B.replace, answers: B.answers, step: B.step, at: Date.now() }));
}
function buildDraft() {
  try {
    const d = JSON.parse(store.get(BUILD_KEY) || "null");
    return d && d.answers && (d.answers.reached || 1) >= 2 && Date.now() - (d.at || 0) < DRAFT_DAYS * 864e5 ? d : null;
  } catch { return null; }
}
// A race outside the dates a plan can reach: before this block starts, or past Garmin's 30 weeks.
function outside(r) {
  const w = B.inputs && B.inputs.window;
  return w ? (r.date < w.from ? "before" : r.date > w.to ? "after" : null) : null;
}
function freshAnswers(i) {
  const inside = (i.races || []).filter((r) => !(i.window && (r.date < i.window.from || r.date > i.window.to)));
  return {
    avg_km: i.avg_km || 0, longest_km: i.longest_km || 0, level: i.level || "regular",
    races: (i.races || []).map((r) => ({ ...r, tier: inside.includes(r) ? r.tier : "C" })), goal_time: null, open: !inside.some((r) => r.tier === "A"),
    open_goal: "faster", open_distance: "half", days: i.days || 4, out: [], long_day: i.long_day ?? 6,
    amount: "build", injury: "none", style: i.level === "new" ? "steady" : "balanced", note: "",
  };
}
// What the server is sent: the answers alone, without the screen's own notes. With no race
// in mind every A race is raced as a B, and races the plan can't reach are left out.
// Plans are for adults: asked once on this phone, before the first plan goes on.
const ADULT_KEY = "daash.adult";
function adult() { return store.get(ADULT_KEY) === "yes"; }
document.addEventListener("change", (e) => {
  if (document.body.dataset.screen !== "build" || !e.target.matches || !e.target.matches("[data-adult]")) return;
  if (e.target.checked) store.set(ADULT_KEY, "yes"); else store.drop(ADULT_KEY);
  const send = document.querySelector('[data-b="send"]');
  if (send) send.disabled = !(B.plan?.preview && adult());
});

function sendable() {
  const a = B.answers;
  const keep = ["avg_km", "longest_km", "level", "goal_time", "open_goal", "open_distance", "days", "out", "long_day", "amount", "injury", "style"];
  return { ...Object.fromEntries(keep.map((k) => [k, a[k]])),
    races: a.races.filter((r) => !outside(r)).map((r) => ({ name: r.name, date: r.date, km: r.km, tier: a.open && r.tier === "A" ? "B" : r.tier })) };
}
function goalRace() { return B.answers.open ? null : [...B.answers.races].filter((r) => r.tier === "A" && !outside(r)).sort((x, y) => x.date.localeCompare(y.date)).pop() || null; }

// One B race every 3 weeks, and none in the 10 days before the A race: the others run for fun.
function settle(changed) {
  const a = B.answers;
  const goal = goalRace();
  let note = "";
  const sorted = [...a.races].sort((x, y) => x.date.localeCompare(y.date));
  let last = null;
  for (const r of sorted) {
    if (r.tier !== "B" || outside(r)) continue;
    const tooNearA = goal && r !== goal && (day(goal.date) - day(r.date)) / 864e5 <= 10 && day(r.date) <= day(goal.date);
    const tooNearB = last && (day(r.date) - day(last.date)) / 864e5 < 21;
    if (tooNearA || tooNearB) {
      const demote = tooNearB && r.id === changed ? last : r;
      demote.tier = "C"; demote.demoted = true;
      note = tooNearA ? `${demote.name} is too close to ${goal.name}, so it's now a fun run.`
        : `Racing more often than every 3 weeks leaves little room for training. ${demote.name} is now a fun run.`;
      if (demote === last) last = r;
    } else last = r;
  }
  a.note = note;
}

// Every answer is asked once and remembered, and the step after this one is asked ahead,
// so moving on draws at once instead of waiting on the server.
const ASKED = new Map();
function buildAsk(preview = false) {
  const body = JSON.stringify({ answers: sendable(), preview, replace: !!B.replace });
  let p = ASKED.get(body);
  if (!p) {
    p = api("build/plan", { method: "POST", body, read: true });
    p.catch(() => ASKED.delete(body));
    ASKED.set(body, p);
    if (ASKED.size > 40) ASKED.delete(ASKED.keys().next().value);
  }
  return p;
}
let aheadTimer;
function askAhead() {
  clearTimeout(aheadTimer);
  aheadTimer = setTimeout(() => {
    const s = B.step;
    if (s === "1" || s === "2" || s === "2c" || s === "3") buildAsk(false).catch(() => {});
    if (s === "4" || s === "5" || s === "6") buildAsk(true).catch(() => {});
  }, 250);
}

function go(step) { location.hash = `#/build${step && step !== "1" ? `/${step}` : ""}`; }

async function showBuild(step, params = new URLSearchParams()) {
  step = step || "1";
  const chosen = params.get("race");
  // A race added since Garmin was last read here: read it again.
  if (chosen && B.inputs && !(B.inputs.races || []).some((r) => r.key === chosen)) B.inputs = null;
  // From Plan's Change this plan: the new plan goes in place of the one running now.
  if (params.get("replace") === "1" && !B.replace) { B.replace = true; B.inputs = null; }
  else if (params.get("replace") === "0" && B.replace) { B.replace = false; B.inputs = null; }
  // A race from Today's card is a plan of its own, not one in place of the plan running now.
  if (chosen && B.replace) { B.replace = false; B.inputs = null; }
  const wide = desktop();
  if (wide) { $("tabs").hidden = true; topbar("plan"); } else { $("topbar").hidden = true; $("tabs").hidden = true; }
  if (step === "sending" || step === "done" || step === "error") return buildSendView(step);
  // Where it was opened from: Plan's cards, the race prompt, a draft picked up. A bare link is "direct".
  const from = params.get("from");
  let opened = !!from, resumed = false;
  if (from || !B.from) B.from = from || "direct";
  if (!B.inputs) {
    opened = true;
    loading();
    try {
      B.inputs = await api(B.replace ? "build?replace=1" : "build");
    } catch (e) {
      if (e instanceof SignedOut) return route();
      return failed(e.message, () => showBuild(step));
    }
    remember(B.inputs);
    const saved = buildDraft();
    resumed = !!(saved && saved.starts === B.inputs.starts && !!saved.replace === !!B.replace && !chosen);
    B.answers = resumed ? saved.answers : freshAnswers(B.inputs);
    B.did = new Set();
  }
  // From the card on Today: that race is the goal, and any other A becomes a B.
  if (chosen && B.answers.races.some((r) => r.key === chosen)) {
    B.answers.races.forEach((r) => { if (r.key === chosen) r.tier = "A"; else if (r.tier === "A") r.tier = "B"; });
    B.answers.open = false;
    settle(B.answers.races.find((r) => r.key === chosen).id);
    saveAnswers();
    history.replaceState(null, "", "#/build");
  }
  if (!(step in STEP_OF) && step !== "hurt") step = "1";
  B.step = step;
  if (opened) track("build_opened", { from: B.from, step, resumed: resumed ? "yes" : "no", replace: B.replace ? "yes" : "no", typed: B.inputs.none ? "yes" : "no" });
  drawBuild();
  if (["2", "4", "6", "7"].includes(step)) refreshBuild();
}

async function refreshBuild() {
  const step = B.step, mine = ++B.asked;
  try {
    const d = await buildAsk(step === "7");
    if (B.step !== step || mine !== B.asked) return;
    // Each answer replaces what the last one said about the race and the goal.
    B.plan = { ...B.plan, chips: null, verdict: null, too_close: null, race_note: null, picked: null, problem: null, failure: null, ...d };
    if (step === "4" && d.amounts && !d.amounts.some((o) => o.id === B.answers.amount)) B.answers.amount = d.amounts.length > 1 ? "build" : "keep";
    if (step === "6" && !d.faster_ok && B.answers.style === "faster") B.answers.style = "balanced";
    if (step === "7" && d.preview) B.sel = d.preview.peak_week - 1;
    if (step === "7" && !d.preview) { B.plan.preview = null; B.plan.failure = d.problem || "This plan can't be built from these answers."; }
    drawBuild();
  } catch (e) {
    if (e instanceof SignedOut) return route();
    if (B.step === step && mine === B.asked) { B.plan = { ...B.plan, failure: e.message }; drawBuild(); }
  }
}

const radio = (on) => `role="radio" aria-checked="${on}"`;
const ring = (on, dis) => `<span class="b-ring ${on ? "on" : ""} ${dis ? "dis" : ""}"><span></span></span>`;
const seg = (items, on, act, cls = "") => `<div class="b-seg ${cls}" role="radiogroup" style="grid-template-columns:repeat(${items.length},minmax(0,1fr))">${items.map(([v, l]) =>
  `<button type="button" ${radio(String(v) === String(on))} data-b="${act}" data-v="${esc(v)}">${esc(l)}</button>`).join("")}</div>`;
const amber = (body) => `<div class="card amber b-amber">${WARN}<span>${body}</span></div>`;
const heading = (t, sub) => `<header class="b-head"><h1>${esc(t)}</h1>${sub ? `<span>${esc(sub)}</span>` : ""}</header>`;

function step1() {
  const i = B.inputs, a = B.answers;
  const max = Math.max(1, ...i.bars.map((b) => b.v));
  const rows = [
    ["Longest recent run", i.longest_on || "Last 6 weeks", `${a.longest_km} km`],
    ["Recent races", i.recent ? `${i.recent.name} · ${i.recent.on}` : "None in the last 12 weeks", i.recent ? i.recent.time : "–"],
    ["How often you run", i.level === a.level ? i.level_line : { new: "Most weeks, for a few months", regular: "Most weeks, for over a year", exp: "Most weeks, for years" }[a.level], `${i.days} days`],
  ];
  // Nothing from Garmin yet (a new account, or one waiting on Garmin's first runs): the numbers are typed in.
  if (i.none) {
    const set = a.avg_km > 0;
    const typed = [["Weekly distance", "Your usual week", set ? `${a.avg_km} km` : "Not set"], ["Longest recent run", "Last 6 weeks", set ? `${a.longest_km} km` : "Not set"],
      ["Running regularly for", "Sets how gently it builds", set ? LEVELS.find((l) => l[0] === a.level)[1] : "Not set"]];
    return heading("Your running lately", "Garmin hasn't sent any running from you yet. Tell us your usual week and the plan starts from there.")
      + `<div class="card b-list">${typed.map(([l, sub, v]) => `<button type="button" class="b-row b-rowbtn" data-b="edit"><span class="stack-tight" style="gap:2px;min-width:0"><span class="strong">${esc(l)}</span><span class="meta">${esc(sub)}</span></span><span class="b-val" style="${set ? "" : "color:var(--muted)"}">${esc(v)}</span></button>`).join("")}</div>`
      + (i.predictions ? `<div class="stack-8"><span class="strong">Garmin's race predictions</span><div class="card b-pred">${i.predictions.map((p) => `<div><span class="meta" style="font-size:12px">${esc(p.l)}</span><span>${esc(p.v)}</span></div>`).join("")}</div></div>` : "");
  }
  return heading("Your running lately", "From Garmin, last 4 weeks. Change anything that looks off.")
    + (i.thin ? amber("Garmin has only a few weeks of your running so far. Check these numbers before you go on.") : "")
    + `<div class="card b-card b-tap" data-b="edit"><div class="b-split"><span class="stack-tight" style="gap:2px"><span class="meta">Weekly distance</span>
        <span class="b-big">${a.avg_km} km<span class="b-unit"> a week</span></span></span><span class="mono-label" style="font-size:11.5px">LAST 4 WEEKS</span></div>
      <div class="b-weeks4">${i.bars.map((b) => `<div><span>${b.v}</span><i style="height:${Math.round(b.v / max * 56)}px"></i></div>`).join("")}</div>
      <div class="b-weeks4-l">${i.bars.map((b) => `<span>${esc(b.l)}</span>`).join("")}</div></div>
    <div class="card b-list">${rows.map(([l, sub, v], k) => k === 1
      ? `<div class="b-row"><span class="stack-tight" style="gap:2px;min-width:0"><span class="strong">${esc(l)}</span><span class="meta">${esc(sub)}</span></span><span class="b-val">${esc(v)}</span></div>`
      : `<button type="button" class="b-row b-rowbtn" data-b="edit"><span class="stack-tight" style="gap:2px;min-width:0"><span class="strong">${esc(l)}</span><span class="meta">${esc(sub)}</span></span><span class="b-val">${esc(v)}</span></button>`).join("")}</div>
    ${i.predictions ? `<div class="stack-8"><span class="strong">Garmin's race predictions</span><div class="card b-pred">${i.predictions.map((p) => `<div><span class="meta" style="font-size:12px">${esc(p.l)}</span><span>${esc(p.v)}</span></div>`).join("")}</div></div>` : ""}`;
}

function raceRow(r, n) {
  const d = day(r.date), out = outside(r);
  const meta = out === "before" ? "Before this block starts" : out === "after" ? "Past Garmin's 30-week calendar" : r.demoted ? "Now a fun run" : r.meta;
  return `<div class="b-race ${n ? "" : "first"} ${out ? "out" : ""}"><span class="b-date"><span>${MON[d.getMonth()].toUpperCase()}</span><b>${d.getDate()}</b></span>
    <span class="stack-tight" style="gap:2px;min-width:0"><span class="b-race-name">${esc(r.name)}</span><span class="meta" style="font-size:12.5px;${r.demoted && !out ? "color:var(--hard)" : ""}">${esc(meta)}</span></span>
    ${out ? "" : `<div class="b-tiers" role="radiogroup" aria-label="Priority for ${esc(r.name)}">${["A", "B", "C"].map((t) =>
      `<button type="button" ${radio(r.tier === t && !(B.answers.open && t === "A"))} class="t-${t}" data-b="tier" data-id="${esc(r.id)}" data-v="${t}">${t}</button>`).join("")}</div>`}</div>`;
}

function step2() {
  const a = B.answers;
  const races = [...a.races].sort((x, y) => x.date.localeCompare(y.date));
  const as = races.filter((r) => r.tier === "A");
  const goal = goalRace();
  let warn = "";
  if (!a.open && as.length > 1) {
    const [early, late] = [as[0], as[as.length - 1]];
    const weeks = Math.round((day(late.date) - day(early.date)) / 6048e5);
    warn = `<div class="card amber b-amber-col"><span class="b-amber">${WARN}<span>Two A races ${weeks} weeks apart is too close to peak for both. Make the ${esc(early.name)} a B race: it still gets a short taper.</span></span>
      <button type="button" class="pill amber-line" data-b="fixA" data-id="${esc(early.id)}">Make the ${esc(early.name)} a B race</button></div>`;
  }
  const p = B.plan || {};
  const verdict = p.verdict && a.goal_time ? p.verdict : null;
  const chips = [...(p.chips || []), "No time"];
  const goalCard = goal ? `<div class="card b-card"><div class="b-split"><span class="strong">Goal time for ${esc(goal.name)}</span><span class="meta" style="font-size:12.5px">Optional</span></div>
      <div class="b-chips">${chips.map((c) => { const on = c === "No time" ? !a.goal_time : a.goal_time === c;
        return `<button type="button" class="b-chip ${on ? "on" : ""}" aria-pressed="${on}" data-b="goal" data-v="${esc(c)}">${esc(c)}</button>`; }).join("")}</div>
      ${verdict ? `<div class="stack-8" style="gap:6px"><span class="b-verdict" style="background:${VERDICT[verdict.kind][0]};color:${VERDICT[verdict.kind][1]}">${esc(verdict.chip)}</span><span class="b-why">${esc(verdict.why)}</span></div>` : ""}
      ${p.race_note ? amber(esc(p.race_note)) : p.too_close ? amber(`${esc(goal.name)} is too close for a plan. Run easy and enjoy it, or pick a later race.`) : ""}</div>` : "";
  return heading("Your races", races.length ? "From your Garmin calendar. Mark one A: that's the goal." : "Nothing on your Garmin calendar in the next 6 months. Add one, or pick a goal.")
    + `<div class="stack-8">${races.length ? `<div class="card b-list">${races.map(raceRow).join("")}</div>
      <span class="meta" style="line-height:1.45">A: the goal. B: matters, gets a short taper. C: for fun, run through it.</span>` : ""}
      ${B.inputs.window?.said ? `<span class="meta" style="line-height:1.45">${esc(B.inputs.window.said)}</span>` : ""}</div>`
    + warn + (a.note ? amber(esc(a.note)) : "")
    + `<button type="button" class="pill tall b-dashed" data-b="addRace">${svg('<path d="M5 12h14"/><path d="M12 5v14"/>', 16)}Add a race</button>`
    + goalCard
    + `<button type="button" class="b-link" data-b="noRace">No race in mind</button>`;
}

function step2c() {
  const a = B.answers;
  return heading("No race? Pick a goal", "The plan runs 12 weeks, then you can plan the next.")
    + `<div class="stack-8" role="radiogroup">${GOALS.map(([id, t, d]) => `<button type="button" class="b-opt" ${radio(a.open_goal === id)} data-b="openGoal" data-v="${id}">${ring(a.open_goal === id)}<span class="stack-tight" style="gap:3px;min-width:0"><span class="b-opt-t">${t}</span><span class="b-opt-d">${d}</span></span></button>`).join("")}</div>`
    + (a.open_goal === "faster" ? `<div class="stack-8"><span class="strong">Which distance</span>${seg(DISTS.map(([id, l]) => [id, l]), a.open_distance, "openDist")}</div>` : "");
}

function step3() {
  const a = B.answers, i = B.inputs;
  return heading("Which days work", i.none ? "Pick what fits your week." : "Filled in from the days you've run lately.")
    + `<div class="stack-8"><div class="b-split"><span class="strong">Days a week</span><span class="meta">${i.none ? "Three is a good start" : `You ran ${i.days} most weeks`}</span></div>${seg([[3, "3"], [4, "4"], [5, "5"], [6, "6"]], a.days, "days", "big")}</div>
    <div class="stack-8"><div class="b-split"><span class="strong">Days you can't run</span><span class="meta">Tap to mark</span></div>
      <div class="b-week">${DOW.map((l, d) => `<button type="button" class="b-day out ${a.out.includes(d) ? "on" : ""}" aria-pressed="${a.out.includes(d)}" data-b="out" data-v="${d}">${l}</button>`).join("")}</div></div>
    <div class="stack-8"><div class="b-split"><span class="strong">Long run day</span><span class="meta">${i.none ? "Most people pick a weekend day" : `Your long runs were on ${DAY_LONG[i.long_day ?? 6]}s`}</span></div>
      <div class="b-week">${DOW.map((l, d) => `<button type="button" class="b-day long ${a.long_day === d ? "on" : ""}" aria-pressed="${a.long_day === d}" ${a.out.includes(d) ? "disabled" : ""} data-b="long" data-v="${d}">${l}</button>`).join("")}</div></div>
    ${i.parkrun_day != null ? `<span class="meta" style="font-size:14px;line-height:1.5">${DAY_LONG[i.parkrun_day]} parkrun counts as one of your days. It stays easy in the weeks before a race.</span>` : ""}`;
}

function amountSpark(from, to, flat, lo, hi) {
  const y = (v) => 36 - (v - lo) / Math.max(1, hi - lo) * 32;
  const pts = [...Array(8)].map((_, k) => `${k * 13},${y(flat ? from + (k % 2) : from + (to - from) * k / 7).toFixed(1)}`);
  return { pts: pts.join(" "), ey: y(flat ? from + 1 : to).toFixed(1) };
}

function step4() {
  const a = B.answers, p = B.plan || {};
  const opts = p.amounts || [];
  const lo = Math.min(a.avg_km, ...opts.map((o) => o.peak)) - 4, hi = Math.max(a.avg_km, ...opts.map((o) => o.peak));
  const hidden = a.level === "new" ? "Build more isn't offered on a first plan. It opens up once you've finished one."
    : a.injury === "niggle" || a.injury === "back" ? "Build more isn't offered while you're carrying a niggle." : "";
  return heading("How much to build", `You're running ${a.avg_km} km a week. The line shows each option from now to its peak.`)
    + (opts.length ? `<div class="stack-8" role="radiogroup">${opts.map((o) => { const on = a.amount === o.id; const s = amountSpark(a.avg_km, o.peak, o.id === "keep", lo, hi);
      return `<button type="button" class="b-opt b-amount" ${radio(on)} data-b="amount" data-v="${o.id}">${ring(on)}
        <span class="stack-tight" style="gap:3px;min-width:0"><span class="b-opt-top"><span class="b-opt-t">${esc(o.t)}</span>${o.rec ? `<span class="b-tag">SUGGESTED</span>` : ""}</span><span class="b-opt-d">${esc(o.d)}</span></span>
        <svg width="92" height="40" viewBox="0 0 92 40" aria-hidden="true" style="overflow:visible"><polyline points="${s.pts}" fill="none" stroke="${on ? "var(--ink)" : "var(--muted)"}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/><circle cx="91" cy="${s.ey}" r="3" fill="${on ? "var(--ink)" : "var(--muted)"}"/></svg></button>`; }).join("")}</div>`
      : p.failure ? amber(esc(p.failure)) : `<div class="skeleton" style="min-height:220px"></div>`)
    + (hidden && opts.length ? `<span class="meta" style="font-size:14px;line-height:1.5">${hidden}</span>` : "");
}

function step5() {
  const a = B.answers;
  const back = Math.round(Math.max(6, a.avg_km) * 0.7);
  const lines = a.injury === "niggle" ? ["A gentler build: about 5% a week, not 10%.", "Easy running only for the first 3 weeks.", "Build more is off. You can change this later."]
    : a.injury === "back" ? [`Starts at ${back} km a week, below your recent average.`, "Easy running only for the first 3 weeks.", "An easier week every third week."] : [];
  return heading("Anything hurting?", "The plan builds more gently if something's not right.")
    + `<div class="stack-8" role="radiogroup">${INJURIES.map(([id, t, d]) => `<button type="button" class="b-opt" ${radio(a.injury === id)} data-b="injury" data-v="${id}">${ring(a.injury === id)}<span class="stack-tight" style="gap:3px;min-width:0"><span class="b-opt-t">${t}</span><span class="b-opt-d">${d}</span></span></button>`).join("")}</div>`
    + (lines.length ? `<div class="card b-card" style="gap:8px"><span class="strong">What changes</span>${lines.map((l) => `<span class="b-change">${esc(l)}</span>`).join("")}</div>` : "");
}

function fasterNote() {
  const a = B.answers, i = B.inputs;
  if (a.days < 4) return `Needs 4 or more days a week and heart-rate data. You picked ${a.days} days.`;
  if (!i.faster_ok) return "Needs heart-rate data from your Garmin, which it doesn't have yet.";
  if (a.level === "new") return "Opens up once you've finished a plan.";
  if (a.injury === "niggle" || a.injury === "back") return "Not while you're easing back.";
  return "";
}

function step6() {
  const a = B.answers, p = B.plan || {};
  const off = fasterNote() !== "" || p.faster_ok === false;
  return heading("How you like to train", "All three get you to the start line. They differ in how hard the hard days are.")
    + `<div class="stack-8" role="radiogroup">${STYLE_OPTS.map((o) => { const dis = o.id === "faster" && off; const on = a.style === o.id && !dis;
      const note = dis ? fasterNote() || "Not offered for this plan." : o.id === "pick" && on ? p.picked || "" : "";
      return `<button type="button" class="b-opt ${dis ? "dis" : ""}" ${radio(on)} ${dis ? 'aria-disabled="true"' : ""} data-b="style" data-v="${o.id}">${ring(on, dis)}
        <span class="stack-tight" style="gap:4px;min-width:0"><span class="b-opt-t">${o.t}</span><span class="b-opt-d">${o.d}</span>
        ${o.based ? `<span class="b-based">${o.based}</span>` : ""}${note ? `<span class="b-note ${dis ? "dim" : ""}">${esc(note)}</span>` : ""}</span></button>`; }).join("")}</div>`;
}

function chart(pv) {
  const weeks = pv.weeks, n = weeks.length, sel = B.sel ?? pv.peak_week - 1;
  const top = Math.max(...weeks.map((w) => w.km));
  const ymax = Math.ceil((top + 4) / 10) * 10, H = 136;
  const y = (v) => Math.round(v / ymax * H);
  const grid = [20, 40, 60].filter((g) => g < ymax);
  const cols = `repeat(${n},minmax(0,1fr))`;
  // Each month named under its first week, unless the last name is too close to fit beside it.
  let month = null, lastAt = -9;
  const months = weeks.map((w, k) => { const m = w.month; const fresh = m !== month; month = m;
    if (!fresh) return "";
    const nextAt = weeks.findIndex((x, j) => j > k && x.month !== m);
    if (k === 0 && nextAt !== -1 && nextAt < 3) return "";
    if (k - lastAt < 3) return "";
    lastAt = k; return m; });
  return `<div class="card b-chart"><div class="b-split" style="align-items:flex-end"><span class="stack-tight" style="gap:2px"><span class="meta">Weekly distance</span>
      <span class="b-big" style="font-size:22px">${pv.start_km} to ${pv.peak_km} km</span></span><span class="meta" style="font-size:12.5px;text-align:right">Peaks in week ${pv.peak_week}</span></div>
    <div class="b-plot"><div class="b-area">${grid.map((g) => `<span class="b-grid" style="bottom:${y(g)}px"></span>`).join("")}
        <span class="b-now" style="bottom:${y(pv.now_km)}px"></span>
        <div class="b-bars" id="b-bars" style="grid-template-columns:${cols}">${weeks.map((w, k) => `<button type="button" class="b-bar ${k === sel ? "on" : ""} ${w.race ? "race" : ""}" aria-label="Week ${w.n}, ${w.km} km" aria-pressed="${k === sel}" data-b="week" data-v="${k}">
          ${w.race ? `<i class="b-dot ${w.race === "A" ? "a" : "b"}"></i>` : ""}<span style="height:${Math.max(4, y(w.km))}px"></span></button>`).join("")}</div></div>
      <div class="b-axis">${grid.map((g) => `<span style="bottom:${y(g) - 6}px">${g}</span>`).join("")}</div>
      <div class="b-months" style="grid-template-columns:${cols}">${months.map((m) => `<span>${m}</span>`).join("")}</div><span></span>
      <div class="b-phases" style="grid-template-columns:${pv.phases.map((p) => `${p.weeks}fr`).join(" ")}">${pv.phases.map((p) => `<span class="${p.weeks <= 2 ? "tight" : ""}" title="${esc(p.label)}">${esc(p.weeks < 2 ? p.label.slice(0, 2) : p.label)}</span>`).join("")}</div><span></span></div>
    <div class="legend"><span><i class="b-dash"></i>You now, ${pv.now_km} km</span><span><i class="b-dot a" style="width:7px;height:7px;border-radius:999px"></i>Race week</span><span>Tap a week</span></div></div>`;
}

function weekCard(pv) {
  const weeks = pv.weeks, k = B.sel ?? pv.peak_week - 1, w = weeks[k], n = weeks.length;
  const dlt = k ? w.km - weeks[k - 1].km : w.km - pv.now_km;
  const delta = k === 0 ? (dlt === 0 ? "Same as you run now" : `${dlt > 0 ? "+" : "−"}${Math.abs(dlt)} km on now`)
    : dlt > 0 ? `+${dlt} km on last week` : dlt < 0 ? `${w.phase === "Taper" ? "Tapering" : "Easier week"}: −${Math.abs(dlt)} km` : "Same as last week";
  const colour = k && dlt > 4 ? "var(--hard)" : k && dlt < 0 ? "var(--easy)" : "var(--muted)";
  const C = { race: "var(--race)", hard: "var(--hard)", long: "var(--long)", easy: "var(--easy)" };
  return `<div class="card b-wk"><div class="b-wk-nav"><button type="button" class="b-arrow" aria-label="Previous week" data-b="prev" ${k === 0 ? 'aria-disabled="true" style="opacity:.35"' : ""}>${ICON.left()}</button>
      <span class="b-wk-mid"><span class="mono-label" style="font-size:11px;letter-spacing:0.06em">WEEK ${w.n} OF ${n} · ${esc(w.phase.toUpperCase())}</span><span class="b-wk-dates">${esc(w.dates)}</span></span>
      <button type="button" class="b-arrow" aria-label="Next week" data-b="next" ${k === n - 1 ? 'aria-disabled="true" style="opacity:.35"' : ""}>${ICON.right(16)}</button></div>
    <div class="b-split"><span class="b-big" style="font-size:26px">${w.km} km</span><span class="meta" style="color:${colour};text-align:right">${esc(delta)}</span></div>
    <div>${w.sessions.map((r) => `<div class="b-sess"><i style="background:${C[r.kind]}"></i><span class="stack-tight" style="gap:1px;min-width:0"><span class="b-sess-type"><span style="color:${C[r.kind]}">${esc(r.type)}</span>${r.day ? `<span class="b-sess-day">${esc(r.day)}</span>` : ""}</span><span class="b-sess-name">${esc(r.name)}</span></span><span class="b-sess-v">${r.km} km</span></div>`).join("")}</div></div>`;
}

function whyList(pv) {
  return `<div class="stack-8"><span class="strong">Why it looks like this</span><div class="card b-list">${pv.whys.map((y, n) =>
    `<button type="button" class="b-whyrow ${n ? "" : "first"}" data-b="jump" data-v="${y.step}"><span class="stack-tight" style="gap:2px;min-width:0"><span class="mono-label" style="font-size:11px;letter-spacing:0.06em">${esc(y.k)}</span><span class="b-why-t">${esc(y.t)}</span></span>${ICON.right(16, "var(--muted)")}</button>`).join("")}</div></div>`
    + (pv.checks.length ? `<div class="card amber b-checks"><span class="strong">Worth a look</span>${pv.checks.map((c) => `<span class="b-check">${WARN.replace('width="18" height="18"', 'width="16" height="16"')}${esc(c)}</span>`).join("")}</div>` : "");
}

function step7() {
  const p = B.plan || {}, pv = p.preview;
  if (!pv) return p.failure ? heading("Your plan", "") + amber(esc(p.failure)) : `<div class="skeleton" style="min-height:60px;max-width:70%"></div><div class="skeleton" style="min-height:260px"></div><div class="skeleton"></div>`;
  const rp = B.replace && B.inputs.replacing;
  return heading(pv.title, pv.sub)
    + (rp ? `<span class="b-replace">Replaces the ${rp.count} sessions of your current plan from ${esc(rp.from)}. Weeks you've already run stay.</span>` : "")
    + chart(pv) + weekCard(pv)
    + (desktop() ? "" : whyList(pv))
    + `<span class="b-disclaim">General guidance for healthy adults, not medical advice. If you're unwell or pregnant, check with a doctor first.</span>`
    + `<label class="b-adult"><input type="checkbox" data-adult ${adult() ? "checked" : ""}> I'm 18 or over</label>`;
}

function hurtView() {
  return `<div class="b-centre"><div class="stack" style="gap:10px"><h1 class="b-h1">Get it looked at first</h1>
    <span class="b-lead">A plan won't help while something hurts. See a physio or your GP, and come back when you can run easy without pain. Your answers are saved.</span></div>
    <div class="stack-8"><a class="pill tall" href="https://www.csp.org.uk/public-patient/find-physio" target="_blank" rel="noopener">Find a physio near you</a>
    <a class="pill tall light" href="#/plan">Back to Plan</a></div></div>`;
}

function summary() {
  // Every step reached so far opens from here, back and forward: their answers are already drafted.
  const a = B.answers, now = STEP_OF[B.step] || 0, reached = Math.max(now, a.reached || 1);
  const races = a.open ? `No race · ${GOALS.find((g) => g[0] === a.open_goal)[1]}`
    : a.races.filter((r) => r.tier !== "C").sort((x, y) => x.date.localeCompare(y.date)).map((r) => `${r.name} (${r.tier})`).join(" · ") + (a.goal_time ? ` · goal ${a.goal_time}` : "");
  const amount = (B.plan?.amounts || []).find((o) => o.id === a.amount);
  const rows = [["1", "Your running", `${a.avg_km} km a week · longest ${a.longest_km} km`], ["2", "Races", races || "None marked"],
    ["3", "Days", `${a.days} a week · long run ${DOW[a.long_day]}`], ["4", "How much", amount ? amount.t : a.amount === "keep" ? `Around ${a.avg_km} km` : "Build steadily"],
    ["5", "Injuries", INJURIES.find((x) => x[0] === a.injury)[1]], ["6", "Style", STYLE_OPTS.find((x) => x.id === a.style).t]];
  return `<span class="strong">Your plan so far</span><div class="card b-list">${rows.map(([s, l, v], k) => { const n = k + 1, cur = n === now, done = n <= reached && !cur;
    return `<button type="button" class="b-sum ${cur ? "cur" : ""} ${k ? "" : "first"}" data-b="${n <= reached && !cur ? "jump" : ""}" data-v="${s}" ${n > reached ? "disabled" : ""} ${cur ? 'aria-current="step"' : ""}>
      <span class="b-sum-dot ${done ? "done" : cur ? "cur" : ""}">${done ? ICON.check(10) : ""}</span><span class="stack-tight" style="gap:2px;min-width:0"><span class="b-sum-l">${esc(l)}</span><span class="meta" style="line-height:1.4">${esc(done || cur ? v : "Not yet")}</span></span></button>`; }).join("")}</div>
    ${reached >= 7 && now < 7 ? `<button type="button" class="pill tall" data-b="jump" data-v="7">Back to your plan</button>` : ""}
    <span class="meta" style="line-height:1.5">Nothing goes to your watch until the last step.</span>`;
}

function footer() {
  const a = B.answers, s = B.step;
  const light = (l, act, flex = 1) => `<button type="button" class="pill tall light" style="flex:${flex}" data-b="${act}">${esc(l)}</button>`;
  const out = (l, act, flex = 1) => `<button type="button" class="pill tall" style="flex:${flex}" data-b="${act}">${esc(l)}</button>`;
  const as = a.races.filter((r) => r.tier === "A").length;
  const blocked = !a.open && goalRace() && (B.plan?.race_note || B.plan?.too_close);
  const typing = B.inputs.none && !(a.avg_km > 0);
  const buttons = { 1: typing ? light("Set my numbers", "edit") : B.inputs.none ? out("Change them", "edit") + light("Looks right", "next1", 1.2)
    : out("That's not right", "edit") + light("Looks right", "next1", 1.2),
    2: light(as > 1 && !a.open ? "Continue anyway" : "Looks right", "next2").replace("<button", blocked ? "<button disabled" : "<button"),
    "2c": light("Continue", "next2c"), 3: light("Looks right", "next3"), 4: light("Continue", "next4"), 5: light("Continue", "next5"),
    6: light("Continue", "next6"), 7: `<button type="button" class="pill tall red" style="flex:1" data-b="send" ${B.plan?.preview && adult() ? "" : "disabled"}>${B.replace && B.inputs.replacing ? "Replace my plan" : "Put it on my Garmin"}</button>` }[s];
  if (!buttons) return "";
  return `<div class="b-foot"><div class="b-foot-in"><div class="pills">${buttons}</div>${s === "7" ? `<span class="meta" style="font-size:12.5px;text-align:center">Nothing goes to your watch until you tap this.</span>` : ""}</div></div>`;
}

function drawBuild() {
  const s = B.step, n = STEP_OF[s];
  const a = B.answers;
  // Each step counted the first time it's reached, so the funnel shows where people stop.
  if ((n && n > (a.reached || 0)) || (s === "hurt" && !a.hurtSeen)) {
    if (s === "hurt") a.hurtSeen = true; else a.reached = n;
    const g = n === 7 && goalRace();
    track("build_step", { step: s, from: B.from, replace: B.replace ? "yes" : "no",
      ...(n === 7 ? { days: a.days, style: a.style, amount: a.amount, gentle: a.injury === "none" ? "no" : "yes", open: a.open ? "yes" : "no",
        races: a.races.filter((r) => !outside(r) && r.tier !== "C").length, level: a.level, typed: B.inputs.none ? "yes" : "no",
        dist: g ? distOf(g.km) : a.open ? a.open_distance || "none" : "none",
        goal: g ? (a.goal_time ? "time" : "no time") : a.open ? a.open_goal || "open" : "none",
        verdict: g && a.goal_time ? B.plan?.verdict?.kind : undefined } : {}) });
  }
  // What stopped them going on: a race too close or out of range, or a plan that couldn't be made.
  if (s === "2" && !a.open && goalRace() && (B.plan?.race_note || B.plan?.too_close)) buildDid("blocked", { reason: B.plan.race_note ? "out of range" : "too close" }, true);
  if (s === "7" && B.plan?.failure) buildDid("blocked", { reason: "no plan" }, true);
  saveAnswers();
  const body = { 1: step1, 2: step2, "2c": step2c, 3: step3, 4: step4, 5: step5, 6: step6, 7: step7, hurt: hurtView }[s]();
  const head = n ? `<div class="b-progress"><button type="button" class="b-back" aria-label="Back" data-b="back">${ICON.left()}</button>
      <div class="b-steps" role="progressbar" aria-valuemin="1" aria-valuemax="7" aria-valuenow="${n}" aria-label="Step ${n} of 7">${[1, 2, 3, 4, 5, 6, 7].map((k) => `<span class="${k <= n ? "on" : ""}"></span>`).join("")}</div>
      <span class="mono-label">${n} of 7</span></div>` : "";
  const side = desktop() && n ? `<aside class="b-side">${s === "7" && B.plan?.preview ? whyList(B.plan.preview) : s !== "7" ? summary() : ""}</aside>` : "";
  const y = window.scrollY;
  $("screen").innerHTML = `<div class="b-wrap ${side ? "with-side" : ""}"><div class="b-main">${head}<div class="b-body">${body}</div></div>${side}</div>${footer()}`;
  document.body.dataset.screen = "build";
  if (B.keepScroll) window.scrollTo(0, y);
  B.keepScroll = false;
  // A new step: its heading takes the focus, so a screen reader says where you are.
  if (B.drawn !== s) {
    B.drawn = s;
    const h = document.querySelector(".b-head h1, .b-centre h1");
    if (h) { h.tabIndex = -1; h.focus({ preventScroll: true }); }
  }
  wireBuild();
  askAhead();
}

function editSheet() {
  const a = B.answers;
  const fresh = B.inputs.none && !(a.avg_km > 0);
  const draft = fresh ? { avg_km: 20, longest_km: 8, level: a.level } : { avg_km: a.avg_km, longest_km: a.longest_km, level: a.level };
  const body = () => `<span class="b-grip"></span><div class="b-split" style="align-items:center"><span class="strong" id="sheet-title" style="font-size:18px;font-weight:700">Adjust your numbers</span>
      <button type="button" class="b-close" aria-label="Close" data-close>${svg('<path d="M18 6 6 18"/><path d="m6 6 12 12"/>')}</button></div>
    ${[["avg_km", "Weekly average", B.inputs.none ? "A usual week lately" : "Last 4 weeks"], ["longest_km", "Longest recent run", "Last 6 weeks"]].map(([k, l, sub]) => `<div class="b-split" style="align-items:center">
      <span class="stack-tight" style="gap:2px"><span class="strong">${l}</span><span class="meta">${sub}</span></span>
      <div class="b-stepper"><button type="button" aria-label="Less" data-s="${k}" data-d="-1">−</button><span aria-live="polite">${draft[k]} km</span><button type="button" aria-label="More" data-s="${k}" data-d="1">+</button></div></div>`).join("")}
    <div class="stack-8"><span class="strong">Running regularly for</span>${seg(LEVELS, draft.level, "level", "small")}</div>
    <span class="meta" style="line-height:1.45">${B.inputs.none ? "Only for this plan. Once Garmin has your runs, the next plan starts from those." : "Only changes this plan. Garmin keeps its own numbers."}</span>
    <button type="button" class="pill tall light" data-save>Save</button>`;
  sheet(body(), (holder, close) => {
    const dialog = holder.querySelector(".sheet");
    dialog.setAttribute("aria-label", "Adjust your numbers");
    const wire = () => {
      dialog.querySelectorAll("[data-s]").forEach((b) => { b.onclick = () => { const k = b.dataset.s;
        draft[k] = Math.max(0, Math.min(k === "avg_km" ? 250 : 60, draft[k] + Number(b.dataset.d))); dialog.innerHTML = body(); wire(); dialog.querySelector(`[data-s="${k}"][data-d="${b.dataset.d}"]`).focus(); }; });
      dialog.querySelectorAll('[data-b="level"]').forEach((b) => { b.onclick = () => { draft.level = b.dataset.v; dialog.innerHTML = body(); wire(); }; });
      dialog.querySelector("[data-close]").onclick = close;
      dialog.querySelector("[data-save]").onclick = () => { Object.assign(a, draft); saveAnswers(); close(); buildDid("numbers", { typed: B.inputs.none ? "yes" : "no" }); drawBuild(); };
    };
    wire();
  });
}

function addRaceSheet() {
  let dist = "10k";
  const min = B.inputs.window?.from || B.inputs.today, max = B.inputs.window?.to || "";
  const body = () => `<span class="b-grip"></span><div class="b-split" style="align-items:center"><span class="strong" id="sheet-title" style="font-size:18px;font-weight:700">Add a race</span>
      <button type="button" class="b-close" aria-label="Close" data-close>${svg('<path d="M18 6 6 18"/><path d="m6 6 12 12"/>')}</button></div>
    <label class="stack-8"><span class="field-label">Name</span><input class="field" id="race-name" maxlength="60" placeholder="Riverside 10K"></label>
    <label class="stack-8"><span class="field-label">Date</span><input class="field" id="race-date" type="date" min="${min}" ${max ? `max="${max}"` : ""}></label>
    <div class="stack-8"><span class="field-label">Distance</span>${seg([...DISTS.map(([id, l]) => [id, l]), ["ultra", "Ultra"]], dist, "dist", "small")}</div>
    <label class="stack-8" id="ultra-km" hidden><span class="field-label">How far</span>
      <span class="b-km"><input class="field" id="race-km" type="number" inputmode="numeric" min="43" max="250" step="1" value="50"><span class="meta">km</span></span></label>
    <button type="button" class="pill tall light" data-save>Add it</button>`;
  sheet(body(), (holder, close) => {
    const dialog = holder.querySelector(".sheet");
    dialog.setAttribute("aria-label", "Add a race");
    dialog.querySelector("#race-name").focus();
    const wire = () => {
      dialog.querySelectorAll('[data-b="dist"]').forEach((b) => { b.onclick = () => { dist = b.dataset.v;
        dialog.querySelectorAll('[data-b="dist"]').forEach((x) => x.setAttribute("aria-checked", String(x.dataset.v === dist)));
        dialog.querySelector("#ultra-km").hidden = dist !== "ultra";
        if (dist === "ultra") dialog.querySelector("#race-km").focus(); }; });
      dialog.querySelector("[data-close]").onclick = close;
      dialog.querySelector("[data-save]").onclick = () => {
        const name = dialog.querySelector("#race-name").value.trim(), when = dialog.querySelector("#race-date").value;
        if (!name || !/^\d{4}-\d{2}-\d{2}$/.test(when)) { toast("Give the race a name and a date."); return; }
        if (when < min || (max && when > max)) { toast(B.inputs.window?.said || "Pick a date from this plan's start to 30 weeks ahead."); return; }
        const ultra = dist === "ultra";
        const km = ultra ? Math.round(Number(dialog.querySelector("#race-km").value)) : DISTS.find((x) => x[0] === dist)[2];
        if (ultra && !(km >= 43 && km <= 250)) { toast("An ultra is 43 to 250 km."); return; }
        const a = B.answers;
        const id = `u${Date.now()}`;
        a.races.push({ id, name, date: when, km, meta: `${ultra ? `Ultra · ${km} km` : DISTS.find((x) => x[0] === dist)[1]} · added`, tier: km >= 10 ? "B" : "C" });
        if (!a.races.some((r) => r.tier === "A") && km >= 10) { a.races.find((r) => r.id === id).tier = "A"; a.open = false; }
        settle(id); saveAnswers(); close(); buildDid("add race", { dist: distOf(km) }); drawBuild(); refreshBuild();
      };
    };
    wire();
  });
}

function wireBuild() {
  const bars = $("b-bars");
  if (bars) bars.onkeydown = (e) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)) return;
    e.preventDefault();
    const n = B.plan.preview.weeks.length, k = B.sel ?? 0;
    B.sel = e.key === "Home" ? 0 : e.key === "End" ? n - 1 : Math.max(0, Math.min(n - 1, k + (e.key === "ArrowLeft" ? -1 : 1)));
    buildDid("chart", {}, true);
    B.keepScroll = true; drawBuild(); $("b-bars").querySelectorAll("button")[B.sel].focus();
  };
}

function buildAction(act, el) {
  const a = B.answers, v = el.dataset.v;
  const redraw = (ask) => { saveAnswers(); B.keepScroll = true; drawBuild(); if (ask) refreshBuild(); };
  switch (act) {
    case "back": { buildDid("back"); const prev = PREV_OF[B.step]; if (B.step === "1") location.hash = "#/plan"; else if (B.step === "3") go(a.open ? "2c" : "2"); else go(prev); return; }
    case "edit": return editSheet();
    case "next1": return go("2");
    case "next2": if (!goalRace()) { a.open = true; saveAnswers(); return go("2c"); } a.open = false; saveAnswers(); return go("3");
    case "next2c": return go("3");
    case "next3": return go("4");
    case "next4": return go("5");
    case "next5": return go(a.injury === "hurts" ? "hurt" : "6");
    case "next6": return go("7");
    case "tier": { const r = a.races.find((x) => x.id === el.dataset.id); if (!r) return; r.tier = v; r.demoted = false; if (v === "A") a.open = false; settle(r.id); return redraw(true); }
    case "fixA": { const r = a.races.find((x) => x.id === el.dataset.id); if (r) { r.tier = "B"; settle(r.id); } return redraw(true); }
    case "goal": a.goal_time = v === "No time" ? null : v; return redraw(true);
    case "noRace": a.open = true; saveAnswers(); return go("2c");
    case "addRace": return addRaceSheet();
    case "openGoal": a.open_goal = v; return redraw();
    case "openDist": a.open_distance = v; return redraw();
    case "days": a.days = Number(v); if (7 - a.out.length < a.days) a.out = a.out.slice(0, 7 - a.days); return redraw();
    case "out": { const d = Number(v); if (a.out.includes(d)) a.out = a.out.filter((x) => x !== d);
      else { if (6 - a.out.length < a.days) { toast("That leaves fewer days than you want to run. Pick fewer days first."); return; }
        a.out = [...a.out, d].sort(); if (a.long_day === d) a.long_day = [6, 5, 0, 1, 2, 3, 4].find((x) => !a.out.includes(x)); }
      return redraw(); }
    case "long": a.long_day = Number(v); return redraw();
    case "amount": a.amount = v; return redraw();
    case "injury": a.injury = v; if (v !== "none" && a.amount === "more") a.amount = "build"; return redraw();
    case "style": if (el.getAttribute("aria-disabled") === "true") return; a.style = v; return redraw(v === "pick");
    case "week": buildDid("chart", {}, true); B.sel = Number(v); B.keepScroll = true; drawBuild(); $("b-bars").querySelectorAll("button")[B.sel].focus(); return;
    case "prev": case "next": { buildDid("chart", {}, true); const n = B.plan.preview.weeks.length; B.sel = Math.max(0, Math.min(n - 1, (B.sel ?? 0) + (act === "prev" ? -1 : 1)));
      B.keepScroll = true; drawBuild(); document.querySelector(`[data-b="${act}"]`).focus(); return; }
    case "jump": buildDid(el.closest(".b-whyrow") ? "why" : "jump", { to: v }); return go(v);
    case "send": buildDid("send", { replace: B.replace ? "yes" : "no" }); return sendPlan(false);
    case "retry": buildDid("retry", { step: "error" }); return sendPlan(true);
    case "toPreview": buildDid("back to preview", { step: "error" }); return go("7");
    case "seePlan": { const to = B.landing || "#/plan";
      buildDid("landing", { step: "done", to: to.startsWith("#/today") || to === "#/" ? "today" : to.includes("week=") ? "week" : "plan" });
      B.inputs = null; B.from = null; B.plan = null; B.job = null; B.replace = false; B.landing = null; B.clock = null; location.hash = to; return; }
    default:
  }
}

document.addEventListener("click", (e) => {
  if (document.body.dataset.screen !== "build") return;
  const el = e.target.closest && e.target.closest("#screen [data-b]");
  if (el && el.dataset.b) buildAction(el.dataset.b, el);
});

async function sendPlan(retry) {
  try {
    B.job = await api("build/send", { method: "POST", body: JSON.stringify({ answers: sendable(), retry, replace: !!B.replace, from: B.from }) });
    B.clock = { at: Date.now(), sent: B.job.sent || 0, moved: Date.now() };
  } catch (e) {
    if (e instanceof SignedOut) return route();
    return toast(e.message);
  }
  go("sending");
}

// How long is left, from how fast the sessions have gone on so far.
function timeLeft(j) {
  const c = B.clock || (B.clock = { at: Date.now(), sent: j.sent || 0, moved: Date.now() });
  const now = Date.now(), sent = j.sent || 0;
  if (sent > c.lastSent || c.lastSent === undefined) { c.lastSent = sent; c.moved = now; }
  if (now - c.moved > 20000) return "Taking longer than usual";
  const done = sent - c.sent;
  if (done < 3) return j.total > 60 ? "About a minute or two" : "About a minute";
  const secs = (now - c.at) / done * (j.total - sent) / 1000;
  if (secs < 10) return "Nearly there";
  if (secs < 50) return `About ${Math.ceil(secs / 10) * 10} seconds left`;
  const mins = Math.round(secs / 60);
  return mins <= 1 ? "About a minute left" : `About ${mins} minutes left`;
}

function buildSendView(step) {
  clearTimeout(B.poll);
  const j = B.job;
  if (!j) {
    // Opened fresh on this screen: what the server has of the send, if anything.
    return api("build/send").then((d) => { if (d.state === "none") location.hash = "#/plan"; else { B.job = d; buildSendView(step); } },
      (e) => (e instanceof SignedOut ? route() : failed(e.message, () => buildSendView(step))));
  }
  const pct = Math.round((j.sent || 0) / Math.max(1, j.total) * 100);
  let body, foot = "";
  if (step === "sending") {
    body = `<div class="b-centre"><div class="stack-8"><span class="kicker">Sending to Garmin</span><h1 class="b-h1">Putting it on your Garmin</h1></div>
      <div class="stack-8"><div class="b-track" role="progressbar" aria-valuemin="0" aria-valuemax="${j.total}" aria-valuenow="${j.sent || 0}"><span style="width:${pct}%"></span></div>
      <div class="b-split meta" style="font-size:14px"><span>${j.sent || 0} of ${j.total} sessions</span><span aria-live="polite">${esc(timeLeft(j))}</span></div></div>
      <span class="meta" style="font-size:14px;line-height:1.5">You can leave this screen. It carries on, and Plan shows it when it's done.</span></div>`;
    B.poll = setTimeout(async () => {
      try { B.job = await api("build/send"); } catch (e) { if (e instanceof SignedOut) return route(); }
      if (!location.hash.startsWith("#/build/sending")) return;
      if (B.job.state === "done") { cache.clear(); store.drop(BUILD_KEY); go("done"); }
      else if (B.job.state === "error") go("error");
      else buildSendView("sending");
    }, 900);
  } else if (step === "done") {
    body = `<div class="b-centre"><span class="b-done">${ICON.check(26).replace('stroke-width="3.4"', 'stroke-width="3"')}</span><div class="stack-8"><h1 class="b-h1">On your watch at its next sync</h1>
      <span class="b-lead">${j.total} sessions over ${j.weeks} weeks. ${esc(j.first || "")}</span></div></div>`;
    // The first run within the week is what Today shows next; a plan starting later opens on its first week.
    const first = j.first_date ? day(j.first_date) : null;
    const soon = first && !j.replaced && (first - day(isoToday())) / 864e5 <= 7;
    const monday = first ? new Date(first.getFullYear(), first.getMonth(), first.getDate() - ((first.getDay() + 6) % 7)) : null;
    B.landing = soon ? "#/today" : monday ? `#/plan?week=${monday.getFullYear()}-${String(monday.getMonth() + 1).padStart(2, "0")}-${String(monday.getDate()).padStart(2, "0")}` : "#/plan";
    foot = `<div class="b-foot"><div class="b-foot-in"><div class="pills"><button type="button" class="pill tall light" data-b="seePlan">${soon ? "See your first run" : "See the first week"}</button></div></div></div>`;
  } else {
    body = `<div class="b-centre"><div class="stack-8"><h1 class="b-h1">Garmin didn't take the plan</h1><span class="b-lead">Nothing changed on your watch, and your answers are saved.</span></div>
      <div class="card amber b-amber">${WARN}<span class="stack-tight" style="gap:4px"><span style="font-size:14.5px;line-height:1.45">${j.sent ? `Garmin was busy and turned it away after ${j.sent} of ${j.total} sessions. Trying again sends the rest.` : "Garmin turned it away before any sessions went on. Try again in a moment."}</span>
      <span class="mono-label" style="font-size:11.5px">${esc(j.said || "GARMIN DIDN'T ANSWER")}</span></span></div></div>`;
    foot = `<div class="b-foot"><div class="b-foot-in"><div class="pills"><button type="button" class="pill tall" data-b="toPreview">Back to preview</button><button type="button" class="pill tall light" style="flex:1.2" data-b="retry">Try again</button></div></div></div>`;
  }
  $("screen").innerHTML = `<div class="b-wrap"><div class="b-main"><div class="b-body">${body}</div></div></div>${foot}`;
  document.body.dataset.screen = "build";
}

// -- routing ------------------------------------------------------------------

async function route(stay) {
  calm = stay === true;
  $("sheet").innerHTML = "";
  $("screen").classList.remove("stale");
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
  if (!panelled) { $("panel").hidden = true; $("panel").innerHTML = ""; if (!calm) window.scrollTo(0, 0); }
  if (screen === "session" && /^\d{4}-\d{2}-\d{2}$/.test(arg || "")) return wide ? showPanel("session", arg, params) : showSession(arg, params);
  if (screen === "run" && /^\d+$/.test(arg || "")) return wide ? showPanel("run", arg, params) : showRun(arg, params);
  if (screen === "plan") return showPlan(arg === "block" ? "block" : "week", params);
  if (screen === "crew") return showCrew(params);
  if (screen === "you") return showYou();
  if (screen === "build") return showBuild(arg, params);
  return showToday();
}

// Back from the background (a home-screen app left open overnight, a tab
// switched back to): the screen catches up on its own, without the skeleton,
// once it has been away a minute or the day has turned (Nov 13).
const RESUME_AFTER_MS = 60 * 1000;
let awayAt = 0, awayDay = "";
document.addEventListener("visibilitychange", () => {
  if (document.hidden) { awayAt = Date.now(); awayDay = serverDay(); return; }
  if (!awayAt || !token.access) return;
  const long = Date.now() - awayAt >= RESUME_AFTER_MS || serverDay() !== awayDay;
  awayAt = 0;
  const screen = document.body.dataset.screen;
  if (!long || dragging || document.querySelector("dialog[open]") || $("sheet").innerHTML) return;
  if (["today", "plan", "crew", "you"].includes(screen)) route(true);
});

// -- pull to refresh (Nov 13) -----------------------------------------------
// At the top of a screen, pulling down drags the page with some resistance and
// draws a ring that fills as it goes; past the line it gives a tick (where the
// phone can), and letting go spins the ring while Garmin's latest comes in,
// then shows a check and springs back. The browser's own pull (Chrome's reload)
// is held off by overscroll-behavior in the CSS.
const PULL_AT = 72, PULL_MAX = 128, PULL_LEAST_MS = 650;
function pullToRefresh() {
  const ring = document.createElement("div");
  ring.id = "ptr";
  ring.setAttribute("aria-hidden", "true");
  ring.innerHTML = `<svg class="ptr-ring" viewBox="0 0 32 32" width="26" height="26"><circle class="track" cx="16" cy="16" r="11"/><circle class="arc" cx="16" cy="16" r="11" pathLength="100"/></svg><span class="tick-done">${ICON.check(16)}</span>`;
  document.body.appendChild(ring);
  const live = document.createElement("p");
  live.className = "sr-only"; live.setAttribute("role", "status");
  document.body.appendChild(live);
  const arc = ring.querySelector(".arc");
  let y0 = null, x0 = 0, dy = 0, busy = false, armed = false, t0 = 0, onRow = false;
  // Rubber band: the further it goes, the harder it pulls back.
  const band = (d) => PULL_MAX * (1 - Math.exp(-d / (PULL_MAX * 1.6)));
  const spring = "cubic-bezier(.2, .9, .25, 1.12)";
  const place = (d, animate) => {
    const screen = $("screen");
    const t = animate ? `transform .5s ${spring}` : "none";
    screen.style.transition = t;
    screen.style.transform = d ? `translate3d(0, ${d}px, 0)` : "";
    const p = Math.min(1, d / PULL_AT);
    ring.style.transition = animate ? `transform .5s ${spring}, opacity .3s ease` : "none";
    ring.style.opacity = d ? String(Math.min(1, p * 1.5)) : "0";
    ring.style.transform = `translate3d(-50%, ${Math.round(d * 0.62) - 44}px, 0) scale(${(0.7 + 0.3 * p).toFixed(3)})`;
    if (!busy) { arc.style.strokeDasharray = `${(p * 86).toFixed(1)} 100`; ring.querySelector(".ptr-ring").style.transform = `rotate(${Math.round(p * 220)}deg)`; }
  };
  const blocked = () => busy || !token.access || desktop() || dragging || document.querySelector("dialog[open]") ||
    $("sheet").innerHTML || document.body.dataset.screen === "build" || window.scrollY > 0;
  addEventListener("touchstart", (e) => {
    if (e.touches.length !== 1 || blocked()) { y0 = null; return; }
    y0 = e.touches[0].clientY; x0 = e.touches[0].clientX; dy = 0; armed = false;
    t0 = Date.now(); onRow = !!(e.target.closest && e.target.closest("[data-movable]"));
  }, { passive: true });
  addEventListener("touchmove", (e) => {
    if (y0 === null) return;
    const down = e.touches[0].clientY - y0, across = Math.abs(e.touches[0].clientX - x0);
    // Scrolling up, sideways, or a row held long enough to lift (holdToMove): not a pull.
    const held = dy === 0 && onRow && Date.now() - t0 >= HOLD_MS - 60;
    if ((dy === 0 && (down <= 0 || across > down)) || held || dragging || window.scrollY > 0) {
      if (dy) place(0, true);
      y0 = null; return;
    }
    e.preventDefault();
    dy = band(down);
    place(dy, false);
    if (!armed && dy >= PULL_AT) { armed = true; ring.classList.add("armed"); navigator.vibrate?.(8); }
    else if (armed && dy < PULL_AT) { armed = false; ring.classList.remove("armed"); }
  }, { passive: false });
  const release = async () => {
    if (y0 === null) return;
    y0 = null;
    if (dy < PULL_AT) { place(0, true); return; }
    busy = true;
    ring.classList.add("spin");
    place(PULL_AT * 0.78, true);
    live.textContent = "Refreshing";
    const began = Date.now();
    pullFresh = document.body.dataset.screen || "today";
    try { await route(true); } catch { /* the screen says what went wrong */ } finally { pullFresh = null; }
    await new Promise((ok) => setTimeout(ok, Math.max(0, PULL_LEAST_MS - (Date.now() - began))));
    ring.classList.add("done");
    live.textContent = "Up to date";
    await new Promise((ok) => setTimeout(ok, 420));
    place(0, true);
    setTimeout(() => { ring.classList.remove("spin", "done", "armed"); busy = false; }, 500);
  };
  addEventListener("touchend", release);
  addEventListener("touchcancel", release);
}

async function start() {
  let message = null;
  if (location.pathname.startsWith("/app/callback")) message = await finishSignIn();
  if (message) return signInView(message);
  window.addEventListener("hashchange", route);
  route();
  pullToRefresh();
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/app/sw.js", { scope: "/app/" }).catch(() => {});
}

start();

// The app as something installed and come back to (Nov 26, PostHog flag pwa-v2), in a real browser:
// the install sheet at a moment that means something (never a first open, 14 days' quiet after Not now),
// iPhone's two taps drawn, the first open from the home screen counted as the install and followed by our
// own words before the phone's notification question, a notification's screen with a way to stop that kind,
// and app_opened with where the open came from. Android Chrome and iPhone Safari by their user agents; the
// home-screen app by its display mode. Run: node tests/app_pwa_test.js  (needs playwright and Chromium)

const fs = require("fs");
const path = require("path");
const { chromium } = require("playwright");

const DIR = path.join(__dirname, "..", "garmin_mcp", "app");
const SHOTS = path.join(__dirname, "..", "garmin-screens");
const TYPES = { ".js": "text/javascript", ".css": "text/css", ".html": "text/html", ".webmanifest": "application/json", ".svg": "image/svg+xml", ".png": "image/png", ".woff2": "font/woff2" };
let failed = 0;
function check(name, ok, detail) {
  console.log(`  ${ok ? "PASS" : "FAIL"}  ${name}${!ok && detail ? " — " + String(detail).slice(0, 400) : ""}`);
  if (!ok) failed += 1;
}

const TODAY = "2026-11-26";
const IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Mobile/15E148 Safari/604.1";
const ANDROID = "Mozilla/5.0 (Linux; Android 15; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36";
const TODAY_SCREEN = { today: TODAY, kicker: "THU 26 NOV", headline: "This week", assistant: "Claude",
  next: { date: TODAY, kicker: "NEXT · TODAY", name: "Easy 12 km", type: "easy", type_label: "Easy", colour: "easy",
    stats: [], segments: [], on_watch: true, schedule_id: 7 },
  ask: { claude: "https://claude.ai/new?q=x", chatgpt: "https://chatgpt.com/?q=x", claude_app: "claude://claude.ai/new?q=x" } };
const WEEK = { week: { title: "Week 4", range: "23–29 Nov", current: true, starts: "2026-11-23", days: [
  { date: "2026-11-23", dow: "MON", n: 23, sessions: [] },
  { date: TODAY, dow: "THU", n: 26, today: true, sessions: [{ name: "Easy 12 km", type_label: "Easy", colour: "easy", state: "next", schedule_id: 7, watch: true }] },
  ...[27, 28, 29].map((n) => ({ date: `2026-11-${n}`, dow: "X", n, sessions: [] }))] },
  block: null, assistant: "Claude", ask: { claude: "https://claude.ai/new?q=x" } };

async function open(browser, hash, o = {}) {
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, serviceWorkers: "block",
    userAgent: o.ua || ANDROID, deviceScaleFactor: o.scale || 1, hasTouch: true, isMobile: true });
  await ctx.addInitScript((o) => {
    localStorage.setItem("daash.access", "x"); localStorage.setItem("daash.refresh", "y");
    if (o.opens) localStorage.setItem("daash.open.count", String(o.opens));
    Object.entries(o.stored || {}).forEach(([k, v]) => localStorage.setItem(k, v));
    if (o.standalone) {
      const mm = window.matchMedia.bind(window);
      window.matchMedia = (q) => /standalone/.test(q) ? { matches: true, media: q, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} } : mm(q);
      Object.defineProperty(navigator, "standalone", { get: () => true });
    }
    // Notifications as a phone has them: asked once, granted; a push address handed back.
    window.__perm = "default";
    try { Object.defineProperty(Notification, "permission", { get: () => window.__perm }); } catch {}
    Notification.requestPermission = async () => (window.__perm = "granted");
    const sub = { endpoint: "https://push.example/x", unsubscribe: async () => true,
      toJSON: () => ({ endpoint: "https://push.example/x", keys: { p256dh: "BPp", auth: "au" } }) };
    Object.defineProperty(navigator, "serviceWorker", { configurable: true, value: { controller: null, addEventListener() {},
      register: async () => ({}), ready: Promise.resolve({ pushManager: { getSubscription: async () => null, subscribe: async () => sub } }) } });
  }, o);
  const posts = [], events = [];
  await ctx.route("**/*", async (r) => {
    const u = new URL(r.request().url());
    if (u.hostname !== "localhost") return r.fulfill({ status: 200, body: "" });
    const method = r.request().method();
    if (u.pathname === "/app/api/event") { events.push(JSON.parse(r.request().postData() || "{}")); return r.fulfill({ json: { ok: true } }); }
    if (u.pathname.startsWith("/app/api/")) {
      const key = `${method} ${u.pathname.replace("/app/api/", "")}`;
      if (method === "POST") posts.push({ key, body: JSON.parse(r.request().postData() || "{}") });
      if (key === "GET today") return r.fulfill({ json: TODAY_SCREEN });
      if (key === "GET plan") return r.fulfill({ json: WEEK });
      if (key === "GET push") return r.fulfill({ json: { key: "BEl62iUYgUivxIkv69yViEuiBIa", pwa: o.flag !== false || undefined,
        on_somewhere: false } });
      if (key === "POST push") {
        const b = JSON.parse(r.request().postData() || "{}");
        return r.fulfill({ json: b.kind ? { ok: true, kinds: { today: true, plan: true, weekly: false }, message: "Weekly summary off." }
          : { ok: true, message: "Notifications on. Today's session at 7am, how each run counted, and your week on Sunday evening. Change any of them in You." } });
      }
      return r.fulfill({ json: { error: "not in this test" } });
    }
    const fp = path.join(DIR, u.pathname.replace(/^\/app\/?/, "") || "index.html");
    if (fs.existsSync(fp) && fs.statSync(fp).isFile()) return r.fulfill({ body: fs.readFileSync(fp), contentType: TYPES[path.extname(fp)] || "application/octet-stream" });
    return r.fulfill({ status: 404, body: "" });
  });
  const page = await ctx.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto(`http://localhost/app/${o.search || ""}#${hash}`);
  await page.waitForTimeout(900);
  return { ctx, page, posts, events, errors };
}
const named = (p, n) => p.events.filter((e) => e.name === n).map((e) => e.properties);
async function planTwice(p) {
  for (const h of ["#/plan", "#/today", "#/plan"]) { await p.page.evaluate((h) => { location.hash = h; }, h); await p.page.waitForTimeout(500); }
  await p.page.waitForTimeout(1600);
}

(async () => {
  const browser = await chromium.launch();
  try {
    console.log("Android Chrome");
    let p = await open(browser, "/today", { opens: 0 });
    await planTwice(p);
    check("never on a first open, however often the plan is seen", !(await p.page.$("#sheet .sheet")));
    check("app_opened, from the web, not the installed app", JSON.stringify(named(p, "app_opened")) === JSON.stringify([{ source: "web", standalone: false }]),
      JSON.stringify(p.events));
    await p.ctx.close();

    p = await open(browser, "/today", { opens: 1 });
    await p.page.evaluate(() => { const e = new Event("beforeinstallprompt"); e.prompt = () => { window.__prompted = true; };
      e.userChoice = Promise.resolve({ outcome: "accepted" }); window.dispatchEvent(e); });
    await planTwice(p);
    const sheet = await p.page.textContent("#sheet").catch(() => "");
    check("second open, the plan seen twice: Put Daash on your home screen, with Install", sheet.includes("Put Daash on your home screen")
      && !!(await p.page.$('[data-pwa="install"]')), sheet);
    check("pwa_install_prompt_shown says the phone and the moment",
      JSON.stringify(named(p, "pwa_install_prompt_shown")) === JSON.stringify([{ platform: "android", trigger: "plan_views" }]), JSON.stringify(p.events));
    await p.page.click('[data-pwa="install"]');
    await p.page.waitForTimeout(300);
    check("Install opens Chrome's own prompt", await p.page.evaluate(() => window.__prompted === true));
    check("accepted, and installed, are counted", JSON.stringify(named(p, "pwa_install_prompt_result")) === JSON.stringify([{ result: "accepted", platform: "android" }])
      && named(p, "pwa_installed").some((x) => x.via === "prompt"), JSON.stringify(p.events));
    check("no old home-screen card beside it", !(await p.page.$('#nudge [data-nudge]')));
    check("no page threw an error", p.errors.length === 0, p.errors.join("; "));
    await p.ctx.close();

    p = await open(browser, "/today", { opens: 1, stored: { "daash.pwa.later": String(Date.now() - 3 * 86400000) } });
    await planTwice(p);
    check("Not now three days ago: quiet", !(await p.page.$("#sheet .sheet")));
    await p.ctx.close();

    console.log("\niPhone Safari");
    p = await open(browser, "/plan?source=card&tool=create_workout", { ua: IPHONE, opens: 1, scale: 2 });
    await p.page.waitForTimeout(1500);
    const ios = await p.page.textContent("#sheet").catch(() => "");
    check("from a chat answer that just sent a workout: the two taps, drawn", ios.includes("Add Daash to your home screen")
      && ios.includes("Tap Share") && ios.includes("Add to Home Screen") && !!(await p.page.$(".ios-bar .ios-share")), ios);
    check("app_opened says chat, and which tool", JSON.stringify(named(p, "app_opened")) === JSON.stringify([{ source: "chat", standalone: false, tool: "create_workout" }]),
      JSON.stringify(p.events));
    check("shown on the watch-send moment", named(p, "pwa_install_prompt_shown").some((x) => x.platform === "ios" && x.trigger === "watch_send"));
    check("the tool leaves the address once read", !(await p.page.evaluate(() => location.hash)).includes("tool="));
    await p.page.screenshot({ path: path.join(SHOTS, "pwa-ios-install.png") });
    await p.page.click('[data-pwa="ok"]');
    check("Got it is counted as acknowledged (iPhone can't say if it was added)",
      named(p, "pwa_install_prompt_result").some((x) => x.result === "acknowledged"));
    // Real Safari on iPhone has no PushManager outside the home-screen app, so no Remind me shows there at
    // all; with one faked here, Remind me leads to adding Daash, never to the phone's permission question.
    const remind = await p.page.$("[data-remind]");
    if (remind) { await remind.click(); await p.page.waitForTimeout(300); }
    check("no permission asked in Safari on iPhone: Remind me says to add Daash first",
      !named(p, "push_permission_prompt_shown").length && await p.page.evaluate(() => window.__perm === "default")
      && (!remind || (await p.page.textContent("#sheet")).includes("notifications come from the home-screen app")));
    await p.ctx.close();

    console.log("\nthe iPhone home-screen app, first open");
    p = await open(browser, "/today", { ua: IPHONE, standalone: true, search: "?source=pwa", scale: 2 });
    await p.page.waitForTimeout(1600);
    check("its first open from the home screen is the install", named(p, "pwa_installed").some((x) => x.platform === "ios" && x.via === "first_standalone_open"),
      JSON.stringify(p.events));
    check("app_opened says pwa, standalone", named(p, "app_opened").some((x) => x.source === "pwa" && x.standalone === true));
    const ask = await p.page.textContent("#sheet").catch(() => "");
    check("then our own words before the phone's question: what comes, at most one a day, never at night",
      ask.includes("A nudge from Daash?") && ask.includes("Today's session") && ask.includes("Never between 9pm and 7am"), ask);
    check("push_permission_prompt_shown after install", named(p, "push_permission_prompt_shown").some((x) => x.trigger === "installed"));
    await p.page.screenshot({ path: path.join(SHOTS, "pwa-push-ask.png") });
    await p.page.click('[data-push="on"]');
    await p.page.waitForTimeout(500);
    check("Turn on asks the phone, then gives Daash the address", named(p, "push_permission_result").some((x) => x.result === "granted" && x.trigger === "installed")
      && p.posts.some((x) => x.key === "POST push" && x.body.subscription && x.body.subscription.endpoint === "https://push.example/x"), JSON.stringify(p.posts));
    check("and says what's on", (await p.page.textContent("#toast")).includes("Today's session at 7am"));
    await p.ctx.close();

    console.log("\nopened from a notification");
    p = await open(browser, "/plan?from=push_weekly", { ua: ANDROID, standalone: true, stored: { "daash.pwa.standalone": "1", "daash.push.on": "1" } });
    await p.page.waitForTimeout(1200);
    check("push_clicked with its type", JSON.stringify(named(p, "push_clicked")) === JSON.stringify([{ type: "weekly" }]), JSON.stringify(p.events));
    check("app_opened says push", named(p, "app_opened").some((x) => x.source === "push"));
    const note = await p.page.textContent(".push-landing").catch(() => "");
    check("the screen it opens has a way to stop that kind", note.includes("Stop weekly summary notifications"), note);
    await p.page.click("[data-push-off]");
    await p.page.waitForTimeout(300);
    check("one tap switches weekly off, from here", p.posts.some((x) => x.key === "POST push" && x.body.kind === "weekly" && x.body.on === false && x.body.where === "landing")
      && (await p.page.textContent(".push-landing")).includes("Weekly summary off."), JSON.stringify(p.posts));
    await p.ctx.close();

    console.log("\nwithout the flag");
    p = await open(browser, "/today", { opens: 1, flag: false });
    await planTwice(p);
    check("none of it: no sheet, no install events", !(await p.page.$("#sheet .sheet")) && !named(p, "pwa_install_prompt_shown").length);
    check("app_opened still counts where opens come from", named(p, "app_opened").length === 1);
    await p.ctx.close();

    console.log("\ndesktop Chrome");
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 }, serviceWorkers: "block" });
    await ctx.close();
    p = await open(browser, "/today", { ua: "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36", opens: 1 });
    await planTwice(p);
    check("a computer without Chrome's install offer: nothing", !(await p.page.$("#sheet .sheet")));
    await p.page.evaluate(() => { const e = new Event("beforeinstallprompt"); e.prompt = () => {}; e.userChoice = Promise.resolve({ outcome: "dismissed" }); window.dispatchEvent(e); });
    await planTwice(p);
    check("with it: the sheet, for the computer", (await p.page.textContent("#sheet").catch(() => "")).includes("Put Daash on your computer"));
    await p.ctx.close();
  } finally {
    await browser.close();
  }
  console.log(failed ? `${failed} check(s) failed` : "all checks passed");
  process.exit(failed ? 1 : 0);
})();

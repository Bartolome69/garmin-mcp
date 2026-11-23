// Get your plan in a real browser (Nov 23): with nothing planned in the next two
// weeks, Today and Plan offer two equal ways to a plan in the order the server
// drew for this person; the chat way asks three things and opens Claude or
// ChatGPT with them written out; the app's way opens the builder. The app's
// files are served as they ship; its API is stubbed with the server's shapes.
//
// Run: node tests/app_getplan_test.js   (needs the playwright package and Chromium)

const fs = require("fs");
const path = require("path");
const { chromium } = require("playwright");

const DIR = path.join(__dirname, "..", "garmin_mcp", "app");
const TYPES = { ".js": "text/javascript", ".css": "text/css", ".html": "text/html", ".webmanifest": "application/json", ".svg": "image/svg+xml", ".png": "image/png" };

let failed = 0;
function check(name, ok, detail) {
  console.log(`  ${ok ? "PASS" : "FAIL"}  ${name}${!ok && detail ? " — " + String(detail).slice(0, 300) : ""}`);
  if (!ok) failed += 1;
}

const TODAY = "2026-11-23";
const todayEmpty = (first) => ({ today: TODAY, kicker: "MON 23 NOV", headline: "No plan on the calendar", empty: true, build: true, assistant: "ChatGPT",
  get_plan: { first, assistant: "ChatGPT", build: true, later: { date: "2026-12-14", said: "14 Dec", week: "2026-12-14" } },
  ask: { claude: "https://claude.ai/new?q=x", chatgpt: "https://chatgpt.com/?q=x" } });
const PLAN_EMPTY = { empty: true, build: true, assistant: "Claude", get_plan: { first: "app", assistant: "Claude", build: true } };
const FORM = { km: 32, days: [1, 3, 5, 6], long_day: 6, race: { race: "half", date: "2027-03-14", name: "Big Half" } };
const LINK = { prompt: "Use Daash to build me a training plan.", claude: "https://claude.ai/new?q=Use%20Daash", chatgpt: "https://chatgpt.com/?q=Use%20Daash",
  claude_app: "claude://claude.ai/new?q=Use%20Daash" };

async function open(browser, hash, answers, look = {}) {
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, serviceWorkers: "block",
    deviceScaleFactor: look.scale || 1, colorScheme: look.dark ? "dark" : "light" });
  await ctx.addInitScript(() => { localStorage.setItem("daash.access", "x"); localStorage.setItem("daash.refresh", "y"); });
  const posts = [];
  const events = [];
  await ctx.route("**/*", async (r) => {
    const u = new URL(r.request().url());
    if (u.hostname !== "localhost") return r.fulfill({ status: 200, body: "" });
    if (u.pathname === "/app/api/event") { events.push(JSON.parse(r.request().postData() || "{}")); return r.fulfill({ json: { ok: true } }); }
    if (u.pathname.startsWith("/app/api/")) {
      const key = `${r.request().method()} ${u.pathname.replace("/app/api/", "")}`;
      if (r.request().method() === "POST") posts.push({ key, body: JSON.parse(r.request().postData() || "{}") });
      const found = answers(key, posts);
      if (found) return r.fulfill({ status: found.status || 200, json: found.json });
      return r.fulfill({ json: { error: "not in this test" } });
    }
    const fp = path.join(DIR, u.pathname.replace(/^\/app\/?/, "") || "index.html");
    if (fs.existsSync(fp) && fs.statSync(fp).isFile()) return r.fulfill({ body: fs.readFileSync(fp), contentType: TYPES[path.extname(fp)] || "application/octet-stream" });
    return r.fulfill({ status: 404, body: "" });
  });
  const page = await ctx.newPage();
  await page.clock.install({ time: new Date(`${TODAY}T08:30:00Z`) });
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto(`http://localhost/app/#${hash}`);
  await page.waitForTimeout(1200);
  return { ctx, page, posts, errors, events };
}

(async () => {
  const browser = await chromium.launch();
  const shot = process.env.SHOT;
  try {
    console.log("Today, the chat way drawn first");
    let p = await open(browser, "/today", (key) => key === "GET today" ? { json: todayEmpty("chat") } : key === "POST plan-choice" ? { json: { ok: true } } : null,
      shot ? { scale: 2 } : {});
    await p.page.waitForSelector(".gp-opt");
    const opts = await p.page.$$eval(".gp-opt", (xs) => xs.map((x) => ({ o: x.dataset.planOption, pos: x.dataset.position,
      t: x.querySelector(".b-opt-t").textContent, d: x.querySelector(".b-opt-d").textContent, h: x.getBoundingClientRect().height, w: x.getBoundingClientRect().width })));
    check("two choices, the chat way first as drawn, with their words",
      opts.length === 2 && opts[0].o === "chat" && opts[0].t === "Build it with Claude or ChatGPT" && opts[0].d === "Best plans. Uses your Garmin history."
      && opts[1].o === "app" && opts[1].t === "Quick plan in the app" && opts[1].d === "No AI chat needed.", JSON.stringify(opts));
    check("the two are the same size", Math.abs(opts[0].h - opts[1].h) < 1 && Math.abs(opts[0].w - opts[1].w) < 1, JSON.stringify(opts));
    check("it says when the plan further out starts", (await p.page.textContent(".gp-later")).includes("sessions from 14 Dec"));
    check("plan_choice_shown is sent once, with the order", JSON.stringify(p.events.filter((e) => e.name === "plan_choice_shown"))
      === JSON.stringify([{ name: "plan_choice_shown", properties: { first: "chat", screen: "today" } }]), JSON.stringify(p.events));
    if (shot) await p.page.screenshot({ path: `${shot}/getplan-today.png` });
    await p.page.evaluate(() => { window.__hashes = []; addEventListener("hashchange", () => window.__hashes.push(location.hash)); });
    await p.page.click('[data-plan-option="app"]');
    await p.page.waitForTimeout(400);
    check("the app's way records the pick at its position and opens the builder from here",
      JSON.stringify(p.posts.map((x) => [x.key, x.body])) === JSON.stringify([["POST plan-choice", { option: "app", position: 2, screen: "today" }]])
      && (await p.page.evaluate(() => window.__hashes[0])) === "#/build?from=choice_today", JSON.stringify(p.posts) + JSON.stringify(await p.page.evaluate(() => [window.__hashes, location.hash])));
    check("no page threw an error", p.errors.length === 0, p.errors.join("; "));
    await p.ctx.close();

    console.log("Plan, the app's way drawn first, then the chat way's three steps");
    p = await open(browser, "/plan", (key) => key === "GET plan" ? { json: PLAN_EMPTY } : key === "POST plan-choice" ? { json: { ok: true } }
      : key === "GET plan-choice/form" ? { json: FORM } : key === "POST plan-choice/link" ? { json: LINK } : null, shot ? { scale: 2 } : {});
    await p.page.waitForSelector(".gp-opt");
    const order = await p.page.$$eval(".gp-opt", (xs) => xs.map((x) => x.dataset.planOption));
    check("the other person's draw puts the app's way first", JSON.stringify(order) === JSON.stringify(["app", "chat"]), JSON.stringify(order));
    if (shot) await p.page.screenshot({ path: `${shot}/getplan-plan.png` });
    await p.page.click('[data-plan-option="chat"]');
    await p.page.waitForSelector(".b-head h1");
    check("the chat way records its pick at position 2 and opens step 1",
      p.posts.some((x) => x.key === "POST plan-choice" && x.body.option === "chat" && x.body.position === 2 && x.body.screen === "plan")
      && (await p.page.textContent(".b-head h1")) === "What are you training for?");
    await p.page.waitForTimeout(400);
    check("Garmin's race on the calendar is filled in", (await p.page.getAttribute('[data-pc-race="half"]', "aria-checked")) === "true"
      && (await p.page.inputValue("#pc-date")) === "2027-03-14");
    if (shot) await p.page.screenshot({ path: `${shot}/getplan-step1.png` });
    await p.page.click('[data-pc="next"]');
    check("step 2 has the weekly km from Garmin", /32\s*km a week/.test(await p.page.textContent(".pc-km")) && /From your Garmin/.test(await p.page.textContent(".b-head")));
    await p.page.click('[data-pc-km="5"]');
    check("and it can be changed", /37\s*km a week/.test(await p.page.textContent(".pc-km")));
    if (shot) await p.page.screenshot({ path: `${shot}/getplan-step2.png` });
    await p.page.click('[data-pc="next"]');
    await p.page.waitForSelector("#pc-open[href]");
    const days = await p.page.$$eval("[data-pc-day]", (xs) => xs.filter((x) => x.getAttribute("aria-pressed") === "true").map((x) => x.textContent));
    check("step 3 has the usual days and the long-run day from Garmin",
      JSON.stringify(days) === JSON.stringify(["Tue", "Thu", "Sat", "Sun"]) && (await p.page.getAttribute('[data-pc-long="6"]', "aria-checked")) === "true", JSON.stringify(days));
    const sent = p.posts.filter((x) => x.key === "POST plan-choice/link").at(-1)?.body;
    check("the link is asked for with the answers as changed", sent && sent.race === "half" && sent.date === "2027-03-14" && sent.km === 37
      && JSON.stringify(sent.days) === JSON.stringify([1, 3, 5, 6]) && sent.long_day === 6, JSON.stringify(sent));
    check("it opens Claude, the assistant this person uses, until they pick another",
      (await p.page.textContent("#pc-open")).startsWith("Open Claude") && (await p.page.getAttribute("#pc-open", "href")) === LINK.claude);
    await p.page.click('[data-pc-to="chatgpt"]');
    check("picking ChatGPT changes the button and is remembered",
      (await p.page.textContent("#pc-open")).startsWith("Open ChatGPT") && (await p.page.getAttribute("#pc-open", "href")) === LINK.chatgpt
      && (await p.page.evaluate(() => localStorage.getItem("daash.plan_assistant"))) === "chatgpt");
    if (shot) await p.page.screenshot({ path: `${shot}/getplan-step3.png`, fullPage: true });
    const [popup] = await Promise.all([p.ctx.waitForEvent("page", { timeout: 3000 }).catch(() => null), p.page.click("#pc-open")]);
    await p.page.waitForTimeout(300);
    check("tapping it opens a new chat at once, not the back-to-my-chat sheet", !!popup && !(await p.page.$(".sheet .ask-choice, #ask-new")));
    const names = p.events.map((e) => [e.name, e.properties]);
    check("each step and the hand-off are counted, step 2 marked as filled in",
      JSON.stringify(names.filter(([n]) => n === "plan_form_step")) === JSON.stringify([["plan_form_step", { step: 1, screen: "plan" }],
        ["plan_form_step", { step: 2, screen: "plan", prefilled: true }], ["plan_form_step", { step: 3, screen: "plan" }]])
      && JSON.stringify(names.filter(([n]) => n === "plan_handoff_opened")) === JSON.stringify([["plan_handoff_opened", { assistant: "chatgpt", screen: "plan", race: "half" }]]),
      JSON.stringify(names));
    check("no page threw an error", p.errors.length === 0, p.errors.join("; "));
    await p.ctx.close();
  } finally {
    await browser.close();
  }
  console.log(failed ? `\n${failed} check(s) failed` : "\nall checks passed");
  process.exit(failed ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(1); });

// The plan card and the share page, in a real browser.
//
// Both ran only in people's chats until the card broke twice on one Sunday
// (Oct 25): a progress answer crashed it ("(w.extra || []).slice is not a
// function"), and an opened run drew every lap the same width. Neither was
// caught, because nothing here drew the card. This draws it the way a chat
// does, with a stand-in host answering its messages, and fails on any error
// the page throws.
//
// Run: node tests/card_test.js   (needs the playwright package and Chromium)

const fs = require("fs");
const os = require("os");
const path = require("path");
const { chromium } = require("playwright");

const ROOT = path.join(__dirname, "..");
const CARD = fs.readFileSync(process.env.CARD_FILE || path.join(ROOT, "garmin_mcp", "ui", "plan.html"), "utf8");
const SHARE = "file://" + path.join(ROOT, "docs", "share", "index.html");
const SESSION_CARD = fs.readFileSync(path.join(ROOT, "garmin_mcp", "ui", "session.html"), "utf8");

let failed = 0;
function check(name, ok, detail) {
  console.log(`  ${ok ? "PASS" : "FAIL"}  ${name}${!ok && detail ? " — " + String(detail).slice(0, 300) : ""}`);
  if (!ok) failed += 1;
}

const monday = (() => { const t = new Date(); t.setDate(t.getDate() - (t.getDay() + 6) % 7); return t; })();
const md = (o) => { const t = new Date(monday); t.setDate(t.getDate() + o); return t.toISOString().slice(0, 10); };

// A long run with three reps, as get_activity_details answers for it.
const DETAILS = {
  summary: { distance_km: 17.29, duration: "1h 21m 08s", duration_seconds: 4868, pace_per_km: "4:41 /km", location: "Tower Hamlets",
    heart_rate: { average_bpm: 144, max_bpm: 168 } },
  splits: [
    { split: 1, intensity: "warmup", distance_km: 6, duration: "30m 03s", pace_per_km: "5:01 /km", avg_hr: 132 },
    { split: 2, distance_km: 2, duration: "8m 02s", pace_per_km: "4:01 /km", avg_hr: 159 },
    { split: 3, intensity: "recovery", distance_km: 0.17, duration: "1m 00s", pace_per_km: "5:55 /km", avg_hr: 154 },
    { split: 4, distance_km: 2, duration: "8m 00s", pace_per_km: "4:00 /km", avg_hr: 163 },
    { split: 5, intensity: "recovery", distance_km: 0.17, duration: "1m 00s", pace_per_km: "5:45 /km", avg_hr: 157 },
    { split: 6, distance_km: 2, duration: "7m 59s", pace_per_km: "4:00 /km", avg_hr: 162 },
    { split: 7, intensity: "recovery", distance_km: 0.19, duration: "1m 00s", pace_per_km: "5:11 /km", avg_hr: 161 },
    { split: 8, intensity: "cooldown", distance_km: 4.76, duration: "24m 04s", pace_per_km: "5:03 /km", avg_hr: 142 },
  ],
  same_route: { this_run_rank: "1 of 4 by pace" },
};
const PLAN = {
  source: "calendar", current_plan: { label: "B1", goal: "Base block (Norwegian singles style): 4 weeks" },
  weeks: [{ week: 1, starts: md(0), current: true, sessions: [{ date: md(0), day: "Mon", name: "Full body (runner) v2", type: "strength",
    sport: "strength", kind: "strength", status: "done", activity_id: 8, actual: "38m 58s" }, { date: md(2), day: "Wed", name: "Long 18km w 3x2km sub-T", type: "long",
    sport: "run", status: "done", activity_id: 7, actual_km: 17.29, actual_pace: "4:41 /km", actual_bpm: 144, planned_km: 18.1 }] }],
  next_session: null,
};
// get_progress counts a week's unplanned runs where get_plan lists them.
const PROGRESS = {
  weeks: [
    { week_of: md(-14), current: false, planned: 3, completed: 3, complete: true, missed: [], extra: 1 },
    { week_of: md(-7), current: false, planned: 3, completed: 3, complete: true, missed: [], extra: 2 },
    { week_of: md(0), current: true, planned: 2, completed: 1, complete: false, missed: [], extra: 0 },
  ],
  completed: 7, planned: 8,
  sessions: [md(-14), md(-12), md(-10), md(-7), md(-5), md(-3), md(0)].map((d) => ({ date: d, done_on: d, name: "Easy 8k", sport: "run", actual_km: 8 })),
  missed: [], unplanned: [{ date: md(-6), name: "Run", sport: "run" }],
  upcoming: [{ date: md(3), name: "Threshold 3x2k", sport: "run" }],
};

// One session, as get_session answers for it: a threshold session changed a
// moment ago, on a week with a strength day and a long run.
const SESSION = {
  card: "session", date: md(2), today: md(1),
  week: { label: "Week 3", starts: md(0), done: 1, planned: 4, days: [
    { date: md(0), type: "strength", status: "done" }, { date: md(1) }, { date: md(2), type: "threshold", status: "ahead" },
    { date: md(3), type: "easy", status: "ahead" }, { date: md(4) }, { date: md(5) }, { date: md(6), type: "long", status: "ahead" }] },
  session: { name: "Sub-T 3 × 2 km", type: "threshold", kind: "run", status: "ahead", key: true, workout_id: 7 },
  totals: { km: 10.9, minutes: 49, rep: { label: "Rep pace /km", value: "3:58–4:08" }, estimated: true },
  segments: [{ k: "easy", km: 2, part: "wu", secs: 618 }, { k: "work", km: 2, secs: 486 }, { k: "rec", km: 0.29, secs: 90, by: "time" },
    { k: "work", km: 2, secs: 486 }, { k: "rec", km: 0.29, secs: 90, by: "time" }, { k: "work", km: 2, secs: 486 },
    { k: "rec", km: 0.29, secs: 90, by: "time" }, { k: "easy", km: 2, part: "cd", secs: 618 }],
  steps: [{ kind: "warmup", label: "Warm up", amount: "2 km" },
    { kind: "repeat", times: 3, steps: [{ kind: "interval", label: "Run", amount: "2 km", target: "3:58–4:08/km" }, { kind: "recovery", label: "Recover", amount: "90 s" }] },
    { kind: "cooldown", label: "Cool down", amount: "2 km" }],
  also: [{ name: "Core", type: "strength", kind: "strength" }],
  change: { was: "Sub-T 3 × 3 km", same_ends: true },
};

// The chat around the card: answers its handshake, sends it a tool result,
// answers the tools it calls, and keeps any link it asks to open.
function host(result, src = "card.html") {
  return `<!doctype html><body><iframe id=f style="width:420px;height:1100px;border:0"></iframe><script>
const f = document.getElementById("f"); window.opened = null; window.asked = [];
window.addEventListener("message", (e) => {
  const m = e.data; if (!m || !m.method) return;
  const reply = (r) => f.contentWindow.postMessage({ jsonrpc: "2.0", id: m.id, result: r }, "*");
  if (m.method === "ui/initialize") reply({ hostInfo: { name: "test" }, hostCapabilities: { openLinks: {} }, hostContext: { theme: "light", displayMode: "inline" } });
  if (m.method === "ui/notifications/initialized") f.contentWindow.postMessage({ jsonrpc: "2.0", method: "ui/notifications/tool-result", params: { structuredContent: ${JSON.stringify(result)}, content: [] } }, "*");
  if (m.method === "tools/call") reply({ structuredContent: m.params.name === "get_activity_details" ? ${JSON.stringify(DETAILS)}
    : m.params.name === "get_plan" ? { ...${JSON.stringify(PLAN)}, _card: "https://daash.test/card/seen?u=f&v=view2&s=sig" } : { error: "not in this test" } });
  if (m.method === "ui/open-link") { window.opened = m.params.url; reply({}); }
  if (m.method === "ui/message") { window.asked.push((m.params.content[0] || m.params.content).text); reply({}); }
});
f.src = ${JSON.stringify(src)};
</script></body>`;
}

function decode(url) {
  const b = url.split("#")[1].replace(/-/g, "+").replace(/_/g, "/");
  return JSON.parse(Buffer.from(b, "base64").toString("utf8"));
}

(async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "daash-card-"));
  // As the hosted server serves it: told where to say it opened.
  const OPENED = "<script>window.__DAASH_CARD_OPENED__ = \"https://daash.test/card/opened\";</script>\n";
  fs.writeFileSync(path.join(dir, "card.html"), CARD.replace("<script>", OPENED + "<script>"));
  const browser = await chromium.launch();
  const errors = [];
  // What the cards report back on the link their answer carries.
  const SEEN = "https://daash.test/card/seen?u=f&v=view1&s=sig";
  const pings = [];
  const listen = (page) => page.route("https://daash.test/**", (r) => {
    pings.push({ url: r.request().url(), body: JSON.parse(r.request().postData() || "{}") });
    return r.fulfill({ status: 204 });
  });
  async function open(result) {
    fs.writeFileSync(path.join(dir, "host.html"), host(result));
    const page = await browser.newPage({ viewport: { width: 420, height: 1100 } });
    page.on("pageerror", (e) => errors.push(e.message));
    await listen(page);
    await page.goto("file://" + path.join(dir, "host.html"));
    await page.waitForTimeout(1200);
    return { page, card: page.frames()[1] };
  }

  try {
    console.log("a progress answer");
    let { page, card } = await open({ ...PROGRESS, _card: SEEN });
    let text = await card.evaluate(() => document.body.innerText);
    check("draws the card rather than the error box", !/didn.t load|not a function/.test(text) && /This week|Week/.test(text), text.slice(0, 200));
    await page.close();

    const opened = pings.filter((p) => p.url.endsWith("/card/opened"));
    check("it says it opened, at once, before anything else",
      opened.length === 1 && opened[0].body.stage === "opened" && opened[0].body.card === "plan" && pings[0] === opened[0], JSON.stringify(opened));
    const seen = pings.filter((p) => !p.url.endsWith("/card/opened"));
    check("it reports the progress answer, then the plan it fetched for itself, each once",
      seen.map((p) => p.url.match(/v=(\w+)/)[1]).join() === "view1,view2" && seen.every((p) => p.body.ok), JSON.stringify(pings));
    pings.length = 0;

    console.log("an opened run");
    ({ page, card } = await open({ ...PLAN, _card: SEEN }));
    await page.waitForTimeout(8000);
    const waited = pings.filter((p) => p.body.stage === "waiting");
    pings.splice(0, pings.length, ...pings.filter((p) => !p.url.endsWith("/card/opened")));
    check("the card reports once that it drew, and never that it was still waiting, with the app and how long it took",
      waited.length === 0 && pings.length === 1 && pings[0].url === SEEN && pings[0].body.card === "plan" && pings[0].body.ok === true
      && /^test/.test(pings[0].body.host) && pings[0].body.ms > 0 && /result/.test(pings[0].body.steps), JSON.stringify(pings));
    await card.getByText(/sub-T/).first().click();
    await page.waitForTimeout(800);
    const bars = await card.evaluate(() => [...document.querySelectorAll(".splits span")].map((b) => ({
      rest: b.classList.contains("rest"), grow: +getComputedStyle(b).flexGrow, h: parseFloat(b.style.height) })));
    check("each lap is drawn", bars.length === 8, bars.length);
    check("the reps in colour and the rest in grey", bars.filter((b) => !b.rest).length === 3 && bars[0].rest && bars[7].rest, JSON.stringify(bars));
    check("each as wide as it took: the warm-up widest, a recovery narrowest",
      bars[0].grow > bars[1].grow && bars[1].grow > bars[2].grow, JSON.stringify(bars.map((b) => b.grow)));
    check("and as tall as it was fast", bars[1].h > bars[0].h && bars[0].h > bars[2].h, JSON.stringify(bars.map((b) => b.h)));
    check("with a dashed line at the average", await card.evaluate(() => !!document.querySelector(".splits i")));

    console.log("a card whose answer never comes");
    // Oct 30: Claude's Android app sent no answer to a card at all. The card
    // says how far it got, so it's plain whether the app ever spoke to it.
    fs.writeFileSync(path.join(dir, "silent.html"), `<!doctype html><body><iframe id=f style="width:420px;height:600px;border:0"></iframe><script>
const f = document.getElementById("f");
window.addEventListener("message", (e) => { const m = e.data; if (m && m.method === "ui/initialize")
  f.contentWindow.postMessage({ jsonrpc: "2.0", id: m.id, result: { hostInfo: { name: "silent" }, hostContext: {} } }, "*"); });
f.src = "card.html";</script></body>`);
    const silent = await browser.newPage();
    silent.on("pageerror", (e) => errors.push(e.message));
    const from = pings.length;
    await listen(silent);
    await silent.goto("file://" + path.join(dir, "silent.html"));
    await silent.waitForTimeout(8800);
    const told = pings.slice(from).map((p) => p.body);
    check("says it opened, then at 8 seconds that it's still waiting, after the app answered it",
      told.length === 2 && told[0].stage === "opened" && told[1].stage === "waiting" && /^silent/.test(told[1].host)
      && /init ok > ready sent$/.test(told[1].steps), JSON.stringify(told));
    await silent.close();

    console.log("sharing that run");
    await card.getByText("Share this run").click();
    await page.waitForTimeout(400);
    const url = await page.evaluate(() => window.opened);
    check("opens the share page", !!url && url.startsWith("https://garmin.daash.run/share/#"), url);
    const run = url ? decode(url) : {};
    check("carrying the run's name, numbers and laps",
      run.run === 1 && /3 × 2 km/.test(run.t) && run.f.length === 4 && run.l.length === 8 && run.k === "Base block", JSON.stringify(run).slice(0, 300));
    check("with the reps' paces and heart rates, and no route ranking",
      run.n.length === 1 && /4:01, 4:00, 4:00/.test(run.n[0][1]) && !JSON.stringify(run).includes("Fastest"), JSON.stringify(run.n));

    console.log("a gym session");
    await card.getByText("Full body (runner) v2").first().click();
    await page.waitForTimeout(800);
    const acts = await card.evaluate(() => [...document.querySelectorAll(".detail .act")].map((b) => b.textContent));
    check("asks about the activity, not a run, and offers no run image", acts.join() === "Ask about this activity", JSON.stringify(acts));
    await page.close();

    console.log("the share page");
    for (const [what, payload, heading] of [
      ["a run", run, "Share this run"],
      ["a week", { v: 1, k: "now", t: "My week", s: "This week", w: md(0), a: ["17.3 km", "run so far"],
        r: [{ d: 2, c: "long", l: "Long", n: "18 km", st: "d", km: 17.3, p: "4:41 /km" }] }, "Share this week"],
    ]) {
      const hash = Buffer.from(JSON.stringify(payload), "utf8").toString("base64").replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
      const share = await browser.newPage();
      share.on("pageerror", (e) => errors.push(e.message));
      await share.goto(SHARE + "#" + hash);
      await share.waitForTimeout(800);
      const drawn = await share.evaluate(() => ({ heading: document.getElementById("heading").textContent, img: document.getElementById("img").src }));
      check(`draws ${what}`, drawn.heading === heading && drawn.img.startsWith("blob:"), JSON.stringify(drawn));
      await share.close();
    }

    console.log("the workout card");
    fs.writeFileSync(path.join(dir, "session.html"), SESSION_CARD);
    fs.writeFileSync(path.join(dir, "host.html"), host({ ...SESSION, _card: SEEN }, "session.html"));
    page = await browser.newPage({ viewport: { width: 420, height: 1100 } });
    page.on("pageerror", (e) => errors.push(e.message));
    await listen(page);
    const before = pings.length;
    await page.goto("file://" + path.join(dir, "host.html"));
    await page.waitForTimeout(800);
    card = page.frames()[1];
    check("and so does the workout card", pings.length === before + 1 && pings[before].body.card === "session"
      && pings[before].body.ok === true && /^test/.test(pings[before].body.host), JSON.stringify(pings.slice(before)));
    const drawn = await card.evaluate(() => ({
      text: document.body.innerText,
      stats: document.querySelectorAll(".stat-v").length,
      segs: document.querySelectorAll(".seg").length,
      steps: document.querySelectorAll(".step").length,
      reps: document.querySelectorAll(".rep").length,
      mini: document.querySelectorAll(".mini i").length,
      me: [...document.querySelectorAll(".mini i")].findIndex((i) => i.classList.contains("me")),
    }));
    check("leads with the session, the key pill and the change", /Sub-T 3 × 2 km/.test(drawn.text) && /Key session/.test(drawn.text)
      && /Was Sub-T 3 × 3 km\. Same warm-up and cool-down\./.test(drawn.text.replace(/\s+/g, " ")), drawn.text.slice(0, 300));
    check("distance, time and rep pace", drawn.stats === 3 && /10\.9/.test(drawn.text) && /~49/.test(drawn.text) && /3:58–4:08/.test(drawn.text), drawn.text.slice(0, 400));
    check("the shape, and the steps with the repeat grouped", drawn.segs === 8 && drawn.steps === 4 && drawn.reps === 1
      && /3 km|2 km at 3:58–4:08 \/km/.test(drawn.text) && /Jog/.test(drawn.text), JSON.stringify(drawn));
    check("the day's other session, the watch line and the week", /Also .*Core/.test(drawn.text)
      && /Updated\. On your watch at the next sync/.test(drawn.text) && drawn.mini === 7 && drawn.me === 2
      && /Week 3 · 1 of 4 done/.test(drawn.text), drawn.text.slice(-300));
    await card.getByText("Undo").click();
    await card.getByText("See the week").click();
    await page.waitForTimeout(300);
    const asked = await page.evaluate(() => window.asked);
    check("Undo and See the week ask in the chat", asked.length === 2 && /^Undo the change to my Sub-T 3 × 2 km/.test(asked[0])
      && /this week on my plan card/.test(asked[1]), JSON.stringify(asked));
    await page.close();

    // Oct 30: a 24-week build to London. The week numbers ran into each
    // other, the last race was cut off, and Build was carried to race day.
    const longPlan = (n) => ({
      label: "MAR", name: "London build", goal: "London Marathon, sub-2:40", starts: md(7), ends: md(7 * n + 5),
      milestones: [{ label: "Half check", target: "1:21" }, { label: "Parkrun", target: "17:45" },
        { label: "Tune-up half", target: "1:17" }, { label: "London", target: "sub-2:40" }],
      phases: [{ label: "Base", from_week: 1, to_week: 8 }, { label: "Build", from_week: 9, to_week: 16 }, { label: "Taper", from_week: n - 1, to_week: n }],
      weeks: Array.from({ length: n }, (_, i) => ({ week: i + 1, starts: md(7 * (i + 1)), planned_km: 50 + i,
        sessions: [{ date: md(7 * (i + 1) + 1), day: "Tue", name: (10 + i) + "km easy", type: "easy", sport: "run", kind: "run", status: "ahead", planned_km: 50 + i }] })),
      next_session: null,
    });
    console.log("a long plan");
    ({ page, card } = await open({ ...longPlan(24), _card: SEEN }));
    let drawnLong = await card.evaluate(() => {
      const sc = document.querySelector(".scroller");
      return { long: !!document.querySelector(".chart.long"), fits: sc && sc.scrollWidth <= sc.clientWidth,
        lbls: [...document.querySelectorAll(".bars .bar-lbl")].map((l) => l.textContent).filter(Boolean),
        bands: [...document.querySelectorAll(".phase")].map((p) => p.textContent + ":" + p.style.gridColumn),
        races: [...document.querySelectorAll(".tl-node")].map((n) => n.getBoundingClientRect().right <= document.documentElement.clientWidth) };
    });
    check("a 24-week plan fits a phone without scrolling, every fourth week numbered",
      drawnLong.long && drawnLong.fits && drawnLong.lbls.join() === "1,5,9,13,17,21", JSON.stringify(drawnLong));
    check("its phases band from the server's weeks, with the weeks not known yet left blank",
      drawnLong.bands.join() === "BASE:span 8,BUILD:span 8,:span 6,TAPER:span 2", JSON.stringify(drawnLong.bands));
    check("and every race on the way is in sight", drawnLong.races.length === 5 && drawnLong.races.every(Boolean), JSON.stringify(drawnLong.races));
    await page.close();
    ({ page, card } = await open({ ...longPlan(30), _card: SEEN }));
    await card.evaluate(() => [...document.querySelectorAll(".bars .bar")][27].click());
    await page.waitForTimeout(200);
    drawnLong = await card.evaluate(() => {
      const sc = document.querySelector(".scroller"), bar = sc.querySelectorAll(".bar")[27];
      return { scrolls: sc.scrollWidth > sc.clientWidth, left: sc.scrollLeft,
        seen: bar.offsetLeft >= sc.scrollLeft && bar.offsetLeft + bar.offsetWidth <= sc.scrollLeft + sc.clientWidth };
    });
    check("30 weeks scroll sideways, and a week picked stays in view after the card redraws",
      drawnLong.scrolls && drawnLong.seen, JSON.stringify(drawnLong));
    await page.close();

    check("no page threw an error", errors.length === 0, errors.join(" | "));
  } finally {
    await browser.close();
    fs.rmSync(dir, { recursive: true, force: true });
  }
  console.log(failed ? `\n${failed} check(s) failed` : "\nall checks passed");
  process.exit(failed ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(1); });

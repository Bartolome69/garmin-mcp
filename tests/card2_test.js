// The redesigned plan card (plan2.html), in a real browser, the way a chat
// draws it: a stand-in host answers its messages, and any error the page
// throws fails the run. What it checks is the handoff's contract: the
// numbers come from the answer and add up, races and states have their
// marks and words, long plans scroll rather than squeeze, and the loading,
// failed and getting-started states draw.
//
// Run: node tests/card2_test.js   (needs the playwright package and Chromium)

const fs = require("fs");
const os = require("os");
const path = require("path");
const { chromium } = require("playwright");

const ROOT = path.join(__dirname, "..");
const CARD = fs.readFileSync(path.join(ROOT, "garmin_mcp", "ui", "plan2.html"), "utf8");

let failed = 0;
function check(name, ok, detail) {
  console.log(`  ${ok ? "PASS" : "FAIL"}  ${name}${!ok && detail ? " — " + String(detail).slice(0, 300) : ""}`);
  if (!ok) failed += 1;
}

const monday = (() => { const t = new Date(); t.setDate(t.getDate() - (t.getDay() + 6) % 7); return t; })();
const md = (o) => { const t = new Date(monday); t.setDate(t.getDate() + o); return t.toISOString().slice(0, 10); };
const thr = [{ k: "easy", km: 3, part: "wu" }, { k: "work", km: 3 }, { k: "rec", km: 0.3 }, { k: "work", km: 3 }, { k: "easy", km: 2, part: "cd" }];

// A 20-week plan in its third week, as get_plan answers for it once the
// server has worked out what the card draws.
function plan(n = 20, current = 3) {
  const weeks = Array.from({ length: n }, (_, i) => {
    const s = md(7 * (i - current + 1));
    const w = { week: i + 1, starts: s, planned_km: 50 + i, run_km: i < current - 1 ? 48 + i : 0, sessions: [] };
    if (i === current - 1) {
      w.current = true; w.run_km = 31.1;
      w.sessions = [
        { date: md(0), day: "Mon", name: "Full body", type: "strength", sport: "strength", kind: "strength", status: "done", state: "done", actual: "35m 00s", activity_id: 1 },
        { date: md(1), day: "Tue", name: "11km easy", type: "easy", sport: "run", kind: "run", status: "done", state: "done", actual_km: 11.1, actual_pace: "5:12 /km", actual_bpm: 133, activity_id: 2, planned_km: 11 },
        { date: md(2), day: "Wed", name: "Sub-T 3x3km", type: "threshold", sport: "run", kind: "run", status: "missed", state: "missed", planned_km: 14.1, workout_id: 3, shape: thr },
        { date: md(3), day: "Thu", name: "Upper body add-on", type: "strength", sport: "strength", kind: "strength", status: "skipped", state: "skipped", optional: true },
        { date: md(4), day: "Fri", name: "9km run club easy", type: "easy", sport: "run", kind: "run", status: "done, moved", state: "early", done_on: md(3), actual_km: 10.0, activity_id: 4, planned_km: 9 },
        { date: md(6), day: "Sun", name: "Long 19km", type: "long", sport: "run", kind: "run", status: "ahead", state: "ahead", planned_km: 18.8, workout_id: 5, schedule_id: 55 },
      ];
      w.extra = [{ date: md(3), day: "Thu", name: "Hackney Running", sport: "run", kind: "run", actual_km: 5.0, actual: "30m 19s", state: "extra" }];
      w.planned_km = 52.9;
    } else if (i > current - 1) {
      w.sessions = [{ date: md(7 * (i - current + 1) + 1), day: "Tue", name: "10km easy", type: "easy", sport: "run", kind: "run", status: "ahead", state: "ahead", planned_km: 10 }];
      if (i >= n - 3) { w.unread = 1; delete w.planned_km; }
    }
    return w;
  });
  return {
    label: "MAR", name: "Marathon build", method: "Base, then the build", goal: "Marathon, sub-2:40",
    starts: weeks[0].starts, ends: md(7 * (n - current + 1) - 1), weeks_total: n, current_week: current,
    phases: [{ label: "Base", from_week: 1, to_week: 6, short: "Ba" }, { label: "Build", from_week: 7, to_week: n - 3, short: "Bu" }, { label: "Taper", from_week: n - 2, to_week: n, short: "T" }],
    races: [{ name: "parkrun", date: md(12), week: current + 1, day: 5, tier: "tune" },
      { name: "Big Half", date: md(40), week: current + 5, day: 5, tier: "key", target: "1:17" },
      { name: "Manchester Marathon", date: md(7 * (n - current) + 6), week: n, day: 6, tier: "goal", target: "sub-2:40" }],
    this_week: { week: current, unit: "km", done: 31, extra: 5, to_go: 19, projected: 50, planned: 53 },
    next_session: { date: md(6), day: "Sun", name: "Long 19km", type: "long", sport: "run", kind: "run", status: "ahead", workout_id: 5, schedule_id: 55,
      planned_km: 18.8, steps: [{ kind: "interval", label: "Run", amount: "19 km", target: "125–150 bpm" }], target: "125–150 bpm" },
    weeks, _card: "https://daash.test/card/seen?u=f&v=view1&s=sig",
  };
}
const DETAILS = {
  summary: { distance_km: 11.1, duration: "57m 44s", pace_per_km: "5:12 /km", heart_rate: { average_bpm: 133 } },
  splits: [{ split: 1, distance_km: 3, duration: "15m 30s", pace_per_km: "5:10 /km" }, { split: 2, distance_km: 3, duration: "15m 40s", pace_per_km: "5:13 /km" },
    { split: 3, distance_km: 3, duration: "15m 35s", pace_per_km: "5:12 /km" }, { split: 4, distance_km: 2.1, duration: "11m 00s", pace_per_km: "5:14 /km" }],
};

function host(result, width) {
  return `<!doctype html><body style="margin:0"><iframe id=f style="width:${width}px;height:1800px;border:0"></iframe><script>
const f = document.getElementById("f"); window.opened = null; window.asked = []; window.calls = [];
window.addEventListener("message", (e) => {
  const m = e.data; if (!m || !m.method) return;
  const reply = (r) => f.contentWindow.postMessage({ jsonrpc: "2.0", id: m.id, result: r }, "*");
  if (m.method === "ui/initialize") reply({ hostInfo: { name: "test" }, hostCapabilities: { openLinks: {} }, hostContext: { theme: "dark", displayMode: "inline" } });
  if (m.method === "ui/notifications/initialized" && ${JSON.stringify(result)} !== null) f.contentWindow.postMessage({ jsonrpc: "2.0", method: "ui/notifications/tool-result", params: { structuredContent: ${JSON.stringify(result)}, content: [] } }, "*");
  if (m.method === "tools/call") { window.calls.push(m.params.name); reply({ structuredContent: m.params.name === "get_activity_details" ? ${JSON.stringify(DETAILS)}
    : m.params.name === "get_workout" ? { steps: [{ kind: "warmup", label: "Warm up", amount: "3 km" }, { kind: "interval", label: "Run", amount: "10 km" }] } : { error: "not in this test" } }); }
  if (m.method === "ui/open-link") { window.opened = m.params.url; reply({}); }
  if (m.method === "ui/message") { window.asked.push((m.params.content[0] || m.params.content).text); reply({}); }
});
f.src = "card.html";
</script></body>`;
}

(async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "daash-card2-"));
  fs.writeFileSync(path.join(dir, "card.html"), CARD.replace("<script>", '<script>window.__DAASH_CARD_OPENED__ = "https://daash.test/card/opened";</script>\n<script>'));
  const browser = await chromium.launch();
  const errors = [];
  const pings = [];
  async function open(result, width = 390, wait = 1200) {
    fs.writeFileSync(path.join(dir, "host.html"), host(result, width));
    const page = await browser.newPage({ viewport: { width, height: 1800 } });
    page.on("pageerror", (e) => errors.push(e.message));
    await page.route("https://daash.test/**", (r) => { pings.push({ url: r.request().url(), body: JSON.parse(r.request().postData() || "{}") }); return r.fulfill({ status: 204 }); });
    await page.goto("file://" + path.join(dir, "host.html"));
    await page.waitForTimeout(wait);
    return { page, card: page.frames()[1] };
  }

  try {
    console.log("a 20-week plan in its third week");
    let { page, card } = await open(plan());
    let got = await card.evaluate(() => ({
      text: document.body.innerText,
      bars: document.querySelectorAll(".bars .bar").length,
      aria: [...document.querySelectorAll(".bars .bar")].map((b) => b.getAttribute("aria-label")),
      marks: [...document.querySelectorAll(".marks .mk")].map((m) => m.className),
      dashed: document.querySelectorAll(".bars .unk").length,
      open: document.querySelectorAll(".detail").length,
      rows: [...document.querySelectorAll(".srow")].map((r) => r.innerText.replace(/\s+/g, " ").trim()),
    }));
    check("header: the title, the goal race with its countdown, and the other races as pills",
      /Marathon build/.test(got.text) && /GOAL RACE/.test(got.text) && /Manchester Marathon · sub-2:40/.test(got.text)
      && /\d+\s*weeks/.test(got.text) && /Also racing · 2 before the goal/.test(got.text) && /parkrun/.test(got.text), got.text.slice(0, 400));
    check("this week reads the answer's numbers, which add up: 31 done, 19 to go, ~50 projected, 5 of it extra",
      /31 km\s*Done/.test(got.text) && /19 km\s*To go/.test(got.text) && /~50 km\s*Projected/.test(got.text)
      && /53 km planned\. Done includes 5 km extra\./.test(got.text), got.text.slice(300, 800));
    check("the block: a bar per week, each a labelled button, race markers by tier, unknown weeks dashed",
      got.bars === 20 && /^Week 7, \d+ km planned$/.test(got.aria[6]) && got.aria[3].includes("parkrun")
      && got.marks.some((c) => /goal-m/.test(c)) && got.marks.some((c) => /\bkey\b/.test(c)) && got.marks.some((c) => /tune/.test(c))
      && got.dashed >= 3, JSON.stringify(got.aria.slice(0, 8)) + JSON.stringify(got.marks));
    check("past 1,150px at 390 wide, this week's finished rows fold behind one line; skipped optional ones with them, missed stay out",
      /^4 done, 1 skipped this week\s*·\s*Show$/.test(await card.evaluate(() => (document.querySelector(".collapsed") || {}).textContent)) && got.rows.some((r) => /^WED .*Missed/.test(r)), await card.evaluate(() => (document.querySelector(".collapsed") || {}).innerText));
    await card.getByRole("button", { name: /this week · Show/ }).click();
    await page.waitForTimeout(300);
    got.rows = await card.evaluate(() => [...document.querySelectorAll(".srow")].map((r) => r.innerText.replace(/\s+/g, " ").trim()));
    const labels = await card.evaluate(() => [...document.querySelectorAll(".bars .bar")].map((b) => (b.querySelector(".bk") || {}).textContent || ""));
    check("at phone width only the chosen week and this week say their km over the bar",
      labels.filter(Boolean).length === 1 && /^\d+$/.test(labels[2]), JSON.stringify(labels));
    const want = [/^MON .*Strength Full body.*35m/, /^TUE .*Easy 11 km.*5:12 \/km · 133 bpm 11\.1/, /^WED .*Missed/,
      /^THU .*Optional · skipped, not counted/, /Not in the plan · 30m 19s 5/, /^FRI .*Done a day early/, /^SAT Rest Planned rest$/, /^SUN .*Long.*Tomorrow|^SUN .*ready for your watch/];
    check("every state has an icon and its words: done, missed, skipped, extra, early, rest, next",
      want.every((re) => got.rows.some((r) => re.test(r))), JSON.stringify(got.rows));
    check("the next session opens by default, with its steps, and nothing else is open", got.open === 1 && /125–150 bpm/.test(got.text), got.open);
    await card.locator(".srow.tap", { hasText: "11 km" }).first().click();
    await page.waitForTimeout(500);
    got = await card.evaluate(() => ({ open: document.querySelectorAll(".detail").length, laps: document.querySelectorAll(".detail .chart span").length,
      text: (document.querySelector(".detail") || {}).innerText || "" }));
    const calls = await page.evaluate(() => window.calls);
    check("a done run opens in place (one row at a time) to its laps, read from Garmin, with Ask and Share",
      got.open === 1 && got.laps === 4 && calls.includes("get_activity_details") && /Ask about this run/.test(got.text), JSON.stringify(got) + calls);
    await card.getByRole("button", { name: "Next week" }).click();
    await page.waitForTimeout(300);
    got = await card.evaluate(() => document.querySelector(".week").innerText);
    check("the next-week button moves the chosen week, with its total", /Week 4/.test(got) && /\d+ km planned/.test(got), got.slice(0, 120));
    check("rest days in a row share one row", /Planned rest · Wed to Sun/.test(got) && (got.match(/Planned rest/g) || []).length === 2, got.slice(0, 300));
    await card.getByRole("button", { name: /^Goal race/ }).click();
    await page.waitForTimeout(300);
    got = await card.evaluate(() => ({ week: document.querySelector(".week").innerText, sel: [...document.querySelectorAll(".bars .bar")].findIndex((b) => b.classList.contains("sel")) }));
    check("the goal race button picks race week", got.sel === 19 && /Week 20/.test(got.week), JSON.stringify(got));
    const seen = pings.filter((p) => p.url.startsWith("https://daash.test/card/seen"));
    check("it reports once that it drew, as the new card", seen.length === 1 && seen[0].body.card === "plan2" && seen[0].body.ok === true, JSON.stringify(seen));
    await page.close();

    console.log("a race's warm-up and cool-down");
    const raced = plan();
    raced.weeks[2].sessions.push({ date: md(5), day: "Sat", name: "Parkrun tune-up", type: "race", sport: "run", kind: "run", status: "ahead", state: "ahead",
      planned_km: 8.5, workout_id: 6, schedule_id: 66, shape: [{ k: "easy", km: 3, part: "wu" }, { k: "easy", km: 4 }, { k: "easy", km: 1.5, part: "cd" }] });
    ({ page, card } = await open(raced));
    got = await card.evaluate(() => {
      const row = [...document.querySelectorAll(".shape")].find((x) => x.parentElement && /Parkrun tune-up/.test(x.parentElement.innerText));
      return row ? [...row.children].map((b) => b.style.background) : null;
    });
    check("are green, the race between them red", JSON.stringify(got) === JSON.stringify(["var(--easy)", "var(--race)", "var(--easy)"]), JSON.stringify(got));
    await page.close();

    console.log("widths and a long plan");
    for (const width of [320, 390, 560, 800]) {
      ({ page, card } = await open(plan(30, 5), width, 900));
      got = await card.evaluate(() => {
        const sc = document.querySelector(".scroller"); const doc = document.documentElement;
        return { scroll: !!sc, overflow: doc.scrollWidth > doc.clientWidth + 1, bar: document.querySelector(".bars .bar").getBoundingClientRect().width,
          selVisible: sc ? (() => { const b = sc.querySelectorAll(".bar")[4]; return b.offsetLeft >= sc.scrollLeft && b.offsetLeft + b.offsetWidth <= sc.scrollLeft + sc.clientWidth; })() : true };
      });
      check(`at ${width}px: no sideways page scroll, bars at least 12px, the chart scrolls only when it must and opens on this week`,
        !got.overflow && got.bar >= 11.5 && got.selVisible && (width >= 560 || got.scroll), JSON.stringify(got));
      await page.close();
    }

    console.log("the Daash app's link, for an account trying the app");
    const withApp = { ...plan(), app: { url: "https://daash.test/app/#/plan?login=abc", note: "x" } };
    for (const width of [320, 390]) {
      ({ page, card } = await open(withApp, width, 900));
      got = await card.evaluate(() => {
        const b = [...document.querySelectorAll(".head button")].find((x) => x.textContent === "Open in Daash app");
        const title = document.querySelector(".head .title"), kicker = document.querySelector(".head .brand .kicker");
        const doc = document.documentElement;
        return { button: !!b, overflow: doc.scrollWidth > doc.clientWidth + 1,
          oneLine: !!b && b.getBoundingClientRect().height < 50 && kicker.getBoundingClientRect().height < 20,
          beside: !!b && Math.abs(b.getBoundingClientRect().top - title.getBoundingClientRect().top) < 12,
          right: b ? b.getBoundingClientRect().right <= doc.clientWidth : false };
      });
      check(`at ${width}px: Open in Daash sits beside the title, on one line, without a sideways scroll`,
        got.button && !got.overflow && got.oneLine && got.beside && got.right, JSON.stringify(got));
      if (width === 390) {
        await card.evaluate(() => [...document.querySelectorAll(".head button")].find((x) => x.textContent === "Open in Daash app").click());
        await page.waitForTimeout(200);
        check("and opens the app's link", (await page.evaluate(() => window.opened)) === withApp.app.url, String(await page.evaluate(() => window.opened)));
      }
      await page.close();
    }
    ({ page, card } = await open(plan(), 390, 900));
    check("no link, no button", !(await card.evaluate(() => /Open in Daash app/.test(document.body.innerText))));
    await page.close();

    console.log("states");
    ({ page, card } = await open(null, 390, 500));
    got = await card.evaluate(() => document.body.innerText);
    check("before the answer: a skeleton saying it's reading the plan, never a blank or a spinner alone", /Reading your plan from Garmin/.test(got), got.slice(0, 120));
    await page.close();
    ({ page, card } = await open({ error: "Garmin took too long to answer." }));
    got = await card.evaluate(() => document.body.innerText);
    check("failed: says so, that nothing changed, with Try again and Open Garmin Connect",
      /Couldn.t load your plan/.test(got) && /Try again/.test(got) && /Open Garmin Connect/.test(got), got);
    await page.close();
    ({ page, card } = await open({ source: "waiting", since: md(0), upload: "https://daash.test/history?u=g&s=x", note: "x", _card: "https://daash.test/card/seen?u=f&v=w&s=s" }));
    got = await card.evaluate(() => document.body.innerText);
    check("day one: what's coming, Build tomorrow's workout and Add past runs", /first run is on its way/.test(got) && /Build tomorrow/.test(got) && /Add past runs/.test(got), got);
    await page.close();
    const coach = plan(8, 3); delete coach.label; delete coach.phases; coach.races = []; coach.source = "calendar"; delete coach.name; delete coach.goal;
    ({ page, card } = await open(coach));
    got = await card.evaluate(() => ({ text: document.body.innerText, bands: [...document.querySelectorAll(".band")].map((b) => b.innerText) }));
    check("a calendar with no Daash plan: Your Garmin calendar, months for phases, Add a race",
      /Your Garmin calendar/.test(got.text) && /Add a race/.test(got.text) && got.bands.some((b) => /^[A-Z]{3}$/.test(b)), JSON.stringify(got.bands));
    await page.close();

    check("no page threw an error", errors.length === 0, errors.join(" | "));
  } finally {
    await browser.close();
    fs.rmSync(dir, { recursive: true, force: true });
  }
  console.log(failed ? `\n${failed} check(s) failed` : "\nall checks passed");
  process.exit(failed ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(1); });

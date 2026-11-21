// Not feeling 100% in a real browser (Nov 21): the session screen's button row,
// the sheet, what a change says about the watch, Retry when Garmin refuses, and
// the week view's watch lines. The app's files are served as they ship; its API
// is stubbed with answers shaped like the server's (app_test.py checks those).
//
// Run: node tests/app_not100_test.js   (needs the playwright package and Chromium)

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
const session = (date, extra) => ({
  date, today: date === TODAY, status: "ahead", kicker: "MON 23 NOV · QUALITY", name: "3 × 3 km", type: "threshold",
  type_label: "Quality", colour: "hard", km: 13.8, minutes: 63, steps: [], schedule_id: 7, workout_id: 70,
  watch: { head: "On your watch", sub: "Scheduled. Arrives at its next sync." },
  easier: { applied: false, label: "Easier", preview: "3 × 2 km" }, assistant: "Claude",
  ask: { claude: "https://claude.ai/new?q=x", chatgpt: "https://chatgpt.com/?q=x", claude_app: "claude://claude.ai/new?q=x" },
  ...extra,
});
const SHEET = {
  date: TODAY, name: "3 × 3 km", title: "Not feeling 100%?", sub: "Busy, tired or a bit flat? Pick what fits. Nothing changes until you confirm.",
  options: [
    { choice: "lighter", label: "Lighter run", said: "Today's 13.8 km becomes 11.1 km, at the same pace." },
    { choice: "skip", label: "Skip this run", said: "Today's 13.8 km comes off. This week: 56 km → 42 km." },
    { choice: "days_off", label: "A few days off", spans: [
      { days: 2, label: "2 days", said: "2 sessions come off.", off: [{ day: "Mon", name: "3 × 3 km", km: 13.8 }, { day: "Tue", name: "Easy run 8 km", km: 8 }],
        totals: [{ label: "This week", from: 56, to: 34 }], back: "Wednesday's easy run 6 km becomes a lighter run.",
        note: "This touches your Valencia Marathon taper; consider asking Claude." },
      { days: 3, label: "3 days", said: "3 sessions come off." }, { days: 5, label: "5 days", said: "5 sessions come off." }] },
    { choice: "ask", label: "Talk it through with Claude", assistant: "Claude",
      ask: { claude: "https://claude.ai/new?q=I%27m%20not%20feeling%20100%25%20today.", chatgpt: "https://chatgpt.com/?q=x" } },
  ],
};
const WEEK = { week: { title: "Week 4", range: "23–29 Nov", current: true, starts: TODAY, days: [
  { date: "2026-11-23", dow: "MON", n: 23, today: true, sessions: [] },
  { date: "2026-11-24", dow: "TUE", n: 24, sessions: [{ name: "Easy run 6.4 km", type_label: "Easy", colour: "easy", state: "ahead", schedule_id: 9, watch: true }] },
  { date: "2026-11-25", dow: "WED", n: 25, sessions: [{ name: "Easy run 6 km", type_label: "Easy", colour: "easy", state: "ahead", schedule_id: 10, watch: true }] },
  ...[26, 27, 28, 29].map((n) => ({ date: `2026-11-${n}`, dow: "X", n, sessions: [] }))] },
  block: null, assistant: "Claude", ask: { claude: "https://claude.ai/new?q=x" } };

const WEEK_BEFORE = { ...WEEK, week: { ...WEEK.week, days: WEEK.week.days.map((d) => d.date === TODAY
  ? { ...d, sessions: [{ name: "3 × 3 km", type_label: "Quality", colour: "hard", state: "next", schedule_id: 7, watch: true }] } : d) } };
const TODAY_SCREEN = { today: TODAY, kicker: "MON 23 NOV", headline: "This week", assistant: "Claude",
  next: { date: TODAY, kicker: "NEXT · TODAY", name: "3 × 3 km", type: "threshold", type_label: "Quality", colour: "hard",
    stats: [], segments: [], on_watch: true, schedule_id: 7, easier: { applied: false, label: "Easier", preview: "3 × 2 km" },
    not_100: { prominent: false } },
  ask: { claude: "https://claude.ai/new?q=x", chatgpt: "https://chatgpt.com/?q=x", claude_app: "claude://claude.ai/new?q=x" } };

async function open(browser, hash, answers) {
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, serviceWorkers: "block" });
  await ctx.addInitScript(() => { localStorage.setItem("daash.access", "x"); localStorage.setItem("daash.refresh", "y"); });
  const posts = [];
  await ctx.route("**/*", async (r) => {
    const u = new URL(r.request().url());
    if (u.hostname !== "localhost") return r.fulfill({ status: 200, body: "" });
    if (u.pathname === "/app/api/event") return r.fulfill({ json: { ok: true } });
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
  return { ctx, page, posts, errors };
}

(async () => {
  const browser = await chromium.launch();
  try {
    console.log("the session screen's buttons");
    let p = await open(browser, `/session/${TODAY}?from=today`, (key) =>
      key === `GET session/${TODAY}` ? { json: session(TODAY, { not_100: { prominent: false } }) } : null);
    const row = await p.page.$$eval(".actions3 > *", (xs) => xs.map((x) => x.textContent.trim()));
    check("the row reads Move, Not feeling 100%, Ask Claude", row.length === 3 && row[0] === "Move" && row[1] === "Not feeling 100%"
      && row[2].startsWith("Ask Claude"), JSON.stringify(row));
    check("with no standalone Easier button", !(await p.page.$('.actions3 [data-act="easier"]')));
    check("and no separate Not feeling 100% card below it", (await p.page.$$("#screen [data-not100]")).length === 1);
    const right = await p.page.$eval(".actions3", (x) => x.getBoundingClientRect().right);
    check("the row fits inside the screen's margin", right <= 390 - 16 + 0.5, String(right));
    check("no page threw an error", p.errors.length === 0, p.errors.join("; "));
    await p.ctx.close();

    console.log("Today's card");
    p = await open(browser, "/today", (key) => key === "GET today" ? { json: TODAY_SCREEN }
      : key.startsWith(`GET not100/${TODAY}`) ? { json: SHEET } : null);
    const pills = await p.page.$$eval(".next-pills > *", (xs) => xs.map((x) => x.textContent.trim()));
    check("Today's card has Not feeling 100% where Easier was, and no Easier",
      pills.includes("Not feeling 100%") && !pills.some((t) => /^Easier|Undo easier/.test(t)) && !(await p.page.$('.next-pills [data-act="easier"]')),
      JSON.stringify(pills));
    await p.page.click(".next-pills .n100-pill");
    await p.page.waitForSelector(".n100-choice");
    check("and it opens the same sheet", (await p.page.textContent("#sheet-title")) === "Not feeling 100%?");
    check("no page threw an error", p.errors.length === 0, p.errors.join("; "));
    await p.ctx.close();

    console.log("a past session");
    p = await open(browser, "/session/2026-11-20?from=plan", (key) =>
      key === "GET session/2026-11-20" ? { json: session("2026-11-20", { status: "missed", missed: true }) } : null);
    const past = await p.page.$$eval(".actions3 > *", (xs) => xs.map((x) => x.textContent.trim()));
    check("offers Move and Ask, no Not feeling 100%", past.length === 2 && !past.some((t) => /Not feeling/.test(t)), JSON.stringify(past));
    await p.ctx.close();

    console.log("the sheet, a change, the watch and the week");
    p = await open(browser, `/session/${TODAY}?from=today`, (key, posts) => {
      if (key === `GET session/${TODAY}`) return { json: session(TODAY, { not_100: { prominent: false } }) };
      if (key.startsWith(`GET not100/${TODAY}`)) return { json: SHEET };
      if (key === `POST not100/${TODAY}`) {
        // Garmin refuses the first time; the retry goes through.
        return posts.filter((x) => x.key === key).length === 1
          ? { status: 502, json: { error: "Couldn't update your watch.", watch_failed: true, detail: "Nothing changed." } }
          : { json: { message: "2 sessions off for 2 days.", watch_said: "Updated on your watch. Arrives at next sync.",
              watch: [{ date: "2026-11-23", name: "3 × 3 km", status: "removed" }, { date: "2026-11-24", name: "Easy run 6.4 km", status: "updated" }],
              undo: [{ put: { workout_id: 70, date: TODAY } }], sessions: 2 } };
      }
      if (key === "GET plan") return { json: posts.filter((x) => x.key === `POST not100/${TODAY}`).length >= 2 ? WEEK : WEEK_BEFORE };
      return null;
    });
    await p.page.click(".n100-pill");
    await p.page.waitForSelector(".n100-choice");
    const go = await p.page.$("#n100-go");
    check("Change my week waits for a choice", await go.isDisabled());
    check("a few days off waits behind a quiet link at the bottom, after the chat",
      !(await p.page.isVisible(".n100-spans")) && (await p.page.textContent(".n100-more")) === "Need a few days off?"
      && await p.page.evaluate(() => [...document.querySelector(".n100-list").children].slice(-2)[0].classList.contains("n100-more")));
    await p.page.click(".n100-more");
    check("the link opens the 2, 3 and 5 days in its place",
      await p.page.isVisible(".n100-spans") && !(await p.page.isVisible(".n100-more"))
      && (await p.page.getAttribute(".n100-more", "aria-expanded")) === "true");
    await p.page.click('.n100-spans [data-days="2"]');
    const detail = await p.page.textContent("[data-span-said]");
    check("picking 2 days lists what comes off and the week's new total right away",
      /Mon\s*3 × 3 km/.test(detail) && /Tue\s*Easy run 8 km/.test(detail) && /This week: 56 km → 34 km/.test(detail), detail);
    check("with the gentle taper note, still allowed", /Valencia Marathon taper; consider asking Claude/.test(detail) && !(await go.isDisabled()), detail);
    const ask = await p.page.getAttribute(".n100-ask", "href");
    check("Talk it through opens Claude with the message filled in", ask.startsWith("https://claude.ai/new?q=I%27m%20not%20feeling"), ask);
    check("and says so with its arrow", (await p.page.textContent(".n100-ask")).includes("Talk it through with Claude ↗"));
    await p.page.click("#n100-go");
    await p.page.waitForSelector(".toast-action");
    check("Garmin refuses: Couldn't update your watch, with Retry",
      (await p.page.textContent("#toast")).startsWith("Couldn't update your watch.") && (await p.page.textContent(".toast-action")) === "Retry",
      await p.page.textContent("#toast"));
    check("and nothing is marked as on the watch", (await p.page.evaluate(() => localStorage.getItem("daash.watch"))) === null);
    // A reload: it still says the change didn't go through, on the session and in the week.
    await p.page.reload();
    await p.page.waitForSelector(".n100-failed", { timeout: 5000 }).catch(() => {});
    const kept = (await p.page.$(".n100-failed")) ? (await p.page.textContent(".n100-failed")).replace(/\s+/g, " ") : "";
    check("after a reload the session still says Couldn't update your watch, with Retry",
      /Couldn't update your watch/.test(kept) && /A few days off didn't reach Garmin, so nothing changed yet/.test(kept) && (await p.page.$("[data-retry]")) !== null, kept);
    await p.page.evaluate(() => { location.hash = "#/plan"; });
    await p.page.waitForSelector(".plan-row");
    await p.page.waitForTimeout(400);
    const before = await p.page.$$eval(".plan-row", (xs) => xs.map((x) => x.textContent.replace(/\s+/g, " ").trim()));
    check("and its row in the week says so too", before.some((t) => /3 × 3 km.*Couldn't update your watch/.test(t)), JSON.stringify(before));
    await p.page.evaluate((t) => { location.hash = `#/session/${t}?from=today`; }, TODAY);
    await p.page.waitForSelector("[data-retry]");
    await p.page.click("[data-retry]");
    await p.page.waitForTimeout(700);
    const sent = p.posts.filter((x) => x.key === `POST not100/${TODAY}`);
    check("Retry sends the same change again, marked as a retry",
      sent.length === 2 && sent[1].body.choice === "days_off" && sent[1].body.days === 2 && sent[1].body.retry === true, JSON.stringify(sent));
    check("once through, the Couldn't update note is gone, here and after a reload",
      !(await p.page.$(".n100-failed")) && (await p.page.evaluate(() => localStorage.getItem("daash.watchFailed"))) === "[]");
    check("then says it's on the watch, with Undo", /2 sessions off for 2 days\. Updated on your watch\. Arrives at next sync\./.test(await p.page.textContent("#toast"))
      && (await p.page.textContent(".toast-action")) === "Undo", await p.page.textContent("#toast"));
    await p.page.evaluate(() => { location.hash = "#/plan"; });
    await p.page.waitForSelector(".plan-row");
    await p.page.waitForTimeout(500);
    const rows = await p.page.$$eval(".plan-row", (xs) => xs.map((x) => x.textContent.replace(/\s+/g, " ").trim()));
    check("the week view shows the session taken off, struck through, removed from the watch",
      rows.some((t) => /3 × 3 km\s*Removed from your watch/.test(t)), JSON.stringify(rows));
    check("and the changed one with the same watch line, updated",
      rows.some((t) => /Easy run 6\.4 km.*Updated on your watch · Arrives at next sync/.test(t)), JSON.stringify(rows));
    check("a row that didn't change keeps its plain On your watch",
      rows.some((t) => /Easy run 6 km.*On your watch/.test(t) && !/Updated/.test(t)), JSON.stringify(rows));
    check("no page threw an error", p.errors.length === 0, p.errors.join("; "));
    await p.ctx.close();
  } finally {
    await browser.close();
  }
  console.log(failed ? `\n${failed} failed` : "\nall checks passed");
  process.exit(failed ? 1 : 0);
})();

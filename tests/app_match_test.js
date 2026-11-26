// Matching version 2 on the week (Nov 26), in a real browser: how a run was
// counted, with Change (another session, extra run, Undo); a question when two
// sessions fit about as well; and after a partial, what to do with the rest.
// The week is what app._week_view answered for Bart's own week (match_test.py
// checks the server side).
//
// Run: node tests/app_match_test.js   (needs the playwright package and Chromium)

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
const TODAY = "2026-11-26";
const WEEK = { week: {"starts": "2026-11-23", "title": "Week of 23 Nov", "done": 24, "planned": 57, "heading": 53, "range": "23 Nov to 29 Nov", "current": true, "days": [{"date": "2026-11-23", "dow": "MON", "n": 23, "today": false, "past": true, "sessions": []}, {"date": "2026-11-24", "dow": "TUE", "n": 24, "today": false, "past": true, "sessions": [{"date": "2026-11-24", "name": "Easy 8 km", "type": "easy", "type_label": "Easy", "colour": "easy", "state": "done", "meta": "5:30 /km", "plan_km": "8", "plan_min": 44, "done_km": "8", "done_pace": "5:30/km", "done_time": "44m 00s", "outcome": "on", "v": "8", "u": "km", "schedule_id": 100, "activity_id": 2, "match_line": "Counted as Tue's Easy 8 km", "match_aid": 2}]}, {"date": "2026-11-25", "dow": "WED", "n": 25, "today": false, "past": true, "sessions": [{"date": "2026-11-25", "name": "Easy 8 km", "type": "easy", "type_label": "Easy", "colour": "easy", "state": "asked", "meta": "Waiting for your answer below", "plan_km": "8", "plan_min": 44, "v": "8", "u": "km", "schedule_id": 104}, {"date": "2026-11-25", "name": "9 km run club", "type": "extra", "type_label": "Extra", "colour": "rec", "state": "extra", "meta": "Not on the plan \u00b7 44m 33s", "v": "8.1", "u": "km", "activity_id": 3, "question": {"text": "Was this yesterday's Easy 8 km or Friday's 9 km run club?", "options": [{"schedule_id": "104", "label": "Yesterday's Easy 8 km"}, {"schedule_id": "102", "label": "Friday's 9 km run club"}]}, "match_aid": 3}]}, {"date": "2026-11-26", "dow": "THU", "n": 26, "today": true, "past": false, "sessions": [{"date": "2026-11-26", "name": "Easy 12 km", "type": "easy", "type_label": "Easy", "colour": "easy", "state": "done", "meta": "5:30 /km", "plan_km": "12", "plan_min": 66, "done_km": "7.8", "done_pace": "5:30/km", "done_time": "42m 54s", "outcome": "short", "v": "7.8", "u": "km", "schedule_id": 101, "activity_id": 1, "match_line": "Counted as today's Easy 12 km (7.8 of 12 km)", "match_aid": 1, "partial_of": "12", "followup": {"text": "You did 7.8 of 12 km.", "add": "Add 2 km to Sunday's long run 20 km", "schedule_id": 101}}]}, {"date": "2026-11-27", "dow": "FRI", "n": 27, "today": false, "past": false, "sessions": [{"date": "2026-11-27", "name": "9 km run club", "type": "easy", "type_label": "Easy", "colour": "easy", "state": "next", "meta": "Tomorrow \u00b7 on your watch", "plan_km": "9", "plan_min": 50, "watch": true, "v": "9", "u": "km", "movable": true, "schedule_id": 102}]}, {"date": "2026-11-28", "dow": "SAT", "n": 28, "today": false, "past": false, "sessions": []}, {"date": "2026-11-29", "dow": "SUN", "n": 29, "today": false, "past": false, "sessions": [{"date": "2026-11-29", "name": "Long run 20 km", "type": "long", "type_label": "Long", "colour": "long", "state": "ahead", "plan_km": "20", "plan_min": 110, "watch": true, "v": "20", "u": "km", "movable": true, "schedule_id": 103}]}], "pick": [{"schedule_id": 100, "label": "Tue \u00b7 Easy 8 km"}, {"schedule_id": 104, "label": "Wed \u00b7 Easy 8 km"}, {"schedule_id": 101, "label": "Thu \u00b7 Easy 12 km"}, {"schedule_id": 102, "label": "Fri \u00b7 9 km run club"}, {"schedule_id": 103, "label": "Sun \u00b7 Long run 20 km"}], "ask_next": {"claude": "https://claude.ai/new?q=Use%20Daash%20to%20plan%20next%20week%2C%20the%20week%20of%202026-11-30%2C%20and%20adjust%20it%20to%20how%20this%20week%20has%20gone.", "chatgpt": "https://chatgpt.com/?q=Use%20Daash%20to%20plan%20next%20week%2C%20the%20week%20of%202026-11-30%2C%20and%20adjust%20it%20to%20how%20this%20week%20has%20gone.", "claude_app": "claude://claude.ai/new?q=Use%20Daash%20to%20plan%20next%20week%2C%20the%20week%20of%202026-11-30%2C%20and%20adjust%20it%20to%20how%20this%20week%20has%20gone."}, "ask": {"claude": "https://claude.ai/new?q=Use%20Daash%20to%20look%20at%20my%20training%20week%20of%202026-11-23.%20Is%20it%20balanced%2C%20and%20is%20anything%20worth%20changing%3F", "chatgpt": "https://chatgpt.com/?q=Use%20Daash%20to%20look%20at%20my%20training%20week%20of%202026-11-23.%20Is%20it%20balanced%2C%20and%20is%20anything%20worth%20changing%3F", "claude_app": "claude://claude.ai/new?q=Use%20Daash%20to%20look%20at%20my%20training%20week%20of%202026-11-23.%20Is%20it%20balanced%2C%20and%20is%20anything%20worth%20changing%3F"}}, block: null, assistant: "Claude", ask: { claude: "https://claude.ai/new?q=x" } };

async function open(browser, answers, look = {}) {
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, serviceWorkers: "block",
    deviceScaleFactor: look.scale || 1, colorScheme: look.dark ? "dark" : "light" });
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
  await page.clock.install({ time: new Date(`${TODAY}T12:30:00Z`) });
  await page.goto("http://localhost/app/#/plan");
  await page.waitForSelector(".plan-list");
  return { ctx, page, posts };
}

(async () => {
  const browser = await chromium.launch();
  try {
    const answers = (key) => key === "GET plan" ? { json: WEEK }
      : key === "POST match" ? { json: { message: "Counted as an extra run." } }
      : key === "POST partial" ? { json: { message: "2 km added to Sunday's long run 20 km.", watch_said: "Updated on your watch. Arrives at next sync.", undo: [{ remove: 5, put: { workout_id: 13, date: "2026-11-29" } }] } }
      : null;
    let p = await open(browser, answers);
    const text = await p.page.textContent(".plan-list");
    check("today's run says how it was counted: 7.8 of 12 km", text.includes("Counted as today's Easy 12 km (7.8 of 12 km)"), text);
    check("Wednesday's session isn't called missed while the question is open",
      !/Missed|Not recorded/.test(text) && !(await p.page.$('.plan-row.is-missed[data-date="2026-11-25"]')), text);
    check("the question asks which it was, with Extra run", text.includes("Was this yesterday's Easy 8 km or Friday's 9 km run club?")
      && (await p.page.$$('.match-q [data-match]')).length === 3);
    check("after the partial: Leave it, add to Sunday (at most a tenth), Skip the rest",
      text.includes("You did 7.8 of 12 km.") && text.includes("Add 2 km to Sunday's long run 20 km") && text.includes("Skip the rest"), text);
    check("no button sits inside a row's link", await p.page.evaluate(() => !document.querySelector(".plan-row button, .plan-row details")));
    await p.page.screenshot({ path: path.join(__dirname, "..", "garmin-screens", "match-week.png"), fullPage: true });
    await p.page.click('.match-q [data-match="extra"]');
    await p.page.waitForSelector("#toast:not([hidden])");
    const sent = p.posts.find((x) => x.key === "POST match");
    check("Extra run answers the question", sent && sent.body.action === "extra" && sent.body.activity_id === "3", JSON.stringify(sent));
    const change = await p.page.$(".match-line details");
    await change.click();
    const opts = await p.page.textContent(".match-line details");
    check("Change opens: another of this week's sessions, Mark as extra run, Undo",
      opts.includes("Count as another session") && opts.includes("Fri · 9 km run club") && opts.includes("Mark as extra run") && opts.includes("Undo"), opts);
    await p.page.click('[data-partial="add"]');
    await p.page.waitForSelector(".toast-action");
    check("adding the rest says so, with Undo", (await p.page.textContent("#toast")).includes("2 km added to Sunday's long run 20 km."));
    await p.ctx.close();
    p = await open(browser, answers, { dark: true, scale: 2 });
    await p.page.screenshot({ path: path.join(__dirname, "..", "garmin-screens", "match-week-dark.png"), fullPage: true });
    await p.ctx.close();
  } finally {
    await browser.close();
  }
  console.log(failed ? `${failed} check(s) failed` : "all checks passed");
  process.exit(failed ? 1 : 0);
})();

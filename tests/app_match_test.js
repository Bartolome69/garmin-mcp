// Matching version 2 on the week, in a real browser (Nov 26; Nov 27 refined).
//
// The weeks are what app._week_view answered for a week like Bart's of 23 Nov
// (tests/fixtures/make_match_weeks.py writes them from the server's own code):
//
// - open, on Thursday: Tuesday's session run on Monday ("Moved from Tue 24");
//   Wednesday's run could be two sessions (a question); Thursday's 7.8 km is a
//   partial of the easy 12 km, chosen over the strides, the rest undecided.
// - decided, on Saturday: Thursday's rest went onto Sunday (+2 km, and Sunday
//   says "+2 km from Thu"); Wednesday's run counted by hand; Saturday's run
//   that day, with no line at all.
//
// Run: node tests/app_match_test.js   (needs the playwright package and Chromium)

const fs = require("fs");
const path = require("path");
const { chromium } = require("playwright");

const DIR = path.join(__dirname, "..", "garmin_mcp", "app");
const SHOTS = path.join(__dirname, "..", "garmin-screens");
const TYPES = { ".js": "text/javascript", ".css": "text/css", ".html": "text/html", ".webmanifest": "application/json", ".svg": "image/svg+xml", ".png": "image/png" };
const OPEN = JSON.parse(fs.readFileSync(path.join(__dirname, "fixtures", "match_week_open.json"), "utf8"));
const DECIDED = JSON.parse(fs.readFileSync(path.join(__dirname, "fixtures", "match_week_decided.json"), "utf8"));
let failed = 0;
function check(name, ok, detail) {
  console.log(`  ${ok ? "PASS" : "FAIL"}  ${name}${!ok && detail ? " — " + String(detail).slice(0, 300) : ""}`);
  if (!ok) failed += 1;
}

async function open(browser, week, today, answers, look = {}) {
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, serviceWorkers: "block",
    deviceScaleFactor: look.scale || 1, colorScheme: look.dark ? "dark" : "light", hasTouch: !!look.touch });
  await ctx.addInitScript(() => { localStorage.setItem("daash.access", "x"); localStorage.setItem("daash.refresh", "y"); });
  const posts = [];
  await ctx.route("**/*", async (r) => {
    const u = new URL(r.request().url());
    if (u.hostname !== "localhost") return r.fulfill({ status: 200, body: "" });
    if (u.pathname === "/app/api/event") return r.fulfill({ json: { ok: true } });
    if (u.pathname.startsWith("/app/api/")) {
      const key = `${r.request().method()} ${u.pathname.replace("/app/api/", "")}`;
      if (r.request().method() === "POST") posts.push({ key, body: JSON.parse(r.request().postData() || "{}") });
      if (key === "GET plan") return r.fulfill({ json: week });
      const found = answers(key, posts);
      if (found) return r.fulfill({ status: found.status || 200, json: found.json });
      return r.fulfill({ json: { error: "not in this test" } });
    }
    const fp = path.join(DIR, u.pathname.replace(/^\/app\/?/, "") || "index.html");
    if (fs.existsSync(fp) && fs.statSync(fp).isFile()) return r.fulfill({ body: fs.readFileSync(fp), contentType: TYPES[path.extname(fp)] || "application/octet-stream" });
    return r.fulfill({ status: 404, body: "" });
  });
  const page = await ctx.newPage();
  await page.clock.install({ time: new Date(`${today}T12:30:00Z`) });
  await page.goto("http://localhost/app/#/plan");
  await page.waitForSelector(".plan-list");
  return { ctx, page, posts };
}

const card = (p, date, name) => p.page.locator(`.plan-row[data-date="${date}"]`, { hasText: name }).first();

(async () => {
  const browser = await chromium.launch();
  fs.mkdirSync(SHOTS, { recursive: true });
  try {
    const answers = (key) => key === "POST match" ? { json: { message: "Counted as an extra run." } }
      : key === "POST partial" ? { json: { message: "Back to the choice." } }
      : null;

    // -- open week, on Thursday --------------------------------------------------
    let p = await open(browser, OPEN, "2026-11-26", answers);
    const list = await p.page.textContent(".plan-list");
    check("Monday's card: Tuesday's session, moved, saying so inline with Change",
      (await card(p, "2026-11-23", "Easy 8 km").textContent()).includes("Moved from Tue 24 · Change"));
    const thu = await card(p, "2026-11-26", "Easy 12 km").textContent();
    check("Thursday: 7.8 of 12 km, Short, and 'Best fit of 2 sessions' (the app chose)",
      thu.includes("7.8 of 12 km") && thu.includes("Short") && thu.includes("Best fit of 2 sessions"), thu);
    check("no 'yesterday's' and no 'Counted as' anywhere on the week", !/yesterday|Counted as/.test(list), list);
    check("the question still asks, by day name and date, with Extra run",
      list.includes("Was this Wed 25 Easy 8 km or Fri 27 9 km run club?") && (await p.page.$$('.match-q [data-match]')).length === 3, list);
    check("the rest of the partial is still asked, onto Sun 29",
      list.includes("You did 7.8 of 12 km.") && list.includes("Sun 29") && list.includes("Skip the rest"), list);
    check("no separate footer line under matched cards any more", !(await p.page.$(".match-line")));
    check("no button sits inside a row's link", await p.page.evaluate(() => !document.querySelector(".plan-row button, .plan-row details")));
    const pad = await p.page.evaluate(() => parseFloat(getComputedStyle(document.querySelector(".plan-row.is-matched")).paddingTop));
    check("matched cards are compact (8px, down from 12px)", pad === 8, pad);
    const tones = await p.page.evaluate(() => ({
      amber: getComputedStyle(document.querySelector('.plan-row[data-date="2026-11-26"] .out-word')).color,
      ink: getComputedStyle(document.body).getPropertyValue("--amber"),
    }));
    check("Short beyond 15% is amber", tones.amber && tones.amber !== "rgba(0, 0, 0, 0)", JSON.stringify(tones));
    await p.page.screenshot({ path: path.join(SHOTS, "match-week-open.png"), fullPage: true });

    // Change from the inline line opens the menu, not the run.
    await card(p, "2026-11-23", "Easy 8 km").locator("[data-change]").click();
    await p.page.waitForSelector(".sheet");
    const menu = await p.page.textContent(".sheet");
    check("Change opens the menu: another session, Mark as an extra run, Undo",
      menu.includes("Count it as another session") && menu.includes("Fri · 9 km run club") && menu.includes("Mark as an extra run")
      && menu.includes("Undo how it was counted"), menu);
    check("and doesn't open the run underneath", (await p.page.evaluate(() => location.hash)) === "#/plan");
    await p.page.click('.sheet [data-match="extra"]');
    await p.page.waitForSelector("#toast:not([hidden])");
    const sent = p.posts.find((x) => x.key === "POST match");
    check("Mark as an extra run sends it for that run", sent && sent.body.action === "extra" && String(sent.body.activity_id) === "5", JSON.stringify(sent));
    await p.ctx.close();

    // -- decided week, on Saturday ---------------------------------------------------
    p = await open(browser, DECIDED, "2026-11-28", answers, { touch: true });
    const sat = await card(p, "2026-11-28", "Easy 6 km").textContent();
    check("Saturday, same day, same type: the tick, 6.3 of 6 km, and no line", sat.includes("6.3 of 6 km") && !sat.includes("Change")
      && !(await card(p, "2026-11-28", "Easy 6 km").locator(".match-inline").count()), sat);
    const thu2 = await card(p, "2026-11-26", "Easy 12 km").textContent();
    check("Thursday shows the decision: +2 km added to Sun 29 long run · Change",
      thu2.includes("+2 km added to Sun 29 long run · Change") && !(await p.page.textContent(".plan-list")).includes("You did 7.8"), thu2);
    check("Sunday says where its extra came from: +2 km from Thu",
      (await card(p, "2026-11-29", "Long run 20 km").textContent()).includes("+2 km from Thu"));
    check("Wednesday's hand-made match says it was their call",
      (await card(p, "2026-11-25", "Easy 8 km").textContent()).includes("Counted here · your call · Change"));
    const dot = await p.page.evaluate(() => getComputedStyle(document.querySelector('.plan-row[data-date="2026-11-28"] .dot')).backgroundColor);
    const easy = await p.page.evaluate(() => { const i = document.createElement("i"); i.style.background = "var(--easy)"; document.body.append(i);
      const c = getComputedStyle(i).backgroundColor; i.remove(); return c; });
    check("a run that went to plan keeps its green dot", dot === easy, `${dot} vs ${easy}`);
    await p.page.screenshot({ path: path.join(SHOTS, "match-week-decided.png"), fullPage: true });

    // A long press on a card with no line still reaches Change.
    // Centred: at the very bottom of the screen the tab bar sits over it.
    await card(p, "2026-11-28", "Easy 6 km").evaluate((el) => el.scrollIntoView({ block: "center" }));
    const box = await card(p, "2026-11-28", "Easy 6 km").boundingBox();
    await p.page.mouse.move(box.x + 120, box.y + box.height / 2);
    await p.page.mouse.down();
    await p.page.waitForTimeout(600);
    await p.page.mouse.up();
    const held = await p.page.$(".sheet");
    check("a long press on Saturday's card (no line) opens the same Change menu", !!held,
      await p.page.evaluate(() => location.hash));
    check("and the press didn't open the run", (await p.page.evaluate(() => location.hash)) === "#/plan");
    if (held) await p.page.click(".sheet [data-close]");

    // Change on a decision reopens it.
    await card(p, "2026-11-26", "Easy 12 km").locator("[data-change]").click();
    await p.page.waitForSelector(".sheet");
    const reopen = await p.page.textContent(".sheet");
    check("Change on the +2 km decision offers to take it back off and choose again",
      reopen.includes("Take the added km back off, and choose again"), reopen);
    await p.page.click('.sheet [data-partial="reopen"]');
    await p.page.waitForSelector("#toast:not([hidden])");
    const re = p.posts.find((x) => x.key === "POST partial");
    check("which asks the server to reopen Thursday's decision", re && re.body.choice === "reopen" && String(re.body.schedule_id) === "101",
      JSON.stringify(re));
    await p.ctx.close();

    p = await open(browser, DECIDED, "2026-11-28", answers, { dark: true, scale: 2 });
    await p.page.screenshot({ path: path.join(SHOTS, "match-week-decided-dark.png"), fullPage: true });
    await p.ctx.close();
  } finally {
    await browser.close();
  }
  console.log(failed ? `${failed} check(s) failed` : "all checks passed");
  process.exit(failed ? 1 : 0);
})();

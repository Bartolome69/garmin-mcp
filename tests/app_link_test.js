// A chat's link into the companion app, in a real browser.
//
// The link carries a single-use sign-in code (login=…) and where it was given
// (from=chat, chat_offer, session_card). The app sends the source once, with
// the first screen it opens, so app_viewed can say which links get opened.
// A code that has expired falls back to signing in by email, and that now
// lands on the screen the link was for, not Today.
// This serves the app's files with its API stubbed.
//
// Run: node tests/app_link_test.js   (needs the playwright package and Chromium)

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

async function open(browser, url, signedIn) {
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, serviceWorkers: "block" });
  if (signedIn) {
    await ctx.addInitScript(() => { localStorage.setItem("daash.access", "x"); localStorage.setItem("daash.refresh", "y"); });
  }
  const reads = [];
  await ctx.route("**/*", (r) => {
    const u = new URL(r.request().url());
    if (u.hostname !== "localhost") return r.fulfill({ status: 200, body: "" });
    if (u.pathname === "/app/api/sign-in/handoff") return r.fulfill({ status: 400, json: { error: "That link has expired." } });
    if (u.pathname === "/app/api/sign-in/email") return r.fulfill({ json: { ok: true } });
    if (u.pathname === "/app/api/sign-in/verify") return r.fulfill({ json: { access_token: "a", refresh_token: "r" } });
    if (u.pathname === "/app/api/event") return r.fulfill({ json: { ok: true } });
    if (u.pathname.startsWith("/app/api/")) {
      reads.push({ path: u.pathname, from: r.request().headers()["x-daash-from"] || null,
        ahead: r.request().headers()["x-daash-ahead"] === "1" });
      return r.fulfill({ json: { error: "not in this test" } });
    }
    const fp = path.join(DIR, u.pathname.replace(/^\/app\/?/, "") || "index.html");
    if (fs.existsSync(fp) && fs.statSync(fp).isFile()) return r.fulfill({ body: fs.readFileSync(fp), contentType: TYPES[path.extname(fp)] || "application/octet-stream" });
    return r.fulfill({ status: 404, body: "" });
  });
  const page = await ctx.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto(url);
  await page.waitForTimeout(1000);
  return { ctx, page, reads, errors };
}

(async () => {
  const browser = await chromium.launch();
  try {
    console.log("signed in already, from a chat");
    let p = await open(browser, "http://localhost/app/#/plan?from=chat", true);
    const shown = p.reads.filter((r) => !r.ahead);
    check("the address is cleaned of the source", (await p.page.evaluate(() => location.hash)) === "#/plan",
      await p.page.evaluate(() => location.hash));
    check("the first screen opened says where the link was given", shown[0] && shown[0].path.startsWith("/app/api/plan")
      && shown[0].from === "chat", JSON.stringify(p.reads));
    check("and only that one", p.reads.filter((r) => r.from).length === 1, JSON.stringify(p.reads));
    check("no page threw an error", p.errors.length === 0, p.errors.join("; "));
    await p.ctx.close();

    console.log("an expired link, then signing in by email");
    p = await open(browser, "http://localhost/app/#/plan?login=old-code&from=chat_offer", false);
    check("the sign-in says the link has expired", /expired/.test(await p.page.textContent("#screen")),
      await p.page.textContent("#screen"));
    await p.page.fill("#email", "runner@example.com");
    await p.page.click("#email-form button");
    await p.page.waitForTimeout(300);
    await p.page.fill("#code", "123456");
    await p.page.click("#code-form button");
    await p.page.waitForTimeout(800);
    check("signed in, it opens the screen the link was for, not Today",
      (await p.page.evaluate(() => location.hash)) === "#/plan", await p.page.evaluate(() => location.hash));
    const first = p.reads.filter((r) => !r.ahead)[0];
    check("and that screen still says where the link was given", first && first.from === "chat_offer", JSON.stringify(p.reads));
    check("no page threw an error", p.errors.length === 0, p.errors.join("; "));
    await p.ctx.close();

    console.log("signing in with no link");
    p = await open(browser, "http://localhost/app/", false);
    await p.page.fill("#email", "runner@example.com");
    await p.page.click("#email-form button");
    await p.page.waitForTimeout(300);
    await p.page.fill("#code", "123456");
    await p.page.click("#code-form button");
    await p.page.waitForTimeout(800);
    check("opens Today, as before", (await p.page.evaluate(() => location.hash)) === "#/today",
      await p.page.evaluate(() => location.hash));
    check("with no source", p.reads.every((r) => !r.from), JSON.stringify(p.reads));
    await p.ctx.close();
  } finally {
    await browser.close();
  }
  console.log(failed ? `\n${failed} failed` : "\nall checks passed");
  process.exit(failed ? 1 : 0);
})();

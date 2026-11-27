// The chat pilot's tab (Nov 27), in a real browser against a mocked API: no Chat tab unless
// /app/api/features says so, the starter chips send a message, the reply streams in, a proposed
// change is a card that does nothing until Confirm, Decline cancels, and thumbs are kept.
// Run: node tests/app_chat_test.js  (needs playwright and Chromium)

const fs = require("fs");
const path = require("path");
const { chromium, devices } = require("playwright");

const DIR = path.join(__dirname, "..", "garmin_mcp", "app");
const SHOTS = path.join(__dirname, "..", "garmin-screens");
const TYPES = { ".js": "text/javascript", ".css": "text/css", ".html": "text/html", ".webmanifest": "application/json", ".svg": "image/svg+xml", ".png": "image/png", ".woff2": "font/woff2" };
let failed = 0;
function check(name, ok, detail) {
  console.log(`  ${ok ? "PASS" : "FAIL"}  ${name}${!ok && detail ? " — " + String(detail).slice(0, 300) : ""}`);
  if (!ok) failed += 1;
}

const STARTERS = ["How did this week go?", "I'm short on time tomorrow, adjust", "Move my long run to Saturday"];
const sse = (...events) => events.map(([e, d]) => `event: ${e}\ndata: ${JSON.stringify(d)}\n\n`).join("");

async function open(browser, { chat }) {
  const ctx = await browser.newContext({ ...devices["iPhone 13"], serviceWorkers: "block" });
  await ctx.addInitScript(() => { localStorage.setItem("daash.access", "x"); localStorage.setItem("daash.refresh", "y"); });
  const seen = { sends: [], actions: [], feedback: [] };
  await ctx.route("**/*", (r) => {
    const u = new URL(r.request().url());
    const body = r.request().postData();
    if (u.pathname === "/app/api/features") return r.fulfill({ json: chat ? { chat: true } : {} });
    if (u.pathname === "/app/api/chat") {
      return chat ? r.fulfill({ json: { messages: [], starters: STARTERS } }) : r.fulfill({ status: 404, json: { error: "Not found" } });
    }
    if (u.pathname === "/app/api/chat/send") {
      seen.sends.push(JSON.parse(body));
      const n = seen.sends.length * 10;
      return r.fulfill({ status: 200, contentType: "text/event-stream", body: sse(
        ["start", { user_id: n + 1, id: n + 2 }], ["tool", { name: "get_plan", label: "Reading your plan" }],
        ["delta", { text: "Saturday is free, so I've proposed moving " }], ["delta", { text: "your **long run** there." }],
        ["pending", { id: n + 3, tool: "move_session", text: "Move “Long run 18 km” from Sunday 29 Nov to Saturday 28 Nov. The watch follows.", status: "pending" }],
        ["done", { id: n + 2, status: "done" }]) });
    }
    if (u.pathname === "/app/api/chat/action") {
      const b = JSON.parse(body);
      seen.actions.push(b);
      return r.fulfill({ json: b.decision === "confirm" ? { id: b.id, status: "done", message: "Moved to Saturday. The watch follows at its next sync.", refresh: true }
        : { id: b.id, status: "declined", message: "Cancelled. Nothing changed." } });
    }
    if (u.pathname === "/app/api/chat/feedback") { seen.feedback.push(JSON.parse(body)); return r.fulfill({ json: { ok: true } }); }
    if (u.pathname === "/app/api/event") return r.fulfill({ json: { ok: true } });
    if (u.pathname.startsWith("/app/api/")) return r.fulfill({ json: { error: "not in this test" } });
    const fp = path.join(DIR, u.pathname.replace(/^\/app\/?/, "") || "index.html");
    if (fs.existsSync(fp) && fs.statSync(fp).isFile()) return r.fulfill({ body: fs.readFileSync(fp), contentType: TYPES[path.extname(fp)] || "application/octet-stream" });
    return r.fulfill({ status: 404, body: "" });
  });
  const page = await ctx.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  return { ctx, page, seen, errors };
}

(async () => {
  const browser = await chromium.launch();
  fs.mkdirSync(SHOTS, { recursive: true });

  console.log("not allowlisted");
  {
    const { ctx, page, errors } = await open(browser, { chat: false });
    await page.goto("http://localhost/app/#/chat");
    await page.waitForTimeout(1200);
    const tabs = await page.$$eval("#tabs a", (as) => as.map((a) => a.textContent.trim()));
    check("no Chat tab", !tabs.includes("Chat") && tabs.length === 4, tabs);
    check("#/chat opens Today instead, and the address says so", (await page.evaluate(() => document.body.dataset.screen)) === "today"
      && page.url().endsWith("#/today"), page.url());
    check("no page threw an error", !errors.length, errors);
    await ctx.close();
  }

  console.log("allowlisted");
  {
    const { ctx, page, seen, errors } = await open(browser, { chat: true });
    await page.goto("http://localhost/app/#/plan");
    await page.waitForTimeout(1000);
    let tabs = await page.$$eval("#tabs a", (as) => as.map((a) => a.textContent.trim()));
    check("the Chat tab appears once the server says so, before You", tabs.join(",") === "Today,Plan,Crew,Chat,You", tabs);
    await page.click("#tabs a[href='#/chat']");
    await page.waitForTimeout(600);
    const chips = await page.$$eval("[data-starter]", (b) => b.map((x) => x.textContent.trim()));
    check("an empty thread shows the starter chips", chips.join("|") === STARTERS.join("|"), chips);
    await page.screenshot({ path: path.join(SHOTS, "chat-empty.png") });
    await page.click(`[data-starter="${STARTERS[2]}"]`);
    await page.waitForTimeout(800);
    check("a chip sends its words, marked as a starter", seen.sends[0] && seen.sends[0].text === STARTERS[2] && seen.sends[0].starter === true, JSON.stringify(seen.sends));
    const reply = await page.$$eval(".msg.ai", (m) => m.map((x) => x.textContent));
    check("the streamed reply is drawn, bold and all", reply.some((t) => t.includes("Saturday is free, so I've proposed moving your long run there."))
      && (await page.$(".msg.ai strong")) !== null, reply);
    check("the proposed change is a card with Confirm and Decline", (await page.$("[data-act=confirm]")) !== null && (await page.$("[data-act=decline]")) !== null);
    check("and nothing has been confirmed", seen.actions.length === 0);
    await page.screenshot({ path: path.join(SHOTS, "chat-proposed.png") });
    await page.click("[data-act=confirm]");
    await page.waitForTimeout(600);
    check("Confirm sends that change's id", seen.actions.length === 1 && seen.actions[0].id === 13 && seen.actions[0].decision === "confirm", JSON.stringify(seen.actions));
    check("the card says done, and what happened follows", (await page.textContent(".chat-action .kicker")).trim() === "Done"
      && (await page.textContent("#chat-log")).includes("Moved to Saturday"));
    check("the buttons are gone, so it can't be confirmed twice", (await page.$("[data-act=confirm]")) === null);
    await page.click("[data-fb=up]");
    await page.waitForTimeout(300);
    check("thumbs up is sent and shown", seen.feedback[0] && seen.feedback[0].id === 12 && seen.feedback[0].value === "up"
      && (await page.getAttribute("[data-fb=up]", "aria-pressed")) === "true", JSON.stringify(seen.feedback));
    await page.screenshot({ path: path.join(SHOTS, "chat-done.png") });

    // Typed, then declined.
    await page.fill("#chat-input", "Move it back");
    await page.press("#chat-input", "Enter");
    await page.waitForTimeout(800);
    check("a typed message sends on Enter, not as a starter", seen.sends[1] && seen.sends[1].text === "Move it back" && seen.sends[1].starter === false, JSON.stringify(seen.sends[1]));
    await page.click("[data-act=decline]");
    await page.waitForTimeout(500);
    check("Decline cancels it", seen.actions[1] && seen.actions[1].decision === "decline"
      && (await page.$$eval(".chat-action .kicker", (k) => k.map((x) => x.textContent.trim()))).includes("Cancelled"));
    tabs = await page.$$eval("#tabs a", (as) => as.map((a) => a.textContent.trim()));
    check("five tabs fit on a phone", tabs.length === 5 && await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
    check("no page threw an error", !errors.length, errors);
    await ctx.close();
  }

  await browser.close();
  console.log(failed ? `\n${failed} failed` : "\nall checks passed");
  process.exit(failed ? 1 : 0);
})();

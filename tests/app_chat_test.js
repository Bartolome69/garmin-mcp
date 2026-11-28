// The chat pilot's tab (Nov 27), in a real browser against a mocked API: no Chat tab unless
// /app/api/features says so, the starter chips send a message, the reply streams in, a proposed
// change is a card that does nothing until Confirm, Decline cancels, and thumbs are kept. And
// (Nov 28) on a phone the keyboard doesn't push the screen up: the title stays, the messages
// scroll in their own column, and the box sits on the keyboard with the tabs out of the way.
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

async function open(browser, { chat, device = "iPhone 13", history = [] }) {
  const ctx = await browser.newContext({ ...devices[device], serviceWorkers: "block" });
  await ctx.addInitScript(() => { localStorage.setItem("daash.access", "x"); localStorage.setItem("daash.refresh", "y"); });
  const seen = { sends: [], actions: [], feedback: [] };
  await ctx.route("**/*", (r) => {
    const u = new URL(r.request().url());
    const body = r.request().postData();
    if (u.pathname === "/app/api/features") return r.fulfill({ json: chat ? { chat: true } : {} });
    if (u.pathname === "/app/api/chat") {
      return chat ? r.fulfill({ json: { messages: history, starters: STARTERS } }) : r.fulfill({ status: 404, json: { error: "Not found" } });
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

  console.log("keyboard on a phone");
  {
    const history = [];
    for (let i = 1; i <= 14; i += 1) {
      history.push({ id: i * 2 - 1, role: "user", text: `Question ${i} about this week's running?` });
      history.push({ id: i * 2, role: "assistant", status: "done", text: `Answer ${i}. Your easy runs were around 5:10 to 5:20/km at 120 to 126 bpm, which looks well controlled. What's left is Sunday's 18 km long run.` });
    }
    const { ctx, page, errors } = await open(browser, { chat: true, device: "Pixel 7", history });
    await page.goto("http://localhost/app/#/plan");
    await page.waitForTimeout(1000);
    await page.click("#tabs a[href='#/chat']");
    await page.waitForTimeout(800);
    const layout = () => page.evaluate(() => {
      const r = (sel) => { const e = document.querySelector(sel); if (!e) return null; const b = e.getBoundingClientRect(); return { top: b.top, bottom: b.bottom, shown: getComputedStyle(e).display !== "none" && b.height > 0 }; };
      const log = document.getElementById("chat-log");
      const msgs = [...document.querySelectorAll("#chat-log .msg")];
      const last = msgs[msgs.length - 1].getBoundingClientRect();
      return { h: window.innerHeight, scrollY: window.scrollY, head: r(".chat-wrap > .section-head"), bar: r("#chat-form"), tabs: r("#tabs"),
        logScrolls: log.scrollHeight > log.clientHeight, lastBottom: last.bottom, kb: document.body.classList.contains("kb") };
    });
    let l = await layout();
    check("a long thread scrolls inside its column, not the page", l.logScrolls && l.scrollY === 0, JSON.stringify(l));
    check("the title is at the top", l.head && l.head.shown && l.head.top >= 0 && l.head.top < 40, JSON.stringify(l.head));
    check("the box sits on the tabs", l.bar && l.tabs && l.tabs.shown && Math.abs(l.bar.bottom - l.tabs.top) <= 2, JSON.stringify({ bar: l.bar, tabs: l.tabs }));
    check("it opens at the newest message, just above the box", l.lastBottom <= l.bar.top + 1 && l.lastBottom > l.bar.top - 140, JSON.stringify(l));
    await page.screenshot({ path: path.join(SHOTS, "chat-phone.png") });

    // The keyboard comes up: Android shrinks the page (interactive-widget=resizes-content).
    const full = page.viewportSize();
    await page.focus("#chat-input");
    await page.setViewportSize({ width: full.width, height: full.height - 330 });
    await page.waitForTimeout(400);
    l = await layout();
    check("with the keyboard up the page doesn't move", l.scrollY === 0, JSON.stringify(l));
    check("the title stays in view", l.head && l.head.shown && l.head.top >= 0 && l.head.top < 40, JSON.stringify(l.head));
    check("the tabs step aside", l.kb && l.tabs && !l.tabs.shown, JSON.stringify(l.tabs));
    check("the box sits on the keyboard", l.bar && Math.abs(l.bar.bottom - l.h) <= 2, JSON.stringify({ bar: l.bar, h: l.h }));
    check("the newest message stays in view above the box", l.lastBottom <= l.bar.top + 1 && l.lastBottom > l.head.bottom, JSON.stringify(l));
    await page.screenshot({ path: path.join(SHOTS, "chat-phone-keyboard.png") });

    // Typing a long message grows the box; the newest message stays in view.
    await page.fill("#chat-input", "One\ntwo\nthree\nfour lines of a longer question about Sunday");
    await page.dispatchEvent("#chat-input", "input");
    await page.waitForTimeout(200);
    l = await layout();
    check("a growing box keeps the newest message in view", l.lastBottom <= l.bar.top + 1 && Math.abs(l.bar.bottom - l.h) <= 2, JSON.stringify(l));

    // The keyboard goes down: the tabs come back under the box.
    await page.fill("#chat-input", "");
    await page.evaluate(() => document.activeElement.blur());
    await page.setViewportSize(full);
    await page.waitForTimeout(500);
    l = await layout();
    check("with the keyboard down the tabs are back under the box", !l.kb && l.tabs.shown && Math.abs(l.bar.bottom - l.tabs.top) <= 2, JSON.stringify(l));

    // Other screens: the tabs step aside for the keyboard there too, and come back.
    await page.goto("http://localhost/app/#/plan");
    await page.waitForTimeout(600);
    check("off the chat the page scrolls normally again", await page.evaluate(() => getComputedStyle(document.body).overflowY !== "hidden"));
    check("no page threw an error", !errors.length, errors);
    await ctx.close();
  }

  console.log("keyboard on a phone, early in a thread");
  {
    const history = [{ id: 1, role: "user", text: "How did this week go?" },
      { id: 2, role: "assistant", status: "done", text: "Four runs, all easy. What's left is Sunday's 18 km long run." }];
    const { ctx, page, errors } = await open(browser, { chat: true, device: "Pixel 7", history });
    await page.goto("http://localhost/app/#/plan");
    await page.waitForTimeout(1000);
    await page.click("#tabs a[href='#/chat']");
    await page.waitForTimeout(800);
    const full = page.viewportSize();
    await page.focus("#chat-input");
    await page.setViewportSize({ width: full.width, height: full.height - 330 });
    await page.waitForTimeout(400);
    const l = await page.evaluate(() => {
      const box = (sel) => document.querySelector(sel).getBoundingClientRect();
      return { scrollY: window.scrollY, h: window.innerHeight, head: box(".chat-wrap > .section-head"),
        first: box("#chat-log .msg"), bar: box("#chat-form") };
    });
    check("a short thread stays just under the title with the keyboard up, not pushed off the top",
      l.scrollY === 0 && l.head.top >= 0 && l.first.top >= l.head.bottom && l.first.top < l.head.bottom + 40, JSON.stringify(l));
    check("and the box is on the keyboard", Math.abs(l.bar.bottom - l.h) <= 2, JSON.stringify(l));
    await page.screenshot({ path: path.join(SHOTS, "chat-phone-keyboard-short.png") });
    check("no page threw an error", !errors.length, errors);
    await ctx.close();
  }

  await browser.close();
  console.log(failed ? `\n${failed} failed` : "\nall checks passed");
  process.exit(failed ? 1 : 0);
})();

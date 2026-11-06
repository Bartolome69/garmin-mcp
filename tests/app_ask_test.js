// The companion app's Ask sheet, in a real browser.
//
// Every Ask used to open a new chat, and people ended up with dozens of
// threads (Nov 6). Ask now offers Back to my chat (the question copied, the
// saved chat or the list of chats opened) or New chat (as before), and can
// remember one chat. On an iPhone, Back to my chat goes through claude.ai/new,
// the one address of the two the Claude app claims, so it opens the app.
// This serves the app's files with its API stubbed, as an iPhone, an Android
// phone and a computer.
//
// Run: node tests/app_ask_test.js   (needs the playwright package and Chromium)

const fs = require("fs");
const path = require("path");
const { chromium, devices } = require("playwright");

const DIR = path.join(__dirname, "..", "garmin_mcp", "app");
const TYPES = { ".js": "text/javascript", ".css": "text/css", ".html": "text/html", ".webmanifest": "application/json", ".svg": "image/svg+xml", ".png": "image/png" };
const QUESTION = "Use Daash to start a crew for me and the friends I run with.";
const NEW = "https://claude.ai/new?q=" + encodeURIComponent(QUESTION);
const CHAT = "https://claude.ai/chat/0b1c2d3e-aaaa-bbbb-cccc-1234567890ab";

let failed = 0;
function check(name, ok, detail) {
  console.log(`  ${ok ? "PASS" : "FAIL"}  ${name}${!ok && detail ? " — " + String(detail).slice(0, 300) : ""}`);
  if (!ok) failed += 1;
}

async function phone(browser, device, opens) {
  const ctx = await browser.newContext({ ...device, serviceWorkers: "block" });
  await ctx.addInitScript((o) => {
    localStorage.setItem("daash.access", "x"); localStorage.setItem("daash.refresh", "y");
    if (o) localStorage.setItem("daash.opens", o);
    window.__opened = []; window.open = (u) => { window.__opened.push(u); return {}; };
    Object.defineProperty(navigator, "clipboard", { value: { writeText: (t) => { window.__copied = t; return Promise.resolve(); } } });
  }, opens || null);
  const events = [], visited = [];
  await ctx.route("**/*", (r) => {
    const u = new URL(r.request().url());
    // A link tapped in the sheet: where it went, and nothing loaded.
    if (u.hostname !== "localhost") { visited.push(r.request().url()); return r.fulfill({ status: 200, body: "" }); }
    if (u.pathname === "/app/api/event") { events.push(JSON.parse(r.request().postData())); return r.fulfill({ json: { ok: true } }); }
    if (u.pathname.startsWith("/app/api/crew")) {
      return r.fulfill({ json: { none: true, assistant: "Claude", ask: { claude: NEW,
        chatgpt: "https://chatgpt.com/?q=" + encodeURIComponent(QUESTION), claude_app: "claude://claude.ai/new?q=" + encodeURIComponent(QUESTION) } } });
    }
    if (u.pathname.startsWith("/app/api/")) return r.fulfill({ json: { error: "not in this test" } });
    const fp = path.join(DIR, u.pathname.replace(/^\/app\/?/, "") || "index.html");
    if (fs.existsSync(fp) && fs.statSync(fp).isFile()) return r.fulfill({ body: fs.readFileSync(fp), contentType: TYPES[path.extname(fp)] || "application/octet-stream" });
    return r.fulfill({ status: 404, body: "" });
  });
  const page = await ctx.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("http://localhost/app/#/crew");
  await page.waitForTimeout(1200);
  const state = async () => ({ ...(await page.evaluate(() => ({ opened: window.__opened.slice(), copied: window.__copied || null,
    toast: document.getElementById("toast").textContent, sheet: !!document.querySelector(".sheet") }))), visited: visited.slice() });
  const ask = async () => { await page.click("text=Start a crew with Claude"); await page.waitForTimeout(300); };
  const tap = async (sel) => { await page.click(sel); await page.waitForTimeout(500); };
  return { ctx, page, events, errors, visited, state, ask, tap };
}

(async () => {
  const browser = await chromium.launch();
  try {
    console.log("an iPhone");
    let p = await phone(browser, devices["iPhone 13"]);
    await p.ask();
    let got = await p.state();
    check("Ask asks first, rather than opening a new chat", got.sheet && got.visited.length === 0, JSON.stringify(got));
    check("showing the question", (await p.page.textContent(".sheet")).includes(QUESTION));
    check("no saved chat offered: one can't open in the iPhone app", !(await p.page.$("#ask-change")));
    check("Back to my chat is a link to claude.ai/new, which the Claude app opens",
      (await p.page.getAttribute("#ask-mine", "href")) === "https://claude.ai/new" && /Claude app/.test(await p.page.textContent(".sheet")));
    await p.tap("#ask-mine");
    got = await p.state();
    check("tapped, it copies the question and goes there", got.copied === QUESTION && got.visited[0] === "https://claude.ai/new" && !got.sheet, JSON.stringify(got));
    await p.ask();
    await p.tap("#ask-new");
    got = await p.state();
    check("New chat goes to a new chat with the question, as before", got.visited[1] === NEW, JSON.stringify(got));
    const sent = p.events.map((e) => e.name + ":" + (e.properties.choice || ""));
    check("each Ask is counted once, with the choice",
      sent.join() === "ask_clicked:,ask_chosen:my_chat,ask_clicked:,ask_chosen:new_chat", JSON.stringify(p.events));
    check("no page threw an error", p.errors.length === 0, p.errors.join("; "));
    await p.ctx.close();

    console.log("an Android phone");
    p = await phone(browser, devices["Pixel 5"]);
    await p.ask();
    check("Back to my chat opens the list of chats", (await p.page.getAttribute("#ask-mine", "href")) === "https://claude.ai/recents");
    await p.tap("#ask-change");
    await p.page.fill("#ask-url", "https://claude.ai/share/0b1c2d3e-aaaa");
    await p.tap("#ask-save");
    got = await p.state();
    check("a share link is refused: it opens a public copy",
      /share link/.test(got.toast) && !(await p.page.evaluate(() => localStorage.getItem("daash.chat.claude"))), got.toast);
    await p.page.fill("#ask-url", "https://chatgpt.com/c/abcdef123456");
    await p.tap("#ask-save");
    check("and so is another assistant's chat", /isn't a Claude chat/.test((await p.state()).toast));
    await p.page.fill("#ask-url", CHAT + "?foo=1");
    await p.tap("#ask-save");
    check("the chat's own address is kept, without anything after it",
      (await p.page.evaluate(() => localStorage.getItem("daash.chat.claude"))) === CHAT && /Change/.test(await p.page.textContent(".sheet")));
    await p.tap("#ask-mine");
    got = await p.state();
    check("then Back to my chat opens that chat, the question copied", got.visited[0] === CHAT && got.copied === QUESTION, JSON.stringify(got));
    await p.ask();
    await p.tap("#ask-forget");
    check("and Forget forgets it", (await p.page.evaluate(() => localStorage.getItem("daash.chat.claude"))) === null);
    check("the saved chat's address is never sent", !JSON.stringify(p.events).includes("claude.ai/chat")
      && p.events.some((e) => e.name === "ask_chat_saved"), JSON.stringify(p.events));
    check("no page threw an error", p.errors.length === 0, p.errors.join("; "));
    await p.ctx.close();

    console.log("a computer, Claude in the browser");
    p = await phone(browser, { viewport: { width: 1280, height: 800 } }, "web");
    await p.page.evaluate((c) => localStorage.setItem("daash.chat.claude", c), CHAT);
    await p.ask();
    await p.tap("#ask-mine");
    got = await p.state();
    check("Back to my chat opens the saved chat", got.opened[0] === CHAT && got.visited.length === 0, JSON.stringify(got));
    await p.ask();
    await p.tap("#ask-new");
    got = await p.state();
    check("New chat opens a new chat with the question", got.opened[1] === NEW, JSON.stringify(got));
    check("no page threw an error", p.errors.length === 0, p.errors.join("; "));
    await p.ctx.close();
  } finally {
    await browser.close();
  }
  console.log(failed ? `\n${failed} failed` : "\nall checks passed");
  process.exit(failed ? 1 : 0);
})();

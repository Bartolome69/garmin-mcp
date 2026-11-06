// The companion app's Ask sheet, in a real browser.
//
// Every Ask used to open a new chat, and people ended up with dozens of
// threads (Nov 6). Ask now offers Back to my chat (the question copied, the
// saved chat or the list of chats opened) or New chat (as before), and can
// remember one chat. This serves the app's files with its API stubbed.
//
// Run: node tests/app_ask_test.js   (needs the playwright package and Chromium)

const fs = require("fs");
const path = require("path");
const { chromium, devices } = require("playwright");

const DIR = path.join(__dirname, "..", "garmin_mcp", "app");
const TYPES = { ".js": "text/javascript", ".css": "text/css", ".html": "text/html", ".webmanifest": "application/json", ".svg": "image/svg+xml", ".png": "image/png" };
const QUESTION = "Use Daash to start a crew for me and the friends I run with.";
const CHAT = "https://claude.ai/chat/0b1c2d3e-aaaa-bbbb-cccc-1234567890ab";

let failed = 0;
function check(name, ok, detail) {
  console.log(`  ${ok ? "PASS" : "FAIL"}  ${name}${!ok && detail ? " — " + String(detail).slice(0, 300) : ""}`);
  if (!ok) failed += 1;
}

(async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ ...devices["iPhone 13"], serviceWorkers: "block" });
  await ctx.addInitScript(() => {
    localStorage.setItem("daash.access", "x"); localStorage.setItem("daash.refresh", "y");
    window.__opened = []; window.open = (u) => { window.__opened.push(u); return {}; };
    Object.defineProperty(navigator, "clipboard", { value: { writeText: (t) => { window.__copied = t; return Promise.resolve(); } } });
  });
  const events = [];
  await ctx.route("**/*", (r) => {
    const u = new URL(r.request().url());
    if (u.pathname === "/app/api/event") { events.push(JSON.parse(r.request().postData())); return r.fulfill({ json: { ok: true } }); }
    if (u.pathname.startsWith("/app/api/crew")) {
      return r.fulfill({ json: { none: true, assistant: "Claude", ask: { claude: "https://claude.ai/new?q=" + encodeURIComponent(QUESTION),
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
  const state = () => page.evaluate(() => ({ opened: window.__opened.slice(), copied: window.__copied || null,
    toast: document.getElementById("toast").textContent, sheet: !!document.querySelector(".sheet") }));
  try {
    await page.goto("http://localhost/app/#/crew");
    await page.waitForTimeout(1200);

    console.log("Ask, with no chat saved");
    await page.click("text=Start a crew with Claude");
    await page.waitForTimeout(300);
    let got = await state();
    check("asks first, rather than opening a new chat", got.sheet && got.opened.length === 0, JSON.stringify(got));
    check("showing the question", (await page.textContent(".sheet")).includes(QUESTION));
    await page.click("#ask-mine");
    await page.waitForTimeout(300);
    got = await state();
    check("Back to my chat copies the question and opens the list of chats",
      got.copied === QUESTION && got.opened[0] === "https://claude.ai/recents" && !got.sheet && /copied/.test(got.toast), JSON.stringify(got));

    console.log("New chat");
    await page.click("text=Start a crew with Claude");
    await page.waitForTimeout(300);
    await page.click("#ask-new");
    await page.waitForTimeout(300);
    got = await state();
    check("opens a new chat with the question, as before", got.opened[1] === "https://claude.ai/new?q=" + encodeURIComponent(QUESTION), JSON.stringify(got));

    console.log("Remembering a chat");
    await page.click("text=Start a crew with Claude");
    await page.waitForTimeout(300);
    await page.click("#ask-change");
    await page.fill("#ask-url", "https://claude.ai/share/0b1c2d3e-aaaa");
    await page.click("#ask-save");
    await page.waitForTimeout(200);
    got = await state();
    check("a share link is refused: it opens a public copy", /share link/.test(got.toast) && !(await page.evaluate(() => localStorage.getItem("daash.chat.claude"))), got.toast);
    await page.fill("#ask-url", "https://chatgpt.com/c/abcdef123456");
    await page.click("#ask-save");
    await page.waitForTimeout(200);
    check("and so is another assistant's chat", /isn't a Claude chat/.test((await state()).toast));
    await page.fill("#ask-url", CHAT + "?foo=1");
    await page.click("#ask-save");
    await page.waitForTimeout(300);
    check("the chat's own address is kept, without anything after it",
      (await page.evaluate(() => localStorage.getItem("daash.chat.claude"))) === CHAT && /Change/.test(await page.textContent(".sheet")));
    await page.click("#ask-mine");
    await page.waitForTimeout(300);
    got = await state();
    check("then Back to my chat opens that chat, the question copied", got.opened[2] === CHAT && got.copied === QUESTION, JSON.stringify(got));
    await page.click("text=Start a crew with Claude");
    await page.waitForTimeout(300);
    await page.click("#ask-forget");
    await page.waitForTimeout(200);
    check("and Forget forgets it", (await page.evaluate(() => localStorage.getItem("daash.chat.claude"))) === null);

    const sent = events.map((e) => e.name + ":" + (e.properties.choice || ""));
    check("each tap is counted, with the choice and never the chat's address",
      sent.filter((x) => x.startsWith("ask_chosen")).join() === "ask_chosen:my_chat,ask_chosen:new_chat,ask_chosen:my_chat"
      && sent.includes("ask_chat_saved:") && !JSON.stringify(events).includes("claude.ai/chat"), JSON.stringify(events));
    check("no page threw an error", errors.length === 0, errors.join("; "));
  } finally {
    await browser.close();
  }
  console.log(failed ? `\n${failed} failed` : "\nall checks passed");
  process.exit(failed ? 1 : 0);
})();

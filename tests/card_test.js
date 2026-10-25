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
  weeks: [{ week: 1, starts: md(0), current: true, sessions: [{ date: md(2), day: "Wed", name: "Long 18km w 3x2km sub-T", type: "long",
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

// The chat around the card: answers its handshake, sends it a tool result,
// answers the tools it calls, and keeps any link it asks to open.
function host(result) {
  return `<!doctype html><body><iframe id=f style="width:420px;height:1100px;border:0"></iframe><script>
const f = document.getElementById("f"); window.opened = null;
window.addEventListener("message", (e) => {
  const m = e.data; if (!m || !m.method) return;
  const reply = (r) => f.contentWindow.postMessage({ jsonrpc: "2.0", id: m.id, result: r }, "*");
  if (m.method === "ui/initialize") reply({ hostInfo: { name: "test" }, hostCapabilities: { openLinks: {} }, hostContext: { theme: "light", displayMode: "inline" } });
  if (m.method === "ui/notifications/initialized") f.contentWindow.postMessage({ jsonrpc: "2.0", method: "ui/notifications/tool-result", params: { structuredContent: ${JSON.stringify(result)}, content: [] } }, "*");
  if (m.method === "tools/call") reply({ structuredContent: m.params.name === "get_activity_details" ? ${JSON.stringify(DETAILS)} : { error: "not in this test" } });
  if (m.method === "ui/open-link") { window.opened = m.params.url; reply({}); }
});
f.src = "card.html";
</script></body>`;
}

function decode(url) {
  const b = url.split("#")[1].replace(/-/g, "+").replace(/_/g, "/");
  return JSON.parse(Buffer.from(b, "base64").toString("utf8"));
}

(async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "daash-card-"));
  fs.writeFileSync(path.join(dir, "card.html"), CARD);
  const browser = await chromium.launch();
  const errors = [];
  async function open(result) {
    fs.writeFileSync(path.join(dir, "host.html"), host(result));
    const page = await browser.newPage({ viewport: { width: 420, height: 1100 } });
    page.on("pageerror", (e) => errors.push(e.message));
    await page.goto("file://" + path.join(dir, "host.html"));
    await page.waitForTimeout(1200);
    return { page, card: page.frames()[1] };
  }

  try {
    console.log("a progress answer");
    let { page, card } = await open(PROGRESS);
    let text = await card.evaluate(() => document.body.innerText);
    check("draws the card rather than the error box", !/didn.t load|not a function/.test(text) && /This week|Week/.test(text), text.slice(0, 200));
    await page.close();

    console.log("an opened run");
    ({ page, card } = await open(PLAN));
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

    check("no page threw an error", errors.length === 0, errors.join(" | "));
  } finally {
    await browser.close();
    fs.rmSync(dir, { recursive: true, force: true });
  }
  console.log(failed ? `\n${failed} check(s) failed` : "\nall checks passed");
  process.exit(failed ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(1); });

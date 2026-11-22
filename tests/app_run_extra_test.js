// A run longer than its plan, in a real browser (Nov 22): its title is what was
// run, the plan in a muted line under it, the planned figures under the stats,
// and the chart's dashed "Plan ends" line with the extra km tinted apart. The
// app's files are served as they ship; its API is stubbed with the answer the
// server gave for a 12 km easy run taken on to 14.2 km (app_test.py checks it).
//
// Run: node tests/app_run_extra_test.js   (needs the playwright package and Chromium)

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

const TODAY = "2026-11-22";
const SECS = [305, 310, 311, 316, 314, 306, 303, 303, 286, 302, 292, 291, 286, 264, 45];
const HRS = [120, 126, 133, 136, 136, 134, 138, 135, 143, 144, 144, 147, 144, 149, 159];
const RUN = {
  activity_id: 25163176203, date: TODAY, kicker: "SUN 22 NOV · 12:34 · EASY", name: "14.2 km easy",
  planned_line: "Planned 12 km · ran 2.2 km extra", type: "easy", type_label: "Easy", colour: "easy",
  verdict: "Ran 2.2 km longer than planned. Heart rate on target until km 12.",
  stats: [{ v: "14.2", l: "km", plan: "12 planned" }, { v: "1:10:34", l: "time" }, { v: "4:59", l: "/km avg" },
    { v: "138", l: "bpm avg", plan: "120–145 planned" }],
  chart: { mode: "km", total_km: 14.2, plan_ends: 12, plan_km: 12, caption: "Heart rate went over 145 in km 12.",
    band: { kind: "hr", lo: 120, hi: 145, text: "120–145" },
    parts: SECS.map((s, i) => ({ k: "km", s, p: i === 14 ? 281 : s, hr: HRS[i], label: i === 14 ? "0.2" : String(i + 1) })) },
  planned_and_run: [{ label: "Run", kind: "interval", note: "12 km", target: "120–145 bpm", pace: "5:03", hr: 136, colour: "easy" },
    { label: "Ran on", kind: "extra", note: "2.2 km after the workout", pace: "4:35", hr: 147, colour: "rec" }],
  km: 14.2, assistant: "Claude", ask: { claude: "https://claude.ai/new?q=x", chatgpt: "https://chatgpt.com/?q=x", claude_app: "claude://claude.ai/new?q=x" },
};

async function open(browser, hash, answers, look = {}) {
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
    console.log("a run longer than its plan");
    const shot = process.env.SHOT;
    const p = await open(browser, `/run/${RUN.activity_id}?from=plan`, (key) => key === `GET run/${RUN.activity_id}` ? { json: RUN } : null);
    await p.page.waitForSelector(".run-chart svg");
    check("the title is what was run", (await p.page.textContent("h1")) === "14.2 km easy");
    check("with the plan in a muted line under it", (await p.page.textContent(".planned-line")) === "Planned 12 km · ran 2.2 km extra");
    check("the verdict leads with the extra and how the planned part went",
      (await p.page.textContent(".verdict")) === "Ran 2.2 km longer than planned. Heart rate on target until km 12.");
    const stats = await p.page.$$eval(".stats4 > div", (xs) => xs.map((x) => [...x.children].map((c) => c.textContent)));
    check("each number has what was planned in small grey under it, where the plan set one",
      JSON.stringify(stats) === JSON.stringify([["14.2", "km", "12 planned"], ["1:10:34", "time", ""], ["4:59", "/km avg", ""], ["138", "bpm avg", "120–145 planned"]]), JSON.stringify(stats));
    const chart = await p.page.$eval(".run-chart svg", (svg) => {
      const bars = [...svg.querySelectorAll("rect")].filter((r) => r.getAttribute("rx") === "3");
      const dash = [...svg.querySelectorAll("line")].find((l) => l.getAttribute("stroke-dasharray") === "4 3");
      const texts = [...svg.querySelectorAll("text")].map((t) => t.textContent);
      const band = [...svg.querySelectorAll("rect")].find((r) => r.getAttribute("rx") === "4");
      return { n: bars.length, tinted: bars.map((b) => (b.getAttribute("fill") || "").startsWith("color-mix")),
        bar12End: Number(bars[11].getAttribute("x")) + Number(bars[11].getAttribute("width")), bar13: Number(bars[12].getAttribute("x")),
        line: dash ? Number(dash.getAttribute("x1")) : null, texts, bandEnd: band ? Number(band.getAttribute("width")) : null };
    });
    check("a dashed line after km 12, labelled Plan ends",
      chart.line !== null && chart.line > chart.bar12End && chart.line < chart.bar13 && chart.texts.includes("Plan ends"), JSON.stringify(chart));
    check("the bars after it are tinted apart, the ones before it aren't",
      JSON.stringify(chart.tinted) === JSON.stringify([...Array(12).fill(false), true, true, true]), JSON.stringify(chart.tinted));
    check("the heart rate target band stops where the plan does", chart.bandEnd !== null && chart.bandEnd <= chart.line + 2, JSON.stringify(chart));
    check("the axis ends at the run's whole distance, not its last part km",
      chart.texts.includes("Km 14.2") && !chart.texts.includes("Km 0.2"), JSON.stringify(chart.texts));
    check("the key names the tinted bars", (await p.page.textContent(".chart-key")).includes("Past the plan"));
    check("no page threw an error", p.errors.length === 0, p.errors.join("; "));
    if (shot) {
      await p.ctx.close();
      for (const dark of [false, true]) {
        const q = await open(browser, `/run/${RUN.activity_id}?from=plan`, (key) => key === `GET run/${RUN.activity_id}` ? { json: RUN } : null, { scale: 2, dark });
        await q.page.screenshot({ path: `${shot}/run-extra-${dark ? "dark" : "light"}.png`, fullPage: true });
        await q.ctx.close();
      }
    } else {
      await p.ctx.close();
    }
  } finally {
    await browser.close();
  }
  console.log(failed ? `\n${failed} check(s) failed` : "\nall checks passed");
  process.exit(failed ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(1); });

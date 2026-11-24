// The run screen (Nov 24), in a real browser, for four runs the server's own
// code answered (tests/run_screens.py writes them): "planned" said once, an
// effort verdict, and the strides drawn over the km bars where they happened.
//   a  8 km easy + strides run to 10.4 km, the strides lapped by hand
//   b  10 km easy + strides, the strides run as the workout's steps
//   c  an easy run with no plan and no strides
//   d  a 12 km easy plan stopped at 9.1 km
//
// Run: node tests/app_run_strides_test.js   (SHOT=dir also saves screenshots)

const fs = require("fs");
const path = require("path");
const { chromium } = require("playwright");

const DIR = path.join(__dirname, "..", "garmin_mcp", "app");
const RUNS = JSON.parse(fs.readFileSync(path.join(__dirname, "fixtures", "run_screens.json"), "utf8"));
const TYPES = { ".js": "text/javascript", ".css": "text/css", ".html": "text/html", ".webmanifest": "application/json", ".svg": "image/svg+xml", ".png": "image/png" };

let failed = 0;
function check(name, ok, detail) {
  console.log(`  ${ok ? "PASS" : "FAIL"}  ${name}${!ok && detail ? " — " + String(detail).slice(0, 300) : ""}`);
  if (!ok) failed += 1;
}

async function open(browser, run, look = {}) {
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, serviceWorkers: "block",
    deviceScaleFactor: look.scale || 1, colorScheme: look.dark ? "dark" : "light" });
  await ctx.addInitScript(() => { localStorage.setItem("daash.access", "x"); localStorage.setItem("daash.refresh", "y"); });
  await ctx.route("**/*", async (r) => {
    const u = new URL(r.request().url());
    if (u.hostname !== "localhost") return r.fulfill({ status: 200, body: "" });
    if (u.pathname === "/app/api/event") return r.fulfill({ json: { ok: true } });
    if (u.pathname === `/app/api/run/${run.activity_id}`) return r.fulfill({ json: run });
    if (u.pathname.startsWith("/app/api/")) return r.fulfill({ json: { error: "not in this test" } });
    const fp = path.join(DIR, u.pathname.replace(/^\/app\/?/, "") || "index.html");
    if (fs.existsSync(fp) && fs.statSync(fp).isFile()) return r.fulfill({ body: fs.readFileSync(fp), contentType: TYPES[path.extname(fp)] || "application/octet-stream" });
    return r.fulfill({ status: 404, body: "" });
  });
  const page = await ctx.newPage();
  await page.clock.install({ time: new Date("2026-11-24T09:30:00Z") });
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto(`http://localhost/app/#/run/${run.activity_id}?from=today`);
  await page.waitForSelector(".run-chart svg", { timeout: 5000 }).catch(() => {});
  await page.waitForTimeout(400);
  return { ctx, page, errors };
}

async function read(page) {
  return page.evaluate(() => {
    const svg = document.querySelector(".run-chart svg");
    const bars = svg ? [...svg.querySelectorAll("rect")].filter((r) => r.getAttribute("rx") === "3") : [];
    const spikes = svg ? [...svg.querySelectorAll("rect.stride-spike")].map((r) => Number(r.getAttribute("x"))) : [];
    const tall = svg ? Math.max(0, ...[...svg.querySelectorAll("rect.stride-spike")].map((r) => Number(r.getAttribute("height")))) : 0;
    const vals = svg ? [...svg.querySelectorAll("text.v")].map((t) => [Number(t.getAttribute("x")), Number(t.getAttribute("y"))]) : [];
    const tickTops = svg ? [...svg.querySelectorAll("rect.stride-spike")].map((r) => [Number(r.getAttribute("x")), Number(r.getAttribute("y"))]) : [];
    const bandRect = svg ? [...svg.querySelectorAll("rect")].find((r) => r.getAttribute("rx") === "4") : null;
    const dash = svg ? [...svg.querySelectorAll("line")].find((l) => l.getAttribute("stroke-dasharray") === "4 3") : null;
    const top = [document.querySelector(".planned-line"), document.querySelector(".verdict"), document.querySelector(".stats4"),
      document.querySelector(".run-chart")].filter(Boolean).map((x) => x.textContent).join(" ");
    return {
      h1: document.querySelector("h1")?.textContent, planned: document.querySelector(".planned-line")?.textContent || null,
      verdict: document.querySelector(".verdict")?.textContent,
      stats: [...document.querySelectorAll(".stats4 > div")].map((x) => [...x.children].map((c) => c.textContent)),
      bars: bars.map((b) => [Number(b.getAttribute("x")), Number(b.getAttribute("width"))]), spikes,
      line: dash ? Number(dash.getAttribute("x1")) : null,
      texts: svg ? [...svg.querySelectorAll("text")].map((t) => t.textContent) : [],
      key: document.querySelector(".chart-key")?.textContent || "", caption: document.querySelector(".chart-caption")?.textContent || null,
      plannedWords: (top.match(/planned/gi) || []).length, tall, vals, tickTops,
      bandW: bandRect ? Number(bandRect.getAttribute("width")) : null, width: svg ? Number(svg.getAttribute("viewBox").split(" ")[2]) : null,
    };
  });
}

(async () => {
  const browser = await chromium.launch();
  const shot = process.env.SHOT;
  try {
    for (const [key, run] of Object.entries(RUNS)) {
      console.log(`run ${key}: ${run.name}`);
      const p = await open(browser, run);
      const s = await read(p.page);
      check("the title is the server's", s.h1 === run.name, s.h1);
      check("the verdict is the server's", s.verdict === run.verdict, s.verdict);
      check("no planned distance under the km", s.stats[0] && !s.stats[0][2], JSON.stringify(s.stats));
      check("no 'Past the plan' key", !s.key.includes("Past the plan"), s.key);
      if (key === "a") {
        check("'planned' appears once at the top: the line under the title", s.plannedWords === 1 && s.planned === "Planned about 7.9 km · ran 2.5 km extra", `${s.plannedWords} ${s.planned}`);
        check("the heart rate reads as the easy range", JSON.stringify(s.stats[3]) === JSON.stringify(["123", "bpm avg", "120–145 easy range"]), JSON.stringify(s.stats[3]));
        check("six strides drawn as spikes, all after the plan's end", s.spikes.length === 6 && s.spikes.every((x) => x > s.line), JSON.stringify(s));
        const [nineX] = s.bars[9];
        check("and where they happened: from the 10th km bar on, not in a block after the run", s.spikes.every((x) => x >= nineX - 2), JSON.stringify([s.spikes, s.bars[9]]));
        check("the key counts them", s.key.includes("6 × strides"), s.key);
        check("each is a short tick on its bar, not a spike on its own scale", s.tall > 0 && s.tall <= 14, String(s.tall));
        check("no pace label sits on a tick: every label is above the ticks under it",
          s.vals.every(([x, y]) => s.tickTops.filter(([tx]) => Math.abs(tx - x) < 14).every(([, ty]) => y < ty)), JSON.stringify([s.vals, s.tickTops]));
        check("the target band runs the full width, past the plan's end", s.bandW !== null && s.bandW >= s.width - 1, `${s.bandW} of ${s.width}`);
        check("Plan ends and Km 10.4 kept", s.texts.includes("Plan ends") && s.texts.includes("Km 10.4"), JSON.stringify(s.texts));
      }
      if (key === "b") {
        check("the workout's six strides drawn in the last km bars", s.spikes.length === 6 && s.spikes.every((x) => x >= s.bars[8][0]), JSON.stringify([s.spikes, s.bars.length]));
        check("no plan line: it ran about what was planned", s.line === null && s.planned === null);
      }
      if (key === "c") check("no strides, no range, no note", s.spikes.length === 0 && !s.caption && !s.key.includes("Target"), JSON.stringify(s));
      if (key === "d") check("stopped short: no plan line, no extra", s.line === null && s.planned === null && s.texts.includes("Km 9.1"), JSON.stringify(s.texts));
      check("no page threw an error", p.errors.length === 0, p.errors.join("; "));
      await p.ctx.close();
      if (shot) {
        const q = await open(browser, run, { scale: 2 });
        await q.page.screenshot({ path: `${shot}/run-${key}.png`, fullPage: false });
        await q.ctx.close();
      }
    }
  } finally {
    await browser.close();
  }
  console.log(failed ? `\n${failed} check(s) failed` : "\nall checks passed");
  process.exit(failed ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(1); });

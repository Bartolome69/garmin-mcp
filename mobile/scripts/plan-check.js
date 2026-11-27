#!/usr/bin/env node
// Plan's acceptance checks (build prompt, sections 5.3, 5.6 and 15), in a browser
// on the web build of the app at 390x844:
//
//   - the week fits on one screen: all seven rows above the fold, against the room
//     a phone actually leaves (iPhone: status bar 59, large-title bar 96, tab bar
//     with home indicator 83; Android: status bar 40, app bar 64, navigation bar
//     with gesture inset 104), measured from the top of the Week / Block / Trends
//     switch, which the web build draws where the phone does;
//   - on Android the extended FAB covers no row;
//   - a 10-rep session's detail fits without scrolling.
//
// The drag itself is tested in plan.test.tsx through the real PanResponder: the web
// version of native tabs stacks the other tabs' screens over the one shown, so a
// browser pointer never reaches the rows (on a phone an inactive tab takes no touches).
//
// The web build draws the Android layout (Material 3 tabs and the FAB), which is
// what the screenshots show. An approximation of a phone (the web uses the
// browser's system font), not a replacement for checking on one.
//
//   EXPO_PUBLIC_DAASH_FIXTURE=1 npx expo export --platform web --output-dir /tmp/web
//   node scripts/plan-check.js /tmp/web [screenshots-dir]

const fs = require('fs');
const http = require('http');
const path = require('path');
const { chromium } = require('playwright');

const ROOT = path.resolve(process.argv[2] || 'dist');
const SHOTS = process.argv[3];
const W = 390, H = 844;
const ROOM = { iPhone: H - 59 - 96 - 83, Android: H - 40 - 64 - 104 };
const SESSION_ROOM = H - 59 - 44 - 83; // a pushed screen: the standard bar, not the large title
const FAB = { bottom: 16, height: 56 };
const TYPES = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.png': 'image/png', '.ttf': 'font/ttf', '.json': 'application/json' };

function serve() {
  return new Promise((ok) => {
    const server = http.createServer((req, res) => {
      const url = decodeURIComponent(req.url.split('?')[0]);
      let file = path.join(ROOT, url);
      if (!file.startsWith(ROOT) || !fs.existsSync(file) || fs.statSync(file).isDirectory()) file = path.join(ROOT, 'index.html');
      res.writeHead(200, { 'content-type': TYPES[path.extname(file)] || 'application/octet-stream' });
      fs.createReadStream(file).pipe(res);
    });
    server.listen(0, () => ok(server));
  });
}

let failed = 0;
const check = (ok, words) => { console.log(`  ${ok ? 'PASS' : 'FAIL'}  ${words}`); if (!ok) failed++; };

(async () => {
  const server = await serve();
  const base = `http://localhost:${server.address().port}`;
  const browser = await chromium.launch();
  for (const scheme of ['dark', 'light']) {
    const page = await browser.newPage({ viewport: { width: W, height: H }, colorScheme: scheme, deviceScaleFactor: SHOTS ? 2 : 1 });
    const errors = [];
    page.on('pageerror', (e) => errors.push(e.message));
    const box = async (id) => page.locator(`[data-testid="${id}"]`).first().boundingBox();
    const shot = async (name) => { if (SHOTS) { fs.mkdirSync(SHOTS, { recursive: true }); await page.screenshot({ path: path.join(SHOTS, `${name}-${scheme}.png`) }); } };

    await page.goto(`${base}/plan`);
    await page.waitForSelector('[data-testid="week-title"]', { timeout: 20000 });
    await page.waitForTimeout(600);
    const title = (await page.locator('[data-testid="week-title"]').first().textContent()).trim();
    check(title === 'Week 4 · Base', `${scheme}: opens on this week ("${title}")`);

    const tabs = await box('plan-tabs');
    const firstRow = await box('row-2026-11-23-rest');
    const lastRow = await box('row-2026-11-29-long');
    const life = await box('button-Life happened');
    const plan = await box('plan');
    const rowsEnd = Math.round(lastRow.y + lastRow.height - tabs.y);
    for (const [phone, room] of Object.entries(ROOM)) {
      check(rowsEnd <= room, `${scheme}: all seven rows end ${rowsEnd} pt below the top of the switch; room on ${phone === 'iPhone' ? 'an' : 'an'} ${phone}: ${room} pt`);
    }
    check(firstRow.x >= 0 && lastRow.x + lastRow.width <= W, `${scheme}: the rows sit inside the screen's width`);
    const actionsEnd = Math.round(life.y + life.height - tabs.y);
    console.log(`  info  ${scheme}: "Life happened" ends ${actionsEnd} pt below the top of the switch`);

    // Android: the FAB sits 16 dp above the navigation bar; it must not cover a row.
    const fab = await box('fab-New workout');
    check(!!fab, `${scheme}: the "New workout" extended FAB is there (Android layout)`);
    const fabTop = ROOM.Android - FAB.bottom - FAB.height;
    check(rowsEnd <= fabTop, `${scheme}: on Android the FAB starts ${fabTop} pt below the switch, after the last row (${rowsEnd})`);
    const tabNames = await page.locator('[data-testid="plan-tabs"] [role="tab"]').allTextContents();
    check(JSON.stringify(tabNames) === JSON.stringify(['Week', 'Block', 'Trends']), `${scheme}: Material 3 tabs Week / Block / Trends (${tabNames.join(', ')})`);
    if (plan) console.log(`  info  ${scheme}: plan view ${Math.round(plan.width)}x${Math.round(plan.height)} in the browser`);
    await shot('plan-week');

    for (const tab of ['Block', 'Trends']) {
      await page.locator(`[data-testid="plan-tab-${tab}"]`).first().dispatchEvent('click');
      await page.waitForSelector(`[data-testid="${tab.toLowerCase()}"]`);
      await page.waitForTimeout(300);
      await shot(`plan-${tab.toLowerCase()}`);
    }
    check(!(await page.locator('[data-testid="fab-New workout"]').count()), `${scheme}: no FAB on Trends`);

    // A 10-rep session fits without scrolling.
    await page.goto(`${base}/plan/session/2026-12-02-intervals`);
    await page.waitForSelector('[data-testid="steps"]', { timeout: 20000 });
    await page.waitForTimeout(400);
    const session = await box('session');
    const last = await box('button-Skip');
    const sessionEnd = Math.round(last.y + last.height - session.y);
    check(sessionEnd <= SESSION_ROOM, `${scheme}: 10 × 400 m ends ${sessionEnd} pt below the top of the screen's content; room on an iPhone: ${SESSION_ROOM} pt`);
    await shot('session');

    check(!errors.length, `${scheme}: no page errors${errors.length ? `: ${errors.join('; ')}` : ''}`);
    await page.close();
  }
  await browser.close();
  server.close();
  console.log(failed ? `\n${failed} failed` : '\nPlan at 390x844: the week fits on one screen, the FAB covers nothing, 10 reps fit');
  process.exit(failed ? 1 : 0);
})();

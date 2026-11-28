#!/usr/bin/env node
// Today's above-the-fold rule (build prompt, section 5.1): at 390x844 the session
// card and This week are fully visible without scrolling. Measured in a browser on
// the web build of the app, against the room an iPhone actually leaves: the status
// bar and safe area at the top (59 pt) and the tab bar with the home indicator at
// the bottom (49 + 34 pt). The web build has neither, so they're added here.
// An approximation of a phone (the web uses the browser's system font), not a
// replacement for checking on one.
//
// Measured from the top of the first thing Today draws in its scroll view (the
// "Sample data" label, a banner or the header, whichever comes first), not from the
// header, so nothing drawn above the header can push This week out unseen.
//
//   npm run layout   (exports the web build with sample data, runs this and plan-check.js)
//   or: EXPO_PUBLIC_DAASH_FIXTURE=1 npx expo export --platform web --output-dir /tmp/web
//       node scripts/layout-check.js /tmp/web [screenshots-dir]

const fs = require('fs');
const http = require('http');
const path = require('path');
const { chromium } = require('playwright');

const ROOT = path.resolve(process.argv[2] || 'dist');
const SHOTS = process.argv[3];
const W = 390, H = 844, IOS_TOP = 59, IOS_BOTTOM = 49 + 34;
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

(async () => {
  const server = await serve();
  const base = `http://localhost:${server.address().port}`;
  const browser = await chromium.launch();
  let failed = 0;
  for (const scheme of ['dark', 'light']) {
    const page = await browser.newPage({ viewport: { width: W, height: H }, colorScheme: scheme, deviceScaleFactor: SHOTS ? 2 : 1 });
    const errors = [];
    page.on('pageerror', (e) => errors.push(e.message));
    await page.goto(`${base}/today`);
    await page.waitForSelector('[data-testid="week"]', { timeout: 20000 });
    await page.waitForTimeout(500);
    const box = async (id) => (await page.locator(`[data-testid="${id}"]`).first().boundingBox());
    const session = await box('session-card'), week = await box('week');
    // The top of the first child of Today's scroll content with any height.
    const top = await page.locator('[data-testid="today"]').first().evaluate((el) => {
      const content = el.firstElementChild || el;
      const first = Array.from(content.children).find((c) => c.getBoundingClientRect().height > 0) || content;
      return { y: first.getBoundingClientRect().top, id: first.getAttribute('data-testid') || first.tagName.toLowerCase() };
    });
    const scrolled = await page.evaluate(() => window.scrollY);
    const room = H - IOS_TOP - IOS_BOTTOM;
    for (const [name, b] of [['session card', session], ['This week', week]]) {
      const bottom = Math.round(b.y + b.height - top.y);
      const ok = bottom <= room;
      console.log(`  ${ok ? 'PASS' : 'FAIL'}  ${scheme}: ${name} ends ${bottom} pt below the top of the first thing drawn (${top.id}); room on an iPhone: ${room} pt`);
      if (!ok) failed++;
    }
    if (scrolled) { console.log(`  FAIL  ${scheme}: the page had scrolled (${scrolled})`); failed++; }
    if (errors.length) { console.log(`  FAIL  ${scheme}: page errors: ${errors.join('; ')}`); failed++; }
    if (SHOTS) {
      fs.mkdirSync(SHOTS, { recursive: true });
      await page.screenshot({ path: path.join(SHOTS, `today-${scheme}.png`) });
    }
    await page.close();
  }
  await browser.close();
  server.close();
  console.log(failed ? `\n${failed} failed` : '\nabove the fold at 390x844: session card and This week fully visible');
  process.exit(failed ? 1 : 0);
})();

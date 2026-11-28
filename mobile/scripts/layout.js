#!/usr/bin/env node
// The layout acceptance checks in one command (milestones 3 and 4, re-run every
// milestone): exports the web build with sample data (the persona, Coach on), then
// runs layout-check.js (Today above the fold at 390x844) and plan-check.js (the week
// fits on one screen, the FAB covers no row, a 10-rep session fits without scrolling).
// Needs Playwright's Chromium: `npx playwright install chromium` once (CI does it).
//
//   npm run layout [-- screenshots-dir]

const { execFileSync } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');

const shots = process.argv[2];
const out = fs.mkdtempSync(path.join(os.tmpdir(), 'daash-web-'));
const run = (cmd, args, env = {}) => execFileSync(cmd, args, { stdio: 'inherit', cwd: path.join(__dirname, '..'), env: { ...process.env, ...env } });

let failed = false;
try {
  run('npx', ['expo', 'export', '--platform', 'web', '--output-dir', out], { EXPO_PUBLIC_DAASH_FIXTURE: '1', CI: '1' });
  for (const check of ['layout-check.js', 'plan-check.js']) {
    console.log(`\n${check}`);
    try { run('node', [path.join(__dirname, check), out, ...(shots ? [shots] : [])]); } catch { failed = true; }
  }
} finally {
  fs.rmSync(out, { recursive: true, force: true });
}
process.exit(failed ? 1 : 0);

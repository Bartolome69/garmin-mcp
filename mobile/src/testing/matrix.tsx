// The full snapshot matrix (build prompt, section 13; milestone 12): every screen (and
// Plan's Block and Trends, and the loading, offline, expired, empty and streaming states in
// STATE_SCREENS) ×
// {light, dark} × {iOS, Android: this suite runs once per Jest project} × {100%, 200% text}
// × {Coach on, Coach off}. Each cell is snapshotted as what a screen reader finds (role,
// name, value, state, in reading order), with the brand-fill count and any clipping, not as a
// React tree: one reviewable file per screen in src/__matrix__/ (cells that read the same
// are written once, with every cell they stand for). Run `npx jest src/matrix -u` to
// rewrite them after a deliberate change.
//
// Every cell must hold:
//   - one brand fill on a screen with a primary action, at most one otherwise, none behind a sheet;
//   - no scalable text in a box of fixed height (it clips at 200%);
//   - every control (anything that answers a press, a switch or a text field, role or not) at
//     least MIN_TOUCH tall, and wide where its width is set;
//   - nothing focusable without a name, and no two controls with the same one;
//   - with the Coach off, nothing of the Coach's (its blocks or its name);
//   - the same numbers with the Coach on and off.
// 200% text: the test renderer doesn't measure or wrap text, so the 200% cells check what
// the code does at that size (layouts that switch on fontScale, text that scales, nothing
// scalable in a box of fixed height), not where real text would wrap or clip. Device check:
// docs/native-app/WALKTHROUGHS.md.
// The screens come from the routes on disk: a route with no entry in SCREENS fails
// (src/matrix/today.test.tsx). The suites are split by tab so Jest runs them side by side.
import { afterAll, beforeAll, describe, expect, it, jest } from '@jest/globals';
import fs from 'fs';
import path from 'path';
import { screen } from 'expo-router/testing-library';
import { Platform } from 'react-native';

import { a11yElements, brandFills, clipped, coachSurfaces, headerTitle, line, locate, plainNumbers, sharedNames, smallTargets, truncated, unnamed } from './a11y';
import { openScreen, SCREENS, screenRoutes, setWindow, STATE_SCREENS, type ScreenCase } from './screens';
import { MIN_TOUCH } from '../ui/Button';

const DIR = path.join(__dirname, '..', '__matrix__');
const os = Platform.OS;
type Scheme = 'light' | 'dark';
type Cell = { scheme: Scheme; text: 1 | 2; coach: boolean };
const CELLS: Cell[] = (['light', 'dark'] as const).flatMap((scheme) => ([1, 2] as const).flatMap((text) => [true, false].map((coach) => ({ scheme, text, coach }))));
const cellName = (c: Cell) => `${c.scheme} · ${c.text * 100}% · Coach ${c.coach ? 'on' : 'off'}`;

const slug = (s: string) => s.toLowerCase().replace(/[’']/g, '').replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');

/** Compares with the screen's file, or writes it (new, or with -u). --ci never writes. */
function matchFile(file: string, content: string) {
  const mode = (expect.getState() as { snapshotState?: { _updateSnapshot?: string } }).snapshotState?._updateSnapshot ?? 'new';
  const exists = fs.existsSync(file);
  if (mode === 'all' || (!exists && mode === 'new')) {
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(file, content);
    return;
  }
  expect(exists ? fs.readFileSync(file, 'utf8') : `(missing ${path.relative(DIR, file)}: run npx jest src/matrix -u)`).toBe(content);
}

/** A fixed clock (Fri 27 Nov 2026, 12:18 UTC), so times drawn on screen are the same every run. */
export const MATRIX_NOW = Date.UTC(2026, 10, 27, 12, 18);

/** Which suite a screen belongs to. */
export const groupOf = (c: ScreenCase) => (/^\(tabs\)\/today|^watch/.test(c.route) ? 'today' : /^\(tabs\)\/plan/.test(c.route) ? 'plan'
  : /^\(tabs\)\/you/.test(c.route) ? 'you' : 'more');

export function coverage() {
  it('every route on disk has a matrix entry, and every entry is a route', () => {
    const routes = screenRoutes();
    const covered = new Set(SCREENS.map((c) => c.route));
    expect(routes.filter((r) => !covered.has(r))).toEqual([]);
    expect([...covered].filter((r) => !routes.includes(r))).toEqual([]);
  });

  // Several cases can share a file name only by mistake.
  it('one file per screen', () => {
    const names = [...SCREENS, ...STATE_SCREENS].map((c) => slug(c.name));
    expect(names.filter((n, i) => names.indexOf(n) !== i)).toEqual([]);
  });
}

export function runMatrix(group: 'today' | 'plan' | 'you' | 'more' | 'states') {
  beforeAll(() => { jest.spyOn(Date, 'now').mockReturnValue(MATRIX_NOW); setWindow(); });
  afterAll(() => { jest.restoreAllMocks(); setWindow(); });
  describe(`the snapshot matrix on ${os}: ${group}`, () => { cases(group); });
}

function cases(group: string) {
  const list = group === 'states' ? STATE_SCREENS : SCREENS.filter((c) => groupOf(c) === group);
  it.each(list.map((c) => [c.name, c] as const))('%s', async (_name, c: ScreenCase) => {
    const cells = CELLS.filter((cell) => cell.coach || !c.coachOnly);
    const problems: string[] = [];
    const reads = new Map<string, string[]>();
    const numbers: Partial<Record<string, string[]>> = {};
    for (const cell of cells) {
      await openScreen(c, cell.scheme, cell.coach, { fontScale: cell.text });
      const tree = screen.toJSON();
      const { front, behind } = locate(tree, c.id);
      const where = cellName(cell);
      const fills = brandFills(front, cell.scheme);
      const behindFills = behind.reduce((n, b) => n + brandFills(b, cell.scheme), 0);
      const clip = clipped(front);
      const small = smallTargets(front);
      const coachBits = cell.coach ? [] : coachSurfaces(front);
      if (c.sheet && !behind.length) problems.push(`${where}: nothing drawn behind the sheet`);
      if (behindFills) problems.push(`${where}: ${behindFills} brand fill(s) behind the screen`);
      const wantsOne = c.primary === true || c.primary === os;
      if (fills > 1 || (wantsOne && fills !== 1)) problems.push(`${where}: ${fills} brand fills${wantsOne ? ', expected 1' : ''}`);
      if (clip.length) problems.push(`${where}: clipped ${clip.join('; ')}`);
      if (small.length) problems.push(`${where}: under ${MIN_TOUCH}: ${small.join('; ')}`);
      if (coachBits.length) problems.push(`${where}: Coach off but shows ${coachBits.join('; ')}`);
      const els = a11yElements(front);
      const nameless = unnamed(els), twice = sharedNames(els);
      if (nameless.length) problems.push(`${where}: no name: ${nameless.join('; ')}`);
      if (twice.length) problems.push(`${where}: controls share a name: ${twice.join('; ')}`);
      numbers[`${cell.scheme}|${cell.text}|${cell.coach}`] = plainNumbers(front);
      const title = headerTitle(front);
      const body = [
        `brand fills: ${fills}${c.sheet ? ` (behind the sheet: ${behindFills} on ${behind.length} screen${behind.length === 1 ? '' : 's'})` : ''}`,
        `clipped: ${clip.length ? clip.join('; ') : 'none'}`,
        `cut to lines: ${truncated(front).join('; ') || 'none'}`,
        ...(title ? [`header "${title}"`] : []),
        ...els.map(line),
      ].join('\n');
      reads.set(body, [...(reads.get(body) ?? []), where]);
    }
    // The same numbers with the Coach on and off, in every theme and text size.
    for (const scheme of ['light', 'dark'] as const) for (const text of [1, 2]) {
      const on = numbers[`${scheme}|${text}|true`], off = numbers[`${scheme}|${text}|false`];
      if (on && off && JSON.stringify(on) !== JSON.stringify(off)) problems.push(`${scheme} · ${text * 100}%: numbers differ with the Coach on (${on.join(' ')}) and off (${off.join(' ')})`);
    }
    expect(problems).toEqual([]);
    const content = [`# ${c.name} · ${os}`, `# ${c.url}`, ''].concat([...reads.entries()].flatMap(([body, where]) => [`## ${where.join(' | ')}`, body, ''])).join('\n');
    matchFile(path.join(DIR, `${slug(c.name)}.${os}.txt`), content);
  }, 120000);
}

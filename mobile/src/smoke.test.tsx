// Every screen (src/testing/screens.tsx), rendered over the real routes with fixture data, in
// light and dark, on iOS and Android (this suite runs once per platform). Each must
// render without an error, and each passes three accessibility checks on what it draws:
//   names     every control has something a screen reader can say
//   targets   every control is at least 44 pt (iOS) / 48 dp (Android), hit slop included
//   clipping  no text sits inside a box of fixed height, which clips at large font sizes
import { afterEach, beforeEach, describe, expect, it, jest } from '@jest/globals';
import { act, fireEvent, renderRouter, screen } from 'expo-router/testing-library';
import { router } from 'expo-router';
import { Platform, StyleSheet } from 'react-native';

import { numbersIn, numbersOfPayload } from './domain/replyGuard';
import ONBOARDING from './features/onboarding/onboarding.fixture.json';
import { CASES } from './features/onboarding/source';
import RUNS from './features/run/runs.fixture.json';
import { PERSONA_TODAY_COACH } from './features/today/fixture';
import { PERSONA_PLAN, PERSONA_PLAN_COACH } from './features/plan/fixture';
import PROPOSALS from './features/watch/proposals.fixture.json';
import { app, COACH_MESSAGES, openScreen, SCREENS, stagedProposal, watchQueue } from './testing/screens';
import { MIN_TOUCH } from './ui/Button';

type Node = { type: string; props: Record<string, any>; children: (Node | string)[] | null };
const all = (tree: unknown): Node[] => {
  const out: Node[] = [];
  const walk = (n: unknown) => {
    if (!n || typeof n !== 'object') return;
    if (Array.isArray(n)) { n.forEach(walk); return; }
    out.push(n as Node);
    ((n as Node).children ?? []).forEach(walk);
  };
  walk(tree);
  return out;
};
const texts = (n: Node): string[] => (n.children ?? []).flatMap((c) => (typeof c === 'string' ? [c] : texts(c)));
const CONTROL = new Set(['button', 'link', 'radio', 'tab', 'checkbox', 'switch', 'adjustable', 'menuitem']);
const isControl = (n: Node) => CONTROL.has(n.props.accessibilityRole) && (n.props.onClick || n.props.onResponderGrant || n.props.onPress);
const describeNode = (n: Node) => n.props.testID ?? n.props.accessibilityLabel ?? texts(n).join(' ').slice(0, 40) ?? n.type;
const slop = (h: unknown) => (typeof h === 'number' ? 2 * h : h && typeof h === 'object' ? ((h as { top?: number }).top ?? 0) + ((h as { bottom?: number }).bottom ?? 0) : 0);

/** The checks' findings for what's on screen now. */
export function audit(tree: unknown) {
  const nodes = all(tree);
  const names: string[] = [];
  const targets: string[] = [];
  const clipping: string[] = [];
  for (const n of nodes) {
    if (isControl(n)) {
      const label = n.props.accessibilityLabel ?? texts(n).filter((t) => /\w/.test(t)).join(' ');
      if (!label) names.push(`${n.props.accessibilityRole} ${n.props.testID ?? '(no id)'}`);
      // The control's height, or the tallest box inside it when the control itself is the hit area wrapper.
      const s = StyleSheet.flatten(n.props.style) ?? {};
      const inner = all(n).map((x) => StyleSheet.flatten(x.props?.style) ?? {});
      // A control that's only words is as tall as their line.
      const height = Math.max(s.minHeight ?? 0, s.height ?? 0, ...inner.map((x) => Math.max(x.minHeight ?? 0, x.height ?? 0, x.lineHeight ?? 0))) + slop(n.props.hitSlop);
      if (height < MIN_TOUCH) targets.push(`${describeNode(n)}: ${height}`);
    }
    const s = StyleSheet.flatten(n.props?.style) ?? {};
    if (n.type === 'View' && typeof s.height === 'number' && s.height < 80) {
      const words = all(n).filter((x) => x !== n && x.type === 'Text' && x.props.allowFontScaling !== false && !(x.props.maxFontSizeMultiplier && x.props.maxFontSizeMultiplier <= 1.5) && texts(x).some((t) => /\w/.test(t)));
      if (words.length) clipping.push(`${describeNode(n)}: height ${s.height}`);
    }
  }
  // Charts: every drawing is itself labelled or sits inside a labelled element (its one-line summary).
  const charts: string[] = [];
  const walk = (n: unknown, labelled: boolean) => {
    if (!n || typeof n !== 'object') return;
    if (Array.isArray(n)) { n.forEach((c) => walk(c, labelled)); return; }
    const node = n as Node;
    const here = labelled || !!(node.props?.accessibilityLabel && (node.props.accessible || node.props.accessibilityRole));
    if (/^RNSVG(SvgView|Svg)/.test(node.type)) { drawings += 1; if (!here) charts.push(node.props.testID ?? '(unnamed chart)'); }
    (node.children ?? []).forEach((c) => walk(c, here));
  };
  walk(tree, false);
  return { names: [...new Set(names)], targets: [...new Set(targets)], clipping: [...new Set(clipping)], charts: [...new Set(charts)] };
}

let drawings = 0;
let errors: string[] = [];
const realError = console.error;
beforeEach(() => {
  errors = [];
  jest.spyOn(console, 'error').mockImplementation((...args: unknown[]) => { errors.push(args.map(String).join(' ')); });
});
afterEach(() => { (console.error as unknown as { mockRestore: () => void }).mockRestore(); console.error = realError; });

const findings: Record<string, ReturnType<typeof audit>> = {};

describe.each(['light', 'dark'] as const)(`every screen on ${Platform.OS}, %s`, (scheme) => {
  it.each(SCREENS.map((c) => [c.name, c] as const))('%s', async (name, c) => {
    expect(await openScreen(c, scheme)).toBeTruthy();
    expect(errors.filter((e) => !/overlapping act|not configured to support act/.test(e))).toEqual([]);
    findings[`${name} (${scheme})`] = audit(screen.toJSON());
  });
  it('Onboarding · landed, on the watch', async () => {
    await renderRouter(app(scheme), { initialUrl: '/onboarding/reveal' });
    await fireEvent.press(await screen.findByTestId('button-Send week 1 to my watch'));
    await fireEvent.press(await screen.findByTestId(`button-Confirm and send ${CASES.reveal.week1.length} changes`));
    await act(async () => { await watchQueue().pump(); });
    expect(await screen.findByTestId('notify-ask', {}, { timeout: 4000 })).toBeTruthy();
    findings[`Onboarding · landed (${scheme})`] = audit(screen.toJSON());
  });
});

// -- Coach on and off (build prompt, sections 1 and 6) -------------------------------------
// Every screen's numbers are identical with coach_enabled on and off. The Coach's blocks are
// set aside (they're absent when off); every number they show must be one the screen already
// shows from the server. Read from what's drawn: text and what screen readers are told.
// The Coach tab itself ('coach') counts as a Coach block: native tabs draw every tab's screen.
const COACH_SLOTS = new Set(['coach', 'coach-line', 'ask-why', 'coach-note', 'coach-ask', 'debrief', 'diff-coach']);
// What the server sends each screen (the development fixtures stand in for it): a Coach block
// is checked against its own screen's data, not every fixture at once.
const RUN_CASES = (RUNS as unknown as { cases: Record<string, unknown> }).cases;
function payloadFor(url: string): Set<string> {
  const run = /\/run\/([^/?]+)/.exec(url)?.[1];
  if (run) return numbersOfPayload(RUN_CASES[run]);
  if (url.startsWith('/today')) return numbersOfPayload(PERSONA_TODAY_COACH);
  if (url.startsWith('/plan')) return numbersOfPayload([PERSONA_PLAN_COACH, PROPOSALS]);
  if (url.startsWith('/onboarding')) return numbersOfPayload(ONBOARDING);
  return new Set();
}
// The Coach tab (drawn on every screen by native tabs) against what its own thread was sent as
// data: the run the question was about, the plan and its proposals, the thread's charts and tags,
// and the runner's own words (questions, memory). Never a reply's words.
const COACH_TAB_NUMBERS = numbersOfPayload([RUN_CASES['2026-11-25-intervals'], PERSONA_PLAN, PROPOSALS,
  COACH_MESSAGES().map((m) => (m.role === 'assistant' || m.role === 'action' ? [m.chart, m.context, m.request] : [m.text, m.context]))]);
// The Coach thread's own chrome, not training numbers: a day separator's clock time, and how many free messages are left today.
// The paywall's Coach row ("Free has 10 messages a day") is there only with the Coach, like the tab: a feature's line, not a number that changes.
const CHROME = new Set(['coach-day', 'coach-remaining', 'plus-item-coach']);
type Where = 'plain' | 'slot' | 'tab';
function numbersSplit(tree: unknown) {
  const out: Record<Where, string[]> = { plain: [], slot: [], tab: [] };
  const walk = (n: unknown, where: Where) => {
    if (typeof n === 'string') { out[where].push(...numbersIn(n)); return; }
    if (!n || typeof n !== 'object') return;
    if (Array.isArray(n)) { n.forEach((c) => walk(c, where)); return; }
    const node = n as Node;
    if (CHROME.has(node.props?.testID)) return;
    const id = node.props?.testID;
    const here: Where = where !== 'plain' ? where : id === 'coach' ? 'tab' : COACH_SLOTS.has(id) ? 'slot' : 'plain';
    for (const said of [node.props?.accessibilityLabel, node.props?.accessibilityValue?.text]) {
      if (typeof said === 'string') out[here].push(...numbersIn(said));
    }
    (node.children ?? []).forEach((c) => walk(c, here));
  };
  walk(tree, 'plain');
  return { plain: out.plain, coach: [...out.slot, ...out.tab], slot: out.slot, tab: out.tab };
}

describe(`Coach on and off on ${Platform.OS}: every screen's numbers are the same`, () => {
  // The Coach's own screens don't exist with it off (the tab is hidden and unreachable).
  const PARITY = SCREENS.filter((c) => !c.coachOnly && !c.staged);
  const seen: Record<string, Partial<Record<'on' | 'off', ReturnType<typeof numbersSplit>>> & { url?: string }> = {};
  const cases = PARITY.flatMap((c) => (['on', 'off'] as const).map((mode) => [`${c.name} · Coach ${mode}`, c, mode] as const));
  it.each(cases)('%s', async (_label, c, mode) => {
    await openScreen(c, 'dark', mode === 'on');
    (seen[c.name] ??= { url: c.url })[mode] = numbersSplit(screen.toJSON());
  });
  it.each((['on', 'off'] as const).map((mode) => [`Staged diff · Coach ${mode}`, mode] as const))('%s', async (_label, mode) => {
    await renderRouter(app('dark', mode === 'on'), { initialUrl: '/plan' });
    await screen.findByTestId('week-title');
    const p = stagedProposal();
    await act(async () => { watchQueue().stage(p); });
    await act(async () => { router.push(`/plan/new-week?id=${encodeURIComponent(p.id)}`); });
    await screen.findByTestId('diff', {}, { timeout: 4000 });
    (seen['New week (diff)'] ??= { url: '/plan/new-week' })[mode] = numbersSplit(screen.toJSON());
  });
  it('every number is identical with the Coach on and off, on every screen', () => {
    const names = Object.keys(seen);
    expect(names.length).toBe(PARITY.length + 1);
    // Said as what one side has that the other doesn't (each number counted), then as the order.
    const minus = (a: string[], b: string[]) => { const left = [...b]; return a.filter((x) => { const i = left.indexOf(x); if (i < 0) return true; left.splice(i, 1); return false; }); };
    const differ = names.filter((n) => JSON.stringify(seen[n].on?.plain) !== JSON.stringify(seen[n].off?.plain)).map((n) => {
      const on = seen[n].on?.plain ?? [], off = seen[n].off?.plain ?? [];
      return { screen: n, onlyOn: minus(on, off), onlyOff: minus(off, on), sameNumbersOtherOrder: !minus(on, off).length && !minus(off, on).length };
    });
    expect(differ).toEqual([]);
    expect(names.filter((n) => seen[n].off?.coach.length)).toEqual([]);
  });
  it('a Coach block shows no number that isn’t on its screen or in what the server sent that screen', () => {
    const invented = Object.entries(seen).map(([n, s]) => {
      const payload = payloadFor(s.url ?? '');
      const slot = (s.on?.slot ?? []).filter((x) => !s.on?.plain.includes(x) && !payload.has(x));
      const tab = (s.on?.tab ?? []).filter((x) => !COACH_TAB_NUMBERS.has(x));
      return [n, [...new Set([...slot, ...tab])]] as const;
    }).filter(([, xs]) => xs.length);
    expect(invented).toEqual([]);
    expect(Object.values(seen).some((s) => s.on?.slot.length)).toBe(true); // the check saw Coach blocks with numbers
    expect(Object.values(seen).some((s) => s.on?.tab.length)).toBe(true);
  });
  it('the check fails a Coach block with a number that isn’t in its data', () => {
    const tree = { type: 'View', props: {}, children: [
      { type: 'Text', props: { testID: 'coach-line' }, children: ['HRV 87 ms, under your range.'] },
      { type: 'View', props: { testID: 'coach' }, children: [{ type: 'Text', props: {}, children: ['Your resting heart rate was 41.'] }] },
    ] };
    const split = numbersSplit(tree);
    expect(split.slot.filter((x) => !payloadFor('/today').has(x))).toEqual(['87']);
    expect(split.tab.filter((x) => !COACH_TAB_NUMBERS.has(x))).toEqual(['41']);
  });
});

describe(`accessibility on ${Platform.OS}`, () => {
  // Run after the screens above: what they found, in one place.
  it('every control has a name', () => {
    expect(Object.fromEntries(Object.entries(findings).filter(([, f]) => f.names.length).map(([k, f]) => [k, f.names]))).toEqual({});
  });
  it(`every control is at least ${MIN_TOUCH} tall`, () => {
    expect(Object.fromEntries(Object.entries(findings).filter(([, f]) => f.targets.length).map(([k, f]) => [k, f.targets]))).toEqual({});
  });
  it('every chart has a spoken summary', () => {
    expect(drawings).toBeGreaterThan(10); // the check saw the charts it checks
    expect(Object.fromEntries(Object.entries(findings).filter(([, f]) => f.charts.length).map(([k, f]) => [k, f.charts]))).toEqual({});
  });
  it('no text sits in a fixed-height box', () => {
    expect(Object.fromEntries(Object.entries(findings).filter(([, f]) => f.clipping.length).map(([k, f]) => [k, f.clipping]))).toEqual({});
  });
});

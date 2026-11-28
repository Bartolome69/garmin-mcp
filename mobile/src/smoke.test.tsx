// Every screen built so far, rendered over the real routes with fixture data, in
// light and dark, on iOS and Android (this suite runs once per platform). Each must
// render without an error, and each passes three accessibility checks on what it draws:
//   names     every control has something a screen reader can say
//   targets   every control is at least 44 pt (iOS) / 48 dp (Android), hit slop included
//   clipping  no text sits inside a box of fixed height, which clips at large font sizes
import { afterEach, beforeEach, describe, expect, it, jest } from '@jest/globals';
import { act, fireEvent, renderRouter, screen } from 'expo-router/testing-library';
import { router, Stack } from 'expo-router';
import type { ReactNode } from 'react';
import { Platform, StyleSheet } from 'react-native';

import TabsLayout from './app/(tabs)/_layout';
import CoachClear from './app/(tabs)/coach/clear';
import CoachFilter from './app/(tabs)/coach/filter';
import CoachLayout from './app/(tabs)/coach/_layout';
import CoachIndex from './app/(tabs)/coach/index';
import CoachLimit from './app/(tabs)/coach/limit';
import CoachMenu from './app/(tabs)/coach/menu';
import CoachNewWeek from './app/(tabs)/coach/new-week';
import CrewLayout from './app/(tabs)/crew/_layout';
import CrewIndex from './app/(tabs)/crew/index';
import PlanLayout from './app/(tabs)/plan/_layout';
import PlanBuilder from './app/(tabs)/plan/builder';
import PlanIndex from './app/(tabs)/plan/index';
import PlanLife from './app/(tabs)/plan/life';
import PlanMove from './app/(tabs)/plan/move';
import PlanNewWeek from './app/(tabs)/plan/new-week';
import PlanNext from './app/(tabs)/plan/next';
import PlanRun from './app/(tabs)/plan/run/[id]';
import PlanSession from './app/(tabs)/plan/session/[id]';
import TodayLayout from './app/(tabs)/today/_layout';
import TodayIndex from './app/(tabs)/today/index';
import TodayNewWeek from './app/(tabs)/today/new-week';
import TodayNext from './app/(tabs)/today/next';
import TodayOptions from './app/(tabs)/today/options';
import TodayReadiness from './app/(tabs)/today/readiness';
import TodayRun from './app/(tabs)/today/run/[id]';
import YouLayout from './app/(tabs)/you/_layout';
import YouConnections from './app/(tabs)/you/connections';
import YouIndex from './app/(tabs)/you/index';
import YouMemory from './app/(tabs)/you/memory';
import YouMemoryClear from './app/(tabs)/you/memory-clear';
import YouMemoryEdit from './app/(tabs)/you/memory-edit';
import YouPriority from './app/(tabs)/you/priority';
import YouRequest from './app/(tabs)/you/request';
import YouSee from './app/(tabs)/you/see';
import YouTrouble from './app/(tabs)/you/trouble';
import OnboardingGoal from './app/onboarding/goal';
import OnboardingIndex from './app/onboarding/index';
import OnboardingLanded from './app/onboarding/landed';
import OnboardingPaces from './app/onboarding/paces';
import OnboardingQuestions from './app/onboarding/questions';
import OnboardingResult from './app/onboarding/result';
import OnboardingReveal from './app/onboarding/reveal';
import OnboardingTry from './app/onboarding/try';
import { OnboardingStack } from './app/onboarding/_layout';
import WatchConfirm from './app/watch/confirm';
import { numbersIn, numbersOfPayload } from './domain/replyGuard';
import ONBOARDING from './features/onboarding/onboarding.fixture.json';
import RUNS from './features/run/runs.fixture.json';
import { PERSONA_TODAY_COACH } from './features/today/fixture';
import PROPOSALS from './features/watch/proposals.fixture.json';
import { FakeCoach } from './features/coach/fake';
import { CoachApiContext } from './features/coach/source';
import { OnboardingProvider } from './features/onboarding/OnboardingProvider';
import { CASES } from './features/onboarding/source';
import { PERSONA_PLAN, PERSONA_PLAN_COACH } from './features/plan/fixture';
import { findSession } from './features/plan/model';
import { skipProposal } from './features/watch/proposals';
import { memoryStore } from './features/watch/store';
import { FakeGarmin } from './features/watch/transport';
import { useWatch, WatchProvider, type WatchContext } from './features/watch/WatchProvider';
import { DEFAULT_FLAGS } from './flags/flags';
import { FlagsProvider } from './flags/FlagsProvider';
import { ThemeProvider } from './theme/ThemeProvider';
import { MIN_TOUCH } from './ui/Button';

let queue: WatchContext | null = null;
function Probe() { queue = useWatch(); return null; }

// The Coach for these screens: one exchange about Wednesday's run (with its chart) and one
// pending proposal, so the thread draws every kind of row.
const coachThread = () => new FakeCoach({ messages: [
  { id: 1, role: 'user', text: 'Why did rep 6 feel harder?', status: 'sent', context: { kind: 'run', id: '2026-11-25-intervals', label: 'About · Wed 25 · 6 × 800 m' } },
  { id: 2, role: 'assistant', text: 'Rep pace averaged 4:01. Your peak heart rate on rep 6 was 172, 4 bpm above rep 5.', status: 'done', sources: ['Run · Garmin'],
    chart: { kind: 'bars', title: 'Peak heart rate per rep', unit: 'bpm', caption: 'bpm', labels: ['Rep 1', 'Rep 2', 'Rep 3', 'Rep 4', 'Rep 5', 'Rep 6'],
      values: [164, 165, 166, 168, 168, 172], band: null, highlight: [5], summary: 'Peak heart rate per rep: 164, 165, 166, 168, 168, 172 bpm.' } },
  { id: 3, role: 'user', text: 'Make it easier then', status: 'sent' },
  { id: 4, role: 'action', text: 'Make today easier: 3 × 2 km at threshold becomes Easy 6 km.', status: 'pending', tool: 'easier', request: { kind: 'easier' } },
] });

function app(scheme: 'dark' | 'light', coach = true) {
  const Root = ({ children }: { children?: ReactNode }) => (
    <ThemeProvider initial={scheme}>
      <FlagsProvider initial={{ ...DEFAULT_FLAGS, coach_enabled: coach }}>
        <WatchProvider transport={new FakeGarmin()} store={memoryStore()} auto={false} pollEveryMs={0}>
          <CoachApiContext.Provider value={coachThread()}>
            <Probe />
            {children ?? <Stack screenOptions={{ headerShown: false }} />}
          </CoachApiContext.Provider>
        </WatchProvider>
      </FlagsProvider>
    </ThemeProvider>
  );
  return {
    _layout: () => <Root />,
    '(tabs)/_layout': TabsLayout,
    '(tabs)/today/_layout': TodayLayout, '(tabs)/today/index': TodayIndex, '(tabs)/today/readiness': TodayReadiness,
    '(tabs)/today/options': TodayOptions, '(tabs)/today/new-week': TodayNewWeek, '(tabs)/today/next': TodayNext,
    '(tabs)/today/run/[id]': TodayRun,
    '(tabs)/plan/_layout': PlanLayout, '(tabs)/plan/index': PlanIndex, '(tabs)/plan/builder': PlanBuilder, '(tabs)/plan/life': PlanLife,
    '(tabs)/plan/move': PlanMove, '(tabs)/plan/new-week': PlanNewWeek, '(tabs)/plan/next': PlanNext, '(tabs)/plan/run/[id]': PlanRun,
    '(tabs)/plan/session/[id]': PlanSession,
    '(tabs)/coach/_layout': CoachLayout, '(tabs)/coach/index': CoachIndex, '(tabs)/coach/menu': CoachMenu,
    '(tabs)/coach/filter': CoachFilter, '(tabs)/coach/clear': CoachClear, '(tabs)/coach/limit': CoachLimit,
    '(tabs)/coach/new-week': CoachNewWeek,
    '(tabs)/crew/_layout': CrewLayout, '(tabs)/crew/index': CrewIndex,
    '(tabs)/you/_layout': YouLayout, '(tabs)/you/index': YouIndex, '(tabs)/you/connections': YouConnections,
    '(tabs)/you/priority': YouPriority, '(tabs)/you/see': YouSee, '(tabs)/you/trouble': YouTrouble, '(tabs)/you/request': YouRequest,
    '(tabs)/you/memory': YouMemory, '(tabs)/you/memory-edit': YouMemoryEdit, '(tabs)/you/memory-clear': YouMemoryClear,
    // Onboarding part way through (the persona's goal, import and plan), so every step has what it shows.
    'onboarding/_layout': () => (
      <OnboardingProvider initial={{ goal: { kind: 'race', name: 'London Marathon', date: '2027-04-24', km: 42.195, target: '3:15' }, result: CASES.import, reveal: CASES.reveal }}>
        <OnboardingStack />
      </OnboardingProvider>
    ),
    'onboarding/index': OnboardingIndex, 'onboarding/try': OnboardingTry, 'onboarding/goal': OnboardingGoal,
    'onboarding/paces': OnboardingPaces, 'onboarding/questions': OnboardingQuestions, 'onboarding/result': OnboardingResult,
    'onboarding/reveal': OnboardingReveal, 'onboarding/landed': OnboardingLanded,
    'watch/confirm': WatchConfirm,
  };
}

// [what, url, the screen's root test id]. Staged: needs a proposal staged first.
export const SCREENS: [string, string, string][] = [
  ['Today', '/today', 'today'],
  ['Readiness sheet', '/today/readiness?signal=sleep', 'readiness-sheet'],
  ['Other options', '/today/options', 'options-sheet'],
  ['Plan · Week', '/plan', 'week-title'],
  ['Session detail', '/plan/session/2026-12-02-intervals', 'session'],
  ['Move sheet', '/plan/move?id=2026-12-02-intervals', 'move-sheet'],
  ['Life happened', '/plan/life', 'life-sheet'],
  ['Builder', '/plan/builder', 'builder'],
  ['Builder · edit', '/plan/builder?id=2026-11-27-tempo', 'builder'],
  ['Post-run (Plan)', '/plan/run/2026-11-25-intervals', 'run'],
  ['Post-run (Today)', '/today/run/2026-11-26-easy', 'run'],
  ['Plus placeholder', '/plan/next?kind=plus', 'plan-next'],
  ['You', '/you', 'you'],
  ['Connections', '/you/connections', 'connections'],
  ['Source priority', '/you/priority', 'priority'],
  ['What Daash can see', '/you/see', 'see-sheet'],
  ['Trouble connecting', '/you/trouble', 'trouble-sheet'],
  ['Request a device', '/you/request', 'request-sheet'],
  ['Coach', '/coach', 'coach'],
  ['Coach · from a run', '/coach?run=2026-11-25-intervals&label=Wed%2025%20%C2%B7%206%20%C3%97%20800%20m', 'coach'],
  ['Coach · menu', '/coach/menu', 'coach-menu'],
  ['Coach · filter', '/coach/filter', 'coach-filter-sheet'],
  ['Coach · clear', '/coach/clear', 'coach-clear-sheet'],
  ['Coach · limit', '/coach/limit', 'coach-limit-sheet'],
  ['Coach memory', '/you/memory', 'memory'],
  ['Coach memory · edit', '/you/memory-edit?id=persona-goal', 'memory-edit'],
  ['Coach memory · add', '/you/memory-edit', 'memory-edit'],
  ['Coach memory · clear', '/you/memory-clear', 'memory-clear-sheet'],
  ['Crew', '/crew', 'crew'],
  ['Onboarding · welcome', '/onboarding', 'welcome'],
  ['Onboarding · try', '/onboarding/try', 'try-card'],
  ['Onboarding · goal', '/onboarding/goal', 'ob-goal'],
  ['Onboarding · goal from Plan', '/onboarding/goal?from=plan', 'ob-goal'],
  ['Onboarding · paces', '/onboarding/paces', 'ob-paces'],
  ['Onboarding · questions', '/onboarding/questions', 'ob-questions'],
  ['Onboarding · result', '/onboarding/result', 'ob-result'],
  ['Onboarding · reveal', '/onboarding/reveal', 'ob-reveal'],
  ['Onboarding · landed, sending', '/onboarding/landed', 'ob-landed'],
];
const STAGED: [string, (id: string) => string, string][] = [
  ['New week (diff)', (id) => `/plan/new-week?id=${encodeURIComponent(id)}`, 'diff'],
  ['New week from the Coach (diff)', (id) => `/coach/new-week?id=${encodeURIComponent(id)}`, 'diff'],
  ['Watch confirm', (id) => `/watch/confirm?id=${encodeURIComponent(id)}`, 'confirm-sheet'],
];

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
  it.each(SCREENS)('%s', async (name, url, id) => {
    await renderRouter(app(scheme), { initialUrl: url });
    expect(await screen.findByTestId(id, {}, { timeout: 4000 })).toBeTruthy();
    expect(errors.filter((e) => !/overlapping act|not configured to support act/.test(e))).toEqual([]);
    findings[`${name} (${scheme})`] = audit(screen.toJSON());
  });
  it.each(STAGED)('%s', async (name, url, id) => {
    await renderRouter(app(scheme), { initialUrl: '/plan' });
    await screen.findByTestId('week-title');
    const p = skipProposal(findSession(PERSONA_PLAN, '2026-12-02-intervals')!);
    await act(async () => { queue!.stage(p); });
    await act(async () => { router.push(url(p.id)); });
    expect(await screen.findByTestId(id, {}, { timeout: 4000 })).toBeTruthy();
    findings[`${name} (${scheme})`] = audit(screen.toJSON());
  });
  it('Onboarding · landed, on the watch', async () => {
    await renderRouter(app(scheme), { initialUrl: '/onboarding/reveal' });
    await fireEvent.press(await screen.findByTestId('button-Send week 1 to my watch'));
    await fireEvent.press(await screen.findByTestId(`button-Confirm and send ${CASES.reveal.week1.length} changes`));
    await act(async () => { await queue!.pump(); });
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
// What the server sends these screens (the development fixtures stand in for it).
const SERVER_NUMBERS = numbersOfPayload([PERSONA_TODAY_COACH, PERSONA_PLAN_COACH, RUNS, PROPOSALS, ONBOARDING]);
// The Coach thread's own chrome, not training numbers: a day separator's clock time, and how many free messages are left today.
const CHROME = new Set(['coach-day', 'coach-remaining']);
function numbersSplit(tree: unknown) {
  const plain: string[] = [];
  const coach: string[] = [];
  const walk = (n: unknown, inCoach: boolean) => {
    if (typeof n === 'string') { (inCoach ? coach : plain).push(...numbersIn(n)); return; }
    if (!n || typeof n !== 'object') return;
    if (Array.isArray(n)) { n.forEach((c) => walk(c, inCoach)); return; }
    const node = n as Node;
    if (CHROME.has(node.props?.testID)) return;
    const here = inCoach || COACH_SLOTS.has(node.props?.testID);
    for (const said of [node.props?.accessibilityLabel, node.props?.accessibilityValue?.text]) {
      if (typeof said === 'string') (here ? coach : plain).push(...numbersIn(said));
    }
    (node.children ?? []).forEach((c) => walk(c, here));
  };
  walk(tree, false);
  return { plain, coach };
}

describe(`Coach on and off on ${Platform.OS}: every screen's numbers are the same`, () => {
  // The Coach's own screens don't exist with it off (the tab is hidden and unreachable).
  const PARITY = SCREENS.filter(([, url]) => !url.startsWith('/coach'));
  const seen: Record<string, Partial<Record<'on' | 'off', ReturnType<typeof numbersSplit>>>> = {};
  const cases = PARITY.flatMap(([name, url, id]) => (['on', 'off'] as const).map((mode) => [`${name} · Coach ${mode}`, name, url, id, mode] as const));
  it.each(cases)('%s', async (_label, name, url, id, mode) => {
    await renderRouter(app('dark', mode === 'on'), { initialUrl: url });
    await screen.findByTestId(id, {}, { timeout: 4000 });
    (seen[name] ??= {})[mode] = numbersSplit(screen.toJSON());
  });
  it.each((['on', 'off'] as const).map((mode) => [`Staged diff · Coach ${mode}`, mode] as const))('%s', async (_label, mode) => {
    await renderRouter(app('dark', mode === 'on'), { initialUrl: '/plan' });
    await screen.findByTestId('week-title');
    const p = skipProposal(findSession(PERSONA_PLAN, '2026-12-02-intervals')!);
    await act(async () => { queue!.stage(p); });
    await act(async () => { router.push(`/plan/new-week?id=${encodeURIComponent(p.id)}`); });
    await screen.findByTestId('diff', {}, { timeout: 4000 });
    (seen['New week (diff)'] ??= {})[mode] = numbersSplit(screen.toJSON());
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
  it('a Coach block shows no number that isn’t on the screen or in what the server sent', () => {
    const invented = Object.entries(seen).map(([n, s]) => [n, (s.on?.coach ?? []).filter((x) => !s.on?.plain.includes(x) && !SERVER_NUMBERS.has(x))] as const)
      .filter(([, xs]) => xs.length);
    expect(invented).toEqual([]);
    expect(Object.values(seen).some((s) => s.on?.coach.length)).toBe(true); // the check saw Coach blocks with numbers
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

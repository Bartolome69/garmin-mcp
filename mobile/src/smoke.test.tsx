// Every screen built so far, rendered over the real routes with fixture data, in
// light and dark, on iOS and Android (this suite runs once per platform). Each must
// render without an error, and each passes three accessibility checks on what it draws:
//   names     every control has something a screen reader can say
//   targets   every control is at least 44 pt (iOS) / 48 dp (Android), hit slop included
//   clipping  no text sits inside a box of fixed height, which clips at large font sizes
import { afterEach, beforeEach, describe, expect, it, jest } from '@jest/globals';
import { act, renderRouter, screen } from 'expo-router/testing-library';
import { router, Stack } from 'expo-router';
import type { ReactNode } from 'react';
import { Platform, StyleSheet } from 'react-native';

import TabsLayout from './app/(tabs)/_layout';
import CoachLayout from './app/(tabs)/coach/_layout';
import CoachIndex from './app/(tabs)/coach/index';
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
import YouPriority from './app/(tabs)/you/priority';
import YouRequest from './app/(tabs)/you/request';
import YouSee from './app/(tabs)/you/see';
import YouTrouble from './app/(tabs)/you/trouble';
import WatchConfirm from './app/watch/confirm';
import { PERSONA_PLAN } from './features/plan/fixture';
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

function app(scheme: 'dark' | 'light') {
  const Root = ({ children }: { children?: ReactNode }) => (
    <ThemeProvider initial={scheme}>
      <FlagsProvider initial={{ ...DEFAULT_FLAGS, coach_enabled: true }}>
        <WatchProvider transport={new FakeGarmin()} store={memoryStore()} auto={false} pollEveryMs={0}>
          <Probe />
          {children ?? <Stack screenOptions={{ headerShown: false }} />}
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
    '(tabs)/coach/_layout': CoachLayout, '(tabs)/coach/index': CoachIndex,
    '(tabs)/crew/_layout': CrewLayout, '(tabs)/crew/index': CrewIndex,
    '(tabs)/you/_layout': YouLayout, '(tabs)/you/index': YouIndex, '(tabs)/you/connections': YouConnections,
    '(tabs)/you/priority': YouPriority, '(tabs)/you/see': YouSee, '(tabs)/you/trouble': YouTrouble, '(tabs)/you/request': YouRequest,
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
  ['Crew', '/crew', 'crew'],
];
const STAGED: [string, (id: string) => string, string][] = [
  ['New week (diff)', (id) => `/plan/new-week?id=${encodeURIComponent(id)}`, 'diff'],
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

// Every screen of the app, for the tests that go over all of them (smoke, the snapshot
// matrix, focus order). The route map is read from src/app on disk, so a new route is in
// it without being listed here; SCREENS says how to open each one (a test fails when a
// route on disk has no entry). Test-only: imports the testing library.
import fs from 'fs';
import path from 'path';
import { act, renderRouter, screen } from 'expo-router/testing-library';
import { router, Stack } from 'expo-router';
import type { ComponentType, ReactNode } from 'react';
import { Dimensions } from 'react-native';

import { OnboardingStack } from '../app/onboarding/_layout';
import { FakeCoach } from '../features/coach/fake';
import type { ServerMessage } from '../features/coach/model';
import { CoachApiContext } from '../features/coach/source';
import { OnboardingProvider } from '../features/onboarding/OnboardingProvider';
import { CASES } from '../features/onboarding/source';
import { PERSONA_PLAN } from '../features/plan/fixture';
import { findSession } from '../features/plan/model';
import { EntitlementsProvider } from '../features/plus/EntitlementsProvider';
import { FakeStore } from '../features/plus/store';
import { skipProposal } from '../features/watch/proposals';
import { memoryStore } from '../features/watch/store';
import { FakeGarmin } from '../features/watch/transport';
import { useWatch, WatchProvider, type WatchContext } from '../features/watch/WatchProvider';
import { DEFAULT_FLAGS } from '../flags/flags';
import { FlagsProvider } from '../flags/FlagsProvider';
import { ThemeProvider } from '../theme/ThemeProvider';

let queue: WatchContext | null = null;
function Probe() { queue = useWatch(); return null; }
/** The watch queue of the app rendered last. */
export const watchQueue = (): WatchContext => queue!;

// The Coach for these screens: one exchange about Wednesday's run (with its chart), a memory
// note and one pending proposal, written today, so the thread draws every kind of row.
const at = () => Math.floor(Date.now() / 1000);
export const COACH_MESSAGES = (): ServerMessage[] => [
  { id: 1, role: 'user', text: 'Why did rep 6 feel harder?', status: 'sent', at: at(), context: { kind: 'run', id: '2026-11-25-intervals', label: 'About · Wed 25 · 6 × 800 m' } },
  { id: 2, role: 'assistant', text: 'Rep pace averaged 4:01. Your peak heart rate on rep 6 was 172, 4 bpm above rep 5.', status: 'done', at: at(), sources: ['Run · Garmin'],
    chart: { kind: 'bars', title: 'Peak heart rate per rep', unit: 'bpm', caption: 'bpm', labels: ['Rep 1', 'Rep 2', 'Rep 3', 'Rep 4', 'Rep 5', 'Rep 6'],
      values: [164, 165, 166, 168, 168, 172], band: null, highlight: [5], summary: 'Peak heart rate per rep: 164, 165, 166, 168, 168, 172 bpm.' } },
  { id: 3, role: 'user', text: 'I’m away 1 to 3 Dec. Make it easier then', status: 'sent', at: at() },
  { id: 4, role: 'memory', text: 'Travelling 1 to 3 Dec', status: 'done', at: at() },
  { id: 5, role: 'action', text: 'Make today easier: 3 × 2 km at threshold becomes Easy 6 km.', status: 'pending', tool: 'easier', request: { kind: 'easier' }, at: at() },
];

const APP_DIR = path.join(__dirname, '..', 'app');

/** Every route file under src/app, as expo-router names it ('(tabs)/today/index', 'plus/trial', …). */
export function routeFiles(dir = APP_DIR, base = ''): string[] {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const rel = base ? `${base}/${e.name}` : e.name;
    if (e.isDirectory()) return routeFiles(path.join(dir, e.name), rel);
    return /\.tsx?$/.test(e.name) && !/\.test\./.test(e.name) ? [rel.replace(/\.tsx?$/, '')] : [];
  }).sort();
}

/** Screens (not layouts) a runner can land on. The root index only redirects. */
export const screenRoutes = () => routeFiles().filter((r) => !/(^|\/)_layout$/.test(r) && r !== 'index');

export function app(scheme: 'dark' | 'light', coach = true, opts: { messages?: () => ServerMessage[] } = {}) {
  const Root = ({ children }: { children?: ReactNode }) => (
    <ThemeProvider initial={scheme}>
      <FlagsProvider initial={{ ...DEFAULT_FLAGS, coach_enabled: coach }}>
        <EntitlementsProvider store={new FakeStore()} fetcher={async () => { throw new Error('offline'); }}>
          <WatchProvider transport={new FakeGarmin()} store={memoryStore()} auto={false} pollEveryMs={0}>
            <CoachApiContext.Provider value={new FakeCoach({ messages: (opts.messages ?? COACH_MESSAGES)() })}>
              <Probe />
              {children ?? <Stack screenOptions={{ headerShown: false }} />}
            </CoachApiContext.Provider>
          </WatchProvider>
        </EntitlementsProvider>
      </FlagsProvider>
    </ThemeProvider>
  );
  const map: Record<string, ComponentType<any>> = {};
  for (const r of routeFiles()) {
    if (r === '_layout' || r === 'index') continue;
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    map[r] = require(path.join(APP_DIR, r)).default;
  }
  map._layout = () => <Root />;
  // Onboarding part way through (the persona's goal, import and plan), so every step has what it shows.
  map['onboarding/_layout'] = () => (
    <OnboardingProvider initial={{ goal: { kind: 'race', name: 'London Marathon', date: '2027-04-24', km: 42.195, target: '3:15' }, result: CASES.import, reveal: CASES.reveal }}>
      <OnboardingStack />
    </OnboardingProvider>
  );
  return map;
}

export type ScreenCase = {
  name: string;
  /** The route file it opens. */
  route: string;
  url: string;
  /** The screen's root test id. */
  id: string;
  /** Needs a proposal staged first; url gets the proposal's id. */
  staged?: boolean;
  /** Drawn over the tab's first screen (a sheet): the screen behind has no brand fill. */
  sheet?: boolean;
  /** Has a primary action (the one brand fill); false for screens with none. */
  primary?: boolean;
  /** Only with the Coach on (the Coach tab's own screens). */
  coachOnly?: boolean;
};

export const SCREENS: ScreenCase[] = [
  { name: 'Today', route: '(tabs)/today/index', url: '/today', id: 'today', primary: true },
  { name: 'Readiness sheet', route: '(tabs)/today/readiness', url: '/today/readiness?signal=sleep', id: 'readiness-sheet', sheet: true },
  { name: 'Other options', route: '(tabs)/today/options', url: '/today/options', id: 'options-sheet', sheet: true },
  { name: 'Plan · Week', route: '(tabs)/plan/index', url: '/plan', id: 'week-title' },
  { name: 'Session detail', route: '(tabs)/plan/session/[id]', url: '/plan/session/2026-12-02-intervals', id: 'session' },
  { name: 'Move sheet', route: '(tabs)/plan/move', url: '/plan/move?id=2026-12-02-intervals', id: 'move-sheet', sheet: true },
  { name: 'Life happened', route: '(tabs)/plan/life', url: '/plan/life', id: 'life-sheet', sheet: true },
  { name: 'Builder', route: '(tabs)/plan/builder', url: '/plan/builder', id: 'builder' },
  { name: 'Builder · edit', route: '(tabs)/plan/builder', url: '/plan/builder?id=2026-11-27-tempo', id: 'builder' },
  { name: 'Post-run (Plan)', route: '(tabs)/plan/run/[id]', url: '/plan/run/2026-11-25-intervals', id: 'run' },
  { name: 'Post-run (Today)', route: '(tabs)/today/run/[id]', url: '/today/run/2026-11-26-easy', id: 'run' },
  { name: 'Plus feature to come', route: '(tabs)/plan/next', url: '/plan/next?kind=plan_styles', id: 'plan-next' },
  { name: 'You', route: '(tabs)/you/index', url: '/you', id: 'you' },
  { name: 'Connections', route: '(tabs)/you/connections', url: '/you/connections', id: 'connections' },
  { name: 'Source priority', route: '(tabs)/you/priority', url: '/you/priority', id: 'priority' },
  { name: 'What Daash can see', route: '(tabs)/you/see', url: '/you/see', id: 'see-sheet', sheet: true },
  { name: 'Trouble connecting', route: '(tabs)/you/trouble', url: '/you/trouble', id: 'trouble-sheet', sheet: true },
  { name: 'Request a device', route: '(tabs)/you/request', url: '/you/request', id: 'request-sheet', sheet: true },
  { name: 'Widgets setup', route: '(tabs)/you/widgets', url: '/you/widgets', id: 'widgets' },
  { name: 'Plus feature to come (You)', route: '(tabs)/you/coming', url: '/you/coming', id: 'you-coming' },
  { name: 'Activities', route: '(tabs)/you/activities', url: '/you/activities', id: 'activities' },
  { name: 'Post-run (You)', route: '(tabs)/you/run/[id]', url: '/you/run/2026-11-24-easy', id: 'run' },
  { name: 'Units and zones', route: '(tabs)/you/training', url: '/you/training', id: 'training' },
  { name: 'Notifications', route: '(tabs)/you/notifications', url: '/you/notifications', id: 'notifications' },
  { name: 'Appearance', route: '(tabs)/you/appearance', url: '/you/appearance', id: 'appearance' },
  { name: 'Privacy', route: '(tabs)/you/privacy', url: '/you/privacy', id: 'privacy' },
  { name: 'Help', route: '(tabs)/you/help', url: '/you/help', id: 'help' },
  { name: 'Sign out', route: '(tabs)/you/sign-out', url: '/you/sign-out', id: 'sign-out-sheet', sheet: true },
  { name: 'Plus (paywall)', route: 'plus/index', url: '/plus?from=you', id: 'paywall' },
  { name: 'Plus (paywall) from the Coach’s limit', route: 'plus/index', url: '/plus?from=coach', id: 'paywall' },
  { name: 'Plus · free week sheet', route: 'plus/trial', url: '/plus/trial', id: 'trial-sheet', sheet: true },
  { name: 'Coach', route: '(tabs)/coach/index', url: '/coach', id: 'coach', coachOnly: true },
  { name: 'Coach · from a run', route: '(tabs)/coach/index', url: '/coach?run=2026-11-25-intervals&label=Wed%2025%20%C2%B7%206%20%C3%97%20800%20m', id: 'coach', coachOnly: true },
  { name: 'Coach · menu', route: '(tabs)/coach/menu', url: '/coach/menu', id: 'coach-menu', sheet: true, coachOnly: true },
  { name: 'Coach · filter', route: '(tabs)/coach/filter', url: '/coach/filter', id: 'coach-filter-sheet', sheet: true, coachOnly: true },
  { name: 'Coach · clear', route: '(tabs)/coach/clear', url: '/coach/clear', id: 'coach-clear-sheet', sheet: true, coachOnly: true },
  { name: 'Coach · limit', route: '(tabs)/coach/limit', url: '/coach/limit', id: 'coach-limit-sheet', sheet: true, coachOnly: true },
  { name: 'Coach memory', route: '(tabs)/you/memory', url: '/you/memory', id: 'memory' },
  { name: 'Coach memory · edit', route: '(tabs)/you/memory-edit', url: '/you/memory-edit?id=persona-goal', id: 'memory-edit', sheet: true },
  { name: 'Coach memory · add', route: '(tabs)/you/memory-edit', url: '/you/memory-edit', id: 'memory-edit', sheet: true },
  { name: 'Coach memory · clear', route: '(tabs)/you/memory-clear', url: '/you/memory-clear', id: 'memory-clear-sheet', sheet: true },
  { name: 'Crew', route: '(tabs)/crew/index', url: '/crew', id: 'crew' },
  { name: 'Onboarding · welcome', route: 'onboarding/index', url: '/onboarding', id: 'welcome' },
  { name: 'Onboarding · try', route: 'onboarding/try', url: '/onboarding/try', id: 'try-card' },
  { name: 'Onboarding · goal', route: 'onboarding/goal', url: '/onboarding/goal', id: 'ob-goal' },
  { name: 'Onboarding · goal from Plan', route: 'onboarding/goal', url: '/onboarding/goal?from=plan', id: 'ob-goal' },
  { name: 'Onboarding · paces', route: 'onboarding/paces', url: '/onboarding/paces', id: 'ob-paces' },
  { name: 'Onboarding · questions', route: 'onboarding/questions', url: '/onboarding/questions', id: 'ob-questions' },
  { name: 'Onboarding · result', route: 'onboarding/result', url: '/onboarding/result', id: 'ob-result' },
  { name: 'Onboarding · reveal', route: 'onboarding/reveal', url: '/onboarding/reveal', id: 'ob-reveal' },
  { name: 'Onboarding · landed, sending', route: 'onboarding/landed', url: '/onboarding/landed', id: 'ob-landed' },
  { name: 'New week (diff)', route: '(tabs)/plan/new-week', url: '/plan/new-week?id=', id: 'diff', staged: true },
  { name: 'New week from Today (diff)', route: '(tabs)/today/new-week', url: '/today/new-week?id=', id: 'diff', staged: true },
  { name: 'New week from the Coach (diff)', route: '(tabs)/coach/new-week', url: '/coach/new-week?id=', id: 'diff', staged: true, coachOnly: true },
  { name: 'Watch confirm', route: 'watch/confirm', url: '/watch/confirm?id=', id: 'confirm-sheet', staged: true, sheet: true },
];

/** The skipped Wednesday intervals, staged: what the staged screens show. */
export const stagedProposal = () => skipProposal(findSession(PERSONA_PLAN, '2026-12-02-intervals')!);

/** Opens a case's screen and waits for its root. */
export async function openScreen(c: ScreenCase, scheme: 'dark' | 'light' = 'dark', coach = true) {
  if (c.staged) {
    await renderRouter(app(scheme, coach), { initialUrl: '/plan' });
    await screen.findByTestId('week-title');
    const p = stagedProposal();
    await act(async () => { watchQueue().stage(p); });
    await act(async () => { router.push(`${c.url}${encodeURIComponent(p.id)}`); });
  } else {
    await renderRouter(app(scheme, coach), { initialUrl: c.url });
  }
  return screen.findByTestId(c.id, {}, { timeout: 10000 });
}

/** The phone's window: width, height and text size (fontScale 2 is 200%). */
export function setWindow({ width = 390, height = 844, fontScale = 1 }: { width?: number; height?: number; fontScale?: number } = {}) {
  const window = { width, height, scale: 3, fontScale };
  act(() => { Dimensions.set({ window, screen: window }); });
}

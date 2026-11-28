// Onboarding end to end (build prompt, sections 5.13, 13 and 15, milestone 9), over
// the real routes, screens and watch queue, against a scripted Garmin and server:
//
//   1. The app's first screen: onboarding until it's done, then Today
//   2. Welcome → try → goal → paces (import) → result → reveal → week 1 to the
//      watch only through the confirm sheet → landed → the notification ask → Today
//   3. The other ways through: four questions, Adjust, back, offline, not connected,
//      leaving before it lands, a failed send
//   4. Plan's "Create a plan" starts the same steps, with Cancel back to Plan
//
// mobile/e2e/5-onboarding.yaml holds the same flow as a Maestro flow for a device.
import { beforeEach, describe, expect, it, jest } from '@jest/globals';
import { router, Stack } from 'expo-router';
import { act, fireEvent, renderRouter, screen, within } from 'expo-router/testing-library';
import type { ReactNode } from 'react';
import { Linking } from 'react-native';

// What the server does in each test.
let mockServer: 'ok' | 'empty' | 'offline' | 'signedOut' | 'server' = 'ok';
let mockPlanServer: 'ok' | 'signedOut' | 'offline' = 'ok';
let mockSignedIn = false;
let mockEmptyPlan = false;
const mockAsk = jest.fn(async () => 'granted' as const);
jest.mock('./features/onboarding/notify', () => ({ askNotifications: () => mockAsk() }));
jest.mock('./features/onboarding/source', () => {
  const real = jest.requireActual('./features/onboarding/source') as typeof import('./features/onboarding/source');
  const c = jest.requireActual('./api/client') as typeof import('./api/client');
  const fail = (mode: string) => {
    if (mode === 'offline') throw new c.Offline();
    if (mode === 'signedOut') throw new c.NotSignedIn();
    if (mode === 'server') throw new c.ApiError('Server error', 500, true);
  };
  return {
    ...real,
    importRuns: jest.fn(async () => { fail(mockServer); return mockServer === 'empty' ? real.CASES.import_empty : real.CASES.import; }),
    makePlan: jest.fn(async (_r: unknown, goal: { kind: string }, coach: boolean) => {
      fail(mockPlanServer);
      const r = goal.kind === 'race' ? real.CASES.reveal : real.CASES.reveal_fit;
      return coach ? { ...r, coachNote: 'Base is long because your weeks have been steady.' } : r;
    }),
  };
});
jest.mock('./api/client', () => ({
  ...(jest.requireActual('./api/client') as object),
  accessToken: async () => (mockSignedIn ? 'token' : null),
}));
jest.mock('./features/plan/source', () => {
  const real = jest.requireActual('./features/plan/source') as typeof import('./features/plan/source');
  return {
    ...real,
    planSource: (...args: Parameters<typeof real.planSource>) => {
      const fetch = real.planSource(...args);
      return async () => { const vm = await fetch(); return mockEmptyPlan ? { ...vm, weeks: [], block: null } : vm; };
    },
  };
});

import Index from './app/index';
import TabsLayout from './app/(tabs)/_layout';
import CoachIndex from './app/(tabs)/coach/index';
import CrewIndex from './app/(tabs)/crew/index';
import PlanLayout from './app/(tabs)/plan/_layout';
import PlanBuilder from './app/(tabs)/plan/builder';
import PlanIndex from './app/(tabs)/plan/index';
import TodayLayout from './app/(tabs)/today/_layout';
import TodayIndex from './app/(tabs)/today/index';
import YouLayout from './app/(tabs)/you/_layout';
import YouIndex from './app/(tabs)/you/index';
import YouTrouble from './app/(tabs)/you/trouble';
import OnboardingLayout from './app/onboarding/_layout';
import OnboardingGoal from './app/onboarding/goal';
import OnboardingIndex from './app/onboarding/index';
import OnboardingLanded from './app/onboarding/landed';
import OnboardingPaces from './app/onboarding/paces';
import OnboardingQuestions from './app/onboarding/questions';
import OnboardingResult from './app/onboarding/result';
import OnboardingReveal from './app/onboarding/reveal';
import OnboardingTry from './app/onboarding/try';
import WatchConfirm from './app/watch/confirm';
import { DbContext } from './db/DbContext';
import { kv } from './db/kv';
import { migratedNodeDb } from './db/testing/nodeDb';
import type { Db } from './db/types';
import { NOTIFICATIONS_KEY, ONBOARDED_KEY } from './features/onboarding/OnboardingProvider';
import { CASES } from './features/onboarding/source';
import { memoryStore } from './features/watch/store';
import { FakeGarmin, type Outcome } from './features/watch/transport';
import { useWatch, WatchProvider, type WatchContext } from './features/watch/WatchProvider';
import { DEFAULT_FLAGS, type Flags } from './flags/flags';
import { FlagsProvider } from './flags/FlagsProvider';
import { ThemeProvider } from './theme/ThemeProvider';

let queue: WatchContext | null = null;
function Probe() { queue = useWatch(); return null; }

function app(db: Db, garmin: FakeGarmin, flags: Flags = DEFAULT_FLAGS) {
  const Root = ({ children }: { children?: ReactNode }) => (
    <DbContext.Provider value={db}>
      <ThemeProvider initial="dark">
        <FlagsProvider initial={flags}>
          <WatchProvider transport={garmin} store={memoryStore()} auto={false} pollEveryMs={0}>
            <Probe />
            {children ?? <Stack screenOptions={{ headerShown: false }} />}
          </WatchProvider>
        </FlagsProvider>
      </ThemeProvider>
    </DbContext.Provider>
  );
  return {
    _layout: () => <Root />,
    index: Index,
    'onboarding/_layout': OnboardingLayout, 'onboarding/index': OnboardingIndex, 'onboarding/try': OnboardingTry,
    'onboarding/goal': OnboardingGoal, 'onboarding/paces': OnboardingPaces, 'onboarding/questions': OnboardingQuestions,
    'onboarding/result': OnboardingResult, 'onboarding/reveal': OnboardingReveal, 'onboarding/landed': OnboardingLanded,
    '(tabs)/_layout': TabsLayout,
    '(tabs)/today/_layout': TodayLayout, '(tabs)/today/index': TodayIndex,
    '(tabs)/plan/_layout': PlanLayout, '(tabs)/plan/index': PlanIndex, '(tabs)/plan/builder': PlanBuilder,
    '(tabs)/coach/index': CoachIndex, '(tabs)/crew/index': CrewIndex,
    '(tabs)/you/_layout': YouLayout, '(tabs)/you/index': YouIndex, '(tabs)/you/trouble': YouTrouble,
    'watch/confirm': WatchConfirm,
  };
}

const garmin = (script: Outcome[] = []) => new FakeGarmin(script, 'ok', 2);
const pump = async () => { await act(async () => { await queue!.pump(); }); };
const press = async (testID: string) => { await fireEvent.press(await screen.findByTestId(testID)); };
const type = async (testID: string, text: string) => { await fireEvent.changeText(await screen.findByTestId(testID), text); };
const goBack = async () => { await act(async () => { router.back(); }); };
const WEEK = CASES.reveal.week1;

let db: Db;
beforeEach(async () => {
  mockServer = 'ok'; mockPlanServer = 'ok'; mockSignedIn = false; mockEmptyPlan = false;
  mockAsk.mockClear();
  db = await migratedNodeDb();
});

/** Step 1 with the persona's race. */
async function londonGoal() {
  await screen.findByTestId('ob-goal');
  await type('race-name', 'London Marathon');
  await type('race-date', '24 Apr 2027');
  await type('race-target', '3:15');
  await press('button-Continue');
  await screen.findByTestId('ob-paces');
}

/** From the welcome to the plan reveal by the import. */
async function toReveal(g: FakeGarmin, flags?: Flags) {
  await renderRouter(app(db, g, flags), { initialUrl: '/onboarding' });
  await press('button-Set up my plan');
  await londonGoal();
  await press('button-Connect Garmin');
  await screen.findByTestId('ob-result');
  await press('button-Looks right');
  await screen.findByTestId('ob-reveal');
}

describe('1. The app\'s first screen', () => {
  it('a runner who hasn\'t onboarded goes to the welcome', async () => {
    await renderRouter(app(db, garmin()), { initialUrl: '/' });
    expect(await screen.findByTestId('welcome')).toBeTruthy();
    expect(screen.getByTestId('button-Try building a session')).toBeTruthy();
    expect(screen.getByTestId('button-Set up my plan')).toBeTruthy();
    expect(screen.getByTestId('sign-in')).toBeTruthy();
  });
  it('an onboarded runner goes to Today', async () => {
    await kv(db).set(ONBOARDED_KEY, true);
    await renderRouter(app(db, garmin()), { initialUrl: '/' });
    expect(await screen.findByTestId('today')).toBeTruthy();
    expect(screen.queryByTestId('welcome')).toBeNull();
  });
  it('Sign in is Garmin\'s own sign-in, on the server', async () => {
    const open = jest.spyOn(Linking, 'openURL').mockResolvedValue(undefined);
    await renderRouter(app(db, garmin()), { initialUrl: '/' });
    await press('sign-in');
    expect(open).toHaveBeenCalledWith(expect.stringMatching(/\/connect\?app=native$/));
    open.mockRestore();
  });
});

describe('2. Onboarding → first workout lands', () => {
  it('try first, then the plan, week 1 to the watch only through the confirm sheet, the landing, the ask, Today', async () => {
    const g = garmin();
    await renderRouter(app(db, g), { initialUrl: '/' });

    // Welcome → try the builder, no account: the example read on the phone, paces from a 10K of 45:00.
    await press('button-Try building a session');
    expect(await screen.findByTestId('builder')).toBeTruthy();
    expect(screen.getByText('Type a session. Paces use a 10K of 45:00 until you connect a watch.')).toBeTruthy();
    expect(screen.getByTestId('builder-input').props.value).toBe('5x1km at threshold, 2 min easy');
    expect(screen.queryByTestId('button-Save and send')).toBeNull();   // sending needs an account
    const card = within(screen.getByTestId('try-card'));
    expect(card.getByText('Like it? Send it to your watch.')).toBeTruthy();
    await fireEvent.press(card.getByTestId('button-Create free account'));

    // Step 1: the goal.
    expect(await screen.findByText('Step 1 of 4')).toBeTruthy();
    await londonGoal();

    // Step 2: import, recommended.
    expect(screen.getByText('Import my recent runs from my watch')).toBeTruthy();
    await press('button-Connect Garmin');
    expect(await screen.findByText('Found 56 runs since September')).toBeTruthy();
    expect(screen.getByLabelText('Best recent race, 10K · 43:30, 12 Oct')).toBeTruthy();
    expect(screen.getByTestId('result-connection')).toHaveTextContent('Connected to Garmin · Forerunner 965');
    await press('button-Looks right');

    // The reveal: the server's plan. Nothing has gone anywhere yet.
    expect(await screen.findByText('London Marathon, Sat 24 Apr')).toBeTruthy();
    expect(screen.getByText('Your plan · 20 weeks')).toBeTruthy();
    expect(screen.getByLabelText('Predicted now, 3:20')).toBeTruthy();
    expect(screen.getByLabelText('Target, 3:15')).toBeTruthy();
    expect(screen.getByTestId('because')).toBeTruthy();
    expect(screen.queryByTestId('coach-note')).toBeNull();
    expect(queue!.ops).toHaveLength(0);
    await press('button-Send week 1 to my watch');

    // The confirm sheet lists exactly what the watch gets. Still nothing sent.
    const sheet = within(await screen.findByTestId('confirm-sheet'));
    expect(sheet.getByText(`${WEEK.length} changes on your watch`)).toBeTruthy();
    for (const s of WEEK) expect(sheet.getByLabelText(new RegExp(`^Add, [A-Z][a-z]{2} ${s.day}, ${s.title.replace(/[+×]/g, '.')}$`))).toBeTruthy();
    expect(sheet.getByText('Connected to Garmin · synced 07:02')).toBeTruthy();
    expect(queue!.ops).toHaveLength(0);
    expect(g.sent).toHaveLength(0);
    await fireEvent.press(sheet.getByTestId(`button-Confirm and send ${WEEK.length} changes`));

    // Landed screen: sending, then Garmin has it, then on the watch.
    expect(await screen.findByTestId('landed-waiting')).toBeTruthy();
    expect(screen.queryByTestId('notify-ask')).toBeNull();
    expect(mockAsk).not.toHaveBeenCalled();
    await pump();
    expect(g.sent.map((o) => [o.kind, o.sessionId])).toEqual(WEEK.map((s) => ['add', s.id]));
    expect(g.sent.every((o) => o.workout?.schema === 'daash.workout.v1')).toBe(true);
    expect(screen.getByTestId('landed-waiting').props.accessibilityLabel).toMatch(/arrives at the next sync$/);
    expect(mockAsk).not.toHaveBeenCalled();
    await pump();

    // On the watch: the filled check, and only now the notification ask.
    expect(await screen.findByTestId('success-check')).toBeTruthy();
    expect(screen.getByTestId('landed-detail')).toHaveTextContent(new RegExp(`^${WEEK[0].title.replace(/[+×]/g, '.')} landed on Forerunner 965 at \\d\\d:\\d\\d\\. The rest of week 1 is on it too\\.$`));
    expect(screen.getByTestId('notify-ask')).toBeTruthy();
    expect(await kv(db).get(ONBOARDED_KEY)).toBeNull();
    await press('button-Allow notifications');

    expect(mockAsk).toHaveBeenCalledTimes(1);
    expect(await screen.findByTestId('today')).toBeTruthy();
    expect(await kv(db).get(ONBOARDED_KEY)).toBe(true);
    expect(await kv(db).get(NOTIFICATIONS_KEY)).toMatchObject({ answer: 'granted' });
  });

  it('Back on the confirm sheet sends nothing and stays on the plan; confirming later still lands', async () => {
    const g = garmin();
    await toReveal(g);
    await press('button-Send week 1 to my watch');
    await fireEvent.press(within(await screen.findByTestId('confirm-sheet')).getByTestId('button-Back'));
    expect(await screen.findByTestId('ob-reveal')).toBeTruthy();
    expect(queue!.ops).toHaveLength(0);
    expect(screen.queryByTestId('ob-landed')).toBeNull();
    await pump();
    expect(g.sent).toHaveLength(0);

    await press('button-Send week 1 to my watch');
    await press(`button-Confirm and send ${WEEK.length} changes`);
    expect(await screen.findByTestId('ob-landed')).toBeTruthy();
  });

  it('Not now: no permission asked, still onboarded, on to Today', async () => {
    await toReveal(garmin(['synced']));
    await press('button-Send week 1 to my watch');
    await press(`button-Confirm and send ${WEEK.length} changes`);
    await pump();
    expect(await screen.findByTestId('success-check')).toBeTruthy();
    await press('button-Not now');
    expect(await screen.findByTestId('today')).toBeTruthy();
    expect(mockAsk).not.toHaveBeenCalled();
    expect(await kv(db).get(ONBOARDED_KEY)).toBe(true);
    expect(await kv(db).get(NOTIFICATIONS_KEY)).toMatchObject({ answer: 'not_now' });
  });

  it('with the Coach on: the "why this shape" note, and the same numbers', async () => {
    await toReveal(garmin(), { ...DEFAULT_FLAGS, coach_enabled: true });
    expect(screen.getByTestId('coach-note')).toHaveTextContent(/why this shape/);
    expect(screen.getByLabelText('Predicted now, 3:20')).toBeTruthy();
    expect(screen.getByLabelText('Target, 3:15')).toBeTruthy();
  });
});

describe('3. The other ways through', () => {
  it('four questions instead, Adjust to change them, back keeps the goal', async () => {
    await renderRouter(app(db, garmin()), { initialUrl: '/onboarding' });
    await press('button-Set up my plan');
    await screen.findByTestId('ob-goal');
    await press('goal-fit');
    await press('button-Continue');
    await press('questions');
    expect(await screen.findByText('How do you train?')).toBeTruthy();
    for (const id of ['exp-exp', 'days-5', 'weekly-50to70']) await press(id);
    await press('button-Continue');
    expect(await screen.findByText('From your answers')).toBeTruthy();
    expect(screen.getByLabelText('Best recent race, None given, using a 10K of 45:00')).toBeTruthy();
    expect(screen.queryByTestId('result-connection')).toBeNull();

    await press('button-Adjust');
    expect(await screen.findByTestId('ob-questions')).toBeTruthy();
    for (const id of ['exp-regular', 'days-4', 'weekly-30to50']) await press(id);
    await press('button-Continue');
    expect(await screen.findByLabelText('Usual week, 40 km · 4 runs, from your answers')).toBeTruthy();
    await press('button-Looks right');
    expect(await screen.findByText('Your fitness plan')).toBeTruthy();
    expect(screen.queryByTestId('predicted')).toBeNull();

    // Back through the steps: the goal is as it was left.
    await goBack(); await goBack(); await goBack(); await goBack(); await goBack(); await goBack();
    expect(await screen.findByTestId('ob-goal')).toBeTruthy();
    expect(screen.getByTestId('goal-fit').props.accessibilityState).toEqual({ selected: true });
  });

  it('nothing to import: says so, and Adjust goes to the four questions', async () => {
    mockServer = 'empty';
    await renderRouter(app(db, garmin()), { initialUrl: '/onboarding/goal' });
    await londonGoal();
    await press('button-Connect Garmin');
    expect(await screen.findByText('No runs in the last 12 weeks')).toBeTruthy();
    expect(screen.getByTestId('result-empty')).toBeTruthy();
    await press('button-Adjust');
    expect(await screen.findByTestId('ob-questions')).toBeTruthy();
  });

  it('offline: says so on the import, and the four questions still work', async () => {
    mockServer = 'offline';
    await renderRouter(app(db, garmin()), { initialUrl: '/onboarding/goal' });
    await londonGoal();
    await press('button-Connect Garmin');
    expect((await screen.findByTestId('import-problem')).props.accessibilityLabel).toBe('You’re offline. Reading your runs needs a connection, or answer 4 questions instead.');
    expect(screen.getByTestId('ob-paces')).toBeTruthy();
    await press('questions');
    expect(await screen.findByTestId('ob-questions')).toBeTruthy();
  });

  it('Garmin not connected: the import opens Garmin\'s sign-in and says what to do next', async () => {
    mockServer = 'signedOut';
    const open = jest.spyOn(Linking, 'openURL').mockResolvedValue(undefined);
    await renderRouter(app(db, garmin()), { initialUrl: '/onboarding/goal' });
    await londonGoal();
    await press('button-Connect Garmin');
    expect((await screen.findByTestId('import-problem')).props.accessibilityLabel).toBe('Sign in on Garmin’s page, then come back here and import.');
    expect(open).toHaveBeenCalledWith(expect.stringMatching(/\/connect\?app=native$/));
    open.mockRestore();
  });

  it('signed in already, the import reads the runs straight away', async () => {
    mockSignedIn = true;
    await renderRouter(app(db, garmin()), { initialUrl: '/onboarding/goal' });
    await londonGoal();
    await press('button-Import my runs');
    expect(await screen.findByTestId('ob-result')).toBeTruthy();
  });

  it('Garmin not connected at the plan: Connect Garmin, then "I\'ve signed in" makes it', async () => {
    mockPlanServer = 'signedOut';
    const open = jest.spyOn(Linking, 'openURL').mockResolvedValue(undefined);
    await renderRouter(app(db, garmin()), { initialUrl: '/onboarding/goal' });
    await londonGoal();
    await press('questions');
    for (const id of ['exp-exp', 'days-5', 'weekly-50to70']) await press(id);
    await press('button-Continue');
    await press('button-Looks right');
    expect(await screen.findByText('Connect Garmin to make your plan')).toBeTruthy();
    await press('button-Connect Garmin');
    expect(open).toHaveBeenCalled();
    mockPlanServer = 'ok';
    await press('button-I’ve signed in');
    expect(await screen.findByTestId('ob-reveal')).toBeTruthy();
    open.mockRestore();
  });

  it('offline at the plan: says so, Try again makes it', async () => {
    mockPlanServer = 'offline';
    await renderRouter(app(db, garmin()), { initialUrl: '/onboarding/goal' });
    await londonGoal();
    await press('button-Connect Garmin');
    await press('button-Looks right');
    expect(await screen.findByText('You’re offline. Your answers are kept; try again when you’re back online.')).toBeTruthy();
    mockPlanServer = 'ok';
    await press('button-Try again');
    expect(await screen.findByTestId('ob-reveal')).toBeTruthy();
  });

  it('leaving before it lands: onboarded, on to Today, and no permission asked yet', async () => {
    await toReveal(garmin());
    await press('button-Send week 1 to my watch');
    await press(`button-Confirm and send ${WEEK.length} changes`);
    await press('button-Go to Today');
    expect(await screen.findByTestId('today')).toBeTruthy();
    expect(mockAsk).not.toHaveBeenCalled();
    expect(await kv(db).get(ONBOARDED_KEY)).toBe(true);
  });

  it('a failed send: Not sent in words, nothing changed on the watch, Resend lands it', async () => {
    const g = garmin(WEEK.map((): Outcome => 'refused'));
    await toReveal(g);
    await press('button-Send week 1 to my watch');
    await press(`button-Confirm and send ${WEEK.length} changes`);
    await pump();
    expect(await screen.findByText('Nothing changed on your watch.')).toBeTruthy();
    expect(screen.getByTestId('button-Trouble connecting?')).toBeTruthy();
    expect(g.sent).toHaveLength(0);
    await press('button-Resend');
    await pump();
    await pump();
    expect(await screen.findByTestId('success-check')).toBeTruthy();
    expect(g.sent).toHaveLength(WEEK.length);
  });

  it('a step opened with nothing before it goes back to where it can start', async () => {
    await renderRouter(app(db, garmin()), { initialUrl: '/onboarding/reveal' });
    expect(await screen.findByTestId('ob-paces')).toBeTruthy();
  });
});

describe('4. Plan\'s "Create a plan"', () => {
  it('starts the steps, with Cancel back to Plan', async () => {
    mockEmptyPlan = true;
    await kv(db).set(ONBOARDED_KEY, true);
    await renderRouter(app(db, garmin()), { initialUrl: '/plan' });
    expect(await screen.findByTestId('plan-empty')).toBeTruthy();
    await press('button-Create a plan');
    expect(await screen.findByTestId('ob-goal')).toBeTruthy();
    await press('ob-cancel');
    expect(await screen.findByTestId('plan-empty')).toBeTruthy();
  });

  it('and ends back on Plan', async () => {
    mockEmptyPlan = true;
    mockSignedIn = true;
    await kv(db).set(ONBOARDED_KEY, true);
    const g = garmin(['synced']);
    await renderRouter(app(db, g), { initialUrl: '/plan' });
    await press('button-Create a plan');
    await londonGoal();
    await press('button-Import my runs');
    await press('button-Looks right');
    await press('button-Send week 1 to my watch');
    await press(`button-Confirm and send ${WEEK.length} changes`);
    await pump();
    await press('button-Not now');
    expect(await screen.findByTestId('plan-empty')).toBeTruthy();
  });
});

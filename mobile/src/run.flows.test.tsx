// Post-run end to end (build prompt, section 5.9, milestone 7), over the real routes:
//
//   10. From a done row on Plan, the run
//   11. From a done day in Today's week strip, the run
//   12. Coach on: the debrief, and "Ask about this run" into the Coach
import { describe, expect, it } from '@jest/globals';
import { router, Stack } from 'expo-router';
import { act, fireEvent, renderRouter, screen, within } from 'expo-router/testing-library';
import type { ReactNode } from 'react';

import TabsLayout from './app/(tabs)/_layout';
import CoachIndex from './app/(tabs)/coach/index';
import CrewIndex from './app/(tabs)/crew/index';
import PlanBuilder from './app/(tabs)/plan/builder';
import PlanRun from './app/(tabs)/plan/run/[id]';
import PlanLayout from './app/(tabs)/plan/_layout';
import PlanIndex from './app/(tabs)/plan/index';
import PlanLife from './app/(tabs)/plan/life';
import PlanMove from './app/(tabs)/plan/move';
import PlanNewWeek from './app/(tabs)/plan/new-week';
import PlanNext from './app/(tabs)/plan/next';
import PlanSession from './app/(tabs)/plan/session/[id]';
import TodayLayout from './app/(tabs)/today/_layout';
import TodayIndex from './app/(tabs)/today/index';
import TodayNewWeek from './app/(tabs)/today/new-week';
import TodayNext from './app/(tabs)/today/next';
import TodayOptions from './app/(tabs)/today/options';
import TodayReadiness from './app/(tabs)/today/readiness';
import TodayRun from './app/(tabs)/today/run/[id]';
import YouLayout from './app/(tabs)/you/_layout';
import YouIndex from './app/(tabs)/you/index';
import WatchConfirm from './app/watch/confirm';
import { memoryStore } from './features/watch/store';
import { FakeGarmin } from './features/watch/transport';
import { WatchProvider, useWatch, type WatchContext } from './features/watch/WatchProvider';
import { DEFAULT_FLAGS, type Flags } from './flags/flags';
import { FlagsProvider } from './flags/FlagsProvider';
import { ThemeProvider } from './theme/ThemeProvider';

let queue: WatchContext | null = null;
function Probe() { queue = useWatch(); return null; }

function app(garmin: FakeGarmin, flags: Flags = DEFAULT_FLAGS) {
  const Root = ({ children }: { children?: ReactNode }) => (
    <ThemeProvider initial="dark">
      <FlagsProvider initial={flags}>
        <WatchProvider transport={garmin} store={memoryStore()} auto={false} pollEveryMs={0}>
          <Probe />
          {children ?? <Stack screenOptions={{ headerShown: false }} />}
        </WatchProvider>
      </FlagsProvider>
    </ThemeProvider>
  );
  return {
    _layout: () => <Root />,
    '(tabs)/_layout': TabsLayout,
    '(tabs)/today/_layout': TodayLayout,
    '(tabs)/today/index': TodayIndex,
    '(tabs)/today/readiness': TodayReadiness,
    '(tabs)/today/options': TodayOptions,
    '(tabs)/today/new-week': TodayNewWeek,
    '(tabs)/today/next': TodayNext,
    '(tabs)/today/run/[id]': TodayRun,
    '(tabs)/plan/_layout': PlanLayout,
    '(tabs)/plan/index': PlanIndex,
    '(tabs)/plan/builder': PlanBuilder,
    '(tabs)/plan/life': PlanLife,
    '(tabs)/plan/move': PlanMove,
    '(tabs)/plan/new-week': PlanNewWeek,
    '(tabs)/plan/next': PlanNext,
    '(tabs)/plan/run/[id]': PlanRun,
    '(tabs)/plan/session/[id]': PlanSession,
    '(tabs)/coach/index': CoachIndex,
    '(tabs)/crew/index': CrewIndex,
    '(tabs)/you/_layout': YouLayout,
    '(tabs)/you/index': YouIndex,
    'watch/confirm': WatchConfirm,
  };
}


describe('10. From Plan', () => {
  it('a done row opens its run, worked out by the server\'s engine', async () => {
    await renderRouter(app(new FakeGarmin()), { initialUrl: '/plan' });
    await screen.findByTestId('week-title');
    await fireEvent.press(await screen.findByTestId('row-2026-11-25-intervals'));
    const run = within(await screen.findByTestId('run'));
    expect(run.getByRole('header')).toHaveTextContent('6 × 800 m');
    expect(run.getByLabelText('Reps, plan 6 × 800 m, done 6 of 6, on plan')).toBeTruthy();
    expect(run.getByTestId('takeaway-pace').props.accessibilityLabel).toBe('Even reps. Fastest and slowest 6 s apart.');
    expect(screen.queryByTestId('debrief')).toBeNull();
    expect(screen.getByTestId('share')).toBeTruthy();
  });
});

describe('11. From Today', () => {
  it('a done day in the week strip opens its run; a planned day doesn\'t', async () => {
    await renderRouter(app(new FakeGarmin()), { initialUrl: '/today' });
    const today = within(await screen.findByTestId('today'));
    expect(today.getByTestId('day-2026-11-28').props.accessibilityRole).toBeUndefined();
    expect(today.getByTestId('day-2026-11-26').props.accessibilityRole).toBe('button');
    await fireEvent.press(today.getByTestId('day-2026-11-26'));
    const run = within(await screen.findByTestId('run'));
    expect(run.getByRole('header')).toHaveTextContent('9 km run club');
    expect(run.getByLabelText('Pace, plan 5:27 to 6:10, done 5:14 avg, off plan')).toBeTruthy();
  });
});

describe('12. Coach on', () => {
  it('the debrief sits under the map, and "Ask about this run" opens the Coach', async () => {
    await renderRouter(app(new FakeGarmin(), { ...DEFAULT_FLAGS, coach_enabled: true }), { initialUrl: '/plan' });
    await screen.findByTestId('week-title');
    await act(async () => { router.push({ pathname: '/plan/run/[id]', params: { id: '2026-11-25-intervals' } }); });
    expect(await screen.findByTestId('debrief-text')).toHaveTextContent(/^Reps were even, 3:58 to 4:04/);
    await fireEvent.press(screen.getByTestId('ask-run'));
    expect(await screen.findByTestId('coach')).toBeTruthy();
  });
});

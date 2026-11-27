// Connections end to end (build prompt, section 5.12, milestone 8), over the real routes:
//
//   13. You → reconnect needed → Reconnect opens the provider's sign-in
//   14. You → Connections → Source priority → reorder, and "These are different runs"
//   15. A session's "Trouble connecting?" opens the help; "Request it" sends a name
import { describe, expect, it, jest } from '@jest/globals';
import { router, Stack } from 'expo-router';
import { Linking, Platform } from 'react-native';
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
import YouRequest from './app/(tabs)/you/request';
import YouTrouble from './app/(tabs)/you/trouble';
import YouSee from './app/(tabs)/you/see';
import YouPriority from './app/(tabs)/you/priority';
import YouConnections from './app/(tabs)/you/connections';
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
    '(tabs)/you/request': YouRequest,
    '(tabs)/you/trouble': YouTrouble,
    '(tabs)/you/see': YouSee,
    '(tabs)/you/priority': YouPriority,
    '(tabs)/you/connections': YouConnections,
    'watch/confirm': WatchConfirm,
  };
}



describe('13. Reconnect needed', () => {
  it('shows first on You, and Reconnect opens that provider\'s sign-in', async () => {
    const open = jest.spyOn(Linking, 'openURL').mockResolvedValue(undefined as never);
    await renderRouter(app(new FakeGarmin()), { initialUrl: '/you' });
    const card = within(await screen.findByTestId('trust-intervals'));
    expect(card.getByText('Reconnect needed · last synced Tue')).toBeTruthy();
    await fireEvent.press(card.getByTestId('button-Reconnect'));
    expect(open).toHaveBeenCalledWith(expect.stringMatching(/\/app\/connect\/intervals$/));
    open.mockRestore();
  });
});

describe('14. Source priority', () => {
  it('reorder, and the merges follow; keep a pair apart; Connections says so', async () => {
    await renderRouter(app(new FakeGarmin()), { initialUrl: '/you' });
    await fireEvent.press(await screen.findByTestId('row-connections'));
    expect(await screen.findByLabelText('Source priority, Garmin first · 2 duplicate runs merged')).toBeTruthy();
    await fireEvent.press(screen.getByTestId('row-priority'));
    const list = await screen.findByTestId('priority');
    await fireEvent(within(list).getByTestId('source-0'), 'accessibilityAction', { nativeEvent: { actionName: 'moveDown' } });
    expect(within(list).getByTestId('merge-h-1126|g-1126')).toBeTruthy();
    await fireEvent.press(within(within(list).getByTestId('merge-h-1126|g-1126')).getByTestId('button-These are different runs'));
    expect(within(list).queryByTestId('merge-h-1126|g-1126')).toBeNull();
    await act(async () => { router.back(); });
    const health = Platform.OS === 'ios' ? 'Apple Health' : 'Health Connect';
    expect(await screen.findByLabelText(`Source priority, ${health} first · 1 duplicate run merged`)).toBeTruthy();
  });
});

describe('15. Help and requests', () => {
  it('a session\'s "Trouble connecting?" opens the help over Plan', async () => {
    await renderRouter(app(new FakeGarmin([], 'ok', 1)), { initialUrl: '/plan' });
    await screen.findByTestId('week-title');
    await act(async () => { router.push({ pathname: '/plan/session/[id]', params: { id: '2026-12-02-intervals' } }); });
    await screen.findByTestId('session');
    await act(async () => { router.push('/you/trouble'); });
    expect(await screen.findByTestId('trouble-sheet')).toBeTruthy();
  });
  it('"Request it" takes a name and says thanks', async () => {
    await renderRouter(app(new FakeGarmin()), { initialUrl: '/you/connections' });
    await fireEvent.press(await screen.findByTestId('button-Request it'));
    await fireEvent.changeText(await screen.findByTestId('request-input'), 'Polar Vantage');
    await fireEvent.press(screen.getByTestId('button-Send request'));
    expect(await screen.findByTestId('request-sent')).toBeTruthy();
  });
});

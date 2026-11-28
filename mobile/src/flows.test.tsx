// The send flows end to end (build prompt, sections 13 and 15, milestone 5), over the
// real routes, screens and watch queue, against a scripted Garmin. The prompt's
// E2E runner (Maestro or Detox) needs a simulator; these are the same flows in
// Jest, and mobile/e2e/ holds them as Maestro flows for a device.
//
//   1. Today "Make it easier" → confirm → on your watch
//   2. Life happened → new week → confirm
//   3. Send fails → Resend → sent
//   4. Remove from watch (from the toast, and from a session)
//   5. Each change of a send is said once, on iOS and Android
import { describe, expect, it, jest } from '@jest/globals';
import { router, Stack } from 'expo-router';
import { act, fireEvent, renderRouter, screen, within } from 'expo-router/testing-library';
import type { ReactNode } from 'react';
import { AccessibilityInfo } from 'react-native';

import TabsLayout from './app/(tabs)/_layout';
import CoachIndex from './app/(tabs)/coach/index';
import CrewIndex from './app/(tabs)/crew/index';
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
import TodayOptions from './app/(tabs)/today/options';
import TodayReadiness from './app/(tabs)/today/readiness';
import YouLayout from './app/(tabs)/you/_layout';
import YouIndex from './app/(tabs)/you/index';
import WatchConfirm from './app/watch/confirm';
import { memoryStore } from './features/watch/store';
import { FakeGarmin, type Outcome } from './features/watch/transport';
import { WatchProvider, useWatch, type WatchContext } from './features/watch/WatchProvider';
import { WatchToast } from './features/watch/WatchToast';
import { DEFAULT_FLAGS } from './flags/flags';
import { FlagsProvider } from './flags/FlagsProvider';
import { ThemeProvider } from './theme/ThemeProvider';

let queue: WatchContext | null = null;
function Probe() { queue = useWatch(); return null; }

function app(garmin: FakeGarmin, clock?: () => number) {
  const Root = ({ children }: { children?: ReactNode }) => (
    <ThemeProvider initial="dark">
      <FlagsProvider initial={DEFAULT_FLAGS}>
        <WatchProvider transport={garmin} store={memoryStore()} auto={false} pollEveryMs={0} clock={clock}>
          <Probe />
          {/* As the app's root: a stack, so the watch confirm opens over the tabs and closes back onto them. */}
          {children ?? <Stack screenOptions={{ headerShown: false }} />}
          <WatchToast />
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
    '(tabs)/plan/_layout': PlanLayout,
    '(tabs)/plan/index': PlanIndex,
    '(tabs)/plan/life': PlanLife,
    '(tabs)/plan/new-week': PlanNewWeek,
    '(tabs)/plan/move': PlanMove,
    '(tabs)/plan/next': PlanNext,
    '(tabs)/plan/session/[id]': PlanSession,
    '(tabs)/coach/index': CoachIndex,
    '(tabs)/crew/index': CrewIndex,
    '(tabs)/you/_layout': YouLayout,
    '(tabs)/you/index': YouIndex,
    'watch/confirm': WatchConfirm,
  };
}

const garmin = (script: Outcome[] = [], syncAfter = 2) => new FakeGarmin(script, 'ok', syncAfter);
const pump = async () => { await act(async () => { await queue!.pump(); }); };
const press = async (testID: string) => { await fireEvent.press(await screen.findByTestId(testID)); };
/** Open a session the way a runner does: from the week, so going back returns to it. */
async function openSession(g: FakeGarmin, id: string) {
  await renderRouter(app(g), { initialUrl: '/plan' });
  await screen.findByTestId('week-title');
  await act(async () => { router.push({ pathname: '/plan/session/[id]', params: { id } }); });
  await screen.findByTestId('session');
}

describe('1. Today "Make it easier" → confirm → on your watch', () => {
  it('proposes the easier day, lists exactly what changes, sends it, and follows it to the watch', async () => {
    const g = garmin();
    await renderRouter(app(g), { initialUrl: '/today' });
    await press('button-Make it easier');

    expect(await screen.findByTestId('diff-kicker')).toHaveTextContent('Make it easier · today');
    expect(screen.getByTestId('diff-title')).toHaveTextContent('1 session changes');
    expect(screen.getByTestId('diff-subtitle')).toHaveTextContent('54 → 50 km this week. Next week unchanged.');
    expect(screen.getByLabelText('FRI 27: 3 × 2 km at threshold, becomes Easy 6 km, Easy · 6 km')).toBeTruthy();
    expect(g.sent).toHaveLength(0); // nothing sent from the diff
    await press('button-Review watch changes');

    const sheet = within(await screen.findByTestId('confirm-sheet'));
    expect(sheet.getByText('1 change on your watch')).toBeTruthy();
    expect(sheet.getByText('This is exactly what Forerunner 965 gets. Nothing else on the watch changes.')).toBeTruthy();
    expect(sheet.getByLabelText('Replace, Fri 27, 3 × 2 km at threshold, with Easy 6 km')).toBeTruthy();
    expect(sheet.getByText('Connected to Garmin · synced 07:02')).toBeTruthy();
    expect(g.sent).toHaveLength(0); // nor from the sheet, until confirmed
    await press('button-Confirm and send 1 change');

    // Back on Today: the new session, sending.
    const card = () => within(screen.getByTestId('session-card'));
    expect(await card().findByText('Easy 6 km')).toBeTruthy();
    expect(card().getByLabelText('Sending')).toBeTruthy();
    await pump();
    expect(g.sent.map((o) => [o.kind, o.sessionId])).toEqual([['replace', '2026-11-27-tempo']]);
    expect(card().getByLabelText('Changed · arrives at next sync')).toBeTruthy();
    await pump();
    expect(card().getByLabelText(/^On your watch · \d\d:\d\d$/)).toBeTruthy();
    // The only toast: the way back.
    const toast = within(screen.getByTestId('send-toast'));
    expect(toast.getByText('Changed your mind?')).toBeTruthy();
    expect(toast.getByText('Remove from watch')).toBeTruthy();
  });
});

describe('2. Life happened → new week → confirm', () => {
  it('a reason and since when, the engine’s new week with its rule and the medical line, then the confirm', async () => {
    const g = garmin();
    await renderRouter(app(g), { initialUrl: '/plan' });
    await press('button-Life happened');

    const life = within(await screen.findByTestId('life-sheet'));
    expect(life.getByText('What changed?')).toBeTruthy();
    expect(life.getByText("Daash doesn't give medical advice. If you're unwell or hurt, check with a doctor or physio.")).toBeTruthy();
    await fireEvent.press(life.getByTestId('reason-niggle'));
    await fireEvent.press(life.getByTestId('since-pick'));
    await fireEvent.press(life.getByTestId('since-2026-11-25'));
    await fireEvent.press(life.getByTestId('button-See the new week'));

    expect(await screen.findByTestId('diff-kicker')).toHaveTextContent('Niggle or injury · since Wed 25');
    expect(screen.getByTestId('diff-title')).toHaveTextContent('2 sessions change');
    expect(screen.getByTestId('diff-rule')).toHaveTextContent(/Niggle: no hard sessions for 5 days/);
    expect(screen.getByTestId('diff-disclaimer')).toHaveTextContent("Daash doesn't give medical advice. If you're unwell or hurt, check with a doctor or physio.");
    expect(screen.getByLabelText(/^Kilometres a week\. This week: 54 before, 36 after\. Next week: unchanged at 55\.5\./)).toBeTruthy();
    await press('button-Review watch changes');

    const sheet = within(await screen.findByTestId('confirm-sheet'));
    expect(sheet.getByText('2 changes on your watch')).toBeTruthy();
    await fireEvent.press(sheet.getByTestId('button-Confirm and send 2 changes'));

    // Back on the week: the new sessions, each with its own status.
    const week = () => within(screen.getByTestId('week-2026-11-23'));
    expect(await week().findByText('Easy 3.5 km')).toBeTruthy();
    expect(week().getByText('Long 6.5 km easy')).toBeTruthy();
    await pump();
    expect(g.sent.map((o) => o.sessionId)).toEqual(['2026-11-27-tempo', '2026-11-29-long']);
    expect(week().getAllByLabelText('Changed · arrives at next sync')).toHaveLength(2);
  });

  it('dragging a move, then "Review and send", goes through the same new week and confirm', async () => {
    const g = garmin();
    await renderRouter(app(g), { initialUrl: '/plan' });
    const row = await screen.findByTestId('open-2026-11-27-tempo');
    await fireEvent(row, 'accessibilityAction', { nativeEvent: { actionName: 'later' } });
    await press('button-Review and send');
    expect(await screen.findByTestId('diff-subtitle')).toHaveTextContent('Tempo moves from Fri to Sat. This week’s kilometres stay the same.');
    await press('button-Review watch changes');
    const sheet = within(await screen.findByTestId('confirm-sheet'));
    expect(sheet.getByLabelText('Remove, Fri 27, 3 × 2 km at threshold')).toBeTruthy();
    expect(sheet.getByLabelText('Add, Sat 28, 3 × 2 km at threshold')).toBeTruthy();
    await fireEvent.press(sheet.getByTestId('button-Confirm and send 2 changes'));
    await pump();
    expect(g.sent.map((o) => [o.kind, o.date])).toEqual([['remove', '2026-11-27'], ['add', '2026-11-28']]);
    expect(await screen.findByTestId('row-2026-11-27-tempo')).toBeTruthy();
    expect(screen.queryByTestId('proposal')).toBeNull(); // the queue has it now
  });
});

describe('3. Send fails → Resend', () => {
  it('a timeout reads "Not sent" in words, nothing changed on the watch, and Resend sends it', async () => {
    const g = garmin(['timeout']);
    await openSession(g, '2026-12-02-intervals');
    await press('button-Send to watch');

    const sheet = within(await screen.findByTestId('confirm-sheet'));
    expect(sheet.getByText('Send to Forerunner 965?')).toBeTruthy();
    expect(sheet.getByLabelText('Wed 2 Dec, 10 × 400 m, 9.5 km')).toBeTruthy();
    expect(sheet.getByLabelText('Targets, pace /km')).toBeTruthy();
    expect(sheet.getByLabelText('Replaces, nothing')).toBeTruthy();
    await fireEvent.press(sheet.getByTestId('button-Confirm and send'));

    expect(await screen.findByLabelText('Sending to Forerunner 965')).toBeTruthy();
    await pump();
    expect(screen.getByText('Not sent. Garmin didn’t answer.')).toBeTruthy();
    expect(screen.getByText('Nothing changed on your watch. Daash retries when Garmin is back.')).toBeTruthy();
    expect(screen.getByTestId('button-Trouble connecting?')).toBeTruthy();
    expect(g.sent).toHaveLength(0);

    await press('button-Resend');
    await pump();
    expect(g.sent.map((o) => o.sessionId)).toEqual(['2026-12-02-intervals']);
    expect(screen.getByLabelText('Changed · arrives at next sync')).toBeTruthy();
  });

  it('a refusal says why, and waits for Resend rather than retrying', async () => {
    const g = garmin(['refused']);
    await openSession(g, '2026-12-02-intervals');
    await press('button-Send to watch');
    await press('button-Confirm and send');
    await pump();
    expect(screen.getByText('Not sent. Garmin said no: that workout is gone from Garmin Connect')).toBeTruthy();
    expect(screen.getByText('Nothing changed on your watch.')).toBeTruthy();
    expect(queue!.ops[0].nextAttemptAt).toBeNull();
  });
});

describe('4. Remove from watch', () => {
  it('from the toast after a send: it comes off the watch and stays in the plan', async () => {
    const g = garmin([], 1);
    await openSession(g, '2026-12-02-intervals');
    await press('button-Send to watch');
    await press('button-Confirm and send');
    await pump();
    expect(within(screen.getByTestId('watch-card')).getByLabelText(/^On your watch · /)).toBeTruthy();
    await press('toast-remove');

    const sheet = within(await screen.findByTestId('confirm-sheet'));
    expect(sheet.getByText('Remove from your watch?')).toBeTruthy();
    expect(sheet.getByLabelText('Remove, Wed 2, 10 × 400 m')).toBeTruthy();
    expect(sheet.getByText('It stays in your plan, marked Not on watch. You can send it again any time.')).toBeTruthy();
    await fireEvent.press(sheet.getByTestId('button-Remove from watch'));
    await pump();
    expect(g.sent.map((o) => o.kind)).toEqual(['add', 'remove']);
    expect(within(screen.getByTestId('watch-card')).getByLabelText('Not on your watch')).toBeTruthy();
    expect(screen.getByText('10 × 400 m')).toBeTruthy(); // still in the plan
  });

  it('from a session already on the watch', async () => {
    const g = garmin([], 1);
    await openSession(g, '2026-11-27-tempo');
    expect(within(await screen.findByTestId('watch-card')).getByLabelText('On your watch · 20:14')).toBeTruthy();
    await press('button-Remove from watch');
    await fireEvent.press(within(await screen.findByTestId('confirm-sheet')).getByTestId('button-Remove from watch'));
    expect(await screen.findByLabelText('Sending to Forerunner 965')).toBeTruthy();
    await pump();
    expect(within(screen.getByTestId('watch-card')).getByLabelText('Not on your watch')).toBeTruthy();
    expect(screen.queryByTestId('send-toast')).toBeNull(); // a removal offers no "Remove from watch"
  });
});

describe('5. Each change of a send is said once', () => {
  const said = () => {
    const spy = jest.spyOn(AccessibilityInfo, 'announceForAccessibility').mockImplementation(() => {});
    spy.mockClear(); // the platform mock may already hold earlier tests' calls
    return { words: () => spy.mock.calls.map((c) => c[0]), done: () => spy.mockRestore() };
  };

  it('on Today: sending, then accepted with the way back, then on the watch', async () => {
    const g = garmin();
    await renderRouter(app(g), { initialUrl: '/today' });
    const a = said();
    try {
      await press('button-Make it easier');
      await press('button-Review watch changes');
      await press('button-Confirm and send 1 change');
      await within(screen.getByTestId('session-card')).findByLabelText('Sending');
      await pump();
      await pump();
      await pump(); // nothing new: nothing said
      expect(a.words()).toEqual([
        'Sending to Forerunner 965',
        'Changed · arrives at next sync. Changed your mind? Remove from watch.',
        'On your watch',
      ]);
    } finally { a.done(); }
  });

  it('on a session: not sent, a retry on its own says nothing new, Resend, then accepted', async () => {
    const g = garmin(['timeout', 'timeout']);
    let now = Date.now();
    // Today stays mounted in its tab behind the Plan's session: still said once.
    await renderRouter(app(g, () => now), { initialUrl: '/today' });
    await screen.findByTestId('today');
    await act(async () => { router.push('/plan'); });
    await screen.findByTestId('week-title');
    await act(async () => { router.push({ pathname: '/plan/session/[id]', params: { id: '2026-12-02-intervals' } }); });
    await screen.findByTestId('session');
    const a = said();
    try {
      await press('button-Send to watch');
      await press('button-Confirm and send');
      await pump();
      expect(screen.getByText('Not sent. Garmin didn’t answer.')).toBeTruthy();
      // Its backoff passes: the automatic retry fails the same way, and isn't said again.
      const op = queue!.ops[0];
      await act(async () => { await queue!.pump(); });
      expect(queue!.ops[0].attempts).toBe(op.attempts); // not due yet
      now += 60_000;
      await pump();
      expect(queue!.ops[0].attempts).toBe(op.attempts + 1);
      await press('button-Resend');
      await pump();
      expect(a.words()).toEqual([
        'Sending to Forerunner 965',
        'Not sent. Garmin didn’t answer.',
        'Sending to Forerunner 965',
        'Changed · arrives at next sync. Changed your mind? Remove from watch.',
      ]);
    } finally { a.done(); }
  });
});

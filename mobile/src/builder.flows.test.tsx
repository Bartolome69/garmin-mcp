// The builder flows end to end (build prompt, sections 5.7 and 15, milestone 6), over
// the real routes, screens and watch queue, against a scripted Garmin.
//
//   5. New workout → read as → Harder → a day → Save and send → confirm → on Plan
//   6. A typo is shown with its fix-it, and the fix-it fixes it
//   7. A vague effort is refused, with what to write instead
//   8. Edit steps: take off the added warm-up, add a step
//   9. Edit a planned session: prefilled, and saving replaces it
import { describe, expect, it } from '@jest/globals';
import { router, Stack } from 'expo-router';
import { act, fireEvent, renderRouter, screen, within } from 'expo-router/testing-library';
import type { ReactNode } from 'react';

import TabsLayout from './app/(tabs)/_layout';
import CoachIndex from './app/(tabs)/coach/index';
import CrewIndex from './app/(tabs)/crew/index';
import PlanBuilder from './app/(tabs)/plan/builder';
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
import YouLayout from './app/(tabs)/you/_layout';
import YouIndex from './app/(tabs)/you/index';
import WatchConfirm from './app/watch/confirm';
import type { BuildOp } from './features/builder/model';
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
    '(tabs)/plan/_layout': PlanLayout,
    '(tabs)/plan/index': PlanIndex,
    '(tabs)/plan/builder': PlanBuilder,
    '(tabs)/plan/life': PlanLife,
    '(tabs)/plan/move': PlanMove,
    '(tabs)/plan/new-week': PlanNewWeek,
    '(tabs)/plan/next': PlanNext,
    '(tabs)/plan/session/[id]': PlanSession,
    '(tabs)/coach/index': CoachIndex,
    '(tabs)/crew/index': CrewIndex,
    '(tabs)/you/_layout': YouLayout,
    '(tabs)/you/index': YouIndex,
    'watch/confirm': WatchConfirm,
  };
}

const pump = async () => { await act(async () => { await queue!.pump(); }); };
const press = async (testID: string) => { await fireEvent.press(await screen.findByTestId(testID)); };
const builder = () => within(screen.getByTestId('builder'));
const type = async (text: string) => { await fireEvent.changeText(await screen.findByTestId('builder-input'), text); };

async function openBuilder(g: FakeGarmin, flags?: Flags) {
  await renderRouter(app(g, flags), { initialUrl: '/plan' });
  await screen.findByTestId('week-title');
  await act(async () => { router.push('/plan/builder'); });
  await screen.findByTestId('builder');
}

describe('5. New workout → Save and send', () => {
  it('reads the text back, shows the steps, steps the paces, and sends exactly that to the watch', async () => {
    const g = new FakeGarmin([], 'ok', 1);
    await openBuilder(g);
    expect(builder().getByText('Type it how you’d say it. Daash turns it into steps.')).toBeTruthy();
    expect(builder().queryByTestId('button-Save and send')).toBeNull(); // nothing to save until there's text
    await type('5x1km at threshold, 2 min easy');

    expect(builder().getByTestId('read-as')).toHaveTextContent('Read as 5 repeats · 1 km at threshold · 2 min easy jog · parsed on your phone');
    const steps = within(builder().getByTestId('builder-steps'));
    expect(steps.getByText('Tempo')).toBeTruthy();
    expect(steps.getByTestId('estimate')).toHaveTextContent('10.1 km · about 52 min');
    expect(steps.getByLabelText('Repeat 5 times')).toBeTruthy();
    expect(steps.getByLabelText('1 km, Threshold, 4:23 to 4:28 per km')).toBeTruthy();
    expect(steps.getAllByText('10 min easy (added)')).toHaveLength(2);

    expect(builder().getByTestId('adjust')).toHaveTextContent(/^Threshold from your 10K of 43:30Easier/);
    await fireEvent.press(builder().getByTestId('button-Harder'));
    expect(builder().getByTestId('adjust')).toHaveTextContent(/^Threshold from your 10K of 43:30 · Harder by 2%Easier/);
    expect(within(builder().getByTestId('builder-steps')).queryByLabelText('1 km, Threshold, 4:23 to 4:28 per km')).toBeNull();

    await press('day-pick');
    await press('day-2026-12-04');
    expect(builder().getByTestId('day-2026-12-04')).toHaveTextContent('Fri 4 Dec');
    expect(g.sent).toHaveLength(0);
    await press('button-Save and send');

    const sheet = within(await screen.findByTestId('confirm-sheet'));
    expect(sheet.getByText('Send to Forerunner 965?')).toBeTruthy();
    expect(sheet.getByLabelText(/^Fri 4 Dec, 5 × 1 km at threshold, 10\.\d km$/)).toBeTruthy();
    expect(sheet.getByLabelText('Replaces, nothing')).toBeTruthy();
    expect(g.sent).toHaveLength(0); // nothing sent until confirmed
    await press('button-Confirm and send');

    // The builder closes onto Plan, and the queue sends the workout as read.
    expect(await screen.findByTestId('week-title')).toBeTruthy();
    expect(screen.queryByTestId('builder')).toBeNull();
    await pump();
    expect(g.sent).toHaveLength(1);
    const op = g.sent[0] as unknown as BuildOp;
    expect(op).toMatchObject({ kind: 'add', date: '2026-12-04', what: '5 × 1 km at threshold' });
    expect(op.workout).toMatchObject({ schema: 'daash.workout.v1', name: '5 × 1 km at threshold', sport: 'running' });
    expect(op.workout.steps.map((s) => (s as { type: string }).type)).toEqual(['warmup', 'repeat', 'cooldown']);
  });

  it('with the Coach on, "parsed on your phone" goes and the Coach\'s note comes', async () => {
    await openBuilder(new FakeGarmin([], 'ok', 1), { ...DEFAULT_FLAGS, coach_enabled: true });
    await type('5x1km at threshold, 2 min easy');
    expect(builder().getByTestId('read-as')).toHaveTextContent(/^Read as 5 repeats · 1 km at threshold · 2 min easy jog$/);
    expect(builder().getByTestId('coach-note')).toHaveTextContent(/^Coach/);
  });
});

describe('6. A typo is shown with its fix-it', () => {
  it('marks the word, offers the likely one, and nothing saves until it reads', async () => {
    await openBuilder(new FakeGarmin());
    await type('5x1km at thresold, 2 min easy');
    expect(builder().queryByTestId('read-as')).toBeNull();
    expect(builder().queryByTestId('builder-steps')).toBeNull();
    expect(builder().getByLabelText('Not read: thresold')).toBeTruthy();
    expect(builder().getByTestId('button-Save and send')).toBeDisabled();
    await press('fix-0');
    expect(screen.getByTestId('builder-input').props.value).toBe('5x1km at threshold, 2 min easy');
    expect(builder().getByTestId('read-as')).toBeTruthy();
    expect(builder().getByTestId('button-Save and send')).not.toBeDisabled();
  });
});

describe('7. A vague effort is refused', () => {
  it('says what to write instead, and offers no steps', async () => {
    await openBuilder(new FakeGarmin());
    await type('6 x 400m hard');
    expect(builder().getByText('"hard" doesn\'t say how fast')).toBeTruthy();
    expect(builder().getByText('For a hard effort name the zone (threshold, 10K, 5K or VO2) or a pace like 4:00/km')).toBeTruthy();
    expect(builder().queryByTestId('fix-0')).toBeNull(); // nothing to tap: the runner chooses
    expect(builder().getByTestId('button-Save and send')).toBeDisabled();
  });
});

describe('8. Edit steps', () => {
  it('one row per step; the added warm-up can come off; a new row reads like the rest', async () => {
    await openBuilder(new FakeGarmin());
    await type('5x1km at threshold, 2 min easy');
    await press('button-Edit steps');
    const edit = () => within(screen.getByTestId('edit-steps'));
    expect(screen.getByTestId('row-0').props.value).toBe('5 x 1 km at threshold with 2 min easy');
    expect(edit().getByText('10 min warm-up (added)')).toBeTruthy();
    expect(builder().getByTestId('button-Save and send')).toBeDisabled(); // Done first

    await fireEvent.press(edit().getByTestId('toggle-warmup'));
    expect(edit().getByText('No warm-up')).toBeTruthy();
    await fireEvent.press(edit().getByTestId('button-Add a step'));
    await fireEvent.changeText(screen.getByTestId('row-1'), '6 x 20 s strides');
    expect(screen.getByTestId('builder-input').props.value).toBe('5 x 1 km at threshold with 2 min easy, 6 x 20 s strides');
    await fireEvent.changeText(screen.getByTestId('row-1'), '6 x 20 s quick');
    expect(edit().getByTestId('row-problem-1')).toHaveTextContent(/^"quick" doesn't say how fast\. For a hard effort/);
    await fireEvent.changeText(screen.getByTestId('row-1'), '6 x 20 s strides');

    await press('button-Done');
    expect(builder().getByTestId('read-as')).toHaveTextContent('Read as 5 repeats · 1 km at threshold · 2 min easy jog · 6 repeats · 20 s strides · parsed on your phone');
    const steps = within(builder().getByTestId('builder-steps'));
    expect(steps.queryByText(/warm-up/)).toBeNull();
    expect(steps.getAllByText('10 min easy (added)')).toHaveLength(1); // the cool-down stays
  });
});

describe('9. Edit a planned session', () => {
  it('opens with its steps as text, and saving replaces it in the plan and on the watch', async () => {
    const g = new FakeGarmin([], 'ok', 1);
    await renderRouter(app(g), { initialUrl: '/plan' });
    await screen.findByTestId('week-title');
    await act(async () => { router.push({ pathname: '/plan/session/[id]', params: { id: '2026-11-27-tempo' } }); });
    await screen.findByTestId('session');
    await fireEvent.press(await screen.findByText('Edit'));

    expect((await screen.findByTestId('builder-input')).props.value).toBe('15 min warm-up, 3 x 2 km at threshold with 2 min jog, 10 min cool-down');
    expect(builder().getByTestId('day-2026-11-27')).toHaveTextContent('Fri 27 Nov');
    expect(builder().queryByTestId('day-planned')).toBeNull(); // it's this session
    await type('15 min warm-up, 4 x 2 km at threshold with 2 min jog, 10 min cool-down');
    await press('button-Save and send');

    const sheet = within(await screen.findByTestId('confirm-sheet'));
    expect(sheet.getByLabelText('Replaces, 3 × 2 km at threshold')).toBeTruthy();
    await press('button-Confirm and send');

    // Back on the session: the new steps, in place of the old.
    const session = within(await screen.findByTestId('session'));
    expect(await session.findByText('4 × 2 km at threshold')).toBeTruthy();
    expect(session.getByLabelText('Repeat 4 times')).toBeTruthy();
    await pump();
    expect(g.sent.map((o) => [o.kind, o.sessionId])).toEqual([['replace', '2026-11-27-tempo']]);
  });

  it('no Edit on a session the builder doesn\'t read', async () => {
    await renderRouter(app(new FakeGarmin()), { initialUrl: '/plan' });
    await screen.findByTestId('week-title');
    await act(async () => { router.push({ pathname: '/plan/session/[id]', params: { id: '2026-11-28-strength' } }); });
    await screen.findByTestId('session');
    expect(screen.queryByText('Edit')).toBeNull();
  });
});

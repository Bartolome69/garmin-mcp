// Plan (milestone 4), rendered from its view model on iOS and on Android (this
// suite runs once per platform), in dark and light, Coach off and on, Free and Plus.
import { describe, expect, it, jest } from '@jest/globals';
import { act, fireEvent, render, screen, within } from '@testing-library/react-native';
import { Platform, processColor, StyleSheet, Text as RNText } from 'react-native';

import { DEFAULT_FLAGS } from '../../flags/flags';
import { FlagsProvider } from '../../flags/FlagsProvider';
import { ThemeProvider } from '../../theme/ThemeProvider';
import { colors } from '../../theme/tokens';
import { PERSONA_PLAN, PERSONA_PLAN_COACH, PERSONA_PLAN_PLUS } from './fixture';
import { findSession, numbersOf, weekIndexOf, type PlanVM } from './model';
import { PlanProvider, usePlan } from './PlanProvider';
import type { PlanTab } from './PlanTabs';
import { PlanView, type PlanActions } from './PlanView';
import { SessionDetailView } from './SessionDetailView';
import type { Pending } from './PlanProvider';
import { memoryStore } from '../watch/store';
import { FakeGarmin } from '../watch/transport';
import { WatchProvider } from '../watch/WatchProvider';

const os = Platform.OS;
const actions = (): PlanActions => ({
  open: jest.fn(), move: jest.fn(), moveAnyway: jest.fn(), keep: jest.fn(), undo: jest.fn(), review: jest.fn(),
  life: jest.fn(), newWorkout: jest.fn(), refresh: jest.fn(), trim: jest.fn(), plus: jest.fn(), change: jest.fn(),
});

async function draw(vm: PlanVM, { scheme = 'dark' as 'dark' | 'light', tab = 'Week' as PlanTab, brand = true, plus = false,
  pending = null as Pending | null, proposalText = null as string | null, a = actions(), index = weekIndexOf(vm, vm.currentWeek) } = {}) {
  const utils = await render(
    <ThemeProvider initial={scheme}>
      <PlanView vm={vm} tab={tab} onTab={jest.fn()} index={index} onIndex={jest.fn()} pending={pending} proposalText={proposalText}
        plus={plus} brand={brand} fresh={{ stale: false, label: '07:02' }} refreshing={false} width={390} actions={a} />
    </ThemeProvider>,
  );
  return { ...utils, a };
}

async function drawSession(id: string, { coach = false, scheme = 'dark' as 'dark' | 'light', vm = PERSONA_PLAN } = {}) {
  const send = jest.fn(), move = jest.fn(), swap = jest.fn(), skip = jest.fn(), resend = jest.fn(), remove = jest.fn(), trouble = jest.fn();
  function Harness() {
    const [step, setStep] = require('react').useState(0);
    return <SessionDetailView session={findSession(vm, id)!} basis={vm.basis} coach={coach} step={step} onStep={setStep}
      actions={{ send, move, swap, skip, resend, remove, trouble }} />;
  }
  const utils = await render(<ThemeProvider initial={scheme}><Harness /></ThemeProvider>);
  return { ...utils, send, move };
}

type Node = { type: string; props: Record<string, any>; children: (Node | string)[] | null };
const nodes = (tree: unknown): Node[] => {
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
const brandFills = (tree: unknown, scheme: 'dark' | 'light') =>
  nodes(tree).filter((n) => StyleSheet.flatten(n.props.style)?.backgroundColor === colors[scheme].brand).length;
const texts = (tree: unknown) => nodes(tree).filter((n) => n.type === 'Text')
  .flatMap((n) => (n.children ?? []).filter((c): c is string => typeof c === 'string' && c.trim() !== ''));

const WEEK4 = PERSONA_PLAN.weeks[1];

describe.each(['dark', 'light'] as const)(`Plan · Week on ${os}, %s`, (scheme) => {
  it('draws "Week 4 · Base", its summary and seven rows', async () => {
    await draw(PERSONA_PLAN, { scheme });
    expect(screen.getByTestId('week-title')).toHaveTextContent('Week 4 · Base');
    expect(screen.getByTestId('week-summary')).toHaveTextContent('23 to 29 Nov · 28 of 54 km · 3 of 6 done');
    const page = within(screen.getByTestId('week-2026-11-23'));
    for (const d of WEEK4.days) expect(page.getByTestId(`row-${d.sessions[0].id}`)).toBeTruthy();
    expect(page.getByText('3 × 2 km at threshold')).toBeTruthy();
    expect(page.getByText('Tempo · 10 km · 4:23–4:28 /km')).toBeTruthy();
  });

  it('status icons are distinct shapes with words: Done, On your watch', async () => {
    await draw(PERSONA_PLAN, { scheme });
    const page = within(screen.getByTestId('week-2026-11-23'));
    expect(page.getAllByLabelText('Done')).toHaveLength(4);
    expect(page.getAllByLabelText('On your watch')).toHaveLength(3);
  });

  it("today's row is on the sunken tone with its date in brand text, not a fill", async () => {
    const { toJSON } = await draw(PERSONA_PLAN, { scheme });
    const row = screen.getByTestId('row-2026-11-27-tempo');
    expect(StyleSheet.flatten(row.props.style).backgroundColor).toBe(colors[scheme].sunken);
    const date = within(row).getByText('27');
    expect(StyleSheet.flatten(date.props.style).color).toBe(colors[scheme].brandText);
    expect(brandFills(toJSON(), scheme)).toBe(os === 'android' ? 1 : 0);
  });

  it('rows read as one sentence for screen readers', async () => {
    await draw(PERSONA_PLAN, { scheme });
    expect(screen.getByLabelText('Fri 27, today, 3 × 2 km at threshold, Tempo · 10 km · 4:23–4:28 per km, On your watch')).toBeTruthy();
    expect(screen.getByLabelText('Mon 30, Rest day, Rest · Recovery')).toBeTruthy();
    expect(screen.getByLabelText('Tue 1, 8 km easy, Easy · 8 km · 5:27–6:10 per km, Not on your watch')).toBeTruthy();
  });
});

describe(`Plan on ${os}: the platform's controls`, () => {
  if (os === 'ios') {
    it('a segmented control for Week / Block / Trends, and "New workout" beside "Life happened"', async () => {
      const { a } = await draw(PERSONA_PLAN);
      expect(screen.getByTestId('plan-tabs').props.values).toEqual(['Week', 'Block', 'Trends']);
      expect(screen.queryByTestId('fab-New workout')).toBeNull();
      await fireEvent.press(screen.getByTestId('button-New workout'));
      expect(a.newWorkout).toHaveBeenCalled();
    });
  } else {
    it('Material 3 tabs and the extended FAB, which is the one brand fill and loses it behind a sheet', async () => {
      const { toJSON, a } = await draw(PERSONA_PLAN);
      expect(screen.getAllByRole('tab').map((t) => within(t).getByText(/./).props.children)).toEqual(['Week', 'Block', 'Trends']);
      expect(screen.getByRole('tab', { selected: true })).toBe(screen.getByTestId('plan-tab-Week'));
      expect(screen.queryByTestId('button-New workout')).toBeNull();
      const fab = screen.getByTestId('fab-New workout');
      expect(StyleSheet.flatten(fab.props.style).backgroundColor).toBe(colors.dark.brand);
      expect(brandFills(toJSON(), 'dark')).toBe(1);
      await fireEvent.press(fab);
      expect(a.newWorkout).toHaveBeenCalled();
      const behind = await draw(PERSONA_PLAN, { brand: false });
      expect(brandFills(behind.toJSON(), 'dark')).toBe(0);
    });

    it('no FAB on Block or Trends', async () => {
      await draw(PERSONA_PLAN, { tab: 'Trends' });
      expect(screen.queryByTestId('fab-New workout')).toBeNull();
    });
  }

  it('"Life happened", "This week" and the chevrons', async () => {
    const onIndex = jest.fn();
    await render(
      <ThemeProvider initial="dark">
        <PlanView vm={PERSONA_PLAN} tab="Week" onTab={jest.fn()} index={1} onIndex={onIndex} pending={null} proposalText={null}
          plus={false} brand fresh={null} refreshing={false} width={390} actions={actions()} />
      </ThemeProvider>,
    );
    await fireEvent.press(screen.getByTestId('week-next'));
    expect(onIndex).toHaveBeenLastCalledWith(2);
    await fireEvent.press(screen.getByTestId('week-prev'));
    expect(onIndex).toHaveBeenLastCalledWith(0);
    await fireEvent.press(screen.getByTestId('week-this'));
    expect(onIndex).toHaveBeenLastCalledWith(1);
  });

  it('opens a planned session, and a done one to its run', async () => {
    const { a } = await draw(PERSONA_PLAN);
    await fireEvent.press(screen.getByTestId('open-2026-11-27-tempo'));
    expect(a.open).toHaveBeenLastCalledWith(expect.objectContaining({ id: '2026-11-27-tempo' }));
    await fireEvent.press(screen.getByTestId('open-2026-11-25-intervals'));
    expect(a.open).toHaveBeenLastCalledWith(expect.objectContaining({ id: '2026-11-25-intervals', done: true }));
  });
});

describe(`Moving a session on ${os}`, () => {
  it('VoiceOver and TalkBack move it a day with actions, the same as a drag', async () => {
    const { a } = await draw(PERSONA_PLAN);
    const row = screen.getByTestId('open-2026-11-27-tempo');
    expect(row.props.accessibilityActions.map((x: { label: string }) => x.label)).toEqual(['Move a day earlier', 'Move a day later']);
    await fireEvent(row, 'accessibilityAction', { nativeEvent: { actionName: 'later' } });
    expect(a.move).toHaveBeenLastCalledWith('2026-11-27-tempo', '2026-11-28');
    expect(screen.getByTestId('open-2026-11-24-easy').props.accessibilityActions).toBeUndefined(); // done stays put
  });

  it('a long press, then a drag down one row, drops it on that day (the real PanResponder)', async () => {
    const { a } = await draw(PERSONA_PLAN);
    // Each day's place in the list, as a phone reports it: 56 pt a row.
    for (const [i, d] of WEEK4.days.entries()) {
      await fireEvent(screen.getByTestId(`day-${d.date}`), 'layout', { nativeEvent: { layout: { x: 0, y: i * 56, width: 358, height: 56 } } });
    }
    const press = screen.getByTestId('open-2026-11-27-tempo');
    let grip = press.parent;
    while (grip && !grip.props.onResponderMove) grip = grip.parent;
    // A finger's touch history, as React Native's responder system keeps it.
    const touch = (y: number, t: number, prev = 0) => ({ nativeEvent: {}, touchHistory: {
      numberActiveTouches: 1, indexOfSingleActiveTouch: 0, mostRecentTimeStamp: t,
      touchBank: [{ touchActive: true, startPageX: 100, startPageY: 0, startTimeStamp: 0, currentPageX: 100, currentPageY: y,
        currentTimeStamp: t, previousPageX: 100, previousPageY: prev, previousTimeStamp: Math.max(0, t - 16) }] } });
    // Before the long press a move doesn't lift anything: a swipe still scrolls.
    expect(grip!.props.onMoveShouldSetResponderCapture(touch(10, 10))).toBe(false);
    await fireEvent(press, 'longPress');
    expect(StyleSheet.flatten(screen.getByTestId('row-2026-11-27-tempo').props.style).borderWidth).toBe(1.5); // lifted
    expect(grip!.props.onMoveShouldSetResponderCapture(touch(10, 12))).toBe(true);
    await act(async () => { grip!.props.onResponderGrant(touch(0, 20)); });
    // The press ends as the pan takes the touch: the row stays lifted.
    await fireEvent(press, 'pressOut');
    await act(async () => { await new Promise((r) => setTimeout(r, 5)); });
    expect(StyleSheet.flatten(screen.getByTestId('row-2026-11-27-tempo').props.style).borderWidth).toBe(1.5);
    for (let i = 1; i <= 4; i++) await act(async () => { grip!.props.onResponderMove(touch(i * 14, 20 + i * 16, (i - 1) * 14)); });
    expect(screen.getByText('Tempo · dropping on Sat')).toBeTruthy();
    await act(async () => { grip!.props.onResponderRelease(touch(56, 100, 56)); });
    expect(a.move).toHaveBeenCalledWith('2026-11-27-tempo', '2026-11-28');
    expect(screen.getByText('Tempo · 10 km · 4:23–4:28 /km')).toBeTruthy(); // set back down
  });

  it('a long press let go without moving puts the row back down and moves nothing', async () => {
    const { a } = await draw(PERSONA_PLAN);
    const press = screen.getByTestId('open-2026-11-27-tempo');
    await fireEvent(press, 'longPress');
    expect(StyleSheet.flatten(screen.getByTestId('row-2026-11-27-tempo').props.style).borderWidth).toBe(1.5);
    await fireEvent(press, 'pressOut');
    await act(async () => { await new Promise((r) => setTimeout(r, 5)); });
    expect(StyleSheet.flatten(screen.getByTestId('row-2026-11-27-tempo').props.style).borderWidth).not.toBe(1.5);
    expect(a.move).not.toHaveBeenCalled();
  });

  it('the back-to-back warning offers "Move anyway" and "Keep it on Fri" before anything changes', async () => {
    const pending: Pending = { kind: 'warn', proposal: { id: '2026-11-27-tempo', from: '2026-11-27', to: '2026-11-26' },
      title: 'Back-to-back hard days.', words: 'Intervals on Wed and tempo on Thu leave no easy day between.', keepLabel: 'Keep it on Fri' };
    const { a } = await draw(PERSONA_PLAN, { pending });
    expect(within(screen.getByTestId('drag-warning')).getByText(/Intervals on Wed and tempo on Thu/)).toBeTruthy();
    await fireEvent.press(screen.getByTestId('button-Move anyway'));
    expect(a.moveAnyway).toHaveBeenCalled();
    await fireEvent.press(screen.getByTestId('button-Keep it on Fri'));
    expect(a.keep).toHaveBeenCalled();
  });

  it('a proposed move says the watch waits for the confirm', async () => {
    const { a } = await draw(PERSONA_PLAN, { proposalText: 'Moving tempo from Fri to Sat.' });
    expect(screen.getByText('Moving tempo from Fri to Sat. Nothing changes on your watch until you confirm.')).toBeTruthy();
    await fireEvent.press(screen.getByTestId('button-Review and send'));
    expect(a.review).toHaveBeenCalled();
    await fireEvent.press(screen.getByTestId('button-Undo'));
    expect(a.undo).toHaveBeenCalled();
  });

  it('the provider runs the check, proposes, warns and undoes', async () => {
    let plan: ReturnType<typeof usePlan> | null = null;
    function Probe() { plan = usePlan(); return <RNText>{plan.proposal ? `${plan.proposal.id}>${plan.proposal.to}` : plan.pending?.kind ?? 'idle'}</RNText>; }
    await render(
      <FlagsProvider initial={DEFAULT_FLAGS}>
        <WatchProvider transport={new FakeGarmin()} store={memoryStore()} auto={false}>
          <PlanProvider fetcher={async () => PERSONA_PLAN}><Probe /></PlanProvider>
        </WatchProvider>
      </FlagsProvider>,
    );
    await screen.findByText('idle');
    expect(plan!.data).toBe(PERSONA_PLAN);
    await act(async () => { plan!.move('2026-11-27-tempo', '2026-12-03'); });
    expect(screen.getByText('warn')).toBeTruthy();
    expect(findSession(plan!.shown!, '2026-11-27-tempo')!.date).toBe('2026-11-27'); // nothing changed yet
    await act(async () => { plan!.confirmPending(); });
    expect(screen.getByText('2026-11-27-tempo>2026-12-03')).toBeTruthy();
    expect(findSession(plan!.shown!, '2026-11-27-tempo')!.watch!.state).toBe('confirming');
    await act(async () => { plan!.clearProposal(); });
    expect(screen.getByText('idle')).toBeTruthy();
    await act(async () => { plan!.move('2026-11-27-tempo', '2026-11-28'); });
    expect(screen.getByText('2026-11-27-tempo>2026-11-28')).toBeTruthy();
  });
});

describe(`Plan · Block and Trends on ${os}`, () => {
  it('Block: weeks to race day, the neutral phase bar and the phases', async () => {
    const { toJSON } = await draw(PERSONA_PLAN, { tab: 'Block' });
    expect(screen.getByText('21 weeks to London')).toBeTruthy();
    expect(screen.getByLabelText(/^21 weeks: Base, weeks 1 to 7; Build, weeks 8 to 14; Peak, weeks 15 to 18; Taper, weeks 19 to 21\. You're in week 4\.$/)).toBeTruthy();
    expect(screen.getByLabelText(/^Base, weeks 1 to 7, now\./)).toBeTruthy();
    expect(screen.getByLabelText(/^Build, weeks 8 to 14, from 4 Jan\./)).toBeTruthy();
    // Neutral: the bar is the text colour, never a session colour.
    const bar = nodes(toJSON()).find((n) => n.props.testID === 'phase-bar')!;
    const segments = (bar.children ?? []) as Node[];
    expect(segments).toHaveLength(21);
    const fills = new Set(segments.map((n) => StyleSheet.flatten(n.props.style).backgroundColor));
    expect([...fills]).toEqual([colors.dark.text]);
  });

  it('Trends (Free): load chart with the band, the week over it in warning with "Trim week 7", the prediction, and a locked trend', async () => {
    const { a } = await draw(PERSONA_PLAN, { tab: 'Trends' });
    expect(screen.getByLabelText(/Week 7 is above the band\.$/)).toBeTruthy();
    // react-native-svg hands the native side processed colours.
    const paint = (c: string) => ({ type: 0, payload: processColor(c) });
    const bar = (starts: string) => screen.getByTestId(`load-${starts}`).props;
    expect(bar('2026-12-14').fill).toEqual(paint(colors.dark.warning));
    expect(bar('2026-12-07').fill).toBeFalsy(); // planned: no fill,
    expect(bar('2026-12-07').stroke).toEqual(paint(colors.dark.text)); // a dashed outline
    expect(bar('2026-12-07').strokeDasharray).toBeTruthy();
    expect(bar('2026-11-16').fill).toEqual(paint(colors.dark.text)); // done: solid
    expect(screen.getByText('Week 7 is 18% above your target band.')).toBeTruthy();
    await fireEvent.press(screen.getByTestId('trim'));
    expect(a.trim).toHaveBeenCalled();
    expect(screen.getByText('3:20:40')).toBeTruthy();
    expect(screen.getByText('Predicted London time')).toBeTruthy();
    expect(screen.getByTestId('trend-locked')).toBeTruthy();
    expect(screen.queryByLabelText(/Race day/)).toBeNull(); // no Plus numbers behind the lock
    await fireEvent.press(screen.getByTestId('see-plus'));
    expect(a.plus).toHaveBeenCalled();
  });

  it('Trends (Plus): the trend and race-day projection', async () => {
    await draw(PERSONA_PLAN_PLUS, { tab: 'Trends', plus: true });
    expect(screen.queryByTestId('trend-locked')).toBeNull();
    expect(screen.getByLabelText('Predicted time over 12 weeks, from Sep 3:27 to 3:20:40 now. Race day · 3:14 if on plan.')).toBeTruthy();
  });
});

describe(`Session detail on ${os}`, () => {
  it('a 10-rep session: one line per step, the reps in a "10 ×" block, pace ranges per km', async () => {
    await drawSession('2026-12-02-intervals');
    expect(screen.getByText('10 × 400 m')).toBeTruthy();
    expect(screen.getByText('Wed 2 Dec · 9.5 km · about 48 min')).toBeTruthy();
    expect(screen.getByLabelText('Repeat 10 times')).toBeTruthy();
    expect(within(screen.getByTestId('repeat')).getByText('4:03–4:10')).toBeTruthy();
    expect(screen.getAllByText('5:27–6:10')).toHaveLength(2);
    expect(screen.getByText('Based on your 10K of 43:30 (12 Oct)')).toBeTruthy();
    expect(screen.getByLabelText('Not on your watch')).toBeTruthy();
  });

  it('Easier and Harder step the targets 2% with the shared pace engine', async () => {
    await drawSession('2026-12-02-intervals');
    await fireEvent.press(screen.getByTestId('button-Easier'));
    expect(screen.getByText('Based on your 10K of 43:30 (12 Oct) · Easier by 2%')).toBeTruthy();
    expect(within(screen.getByTestId('repeat')).getByText('4:08–4:15')).toBeTruthy();
    await fireEvent.press(screen.getByTestId('button-Harder'));
    await fireEvent.press(screen.getByTestId('button-Harder'));
    expect(screen.getByText('Based on your 10K of 43:30 (12 Oct) · Harder by 2%')).toBeTruthy();
    expect(within(screen.getByTestId('repeat')).getByText('3:58–4:05')).toBeTruthy();
  });

  it('on the watch: no send until the targets change, then "Send changes"', async () => {
    const { send } = await drawSession('2026-11-27-tempo');
    expect(screen.getByLabelText('On your watch · 20:14')).toBeTruthy();
    expect(screen.queryByTestId('button-Send to watch')).toBeNull();
    await fireEvent.press(screen.getByTestId('button-Harder'));
    expect(screen.getByText('The watch has the old targets until you send.')).toBeTruthy();
    await fireEvent.press(screen.getByTestId('button-Send changes'));
    expect(send).toHaveBeenCalled();
  });

  it('Move, Swap and Skip; "Send to watch" is the one brand fill', async () => {
    const { move, toJSON } = await drawSession('2026-12-02-intervals');
    await fireEvent.press(screen.getByTestId('button-Move'));
    expect(move).toHaveBeenCalled();
    expect(screen.getByTestId('button-Swap')).toBeTruthy();
    expect(screen.getByTestId('button-Skip')).toBeTruthy();
    expect(brandFills(toJSON(), 'dark')).toBe(1);
    expect(StyleSheet.flatten(screen.getByTestId('button-Send to watch').props.style).backgroundColor).toBe(colors.dark.brand);
  });

  it('Coach on adds one card on why this session, and nothing else changes', async () => {
    const off = await drawSession('2026-12-02-intervals');
    const offTexts = texts(off.toJSON());
    expect(screen.queryByTestId('coach-note')).toBeNull();
    const on = await drawSession('2026-12-02-intervals', { coach: true, vm: PERSONA_PLAN_COACH });
    expect(screen.getByText('Coach · why this session')).toBeTruthy();
    const onTexts = texts(on.toJSON()).filter((t) => t !== 'Coach · why this session' && !t.startsWith('Short reps'));
    expect(onTexts).toEqual(offTexts);
  });
});

it('Coach on and off: every number on Plan is the same', async () => {
  expect(numbersOf(PERSONA_PLAN_COACH)).toEqual(numbersOf(PERSONA_PLAN));
  const off = await draw(PERSONA_PLAN);
  const on = await draw(PERSONA_PLAN_COACH);
  expect(texts(on.toJSON())).toEqual(texts(off.toJSON()));
});

describe.each(['Week', 'Block', 'Trends'] as const)(`snapshot on ${os}`, (tab) => {
  it(tab, async () => {
    const { toJSON } = await draw(PERSONA_PLAN, { tab });
    expect(toJSON()).toMatchSnapshot(`plan-${tab}-${os}`);
  });
});

it(`snapshot: session detail on ${os}`, async () => {
  const { toJSON } = await drawSession('2026-12-02-intervals');
  expect(toJSON()).toMatchSnapshot(`session-${os}`);
});

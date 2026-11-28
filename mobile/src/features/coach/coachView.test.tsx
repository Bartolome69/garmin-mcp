// The Coach tab as drawn (boards P3-Coach-conv-ios/android, CoachStreaming, CoachLimit,
// CoachOffline, P1-Coach-confirm): one thread in dark and light (this suite runs once per
// platform), the one brand fill, no hue on the Coach, the send button, and the states.
import { describe, expect, it, jest } from '@jest/globals';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react-native';
import { Platform, StyleSheet, Text as RNText } from 'react-native';

import { ThemeProvider } from '../../theme/ThemeProvider';
import { colors } from '../../theme/tokens';
import FIXTURE from '../watch/proposals.fixture.json';
import type { Proposal } from '../watch/model';
import { CoachView, type CardState, type CoachActions } from './CoachView';
import { InlineChartView } from './InlineChart';
import { HRV_CHART, REP_HR_CHART } from './fake';
import { memoryItemsStore } from './memory';
import { MemoryProvider, useMemory, type MemoryContext } from './MemoryProvider';
import { MIN_TOUCH } from '../../ui/Button';
import { threadRows, type CoachMessage, type Limit } from './model';

const os = Platform.OS;
const EASIER = (FIXTURE as unknown as { cases: Record<string, Proposal> }).cases.easier;
const NOW = new Date(2026, 10, 27, 9, 0).getTime();
const H = 3_600_000;
const THREAD: CoachMessage[] = [
  { id: '1', role: 'user', text: 'Why did rep 6 feel harder?', status: 'sent', at: NOW - 40 * H, context: { kind: 'run', id: 'r', label: 'About · Wed 25 · 6 × 800 m' } },
  { id: '2', role: 'assistant', text: 'Rep 6 peaked at 172 bpm, 4 above rep 5.', status: 'done', at: NOW - 40 * H },
  { id: '3', role: 'user', text: 'Should I still do the tempo? I feel fine.', status: 'sent', at: NOW - 2 * H, context: { kind: 'readiness', id: null, label: 'From Today · Back off' } },
  { id: '4', role: 'assistant', text: 'Your call. HRV has been under your range of 54–66 ms for two nights: 52 and 49 ms.', status: 'done', at: NOW - 2 * H,
    chart: HRV_CHART, sources: ['HRV · Garmin', 'Sleep · Garmin'], feedback: 'up', flags: [] },
  { id: '5', role: 'user', text: 'Make it easier then.', status: 'sent', at: NOW - H },
  { id: '6', role: 'action', text: 'Make today easier: 3 × 2 km at threshold becomes Easy 6 km.', status: 'pending', at: NOW - H, request: { kind: 'easier' }, proposalId: EASIER.id },
];
const FREE: Limit = { limit: 10, remaining: 7, reached: false };

function draw({ scheme = 'dark' as 'dark' | 'light', messages = THREAD, limit = FREE, offline = false, streaming = false, brand = true,
  showEarlier = false, authFailure = null as 'signedOut' | 'reconnect' | null, cards = { 6: { proposal: EASIER, canReview: true, batch: null } } as Record<string, CardState> } = {}) {
  const actions = Object.fromEntries(['send', 'sendAgain', 'retry', 'removeContext', 'feedback', 'review', 'decline', 'inPlan', 'resend', 'showEarlier',
    'limit', 'editMemory', 'retryLoad', 'endSearch', 'clearFilter'].map((k) => [k, jest.fn()])) as unknown as CoachActions;
  const latest = Object.entries(cards).filter(([, c]) => !c.batch && c.canReview).map(([k]) => k).at(-1) ?? null;
  return render(
    <ThemeProvider initial={scheme}>
      <CoachView rows={threadRows(messages, NOW, showEarlier)} empty={!messages.length} starters={['Why Back off?', 'How’s my week?', 'Move Sunday']}
        limit={limit} offline={offline} streaming={streaming} context={null} searching={false} query="" onQuery={() => {}} filter={null}
        cards={cards} latestPending={latest} brand={brand} actions={actions} loadFailed={null} authFailure={authFailure} />
    </ThemeProvider>,
  ).then((u) => ({ ...u, actions }));
}

type Node = { type: string; props: Record<string, any>; children: (Node | string)[] | null };
const nodes = (t: unknown): Node[] => {
  const out: Node[] = [];
  const walk = (n: unknown) => { if (!n || typeof n !== 'object') return; if (Array.isArray(n)) { n.forEach(walk); return; } out.push(n as Node); ((n as Node).children ?? []).forEach(walk); };
  walk(t);
  return out;
};
const brandFills = (t: unknown, scheme: 'dark' | 'light') => nodes(t).filter((n) => StyleSheet.flatten(n.props.style)?.backgroundColor === colors[scheme].brand).length;

describe.each(['dark', 'light'] as const)(`The Coach on ${os}, %s`, (scheme) => {
  it('one thread: older days collapsed, today with its tags, a reply with a chart and sources, a proposal card', async () => {
    const { toJSON } = await draw({ scheme });
    expect(screen.getByTestId('coach-earlier').props.accessibilityLabel).toBe('Earlier · 2 messages since Wed. Show');
    expect(screen.getAllByTestId('context-tag').map((t) => t.props.accessibilityLabel)).toEqual(['From Today · Back off']);
    expect(within(screen.getByTestId('coach-reply-4')).getByLabelText(HRV_CHART.summary)).toBeTruthy();
    expect(within(screen.getByTestId('proposal-6')).getByLabelText('FRI 27: 3 × 2 km at threshold, becomes Easy 6 km')).toBeTruthy();
    expect(toJSON()).toMatchSnapshot(`coach-${scheme}-${os}`);
  });
  it('one brand fill, the newest proposal’s Review; none behind a sheet; the send button is neutral', async () => {
    const { toJSON } = await draw({ scheme });
    expect(brandFills(toJSON(), scheme)).toBe(1);
    expect(StyleSheet.flatten(screen.getByTestId('button-Review 1 watch change').props.style).backgroundColor).toBe(colors[scheme].brand);
    const behind = await draw({ scheme, brand: false });
    expect(brandFills(behind.toJSON(), scheme)).toBe(0);
    expect(StyleSheet.flatten(screen.getAllByTestId('coach-send').at(-1)!.props.style).backgroundColor).toBeUndefined();
  });
});

describe(`The Coach's states on ${os}`, () => {
  it('Show opens the earlier days', async () => {
    const { actions } = await draw();
    await fireEvent.press(screen.getByTestId('coach-earlier'));
    expect(actions.showEarlier).toHaveBeenCalled();
    await draw({ showEarlier: true });
    expect(screen.getAllByTestId('coach-day').map((d) => d.props.children)).toEqual(['Wed 25 · 17:00', 'Today · 07:00']);
  });
  it('send: disabled while empty, enabled with words, sends them and empties the field', async () => {
    const { actions } = await draw();
    expect(screen.getByTestId('coach-send').props.accessibilityState).toEqual({ disabled: true });
    await fireEvent.changeText(screen.getByTestId('composer'), 'How’s my week?');
    expect(screen.getByTestId('coach-send').props.accessibilityState).toEqual({ disabled: false });
    await fireEvent.press(screen.getByTestId('coach-send'));
    expect(actions.send).toHaveBeenCalledWith('How’s my week?', false);
    expect(screen.getByTestId('composer').props.value).toBe('');
    expect(screen.getByTestId('coach-remaining')).toHaveTextContent('7 left today');
  });
  it('streaming: the reply so far, what the Coach is reading, chips and send off', async () => {
    await draw({ streaming: true, messages: [...THREAD, { id: 'local:0:reply', role: 'assistant', text: 'Your call.', status: 'streaming', at: NOW, working: 'Checking readiness' }] });
    expect(screen.getByTestId('streaming').props.accessibilityLabel).toBe('Checking readiness');
    expect(screen.getByTestId('chip-Why Back off?').props.accessibilityState).toEqual({ disabled: true });
  });
  it('the limit: the banner opens the sheet; chips go; a proposed change can still be reviewed', async () => {
    const { actions } = await draw({ limit: { limit: 10, remaining: 0, reached: true } });
    expect(screen.queryByTestId('coach-chips')).toBeNull();
    expect(screen.queryByTestId('coach-remaining')).toBeNull();
    await fireEvent.press(screen.getByTestId('coach-limit'));
    expect(actions.limit).toHaveBeenCalled();
    await fireEvent.press(screen.getByTestId('button-Review 1 watch change'));
    expect(actions.review).toHaveBeenCalled();
  });
  it('offline: the banner says what still works; sending is off', async () => {
    await draw({ offline: true });
    expect(screen.getByTestId('coach-offline')).toHaveTextContent(/Offline · the Coach needs a connection\. Your plan and runs still work\./);
    await fireEvent.changeText(screen.getByTestId('composer'), 'Hello');
    expect(screen.getByTestId('coach-send').props.accessibilityState).toEqual({ disabled: true });
  });
  it('after the confirm, the card follows the watch: Not sent with Resend and Do it in Plan', async () => {
    const { actions } = await draw({ cards: { 6: { proposal: EASIER, canReview: true, batch: { state: 'failed', at: null, error: 'Not sent. Garmin didn’t answer.', opId: 'op1' } } } });
    expect(screen.queryByTestId('button-Not now')).toBeNull();
    await fireEvent.press(screen.getByTestId('button-Resend'));
    expect(actions.resend).toHaveBeenCalledWith('op1');
    expect(screen.getByText('Nothing changed on your watch.')).toBeTruthy();
  });
  it('the empty thread says what the Coach reads, and the medical line', async () => {
    await draw({ messages: [], cards: {} });
    expect(screen.getByTestId('coach-empty')).toHaveTextContent(/Daash doesn't give medical advice/);
    expect(screen.getByTestId('coach-chips')).toBeTruthy();
  });
  it('no "AI" anywhere in the thread’s own words', async () => {
    const { toJSON } = await draw();
    const words = nodes(toJSON()).flatMap((n) => (n.children ?? []).filter((c): c is string => typeof c === 'string'));
    expect(words.join(' ')).not.toMatch(/\bAI\b/);
  });
  it('signed out over a cached thread: the sign-in banner, the thread still there, no Send again, sending off', async () => {
    const unsent: CoachMessage = { id: 'local:0', role: 'user', text: 'And Sunday?', status: 'not_sent', at: NOW - 60_000, error: '' };
    await draw({ authFailure: 'signedOut', messages: [...THREAD, unsent] });
    expect(screen.getByTestId('failure-banner')).toHaveTextContent(/You’re signed out/);
    expect(screen.getByTestId('question-5')).toBeTruthy();
    expect(screen.queryByTestId('button-Send again')).toBeNull();
    await fireEvent.changeText(screen.getByTestId('composer'), 'Hello');
    expect(screen.getByTestId('coach-send').props.accessibilityState).toEqual({ disabled: true });
  });
  it('“Saved to memory” has an Edit at least MIN_TOUCH square', async () => {
    await draw({ messages: [...THREAD, { id: 'memory:m1', role: 'memory', text: 'Travelling 1 to 3 Dec', status: 'done', at: NOW - 30_000, memoryId: 'm1' }] });
    const edit = screen.getByLabelText('Edit memory: Travelling 1 to 3 Dec');
    const style = StyleSheet.flatten(edit.props.style) as { minWidth?: number; minHeight?: number };
    expect(style.minWidth).toBeGreaterThanOrEqual(MIN_TOUCH);
    expect(style.minHeight).toBeGreaterThanOrEqual(MIN_TOUCH);
  });
});

describe('the inline chart', () => {
  it('a marked bar has a shape as well as a colour: an outline and a dot', async () => {
    const { toJSON } = await render(<ThemeProvider initial="dark"><InlineChartView chart={REP_HR_CHART} testID="chart" /></ThemeProvider>);
    const last = REP_HR_CHART.values.length - 1;
    const marked = nodes(toJSON()).find((n) => n.props.testID === `bar-marked-${last}`);
    const plain = nodes(toJSON()).find((n) => n.props.testID === 'bar-0');
    // Outlined, with a dot above it; an unmarked bar has neither.
    expect(nodes(marked).some((n) => n.type === 'RNSVGRect' && n.props.strokeWidth === 2)).toBe(true);
    expect(nodes(marked).some((n) => n.type === 'RNSVGCircle')).toBe(true);
    expect(nodes(plain).some((n) => n.type === 'RNSVGCircle' || n.props.strokeWidth === 2)).toBe(false);
  });
});

describe('memory', () => {
  it('two changes before a render both stay (two memory events in one reply)', async () => {
    let ctx: MemoryContext | null = null;
    function Probe() { ctx = useMemory(); return <RNText>{String(ctx.items.length)}</RNText>; }
    await render(<MemoryProvider store={memoryItemsStore()} seed={[]}><Probe /></MemoryProvider>);
    await waitFor(() => expect(ctx!.loaded).toBe(true));
    await act(async () => { await Promise.all([ctx!.add('travel', 'Travelling 1 to 3 Dec', 'chat', 'a'), ctx!.add('note', 'Treadmill only', 'chat', 'b')]); });
    expect(ctx!.items.map((i) => i.id)).toEqual(['a', 'b']);
    await act(async () => { await Promise.all([ctx!.update('a', { text: 'Travelling 1 to 4 Dec' }), ctx!.remove('b')]); });
    expect(ctx!.items.map((i) => i.text)).toEqual(['Travelling 1 to 4 Dec']);
  });
});

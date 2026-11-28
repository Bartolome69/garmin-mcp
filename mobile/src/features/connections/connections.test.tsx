// Connections, source priority and de-dupe (milestone 8), on iOS and Android (this
// suite runs once per platform), dark and light.
import { describe, expect, it, jest } from '@jest/globals';
import { act, fireEvent, render, screen, within } from '@testing-library/react-native';
import { useState } from 'react';
import { Platform, StyleSheet } from 'react-native';

import { ThemeProvider } from '../../theme/ThemeProvider';
import { colors } from '../../theme/tokens';
import { ConnectionsView, PriorityView, SeeSheet, TroubleSheet, YouView, type ConnectionsActions } from './ConnectionsView';
import { badgeWords, mergeRows, prioritySummary, sourceName, withChanges, youSummary, type ConnectionsVM } from './model';
import { reconnectUrl } from './reconnect';
import { fixtureConnections } from './source';

const os = Platform.OS;
const VM = fixtureConnections(os);
const OTHER = fixtureConnections(os === 'ios' ? 'android' : 'ios');
const HEALTH = os === 'ios' ? 'Apple Health' : 'Health Connect';

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
  nodes(tree).filter((n) => StyleSheet.flatten(n.props?.style)?.backgroundColor === colors[scheme].brand).length;
const actions = (): ConnectionsActions => ({ reconnect: jest.fn(), priority: jest.fn(), see: jest.fn(), trouble: jest.fn(), request: jest.fn(), tell: jest.fn() });

describe('the model', () => {
  it('the device\'s merge rows are the server\'s, for both phones', () => {
    for (const vm of [VM, OTHER]) expect(mergeRows(vm, vm.priority, vm.keptApart)).toEqual(vm.merges);
  });
  it('the phone\'s health store has the phone\'s name', () => {
    const health = VM.providers.find((p) => p.route === 'health')!;
    expect(badgeWords(health, 'ios')).toBe('Via Apple Health');
    expect(badgeWords(health, 'android')).toBe('Via Health Connect');
    expect(sourceName(VM.providers.find((p) => p.route === 'intervals')!, os)).toBe('COROS via Intervals.icu');
  });
  it('a new order changes which copy is kept; keeping a pair apart undoes its merge', () => {
    const flipped = withChanges(VM, ['health', 'garmin', 'intervals'], null);
    expect(flipped.merges.find((m) => m.date === '2026-11-26')!.keptProvider).toBe('health');
    const apart = withChanges(VM, null, [['g-1126', 'h-1126']]);
    expect(apart.merges.map((m) => m.id)).toEqual(['g-1122|i-1122']);
  });
  it('the rows\' summaries', () => {
    expect(prioritySummary(VM, os)).toBe('Garmin first · 2 duplicate runs merged');
    expect(prioritySummary(withChanges(VM, null, [['g-1126', 'h-1126'], ['g-1122', 'i-1122']]), os)).toBe('Garmin first · no duplicates this month');
    expect(youSummary(VM)).toBe('Connected to Garmin');
  });
  it('Reconnect opens the server\'s sign-in for that provider', () => {
    const coros = VM.providers.find((p) => p.key === 'intervals')!;
    expect(reconnectUrl(coros)).toMatch(/\/app\/connect\/intervals$/);
    expect(reconnectUrl({ ...VM.providers[0], reconnectUrl: null })).toMatch(/\/connect$/);
  });
});

describe.each(['dark', 'light'] as const)(`Connections on ${os}, %s`, (scheme) => {
  it('one card per provider: name, badge, state in words, capability chips ticked or struck through', async () => {
    const { toJSON } = await render(<ThemeProvider initial={scheme}><ConnectionsView vm={VM} asked={[]} actions={actions()} os={os} /></ThemeProvider>);
    expect(screen.getByLabelText('Garmin, Live, Connected to Garmin · Forerunner 965 · synced 07:02')).toBeTruthy();
    expect(screen.getByLabelText('COROS, Via Intervals.icu, Reconnect needed · last synced Tue')).toBeTruthy();
    const health = VM.providers.find((p) => p.route === 'health')!;
    expect(screen.getByLabelText(`${health.name}, Via ${HEALTH}, Reads only · synced 06:58`)).toBeTruthy();
    const coros = within(screen.getByTestId('provider-intervals'));
    expect(coros.getByLabelText('Activities, yes')).toBeTruthy();
    expect(coros.getByLabelText('Sleep, no')).toBeTruthy();
    expect(coros.getByLabelText('Send workouts, no')).toBeTruthy();
    expect(StyleSheet.flatten(coros.getByText('Send workouts').props.style).textDecorationLine).toBe('line-through');
    // Reconnect needed: icon, words and the button, only where it's needed.
    expect(StyleSheet.flatten(coros.getByTestId('status-intervals').props.style).color).toBe(colors[scheme].warning);
    expect(coros.getByTestId('button-Reconnect')).toBeTruthy();
    expect(within(screen.getByTestId('provider-garmin')).queryByTestId('button-Reconnect')).toBeNull();
    expect(brandFills(toJSON(), scheme)).toBe(0);
  });
});

describe(`Connections on ${os}: rows and trust`, () => {
  it('Source priority, coming soon, request, the two links and the independence line', async () => {
    const a = actions();
    await render(<ThemeProvider initial="dark"><ConnectionsView vm={VM} asked={[]} actions={a} os={os} /></ThemeProvider>);
    expect(screen.getByLabelText('Source priority, Garmin first · 2 duplicate runs merged')).toBeTruthy();
    await fireEvent.press(screen.getByTestId('row-priority'));
    expect(a.priority).toHaveBeenCalled();
    await fireEvent.press(screen.getByTestId('button-Tell me when'));
    expect(a.tell).toHaveBeenCalledWith('tell:Polar, Suunto');
    await fireEvent.press(screen.getByTestId('button-Request it'));
    await fireEvent.press(screen.getByTestId('link-see'));
    await fireEvent.press(screen.getByTestId('link-trouble'));
    await fireEvent.press(within(screen.getByTestId('provider-intervals')).getByTestId('button-Reconnect'));
    for (const f of [a.request, a.see, a.trouble]) expect(f).toHaveBeenCalledTimes(1);
    expect(a.reconnect).toHaveBeenCalledWith(expect.objectContaining({ key: 'intervals' }));
    expect(screen.getByText('“Connected to” means you linked your own account. Daash is independent and not endorsed by any watch maker.')).toBeTruthy();
  });
  it('"Tell me when", once asked, says so', async () => {
    await render(<ThemeProvider initial="dark"><ConnectionsView vm={VM} asked={['tell:Polar, Suunto']} actions={actions()} os={os} /></ThemeProvider>);
    expect(screen.getByTestId('button-We’ll tell you')).toBeDisabled();
  });
  it('What Daash can and can\'t see, per source, from its capabilities', async () => {
    await render(<ThemeProvider initial="dark"><SeeSheet vm={VM} os={os} /></ThemeProvider>);
    const coros = within(screen.getByTestId('see-intervals'));
    expect(coros.getByText('COROS via Intervals.icu')).toBeTruthy();
    expect(coros.getByText('Can: activities.')).toBeTruthy();
    expect(coros.getByText('Can’t: sleep, send workouts.')).toBeTruthy();
  });
  it('Trouble connecting: the steps, and a reconnect for what needs one', async () => {
    const onReconnect = jest.fn();
    await render(<ThemeProvider initial="dark"><TroubleSheet vm={VM} onReconnect={onReconnect} /></ThemeProvider>);
    await fireEvent.press(screen.getByTestId('button-Reconnect COROS'));
    expect(onReconnect).toHaveBeenCalledWith(expect.objectContaining({ key: 'intervals' }));
  });
});

describe(`You on ${os}`, () => {
  it('a provider needing a reconnect comes first, with the one brand fill; under a sheet it gives it up', async () => {
    const reconnect = jest.fn();
    const { toJSON } = await render(<ThemeProvider initial="dark"><YouView vm={VM} brand actions={{ connections: jest.fn(), reconnect }} /></ThemeProvider>);
    const card = within(screen.getByTestId('trust-intervals'));
    expect(card.getByText('Reconnect needed · last synced Tue')).toBeTruthy();
    expect(card.getByText('Your plan is safe. Runs from here wait until it’s back.')).toBeTruthy();
    expect(brandFills(toJSON(), 'dark')).toBe(1);
    await fireEvent.press(card.getByTestId('button-Reconnect'));
    expect(reconnect).toHaveBeenCalled();
    expect(screen.getByLabelText('Watches and apps, Connected to Garmin')).toBeTruthy();
    const behind = await render(<ThemeProvider initial="dark"><YouView vm={VM} brand={false} actions={{ connections: jest.fn(), reconnect }} /></ThemeProvider>);
    expect(brandFills(behind.toJSON(), 'dark')).toBe(0);
  });
  it('all connected: no trust card', async () => {
    const fine: ConnectionsVM = { ...VM, providers: VM.providers.map((p) => ({ ...p, state: 'live' as const })) };
    await render(<ThemeProvider initial="dark"><YouView vm={fine} brand actions={{ connections: jest.fn(), reconnect: jest.fn() }} /></ThemeProvider>);
    expect(screen.queryByTestId('trust-intervals')).toBeNull();
  });
});

function PriorityHarness({ plus = jest.fn() }: { plus?: () => void }) {
  const [priority, setPriority] = useState<string[] | null>(null);
  const [apart, setApart] = useState<[string, string][] | null>(null);
  const vm = withChanges(VM, priority, apart);
  return (
    <PriorityView vm={vm} os={os} plus={false} actions={{
      reorder: setPriority,
      keepApart: (id) => setApart([...(apart ?? []), id.split('|') as [string, string]]),
      mergeAgain: (pair) => setApart((apart ?? []).filter((p) => p.join('|') !== pair.join('|'))),
      plus,
    }} />
  );
}

describe(`Source priority on ${os}`, () => {
  it('the order, numbered, and this month\'s merges with what was kept and how far apart', async () => {
    await render(<ThemeProvider initial="dark"><PriorityHarness /></ThemeProvider>);
    expect(screen.getByLabelText('1, Garmin, Activities, sleep, HRV')).toBeTruthy();
    expect(screen.getByLabelText(`2, ${HEALTH}, Activities, sleep`)).toBeTruthy();
    expect(screen.getByLabelText('3, COROS via Intervals.icu, Activities')).toBeTruthy();
    const thu = within(screen.getByTestId('merge-g-1126|h-1126'));
    expect(thu.getByText('Thu 26 · 9.2 km')).toBeTruthy();
    expect(thu.getByText('Kept Garmin')).toBeTruthy();
    expect(thu.getByText(`Also came from ${HEALTH}. Started 12 s apart, distances 0.3% apart. Hidden, not deleted.`)).toBeTruthy();
    expect(screen.getByTestId('merge-g-1122|i-1122')).toBeTruthy();
  });

  it('moved with a screen reader: the order changes and the merge keeps the new top source', async () => {
    await render(<ThemeProvider initial="dark"><PriorityHarness /></ThemeProvider>);
    const first = screen.getByTestId('source-0');
    expect(first.props.accessibilityActions.map((a: { name: string }) => a.name)).toEqual(['moveDown']);
    await fireEvent(first, 'accessibilityAction', { nativeEvent: { actionName: 'moveDown' } });
    expect(screen.getByLabelText(`1, ${HEALTH}, Activities, sleep`)).toBeTruthy();
    expect(within(screen.getByTestId('merge-h-1126|g-1126')).getByText(`Kept ${HEALTH}`)).toBeTruthy();
  });

  it('dragged by its handle: a row\'s height down is one place down', async () => {
    await render(<ThemeProvider initial="dark"><PriorityHarness /></ThemeProvider>);
    const grip = screen.getByTestId('grip-0');
    const touch = (y: number, t: number, prev = 0) => ({ nativeEvent: {}, touchHistory: {
      numberActiveTouches: 1, indexOfSingleActiveTouch: 0, mostRecentTimeStamp: t,
      touchBank: [{ touchActive: true, startPageX: 300, startPageY: 0, startTimeStamp: 0, currentPageX: 300, currentPageY: y,
        currentTimeStamp: t, previousPageX: 300, previousPageY: prev, previousTimeStamp: Math.max(0, t - 16) }] } });
    await act(async () => { grip.props.onResponderGrant(touch(0, 0)); });
    for (let i = 1; i <= 8; i++) await act(async () => { grip.props.onResponderMove(touch(i * 16, i * 16, (i - 1) * 16)); });
    await act(async () => { grip.props.onResponderRelease(touch(128, 140, 128)); });
    // 128 pt is two rows: Garmin goes to the bottom.
    expect(screen.getByLabelText('3, Garmin, Activities, sleep, HRV')).toBeTruthy();
    expect(within(screen.getByTestId('merge-i-1122|g-1122')).getByText('Kept COROS via Intervals.icu')).toBeTruthy();
  });

  it('"These are different runs" takes the merge apart, and it can be merged again', async () => {
    await render(<ThemeProvider initial="dark"><PriorityHarness /></ThemeProvider>);
    await fireEvent.press(within(screen.getByTestId('merge-g-1126|h-1126')).getByTestId('button-These are different runs'));
    expect(screen.queryByTestId('merge-g-1126|h-1126')).toBeNull();
    const apart = within(screen.getByTestId('apart-g-1126|h-1126'));
    expect(apart.getByText('Thu 26 · 9.2 km · both shown')).toBeTruthy();
    await fireEvent.press(apart.getByTestId('button-Merge them again'));
    expect(screen.getByTestId('merge-g-1126|h-1126')).toBeTruthy();
  });

  it('advanced merge rules are Plus: shown, locked, with today\'s rule in words', async () => {
    const plus = jest.fn();
    await render(<ThemeProvider initial="dark"><PriorityHarness plus={plus} /></ThemeProvider>);
    expect(screen.getByText('Pick the source per metric, tune match windows. Now: within 1 minute and ±2%.')).toBeTruthy();
    expect(screen.getByTestId('advanced-plus')).toHaveTextContent('Plus');
    expect(screen.getByTestId('advanced').props.accessibilityLabel).toMatch(/^Advanced merge rules, Plus\./);
    await fireEvent.press(screen.getByTestId('advanced'));
    expect(plus).toHaveBeenCalled();
  });
});

it(`snapshot: Connections on ${os}`, async () => {
  const { toJSON } = await render(<ThemeProvider initial="dark"><ConnectionsView vm={VM} asked={[]} actions={actions()} os={os} /></ThemeProvider>);
  expect(toJSON()).toMatchSnapshot(`connections-${os}`);
});

it(`snapshot: Source priority on ${os}`, async () => {
  const { toJSON } = await render(<ThemeProvider initial="dark"><PriorityHarness /></ThemeProvider>);
  expect(toJSON()).toMatchSnapshot(`priority-${os}`);
});

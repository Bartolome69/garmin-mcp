// Plus end to end (build prompt, sections 1, 5.14 and 5.15, milestone 10), over the
// real routes with a stand-in store:
//
//   16. A locked feature → paywall → free week sheet → Start (FakeStore) → unlocked
//   17. Widgets on You: locked → Plus → previews; Restore purchases; Manage
//   18. The sheet's other outcomes: cancelled, waiting for approval, failed, no store
//   19. Nothing on Today, Plan's week or sending to the watch is gated, and nothing
//       opens the paywall by itself
import { afterEach, describe, expect, it, jest } from '@jest/globals';
import { router, Stack } from 'expo-router';
import { Linking, Platform } from 'react-native';
import { act, fireEvent, renderRouter, screen, within } from 'expo-router/testing-library';
import type { ReactNode } from 'react';

import TabsLayout from './app/(tabs)/_layout';
import CoachIndex from './app/(tabs)/coach/index';
import CrewIndex from './app/(tabs)/crew/index';
import PlanLayout from './app/(tabs)/plan/_layout';
import PlanBuilder from './app/(tabs)/plan/builder';
import PlanIndex from './app/(tabs)/plan/index';
import PlanLife from './app/(tabs)/plan/life';
import PlanMove from './app/(tabs)/plan/move';
import PlanRun from './app/(tabs)/plan/run/[id]';
import PlanNewWeek from './app/(tabs)/plan/new-week';
import PlanNext from './app/(tabs)/plan/next';
import PlanSession from './app/(tabs)/plan/session/[id]';
import TodayLayout from './app/(tabs)/today/_layout';
import TodayIndex from './app/(tabs)/today/index';
import TodayNewWeek from './app/(tabs)/today/new-week';
import YouLayout from './app/(tabs)/you/_layout';
import YouIndex from './app/(tabs)/you/index';
import YouPriority from './app/(tabs)/you/priority';
import YouConnections from './app/(tabs)/you/connections';
import YouWidgets from './app/(tabs)/you/widgets';
import PlusPaywall from './app/plus/index';
import PlusTrial from './app/plus/trial';
import WatchConfirm from './app/watch/confirm';
import { setAnalyticsSink } from './analytics/analytics';
import { EntitlementsProvider } from './features/plus/EntitlementsProvider';
import { FakeStore, PRODUCT_IDS, UnavailableStore, type Store } from './features/plus/store';
import { memoryStore } from './features/watch/store';
import { FakeGarmin } from './features/watch/transport';
import { useWatch, WatchProvider, type WatchContext } from './features/watch/WatchProvider';
import { DEFAULT_FLAGS, type Flags } from './flags/flags';
import { FlagsProvider } from './flags/FlagsProvider';
import { ThemeProvider } from './theme/ThemeProvider';

const offline = async () => { throw new Error('offline'); };
let queue: WatchContext | null = null;
function Probe() { queue = useWatch(); return null; }

function app(store: Store, { flags = DEFAULT_FLAGS, garmin = new FakeGarmin() }: { flags?: Flags; garmin?: FakeGarmin } = {}) {
  const Root = ({ children }: { children?: ReactNode }) => (
    <ThemeProvider initial="dark">
      <FlagsProvider initial={flags}>
        <EntitlementsProvider store={store} fetcher={offline} verify={async () => null}>
          <WatchProvider transport={garmin} store={memoryStore()} auto={false} pollEveryMs={0}>
            <Probe />
            {children ?? <Stack screenOptions={{ headerShown: false }} />}
          </WatchProvider>
        </EntitlementsProvider>
      </FlagsProvider>
    </ThemeProvider>
  );
  return {
    _layout: () => <Root />,
    '(tabs)/_layout': TabsLayout,
    '(tabs)/today/_layout': TodayLayout, '(tabs)/today/index': TodayIndex, '(tabs)/today/new-week': TodayNewWeek,
    '(tabs)/plan/_layout': PlanLayout, '(tabs)/plan/index': PlanIndex, '(tabs)/plan/next': PlanNext,
    '(tabs)/plan/new-week': PlanNewWeek, '(tabs)/plan/session/[id]': PlanSession,
    '(tabs)/plan/builder': PlanBuilder, '(tabs)/plan/life': PlanLife, '(tabs)/plan/move': PlanMove, '(tabs)/plan/run/[id]': PlanRun,
    '(tabs)/coach/index': CoachIndex, '(tabs)/crew/index': CrewIndex,
    '(tabs)/you/_layout': YouLayout, '(tabs)/you/index': YouIndex, '(tabs)/you/widgets': YouWidgets,
    '(tabs)/you/priority': YouPriority, '(tabs)/you/connections': YouConnections,
    'plus/index': PlusPaywall, 'plus/trial': PlusTrial,
    'watch/confirm': WatchConfirm,
  };
}

const events: [string, Record<string, unknown>][] = [];
setAnalyticsSink((e, p) => { events.push([e, p]); });
afterEach(() => { events.length = 0; });

async function toTrends() {
  const tabs = await screen.findByTestId('plan-tabs');
  if (Platform.OS === 'ios') await fireEvent(tabs, 'valueChange', 'Trends');
  else await fireEvent.press(screen.getByTestId('plan-tab-Trends'));
  return screen.findByTestId('trends');
}

describe('16. A locked feature → paywall → free week → unlocked', () => {
  it('the prediction trend: See Plus, Try Plus free, Start free week, and the trend is there', async () => {
    const store = new FakeStore();
    await renderRouter(app(store), { initialUrl: '/plan' });
    await toTrends();
    expect(screen.getByTestId('trend-locked')).toBeTruthy();
    expect(screen.queryByTestId('trend')).toBeNull();

    await fireEvent.press(screen.getByTestId('see-plus'));
    const paywall = await screen.findByTestId('paywall');
    expect(within(paywall).getByText('Daash Plus')).toBeTruthy();
    expect(within(paywall).getByText('See where your training is heading.')).toBeTruthy();
    expect(within(paywall).getByLabelText('A preview of the prediction trend and race-day projection')).toBeTruthy();
    expect(events).toContainEqual(['paywall_viewed', { from: 'trend', coach: false }]);

    await fireEvent.press(within(paywall).getByTestId('button-Try Plus free for 7 days'));
    const sheet = await screen.findByTestId('trial-sheet');
    expect(within(sheet).getByText('How your free week works')).toBeTruthy();
    // The price is the store's own string.
    expect(await within(sheet).findByLabelText('Day 7: £4.99 a month starts, unless you cancel')).toBeTruthy();
    expect(within(sheet).getByLabelText('Today: Everything in Plus unlocks')).toBeTruthy();
    expect(within(sheet).getByLabelText('Day 5: We remind you the trial is ending')).toBeTruthy();
    expect(within(sheet).getByText('No payment due today. Cancel anytime.')).toBeTruthy();
    expect(within(sheet).getByText('Plans, Today and sending to your watch stay free either way.')).toBeTruthy();

    await fireEvent.press(within(sheet).getByTestId('button-Start free week'));
    expect(store.calls).toContain(`purchase:${PRODUCT_IDS.monthly}`);
    expect(events).toContainEqual(['trial_started', { product: PRODUCT_IDS.monthly, trial: true }]);
    // Back where the lock was, now open.
    expect(await screen.findByTestId('trend')).toBeTruthy();
    expect(screen.queryByTestId('trend-locked')).toBeNull();
    expect(screen.queryByTestId('paywall')).toBeNull();
  });

  it('advanced merge rules open Plus from Source priority; once on Plus the row is unlocked', async () => {
    await renderRouter(app(new FakeStore()), { initialUrl: '/you/priority' });
    const row = await screen.findByTestId('advanced');
    expect(within(row).getByText('Plus')).toBeTruthy();
    await fireEvent.press(row);
    expect(await screen.findByTestId('paywall')).toBeTruthy();
    expect(events).toContainEqual(['paywall_viewed', { from: 'merge_rules', coach: false }]);
    await fireEvent.press(screen.getByTestId('button-Try Plus free for 7 days'));
    await fireEvent.press(await screen.findByTestId('button-Start free week'));
    expect(await screen.findByTestId('priority')).toBeTruthy();
    await screen.findByLabelText(/^Advanced merge rules\. /);
    expect(screen.queryByTestId('advanced-plus')).toBeNull();
  });

  it('with the Coach on, the paywall adds the daily limit, from the flags; with it off, never', async () => {
    await renderRouter(app(new FakeStore(), { flags: { ...DEFAULT_FLAGS, coach_enabled: true, coach_daily_limit: 10 } }), { initialUrl: '/plus?from=you' });
    expect(await screen.findByLabelText('Coach without a daily limit, Free has 10 messages a day')).toBeTruthy();
  });

  it('Coach off: no Coach line anywhere on the paywall', async () => {
    await renderRouter(app(new FakeStore()), { initialUrl: '/plus?from=you' });
    const paywall = await screen.findByTestId('paywall');
    expect(within(paywall).queryByText(/Coach/)).toBeNull();
    expect(within(paywall).getByLabelText('Home and lock screen widgets, Readiness, week and countdown')).toBeTruthy();
    expect(within(paywall).getByLabelText('Sending workouts to your watch')).toBeTruthy();
  });

  it('the paywall closes back to where it opened, and Not now leaves the sheet', async () => {
    await renderRouter(app(new FakeStore()), { initialUrl: '/plan' });
    await toTrends();
    await fireEvent.press(screen.getByTestId('see-plus'));
    await fireEvent.press(await screen.findByTestId('button-Try Plus free for 7 days'));
    await fireEvent.press(await screen.findByTestId('button-Not now'));
    expect(await screen.findByTestId('paywall')).toBeTruthy();
    await fireEvent.press(screen.getByTestId('paywall-close'));
    expect(await screen.findByTestId('trend-locked')).toBeTruthy();
  });
});

describe('17. Plus in You', () => {
  it('Free: the Plus row and Widgets (locked) both open Plus; after the free week, previews and the trial state', async () => {
    await renderRouter(app(new FakeStore()), { initialUrl: '/you' });
    const section = await screen.findByTestId('you-plus');
    expect(within(section).getByTestId('plus-status')).toHaveTextContent('Prediction trend, plan styles, widgets');
    await fireEvent.press(within(section).getByTestId('row-widgets'));
    expect(await screen.findByTestId('widgets-locked')).toBeTruthy();
    await fireEvent.press(screen.getByTestId('widgets-see-plus'));
    expect(events).toContainEqual(['paywall_viewed', { from: 'widgets', coach: false }]);
    await fireEvent.press(await screen.findByTestId('button-Try Plus free for 7 days'));
    await fireEvent.press(await screen.findByTestId('button-Start free week'));
    expect(await screen.findByTestId('widgets')).toBeTruthy();
    expect(screen.queryByTestId('widgets-locked')).toBeNull();
    expect(screen.getByText('Widgets read cached data. When it’s old they show the time, never a blank.')).toBeTruthy();
    await act(async () => { router.back(); });
    expect(await screen.findByTestId('plus-badge')).toHaveTextContent('Trial');
    expect(screen.getByTestId('plus-status').props.children).toMatch(/^Free week · Plus starts /);
  });

  it('Restore purchases brings back what the store holds, and Manage opens the store', async () => {
    const open = jest.spyOn(Linking, 'openURL').mockResolvedValue(undefined as never);
    const now = Date.now();
    const bought = { productId: PRODUCT_IDS.monthly, transactionId: 't1', purchasedAt: now - 86400000, expiresAt: now + 29 * 86400000,
      trial: false, willRenew: true, platform: 'app_store' as const, receipt: 'r1' };
    // A new phone: the store finds the account's purchase only when asked to restore.
    const store = new FakeStore();
    store.restore = async () => { store.deliver(bought); return [bought]; };
    await renderRouter(app(store), { initialUrl: '/you' });
    await fireEvent.press(await screen.findByTestId('row-restore'));
    expect(await screen.findByTestId('restore-message')).toHaveTextContent('Purchases restored.');
    expect(screen.getByTestId('plus-badge')).toHaveTextContent('Active');
    await fireEvent.press(screen.getByTestId('row-manage'));
    expect(open).toHaveBeenCalledWith(store.manageUrl());
    open.mockRestore();
  });

  it('Restore with nothing to restore says so', async () => {
    await renderRouter(app(new FakeStore()), { initialUrl: '/you' });
    await fireEvent.press(await screen.findByTestId('row-restore'));
    expect(await screen.findByTestId('restore-message')).toHaveTextContent('No purchases found for this account.');
  });
});

describe('18. The free week sheet’s other outcomes', () => {
  async function toSheet(store: Store) {
    await renderRouter(app(store), { initialUrl: '/plus?from=you' });
    await fireEvent.press(await screen.findByTestId('button-Try Plus free for 7 days'));
    return screen.findByTestId('trial-sheet');
  }

  it('cancelled at the store: nothing changes, nothing said', async () => {
    const store = new FakeStore({ outcome: 'cancelled' });
    await toSheet(store);
    await fireEvent.press(await screen.findByTestId('button-Start free week'));
    expect(screen.getByTestId('trial-sheet')).toBeTruthy();
    expect(screen.queryByTestId('trial-outcome')).toBeNull();
  });

  it('Ask to Buy: waiting for approval, in words', async () => {
    await toSheet(new FakeStore({ outcome: 'pending' }));
    await fireEvent.press(await screen.findByTestId('button-Start free week'));
    expect(await screen.findByTestId('trial-outcome')).toHaveTextContent('Waiting for approval. Plus unlocks as soon as it’s approved.');
  });

  it('a failed purchase says nothing was charged; the store offline offers Try again', async () => {
    const store = new FakeStore({ outcome: 'fail' });
    await toSheet(store);
    await fireEvent.press(await screen.findByTestId('button-Start free week'));
    expect(await screen.findByTestId('trial-outcome')).toHaveTextContent('The purchase didn’t go through. You haven’t been charged.');

    store.outcome = 'offline';
    await act(async () => { router.back(); });
    await renderRouter(app(store), { initialUrl: '/plus/trial' });
    expect(await screen.findByTestId('trial-error')).toBeTruthy();
    expect(screen.getByTestId('button-Start free week')).toBeDisabled();
    store.outcome = 'purchased';
    await fireEvent.press(screen.getByTestId('button-Try again'));
    expect(await screen.findByLabelText('Day 7: £4.99 a month starts, unless you cancel')).toBeTruthy();
  });

  it('a build without a store: says so, and can’t start', async () => {
    await renderRouter(app(new UnavailableStore()), { initialUrl: '/plus/trial' });
    expect(await screen.findByText('Daash Plus isn’t on sale in this version yet.')).toBeTruthy();
    expect(screen.getByTestId('button-Start free week')).toBeDisabled();
    // Without the store's price, no price is made up.
    expect(screen.getByLabelText('Day 7: Plus starts at the store’s price, unless you cancel')).toBeTruthy();
  });

  it('after the free week was used, the paywall offers Plus without a trial, at the store’s price', async () => {
    const now = Date.now();
    const store = new FakeStore({ owned: [{ productId: PRODUCT_IDS.monthly, transactionId: 't', purchasedAt: now - 40 * 86400000, expiresAt: now - 33 * 86400000,
      trial: true, willRenew: false, platform: 'app_store', receipt: 'r' }] });
    await renderRouter(app(store), { initialUrl: '/plus?from=you' });
    await fireEvent.press(await screen.findByTestId('button-Get Plus'));
    expect(await screen.findByTestId('trial-price')).toHaveTextContent('£4.99 a month, from today.');
    expect(screen.queryByText('No payment due today. Cancel anytime.')).toBeNull();
  });
});

describe('19. Free stays whole', () => {
  it('Today, Plan’s week and the watch send carry no lock, and send for a Free runner', async () => {
    const garmin = new FakeGarmin();
    await renderRouter(app(new FakeStore(), { garmin }), { initialUrl: '/today' });
    const today = within(await screen.findByTestId('today'));
    expect(today.queryByTestId('see-plus')).toBeNull();
    expect(today.queryByText(/Plus/)).toBeNull();
    expect(today.queryByLabelText(/Plus/)).toBeNull();
    await act(async () => { router.push('/plan'); });
    await screen.findByTestId('week-title');
    const plan = within(screen.getByTestId('plan'));
    expect(plan.queryByTestId('see-plus')).toBeNull();
    expect(plan.queryByText(/Plus/)).toBeNull();
    expect(screen.queryByTestId('paywall')).toBeNull();
    // A session goes to the watch through the confirm sheet, Free or not.
    await act(async () => { router.push('/plan/session/2026-12-02-intervals'); });
    await fireEvent.press(await screen.findByTestId('button-Send to watch'));
    await fireEvent.press(within(await screen.findByTestId('confirm-sheet')).getByTestId('button-Confirm and send'));
    await act(async () => { await queue!.pump(); });
    expect(garmin.sent.map((o) => o.kind)).toEqual(['add']);
    expect(screen.queryByTestId('paywall')).toBeNull();
  });

  it('in the code: Today, the watch, the builder and onboarding never read Plus or open the paywall', () => {
    const fs = require('fs') as typeof import('fs');
    const path = require('path') as typeof import('path');
    const walk = (d: string): string[] => fs.readdirSync(d, { withFileTypes: true })
      .flatMap((e) => (e.isDirectory() ? walk(path.join(d, e.name)) : [path.join(d, e.name)]))
      .filter((f) => /\.tsx?$/.test(f) && !/\.test\.tsx?$/.test(f));
    const free = ['features/today', 'features/watch', 'features/builder', 'features/onboarding', 'features/run',
      'app/(tabs)/today', 'app/watch', 'app/onboarding', 'app/(tabs)/plan/builder.tsx', 'app/(tabs)/plan/new-week.tsx', 'app/(tabs)/plan/life.tsx']
      .map((p) => path.join(__dirname, p))
      .flatMap((p) => (fs.existsSync(p) && fs.statSync(p).isDirectory() ? walk(p) : fs.existsSync(p) ? [p] : []));
    expect(free.length).toBeGreaterThan(20);
    // today/_layout mounts the widgets' sync, which reads Plus to write the right snapshot; it gates nothing on Today.
    const gated = free.filter((f) => !f.endsWith(path.join('today', '_layout.tsx')))
      .filter((f) => /usePlus|useEntitlement|openPaywall|['"]\/plus/.test(fs.readFileSync(f, 'utf8')));
    expect(gated.map((f) => path.relative(__dirname, f))).toEqual([]);

    // The paywall opens only from a lock or You's Plus row, by the runner's tap.
    const all = walk(__dirname);
    const openers = all.filter((f) => /openPaywall\(/.test(fs.readFileSync(f, 'utf8'))).map((f) => path.relative(__dirname, f)).sort();
    expect(openers).toEqual(['app/(tabs)/plan/index.tsx', 'app/(tabs)/you/index.tsx', 'app/(tabs)/you/priority.tsx',
      'app/(tabs)/you/widgets.tsx', 'features/plus/open.ts'].sort());
    // Only the free week sheet buys.
    const buyers = all.filter((f) => /\.startTrial\(/.test(fs.readFileSync(f, 'utf8'))).map((f) => path.relative(__dirname, f));
    expect(buyers).toEqual(['app/plus/trial.tsx']);
    // No price is written into the app: prices come from the store (the fake store's sample data aside).
    const priced = all.filter((f) => !f.endsWith(path.join('plus', 'store.ts')))
      .filter((f) => /[£$€]\s?\d|\d\s?€/.test(fs.readFileSync(f, 'utf8'))).map((f) => path.relative(__dirname, f));
    expect(priced).toEqual([]);
  });
});

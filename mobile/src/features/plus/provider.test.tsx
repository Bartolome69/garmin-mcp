// The entitlements provider and the widgets' sync, with a real (Node) database:
// the cached answer opens the app, the server's answer replaces a purchase once
// checked, a trial runs out offline, and the widgets get a snapshot only when it
// draws something new.
import { describe, expect, it } from '@jest/globals';
import { act, render, waitFor } from '@testing-library/react-native';
import type { ReactNode } from 'react';

import { DbContext } from '../../db/DbContext';
import { kv } from '../../db/kv';
import { migratedNodeDb } from '../../db/testing/nodeDb';
import type { Db } from '../../db/types';
import { DEFAULT_FLAGS } from '../../flags/flags';
import { FlagsProvider } from '../../flags/FlagsProvider';
import { PERSONA_TODAY } from '../today/fixture';
import { TodayProvider } from '../today/TodayProvider';
import { memoryStore } from '../watch/store';
import { FakeGarmin } from '../watch/transport';
import { WatchProvider } from '../watch/WatchProvider';
import { FakeWidgetBridge } from '../widgets/bridge';
import { LOCKED_MESSAGE, LOCKED_USED_MESSAGE } from '../widgets/snapshot';
import { WidgetSync } from '../widgets/WidgetSync';
import { FREE, type Entitlement } from './entitlement';
import { EntitlementsProvider, useEntitlement, type EntitlementsState } from './EntitlementsProvider';
import { FakeReminder } from './reminder';
import { FakeStore, PRODUCT_IDS, type Purchase } from './store';

const DAY = 86400000;
const NOW = Date.UTC(2026, 10, 28, 9);
let state: EntitlementsState | null = null;
function Probe() { state = useEntitlement(); return null; }

function Harness({ db, children, flagsPlus = false }: { db: Db | null; children: ReactNode; flagsPlus?: boolean }) {
  return (
    <DbContext.Provider value={db}>
      <FlagsProvider initial={{ ...DEFAULT_FLAGS, plus: flagsPlus }}>{children}</FlagsProvider>
    </DbContext.Provider>
  );
}

const never = () => new Promise<Entitlement>(() => {});

describe('EntitlementsProvider', () => {
  it('opens on the cached answer while the server is slow', async () => {
    const db = await migratedNodeDb();
    await kv(db).set('entitlement', { tier: 'plus', expiresAt: null, source: 'server', since: NOW, willRenew: true, productId: null });
    await render(<Harness db={db}><EntitlementsProvider store={new FakeStore()} fetcher={never} clock={() => NOW}><Probe /></EntitlementsProvider></Harness>);
    await waitFor(() => expect(state!.plus).toBe(true));
    expect(state!.entitlement.source).toBe('server');
  });

  it('a cached trial that ran out is Free, offline included', async () => {
    const db = await migratedNodeDb();
    await kv(db).set('entitlement', { tier: 'trial', expiresAt: NOW - DAY, source: 'app_store', since: NOW - 8 * DAY, willRenew: true, productId: null });
    await render(<Harness db={db}><EntitlementsProvider store={new FakeStore()} fetcher={async () => { throw new Error('offline'); }} clock={() => NOW}><Probe /></EntitlementsProvider></Harness>);
    await waitFor(() => expect(state!.entitlement.lapsed?.tier).toBe('trial'));
    expect(state!.plus).toBe(false);
  });

  it('a purchase unlocks at once; the server’s checked answer is kept on the device', async () => {
    const db = await migratedNodeDb();
    const checked: Entitlement = { tier: 'trial', expiresAt: NOW + 7 * DAY, source: 'server', since: NOW, willRenew: true, productId: PRODUCT_IDS.monthly };
    const verified: Purchase[] = [];
    await render(<Harness db={db}>
      <EntitlementsProvider store={new FakeStore({ clock: () => NOW })} fetcher={async () => FREE} clock={() => NOW}
        verify={async (p) => { verified.push(p); return checked; }}><Probe /></EntitlementsProvider>
    </Harness>);
    await waitFor(() => expect(state).not.toBeNull());
    await act(async () => { await state!.loadProducts(); });
    expect(state!.products?.[0].price).toBe('£4.99');
    let outcome: unknown;
    await act(async () => { outcome = await state!.startTrial(); });
    expect(outcome).toEqual({ kind: 'started' });
    expect(state!.plus).toBe(true);
    expect(state!.entitlement.tier).toBe('trial');
    await waitFor(() => expect(verified).toHaveLength(1));
    await waitFor(async () => expect(await kv(db).get('entitlement')).toEqual(checked));
  });

  it('starting the free week sets the day 5 reminder; buying Plus takes it away', async () => {
    const reminder = new FakeReminder();
    const store = new FakeStore({ clock: () => NOW });
    await render(<Harness db={null}><EntitlementsProvider store={store} fetcher={never} verify={async () => null} reminder={reminder} clock={() => NOW}><Probe /></EntitlementsProvider></Harness>);
    await waitFor(() => expect(reminder.calls).toEqual(['clear']));
    await act(async () => { await state!.loadProducts(); await state!.startTrial(); });
    await waitFor(() => expect(reminder.current?.at).toBe(NOW + 5 * DAY));
    expect(reminder.current?.title).toBe('Your free week ends in 2 days');
    await act(async () => {
      store.deliver({ productId: PRODUCT_IDS.yearly, transactionId: 'y', purchasedAt: NOW, expiresAt: NOW + 365 * DAY, trial: false, willRenew: true, platform: 'app_store', receipt: 'r' });
    });
    await waitFor(() => expect(reminder.current).toBeNull());
  });

  it('a launch doesn’t clear the reminder before the cached free week is read', async () => {
    const db = await migratedNodeDb();
    await kv(db).set('entitlement', { tier: 'trial', expiresAt: NOW + 6 * DAY, source: 'server', since: NOW - DAY, willRenew: true, productId: null });
    const reminder = new FakeReminder();
    await render(<Harness db={db}><EntitlementsProvider store={new FakeStore()} fetcher={never} reminder={reminder} clock={() => NOW}><Probe /></EntitlementsProvider></Harness>);
    await waitFor(() => expect(reminder.current?.at).toBe(NOW + 4 * DAY));
    expect(reminder.calls).toEqual(['set']);
  });

  it('the features flags’ plus still unlocks (the server switching it on for someone)', async () => {
    await render(<Harness db={null} flagsPlus><EntitlementsProvider store={new FakeStore()} fetcher={never}><Probe /></EntitlementsProvider></Harness>);
    await waitFor(() => expect(state!.plus).toBe(true));
  });

  it('a purchase that arrives later (Ask to Buy approved) unlocks without asking', async () => {
    const store = new FakeStore({ clock: () => NOW });
    await render(<Harness db={null}><EntitlementsProvider store={store} fetcher={never} verify={async () => null} clock={() => NOW}><Probe /></EntitlementsProvider></Harness>);
    await waitFor(() => expect(state).not.toBeNull());
    expect(state!.plus).toBe(false);
    await act(async () => {
      store.deliver({ productId: PRODUCT_IDS.monthly, transactionId: 'later', purchasedAt: NOW, expiresAt: NOW + 30 * DAY, trial: false, willRenew: true, platform: 'app_store', receipt: 'r' });
    });
    expect(state!.entitlement.tier).toBe('plus');
  });
});

describe('WidgetSync', () => {
  function app(bridge: FakeWidgetBridge, store: FakeStore, now = () => Date.now()) {
    return (
      <Harness db={null}>
        <EntitlementsProvider store={store} fetcher={never} verify={async () => null}>
          <WatchProvider transport={new FakeGarmin()} store={memoryStore()} auto={false} pollEveryMs={0}>
            <TodayProvider fetcher={async () => PERSONA_TODAY}>
              <WidgetSync bridge={bridge} clock={now} />
              <Probe />
            </TodayProvider>
          </WatchProvider>
        </EntitlementsProvider>
      </Harness>
    );
  }

  it('Free: the widgets get the Plus message; Plus starts: today’s snapshot, written once', async () => {
    const bridge = new FakeWidgetBridge();
    const store = new FakeStore();
    const r = await render(app(bridge, store));
    await waitFor(() => expect(bridge.last?.message).toBe(LOCKED_MESSAGE));
    await act(async () => { await state!.loadProducts(); await state!.startTrial(); });
    await waitFor(() => expect(bridge.last?.readiness?.word).toBe('Back off'));
    expect(bridge.last?.session?.title).toBe('3 × 2 km');
    expect(bridge.reloads).toBe(bridge.written.length);
    const writes = bridge.written.length;
    await r.rerender(app(bridge, store));
    expect(bridge.written.length).toBe(writes);
  });

  it('Free after the free week ran out: the widgets don’t offer it again', async () => {
    const bridge = new FakeWidgetBridge();
    const now = Date.now();
    const store = new FakeStore({ owned: [{ productId: PRODUCT_IDS.monthly, transactionId: 't', purchasedAt: now - 40 * DAY, expiresAt: now - 33 * DAY,
      trial: true, willRenew: false, platform: 'app_store', receipt: 'r' }] });
    await render(app(bridge, store));
    await waitFor(() => expect(bridge.last?.message).toBe(LOCKED_USED_MESSAGE));
  });
});

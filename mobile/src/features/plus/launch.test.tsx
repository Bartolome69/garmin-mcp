// A Plus runner opening the app: Free until the cached answer is read would mean
// Plan fetches the Free plan (no trend) and the widgets get the Plus message. The
// entitlement isn't known until the cached answer has been read, and nothing that
// depends on it acts before then. And Plus changing while a plan fetch is in
// flight asks again for the right plan, without saving the old one under the new key.
import { describe, expect, it, jest } from '@jest/globals';
import { act, render, waitFor } from '@testing-library/react-native';
import type { ReactNode } from 'react';

import type { Cached } from '../../data/resource';
import { DbContext } from '../../db/DbContext';
import { kv } from '../../db/kv';
import { migratedNodeDb } from '../../db/testing/nodeDb';
import type { Db } from '../../db/types';
import { DEFAULT_FLAGS } from '../../flags/flags';
import { FlagsProvider } from '../../flags/FlagsProvider';
import type { PlanVM } from '../plan/model';
import { PlanProvider, usePlan, type PlanState } from '../plan/PlanProvider';
import { PERSONA_TODAY } from '../today/fixture';
import { TodayProvider } from '../today/TodayProvider';
import { memoryStore } from '../watch/store';
import { FakeGarmin } from '../watch/transport';
import { WatchProvider } from '../watch/WatchProvider';
import { FakeWidgetBridge } from '../widgets/bridge';
import { WidgetSync } from '../widgets/WidgetSync';
import type { Entitlement } from './entitlement';
import { EntitlementsProvider, useEntitlement, type EntitlementsState } from './EntitlementsProvider';
import { FakeStore } from './store';

// The plan the server sends depends on Plus (the trend), and it's slow.
const mockCalls: boolean[] = [];
let mockRelease: Array<() => void> = [];
jest.mock('../plan/source', () => ({
  planSource: (_coach: boolean, plus: boolean) => () => {
    mockCalls.push(plus);
    return new Promise<PlanVM>((resolve) => {
      // eslint-disable-next-line @typescript-eslint/no-require-imports
      const f = require('../plan/fixture');
      mockRelease.push(() => resolve(plus ? { ...f.PERSONA_PLAN, trends: f.PERSONA_PLAN_PLUS.trends } : f.PERSONA_PLAN));
    });
  },
}));

const PLUS: Entitlement = { tier: 'plus', expiresAt: null, source: 'server', since: 0, willRenew: true, productId: null };
const never = () => new Promise<Entitlement>(() => {});

let plan: PlanState | null = null;
let ent: EntitlementsState | null = null;
function Probe() { plan = usePlan(); ent = useEntitlement(); return null; }

function Harness({ db, children }: { db: Db | null; children: ReactNode }) {
  return (
    <DbContext.Provider value={db}>
      <FlagsProvider initial={DEFAULT_FLAGS}>{children}</FlagsProvider>
    </DbContext.Provider>
  );
}

const releaseAll = async () => { await act(async () => { const r = mockRelease; mockRelease = []; r.forEach((f) => f()); }); };

describe('launching as a Plus runner', () => {
  it('Plan asks for the Plus plan only, and keeps it under the Plus key', async () => {
    mockCalls.length = 0; mockRelease = [];
    const db = await migratedNodeDb();
    await kv(db).set('entitlement', PLUS);
    await render(
      <Harness db={db}>
        <EntitlementsProvider store={new FakeStore()} fetcher={never}>
          <WatchProvider transport={new FakeGarmin()} store={memoryStore()} auto={false} pollEveryMs={0}>
            <PlanProvider><Probe /></PlanProvider>
          </WatchProvider>
        </EntitlementsProvider>
      </Harness>,
    );
    await waitFor(() => expect(mockRelease.length).toBeGreaterThan(0));
    await releaseAll();
    await waitFor(() => expect(plan!.data?.trends.trend).not.toBeNull());
    expect(ent!.plus).toBe(true);
    expect(mockCalls).toEqual([true]);
    const saved = await kv(db).get<Cached<PlanVM>>('plan:plain:plus');
    expect(saved?.data.trends.trend).not.toBeNull();
    expect(await kv(db).get('plan:plain:free')).toBeNull();
  });

  it('Plus arriving while the Free plan is in flight: asks again, and each plan is kept under its own key', async () => {
    mockCalls.length = 0; mockRelease = [];
    const db = await migratedNodeDb();
    let answer: (e: Entitlement) => void = () => {};
    const slow = () => new Promise<Entitlement>((resolve) => { answer = resolve; });
    await render(
      <Harness db={db}>
        <EntitlementsProvider store={new FakeStore()} fetcher={slow}>
          <WatchProvider transport={new FakeGarmin()} store={memoryStore()} auto={false} pollEveryMs={0}>
            <PlanProvider><Probe /></PlanProvider>
          </WatchProvider>
        </EntitlementsProvider>
      </Harness>,
    );
    await waitFor(() => expect(mockCalls).toEqual([false]));
    // The server says Plus (bought on the web) while the Free plan is still on its way.
    await act(async () => { answer(PLUS); });
    await waitFor(() => expect(ent!.plus).toBe(true));
    await releaseAll();
    await waitFor(() => expect(mockCalls).toEqual([false, true]));
    await releaseAll();
    await waitFor(() => expect(plan!.data?.trends.trend).not.toBeNull());
    expect((await kv(db).get<Cached<PlanVM>>('plan:plain:plus'))?.data.trends.trend).not.toBeNull();
    expect((await kv(db).get<Cached<PlanVM>>('plan:plain:free'))?.data.trends.trend).toBeNull();
  });

  it('the widgets never get the Plus message for a Plus runner', async () => {
    const db = await migratedNodeDb();
    await kv(db).set('entitlement', PLUS);
    const bridge = new FakeWidgetBridge();
    await render(
      <Harness db={db}>
        <EntitlementsProvider store={new FakeStore()} fetcher={never}>
          <WatchProvider transport={new FakeGarmin()} store={memoryStore()} auto={false} pollEveryMs={0}>
            <TodayProvider fetcher={async () => PERSONA_TODAY}>
              <WidgetSync bridge={bridge} />
            </TodayProvider>
          </WatchProvider>
        </EntitlementsProvider>
      </Harness>,
    );
    await waitFor(() => expect(bridge.last?.readiness?.word).toBe('Back off'));
    expect(bridge.written.every((s) => !s.locked)).toBe(true);
  });
});

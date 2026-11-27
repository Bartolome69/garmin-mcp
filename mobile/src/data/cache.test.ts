// The offline cache end to end: a screen's answer written to the device's SQLite,
// read back first next time, aged into "Updated …" labels, and gone after sign-out.
import { describe, expect, it, jest } from '@jest/globals';
import { act, renderHook, waitFor } from '@testing-library/react-native';

jest.mock('expo-secure-store', () => ({ getItemAsync: async () => null, setItemAsync: async () => {}, deleteItemAsync: async () => {} }));

import { ApiError, NotSignedIn, Offline, ReconnectNeeded, TimedOut } from '../api/client';
import { signOut } from '../api/session';
import { kv } from '../db/kv';
import { migratedNodeDb } from '../db/testing/nodeDb';
import type { Db } from '../db/types';
import { freshness, STALE_AFTER_MS } from './freshness';
import { failureKind, useResource, type Cached, type ResourceStore } from './resource';

const kvStore = <T,>(db: Db, key: string): ResourceStore<T> => ({ load: () => kv(db).get<Cached<T>>(key), save: (v) => kv(db).set(key, v) });

describe('the offline cache on the device\'s SQLite', () => {
  it('writes the answer to the device', async () => {
    const db = await migratedNodeDb();
    const store = kvStore<{ km: number }>(db, 'today:plain');
    const { result } = await renderHook(() => useResource(async () => ({ km: 54 }), store, () => 1000));
    await waitFor(() => expect(result.current?.data).toEqual({ km: 54 }));
    await waitFor(() => expect(result.current?.refreshing).toBe(false));
    // The save finishes before refreshing turns false.
    expect(await kv(db).get('today:plain')).toEqual({ data: { km: 54 }, fetchedAt: 1000 });
  });

  it('next launch, offline: the cached copy draws with its age, and the failure says why', async () => {
    const db = await migratedNodeDb();
    const store = kvStore<{ km: number }>(db, 'today:plain');
    await store.save({ data: { km: 54 }, fetchedAt: 1000 });
    const { result } = await renderHook(() => useResource(async () => { throw new Offline(); }, store));
    await waitFor(() => expect(result.current?.failure).toBe('offline'));
    expect(result.current.data).toEqual({ km: 54 });
    expect(result.current.fetchedAt).toBe(1000);
  });

  it('a corrupt cached value reads as nothing, not a crash', async () => {
    const db = await migratedNodeDb();
    await db.run('INSERT INTO kv (key, value, updated_at) VALUES (?, ?, ?)', ['today:plain', '{not json', 1]);
    expect(await kv(db).get('today:plain')).toBeNull();
  });

  it('after sign-out the next runner sees nothing cached', async () => {
    const db = await migratedNodeDb();
    const store = kvStore<{ km: number }>(db, 'plan:plain:free');
    await store.save({ data: { km: 54 }, fetchedAt: 1 });
    await signOut(db);
    const { result } = await renderHook(() => useResource(async () => { throw new NotSignedIn(); }, store));
    await waitFor(() => expect(result.current?.failure).toBe('signedOut'));
    expect(result.current.data).toBeNull();
  });
});

describe('staleness', () => {
  const T = new Date(2026, 10, 27, 7, 2).getTime();
  it('fresh up to 30 minutes, then "Updated … ago"', () => {
    expect(freshness(T, T + STALE_AFTER_MS - 1)!.stale).toBe(false);
    expect(freshness(T, T + STALE_AFTER_MS)!.stale).toBe(true);
    expect(freshness(T, T + STALE_AFTER_MS)!.label).toBe('Updated 30 min ago');
  });
  it('a clock behind the copy never shows a negative age', () => {
    expect(freshness(T, T - 60000)).toEqual({ stale: false, label: '07:02' });
  });
});

describe('why a refresh failed', () => {
  it.each([
    [new Offline(), 'offline'], [new TimedOut(), 'offline'], [new ReconnectNeeded(), 'reconnect'],
    [new NotSignedIn(), 'signedOut'], [new ApiError('x', 500, true), 'server'], [new Error('x'), 'server'],
  ])('%s → %s', (err, kind) => {
    expect(failureKind(err)).toBe(kind);
  });
  it('a successful refresh clears the failure', async () => {
    let fail = true;
    const { result } = await renderHook(() => useResource(async () => { if (fail) throw new Offline(); return 1; }));
    await waitFor(() => expect(result.current?.failure).toBe('offline'));
    fail = false;
    await act(async () => { await result.current.refresh(); });
    expect(result.current?.failure).toBeNull();
    expect(result.current.data).toBe(1);
  });
});

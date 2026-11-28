// A screen's data, offline first (build prompt, sections 3 and 7): the last copy
// from the device draws at once, then the server's answer replaces it. A failed
// refresh keeps what's there and says so; nothing ever spins over content.
import { useCallback, useEffect, useRef, useState } from 'react';

import { NotSignedIn, Offline, ReconnectNeeded, TimedOut } from '../api/client';

/** Why the last refresh failed, so a screen can say the right thing. */
export type FailureKind = 'offline' | 'reconnect' | 'signedOut' | 'server';

export function failureKind(err: unknown): FailureKind {
  if (err instanceof ReconnectNeeded) return 'reconnect';
  if (err instanceof NotSignedIn) return 'signedOut';
  if (err instanceof Offline || err instanceof TimedOut) return 'offline';
  return 'server';
}

export type Cached<T> = { data: T; fetchedAt: number };
export type ResourceStore<T> = { load(): Promise<Cached<T> | null>; save(value: Cached<T>): Promise<void> };

export type Resource<T> = {
  data: T | null;
  fetchedAt: number | null;
  refreshing: boolean;
  error: string | null;        // the last refresh failed; data (if any) is the cached copy
  failure: FailureKind | null; // why it failed
  refresh: () => Promise<void>;
};

export type ResourceOptions = {
  /** False until what the fetch depends on is known (Plus, say): nothing is read or fetched before then. */
  enabled?: boolean;
  /**
   * Which variant of the data this is (Plan's "plan:plain:plus"). When it changes,
   * that variant's cached copy replaces what's drawn, a fetch for the old one that
   * is still on its way is kept under the old variant only, and the new one is
   * asked for as soon as that fetch ends.
   */
  key?: string;
};

export function useResource<T>(fetcher: () => Promise<T>, store?: ResourceStore<T>, now: () => number = Date.now, opts: ResourceOptions = {}): Resource<T> {
  const { enabled = true, key } = opts;
  const [state, setState] = useState<{ data: T | null; fetchedAt: number | null; error: string | null; failure: FailureKind | null }>(
    { data: null, fetchedAt: null, error: null, failure: null });
  const [refreshing, setRefreshing] = useState(false);
  const busy = useRef(false);
  // A refresh asked for while one for another variant was on its way: runs when that one ends.
  const pending = useRef(false);
  // The variant the fetch on its way is for, and the variant of what's drawn.
  const inFlight = useRef<string | undefined>(undefined);
  const drawn = useRef<string | undefined>(undefined);
  // The latest fetcher, store and clock, read when a refresh runs: a new function
  // each render must not start a new refresh each render.
  const latest = useRef({ fetcher, store, now, key, enabled });
  latest.current = { fetcher, store, now, key, enabled };

  const refresh = useCallback(async (): Promise<void> => {
    if (!latest.current.enabled) return;
    if (busy.current) {
      if (latest.current.key !== inFlight.current) pending.current = true;
      return;
    }
    busy.current = true;
    const target = { key: latest.current.key, store: latest.current.store };
    inFlight.current = target.key;
    setRefreshing(true);
    try {
      const data = await latest.current.fetcher();
      const value = { data, fetchedAt: latest.current.now() };
      if (latest.current.key !== target.key) {
        // The variant changed on the way: keep this one where it belongs, and ask for the new one.
        pending.current = true;
        await target.store?.save(value).catch(() => {});
      } else {
        drawn.current = target.key;
        setState({ ...value, error: null, failure: null });
        await latest.current.store?.save(value).catch(() => {});
      }
    } catch (err) {
      if (latest.current.key === target.key) {
        setState((s) => ({ ...s, error: err instanceof Error ? err.message : 'Couldn\u2019t reach Daash', failure: failureKind(err) }));
      } else {
        pending.current = true;
      }
    } finally {
      busy.current = false;
      setRefreshing(false);
    }
    if (pending.current) {
      pending.current = false;
      await refresh();
    }
  }, []);

  // On mount, and when the store or variant changes (the database opening, Plus
  // starting): the cached copy first, then the server.
  useEffect(() => {
    if (!enabled) return;
    let live = true;
    (async () => {
      const cached = await store?.load().catch(() => null);
      // What's drawn stays unless it's another variant's, or there's nothing yet.
      if (live && cached) {
        const other = drawn.current !== key;
        drawn.current = key;
        setState((s) => (s.data && !other ? s : { data: cached.data, fetchedAt: cached.fetchedAt, error: null, failure: null }));
      }
      if (live) await refresh();
    })();
    return () => { live = false; };
  }, [store, refresh, enabled, key]);

  return { ...state, refreshing, refresh };
}

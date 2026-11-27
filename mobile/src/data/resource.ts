// A screen's data, offline first (build prompt, sections 3 and 7): the last copy
// from the device draws at once, then the server's answer replaces it. A failed
// refresh keeps what's there and says so; nothing ever spins over content.
import { useCallback, useEffect, useRef, useState } from 'react';

export type Cached<T> = { data: T; fetchedAt: number };
export type ResourceStore<T> = { load(): Promise<Cached<T> | null>; save(value: Cached<T>): Promise<void> };

export type Resource<T> = {
  data: T | null;
  fetchedAt: number | null;
  refreshing: boolean;
  error: string | null;        // the last refresh failed; data (if any) is the cached copy
  refresh: () => Promise<void>;
};

export function useResource<T>(fetcher: () => Promise<T>, store?: ResourceStore<T>, now: () => number = Date.now): Resource<T> {
  const [state, setState] = useState<{ data: T | null; fetchedAt: number | null; error: string | null }>(
    { data: null, fetchedAt: null, error: null });
  const [refreshing, setRefreshing] = useState(false);
  const busy = useRef(false);
  // The latest fetcher, store and clock, read when a refresh runs: a new function
  // each render must not start a new refresh each render.
  const latest = useRef({ fetcher, store, now });
  latest.current = { fetcher, store, now };

  const refresh = useCallback(async () => {
    if (busy.current) return;
    busy.current = true;
    setRefreshing(true);
    try {
      const data = await latest.current.fetcher();
      const value = { data, fetchedAt: latest.current.now() };
      setState({ ...value, error: null });
      await latest.current.store?.save(value).catch(() => {});
    } catch (err) {
      setState((s) => ({ ...s, error: err instanceof Error ? err.message : 'Couldn\u2019t reach Daash' }));
    } finally {
      busy.current = false;
      setRefreshing(false);
    }
  }, []);

  // On mount, and when the store changes (the database opening): the cached copy first, then the server.
  useEffect(() => {
    let live = true;
    (async () => {
      const cached = await store?.load().catch(() => null);
      if (live && cached) setState((s) => (s.data ? s : { data: cached.data, fetchedAt: cached.fetchedAt, error: null }));
      if (live) await refresh();
    })();
    return () => { live = false; };
  }, [store, refresh]);

  return { ...state, refreshing, refresh };
}

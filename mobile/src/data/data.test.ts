import { describe, expect, it } from '@jest/globals';
import { act, renderHook, waitFor } from '@testing-library/react-native';

import { freshness, STALE_AFTER_MS } from './freshness';
import { useResource, type Cached } from './resource';

const T = new Date(2026, 10, 27, 7, 2).getTime();

describe('freshness', () => {
  it('fresh data shows the time it was read', () => {
    expect(freshness(T, T + 5 * 60000)).toEqual({ stale: false, label: '07:02' });
  });
  it('stale data says how old it is', () => {
    expect(freshness(T, T + STALE_AFTER_MS + 1)).toEqual({ stale: true, label: 'Updated 30 min ago' });
    expect(freshness(T, T + 2 * 3600000 + 60000)).toEqual({ stale: true, label: 'Updated 2h ago' });
    expect(freshness(T, T + 30 * 3600000)).toEqual({ stale: true, label: 'Updated yesterday' });
    expect(freshness(T, T + 5 * 86400000)).toEqual({ stale: true, label: 'Updated 27 Nov' });
  });
  it('nothing read yet: no label', () => {
    expect(freshness(null, T)).toBeNull();
  });
});

describe('offline first', () => {
  function memoryStore<T>(initial: Cached<T> | null) {
    let value = initial;
    return { saved: () => value, store: { load: async () => value, save: async (v: Cached<T>) => { value = v; } } };
  }

  it('draws the cached copy at once, then the fresh answer, and keeps it', async () => {
    const m = memoryStore({ data: 'cached', fetchedAt: 1 });
    let release: (v: string) => void = () => {};
    const fetcher = () => new Promise<string>((ok) => { release = ok; });
    const { result } = await renderHook(() => useResource(fetcher, m.store, () => 99));
    await waitFor(() => expect(result.current.data).toBe('cached'));
    expect(result.current.fetchedAt).toBe(1);
    await act(async () => release('fresh'));
    await waitFor(() => expect(result.current.data).toBe('fresh'));
    expect(m.saved()).toEqual({ data: 'fresh', fetchedAt: 99 });
  });

  it('a failed refresh keeps the cached copy and says it failed, never blanks the screen', async () => {
    const m = memoryStore({ data: 'cached', fetchedAt: 1 });
    const { result } = await renderHook(() => useResource(async () => { throw new Error('offline'); }, m.store));
    await waitFor(() => expect(result.current.error).toBe('offline'));
    expect(result.current.data).toBe('cached');
  });

  it('with nothing cached and no network, it says so instead of spinning', async () => {
    const { result } = await renderHook(() => useResource(async () => { throw new Error('offline'); }));
    await waitFor(() => expect(result.current.error).toBe('offline'));
    expect(result.current.data).toBeNull();
    expect(result.current.refreshing).toBe(false);
  });
});

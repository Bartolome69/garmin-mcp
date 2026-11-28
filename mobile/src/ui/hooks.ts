// Small hooks that keep screens from redrawing when nothing they show changed
// (milestone 12, performance): the same actions object across renders, the same value
// while it's equal, and a render counter the tests read (a no-op outside Jest).
import { useMemo, useRef } from 'react';

/** The same actions object across renders, each calling the latest handler: memoised children don't redraw for a new closure. */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function useStableActions<T extends { [K in keyof T]: ((...args: any[]) => unknown) | undefined }>(actions: T): T {
  const latest = useRef(actions);
  latest.current = actions;
  const keys = Object.keys(actions).filter((k) => typeof (actions as Record<string, unknown>)[k] === 'function').sort().join('|');
  return useMemo(() => Object.fromEntries(keys.split('|').filter(Boolean).map((k) => [k,
    (...args: unknown[]) => (latest.current as Record<string, ((...a: unknown[]) => unknown) | undefined>)[k]?.(...args)])) as T,
  [keys]);
}

/** The previous value while the new one is equal to it (by JSON), so a memoised view keeps its props. */
export function useStable<T>(value: T): T {
  const last = useRef<{ value: T; json: string } | null>(null);
  const json = JSON.stringify(value);
  if (!last.current || last.current.json !== json) last.current = { value, json };
  return last.current.value;
}

const IN_JEST = typeof process !== 'undefined' && process.env.JEST_WORKER_ID !== undefined;
/** How many times each counted component has rendered (tests only). */
export const renderCounts = new Map<string, number>();
/** Counts a render under `name` in tests; does nothing in the app. */
export function useRenderCount(name: string): void {
  if (IN_JEST) renderCounts.set(name, (renderCounts.get(name) ?? 0) + 1);
}

// A recorded run, offline first: the copy on the device draws at once, then the
// server's answer replaces it (data/resource.ts). A run doesn't change once it's
// recorded, so a cached one is never shown as stale.
import { useMemo } from 'react';

import { kv } from '../../db/kv';
import { useDb } from '../../db/DbContext';
import { useResource, type Cached, type Resource, type ResourceStore } from '../../data/resource';
import type { RunVM } from './model';
import { runSource } from './source';

export function useRun(id: string, coach: boolean, fetcher?: () => Promise<RunVM>): Resource<RunVM> {
  const db = useDb();
  const key = `run:${id}:${coach ? 'coach' : 'plain'}`;
  const source = useMemo(() => fetcher ?? runSource(id, coach), [fetcher, id, coach]);
  const store = useMemo<ResourceStore<RunVM> | undefined>(() => db ? {
    load: () => kv(db).get<Cached<RunVM>>(key),
    save: (value) => kv(db).set(key, value),
  } : undefined, [db, key]);
  return useResource(source, store);
}

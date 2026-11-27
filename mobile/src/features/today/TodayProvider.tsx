import { createContext, useContext, useMemo, type ReactNode } from 'react';

import { kv } from '../../db/kv';
import { useDb } from '../../db/DbContext';
import { useResource, type Cached, type Resource, type ResourceStore } from '../../data/resource';
import { useFlags } from '../../flags/FlagsProvider';
import type { TodayVM } from './model';
import { todaySource } from './source';

const TodayContext = createContext<Resource<TodayVM> | null>(null);

/** Today's data for the screen and its sheets: one fetch, cached on the device per Coach setting. */
export function TodayProvider({ children, fetcher }: { children: ReactNode; fetcher?: () => Promise<TodayVM> }) {
  const db = useDb();
  const { flags } = useFlags();
  const coach = flags.coach_enabled;
  const source = useMemo(() => fetcher ?? todaySource(coach), [fetcher, coach]);
  const store = useMemo<ResourceStore<TodayVM> | undefined>(() => db ? {
    load: () => kv(db).get<Cached<TodayVM>>(`today:${coach ? 'coach' : 'plain'}`),
    save: (value) => kv(db).set(`today:${coach ? 'coach' : 'plain'}`, value),
  } : undefined, [db, coach]);
  const resource = useResource(source, store);
  return <TodayContext.Provider value={resource}>{children}</TodayContext.Provider>;
}

export function useToday(): Resource<TodayVM> {
  const r = useContext(TodayContext);
  if (!r) throw new Error('useToday outside TodayProvider');
  return r;
}

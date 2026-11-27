// Connections for the You tab's screens: the server's view, cached on the device,
// with the runner's own source order, kept-apart pairs and "Tell me when" requests
// applied at once and kept on the device until the server has them.
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';

import { useDb } from '../../db/DbContext';
import { kv } from '../../db/kv';
import { useResource, type Cached, type ResourceStore } from '../../data/resource';
import { withChanges, type ConnectionsVM } from './model';
import { fetchConnections, sendChanges } from './source';

export type Changes = { priority: string[] | null; keptApart: [string, string][] | null; asked: string[] };
const NONE: Changes = { priority: null, keptApart: null, asked: [] };
const CHANGES_KEY = 'connections:changes';

type State = {
  vm: ConnectionsVM | null;
  error: string | null;
  refresh: () => Promise<void>;
  setPriority: (order: string[]) => void;
  /** "These are different runs": the merge comes apart. */
  keepApart: (mergeId: string) => void;
  /** Undo that: let the rule merge them again. */
  mergeAgain: (pair: [string, string]) => void;
  asked: string[];
  ask: (what: string) => void;
};

const Ctx = createContext<State | null>(null);

export function ConnectionsProvider({ children, fetcher }: { children: ReactNode; fetcher?: () => Promise<ConnectionsVM> }) {
  const db = useDb();
  const store = useMemo<ResourceStore<ConnectionsVM> | undefined>(() => db ? {
    load: () => kv(db).get<Cached<ConnectionsVM>>('connections'),
    save: (value) => kv(db).set('connections', value),
  } : undefined, [db]);
  const resource = useResource(fetcher ?? fetchConnections, store);
  const [changes, setChanges] = useState<Changes>(NONE);

  useEffect(() => { if (db) kv(db).get<Changes>(CHANGES_KEY).then((c) => { if (c) setChanges((now) => (now === NONE ? c : now)); }).catch(() => {}); }, [db]);

  const update = useCallback((next: (c: Changes) => Changes, send?: (c: Changes) => Parameters<typeof sendChanges>[0]) => {
    setChanges((c) => {
      const n = next(c);
      if (db) kv(db).set(CHANGES_KEY, n).catch(() => {});
      if (send) sendChanges(send(n));
      return n;
    });
  }, [db]);

  const vm = useMemo(() => (resource.data ? withChanges(resource.data, changes.priority, changes.keptApart) : null), [resource.data, changes]);

  const value = useMemo<State>(() => ({
    vm,
    error: resource.error,
    refresh: resource.refresh,
    setPriority: (order) => update((c) => ({ ...c, priority: order }), (n) => ({ priority: n.priority! })),
    keepApart: (id) => {
      const [a, b] = id.split('|') as [string, string];
      update((c) => ({ ...c, keptApart: [...(c.keptApart ?? vm?.keptApart ?? []), [a, b]] }), (n) => ({ keptApart: n.keptApart! }));
    },
    mergeAgain: ([a, b]) => update((c) => ({ ...c, keptApart: (c.keptApart ?? vm?.keptApart ?? []).filter(([x, y]) => !(x === a && y === b)) }),
      (n) => ({ keptApart: n.keptApart! })),
    asked: changes.asked,
    ask: (what) => update((c) => ({ ...c, asked: [...new Set([...c.asked, what])] })),
  }), [vm, resource.error, resource.refresh, update, changes.asked]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useConnections(): State {
  const c = useContext(Ctx);
  if (!c) throw new Error('useConnections outside ConnectionsProvider');
  return c;
}

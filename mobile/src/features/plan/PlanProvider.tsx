// Plan's data for the screen, the session detail and the Move sheet: one fetch,
// cached on the device (offline first), and the one move waiting for a confirm.
import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from 'react';

import { kv } from '../../db/kv';
import { useDb } from '../../db/DbContext';
import { useResource, type Cached, type Resource, type ResourceStore } from '../../data/resource';
import { useFlags } from '../../flags/FlagsProvider';
import type { PlanVM } from './model';
import { drop, withProposal, type DropResult, type Proposal } from './moves';
import { planSource } from './source';

/** A drop that needs a decision: the back-to-back warning, or a refusal to say. */
export type Pending = Extract<DropResult, { kind: 'warn' } | { kind: 'refused' }>;

export type PlanState = Resource<PlanVM> & {
  /** The plan as drawn: the server's, with the proposed move applied. */
  shown: PlanVM | null;
  proposal: Proposal | null;
  pending: Pending | null;
  /** Drop session `id` on `to`: proposes it, or asks first when it clashes. */
  move: (id: string, to: string) => DropResult;
  /** "Move anyway" on the warning. */
  confirmPending: () => void;
  /** "Keep it on Fri", or dismissing a refusal. */
  clearPending: () => void;
  /** Undo the proposed move. */
  clearProposal: () => void;
};

const PlanContext = createContext<PlanState | null>(null);

export function PlanProvider({ children, fetcher }: { children: ReactNode; fetcher?: () => Promise<PlanVM> }) {
  const db = useDb();
  const { flags } = useFlags();
  const { coach_enabled: coach, plus } = flags;
  const key = `plan:${coach ? 'coach' : 'plain'}:${plus ? 'plus' : 'free'}`;
  const source = useMemo(() => fetcher ?? planSource(coach, plus), [fetcher, coach, plus]);
  const store = useMemo<ResourceStore<PlanVM> | undefined>(() => db ? {
    load: () => kv(db).get<Cached<PlanVM>>(key),
    save: (value) => kv(db).set(key, value),
  } : undefined, [db, key]);
  const resource = useResource(source, store);
  const [proposal, setProposal] = useState<Proposal | null>(null);
  const [pending, setPending] = useState<Pending | null>(null);

  const move = useCallback((id: string, to: string): DropResult => {
    if (!resource.data) return { kind: 'none' };
    // One move at a time: a new drop replaces the one waiting.
    const result = drop(withProposal(resource.data, null), id, to);
    if (result.kind === 'propose') { setPending(null); setProposal(result.proposal); }
    else if (result.kind === 'warn' || result.kind === 'refused') setPending(result);
    return result;
  }, [resource.data]);

  const value = useMemo<PlanState>(() => ({
    ...resource,
    shown: resource.data ? withProposal(resource.data, proposal) : null,
    proposal,
    pending,
    move,
    confirmPending: () => { if (pending?.kind === 'warn') setProposal(pending.proposal); setPending(null); },
    clearPending: () => setPending(null),
    clearProposal: () => setProposal(null),
  }), [resource, proposal, pending, move]);

  return <PlanContext.Provider value={value}>{children}</PlanContext.Provider>;
}

export function usePlan(): PlanState {
  const p = useContext(PlanContext);
  if (!p) throw new Error('usePlan outside PlanProvider');
  return p;
}

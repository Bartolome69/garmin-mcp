// Plan's data for the screen, the session detail and the Move sheet: one fetch,
// cached on the device (offline first), and the one move waiting for a confirm.
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';

import { kv } from '../../db/kv';
import { useDb } from '../../db/DbContext';
import { onPlanChanged } from '../../data/planChanged';
import { useResource, type Cached, type Resource, type ResourceStore } from '../../data/resource';
import { useFlags } from '../../flags/FlagsProvider';
import { useEntitlement } from '../plus/EntitlementsProvider';
import { applyToPlan } from '../watch/apply';
import { useWatch } from '../watch/WatchProvider';
import type { PlanVM } from './model';
import { drop, withProposal, type DropResult, type Proposal } from './moves';
import { planSource } from './source';

/** A drop that needs a decision: the back-to-back warning, or a refusal to say. */
export type Pending = Extract<DropResult, { kind: 'warn' } | { kind: 'refused' }>;

export type PlanState = Resource<PlanVM> & {
  /** The plan as drawn: the server's, with confirmed changes and the proposed move applied, watch states from the queue. */
  shown: PlanVM | null;
  proposal: Proposal | null;
  /** The proposed move came through the back-to-back warning ("Move anyway"). */
  warned: boolean;
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
  const coach = flags.coach_enabled;
  // Plus or a running free week: the server adds the prediction trend.
  const { plus, ready } = useEntitlement();
  const key = `plan:${coach ? 'coach' : 'plain'}:${plus ? 'plus' : 'free'}`;
  const source = useMemo(() => fetcher ?? planSource(coach, plus), [fetcher, coach, plus]);
  const store = useMemo<ResourceStore<PlanVM> | undefined>(() => db ? {
    load: () => kv(db).get<Cached<PlanVM>>(key),
    save: (value) => kv(db).set(key, value),
  } : undefined, [db, key]);
  // Nothing is read or fetched until Plus is known, so a Plus runner never gets the
  // Free plan first. Plus starting (or ending) changes the key: that plan's copy
  // draws and the server is asked again at once (data/resource.ts).
  const resource = useResource(source, store, undefined, { enabled: ready, key, foreground: true });
  const { refresh } = resource;
  // A plan made elsewhere (onboarding from "Create a plan"): fetch it rather than keep the empty one.
  useEffect(() => onPlanChanged(() => { refresh(); }), [refresh]);
  const watch = useWatch();
  const [proposal, setProposal] = useState<Proposal | null>(null);
  const [warned, setWarned] = useState(false);
  const [pending, setPending] = useState<Pending | null>(null);
  // The plan with confirmed changes: what a new drop is checked against.
  const base = useMemo(() => (resource.data ? applyToPlan(resource.data, watch.applied, watch.ops) : null),
    [resource.data, watch.applied, watch.ops]);

  // Once the move is confirmed, the queue draws it; the local proposal goes.
  useEffect(() => {
    if (proposal && watch.applied.some((p) => p.id === `move:${proposal.id}:${proposal.to}`)) { setProposal(null); setWarned(false); }
  }, [proposal, watch.applied]);

  const move = useCallback((id: string, to: string): DropResult => {
    if (!base) return { kind: 'none' };
    // One move at a time: a new drop replaces the one waiting.
    const result = drop(base, id, to);
    if (result.kind === 'propose') { setPending(null); setProposal(result.proposal); setWarned(false); }
    else if (result.kind === 'warn' || result.kind === 'refused') setPending(result);
    return result;
  }, [base]);

  const value = useMemo<PlanState>(() => ({
    ...resource,
    shown: base ? withProposal(base, proposal) : null,
    proposal,
    warned,
    pending,
    move,
    confirmPending: () => { if (pending?.kind === 'warn') { setProposal(pending.proposal); setWarned(true); } setPending(null); },
    clearPending: () => setPending(null),
    clearProposal: () => { setProposal(null); setWarned(false); },
  }), [resource, base, proposal, warned, pending, move]);

  return <PlanContext.Provider value={value}>{children}</PlanContext.Provider>;
}

export function usePlan(): PlanState {
  const p = useContext(PlanContext);
  if (!p) throw new Error('usePlan outside PlanProvider');
  return p;
}

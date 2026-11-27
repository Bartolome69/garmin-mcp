// What onboarding has gathered so far: the goal, the paces (imported or answered)
// and the plan the server made from them. Finishing marks the device onboarded.
import { createContext, useContext, useMemo, useState, type ReactNode } from 'react';

import { useDb } from '../../db/DbContext';
import { kv } from '../../db/kv';
import type { Goal, PaceResult, Reveal } from './model';

export const ONBOARDED_KEY = 'onboarded';

type State = {
  goal: Goal | null; setGoal: (g: Goal) => void;
  result: PaceResult | null; setResult: (r: PaceResult) => void;
  reveal: Reveal | null; setReveal: (r: Reveal) => void;
  finish: () => Promise<void>;
};

const Ctx = createContext<State | null>(null);

export function OnboardingProvider({ children }: { children: ReactNode }) {
  const db = useDb();
  const [goal, setGoal] = useState<Goal | null>(null);
  const [result, setResult] = useState<PaceResult | null>(null);
  const [reveal, setReveal] = useState<Reveal | null>(null);
  const value = useMemo<State>(() => ({
    goal, setGoal, result, setResult, reveal, setReveal,
    finish: async () => { if (db) await kv(db).set(ONBOARDED_KEY, true).catch(() => {}); },
  }), [goal, result, reveal, db]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useOnboarding(): State {
  const c = useContext(Ctx);
  if (!c) throw new Error('useOnboarding outside OnboardingProvider');
  return c;
}

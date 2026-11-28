// What onboarding has gathered so far: the goal, the paces (imported or answered)
// and the plan the server made from them. Changing the goal or the paces drops the
// plan made from the old ones. Finishing marks the device onboarded; the app's
// first screen reads that to choose between onboarding and Today.
import { createContext, useContext, useMemo, useState, type ReactNode } from 'react';

import { useDb } from '../../db/DbContext';
import { kv } from '../../db/kv';
import type { Goal, PaceResult, Reveal } from './model';

export const ONBOARDED_KEY = 'onboarded';
/** The answer to the one notification ask, so it's never asked twice. */
export const NOTIFICATIONS_KEY = 'notifications.permission';

/** Where onboarding started: the welcome screen, or Plan's "Create a plan" (signed in, already onboarded). */
export type Origin = 'start' | 'plan';

export type OnboardingState = {
  goal: Goal | null; setGoal: (g: Goal) => void;
  result: PaceResult | null; setResult: (r: PaceResult) => void;
  reveal: Reveal | null; setReveal: (r: Reveal) => void;
  origin: Origin; setOrigin: (o: Origin) => void;
  /** The notification ask's answer, kept on the device. */
  rememberNotifications: (answer: 'granted' | 'denied' | 'not_now') => Promise<void>;
  finish: () => Promise<void>;
};

const Ctx = createContext<OnboardingState | null>(null);

export function OnboardingProvider({ children, initial }: {
  children: ReactNode;
  /** Tests and previews: start part way through. */
  initial?: { goal?: Goal | null; result?: PaceResult | null; reveal?: Reveal | null; origin?: Origin };
}) {
  const db = useDb();
  const [goal, setGoalState] = useState<Goal | null>(initial?.goal ?? null);
  const [result, setResultState] = useState<PaceResult | null>(initial?.result ?? null);
  const [reveal, setReveal] = useState<Reveal | null>(initial?.reveal ?? null);
  const [origin, setOrigin] = useState<Origin>(initial?.origin ?? 'start');
  const value = useMemo<OnboardingState>(() => ({
    goal, result, reveal, origin, setReveal, setOrigin,
    setGoal: (g) => { setGoalState(g); setReveal(null); },
    setResult: (r) => { setResultState(r); setReveal(null); },
    rememberNotifications: async (answer) => { if (db) await kv(db).set(NOTIFICATIONS_KEY, { answer, at: Date.now() }).catch(() => {}); },
    finish: async () => { if (db) await kv(db).set(ONBOARDED_KEY, true).catch(() => {}); },
  }), [goal, result, reveal, origin, db]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useOnboarding(): OnboardingState {
  const c = useContext(Ctx);
  if (!c) throw new Error('useOnboarding outside OnboardingProvider');
  return c;
}

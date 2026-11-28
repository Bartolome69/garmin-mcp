// What onboarding has gathered so far: the goal, the paces (imported or answered)
// and the plan the server made from them. Changing the goal or the paces drops the
// plan made from the old ones. Finishing marks the device onboarded; the app's
// first screen reads that to choose between onboarding and Today.
import { createContext, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';

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
  /**
   * A plan the server made from these paces and this goal: kept only if they are still
   * the ones onboarding has, so a slow reply to answers since changed is dropped.
   */
  revealFor: (result: PaceResult, goal: Goal, r: Reveal) => boolean;
  /** Whether the paces and goal a request was made from are still current. */
  current: (result: PaceResult | null, goal: Goal | null) => boolean;
  origin: Origin; setOrigin: (o: Origin) => void;
  /** Whether the notification ask was answered on this device already (null while that's read). */
  notificationsAnswered: boolean | null;
  /** The notification ask's answer, kept on the device so it's never asked twice. */
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
  const [answered, setAnswered] = useState<boolean | null>(null);
  // The latest paces and goal, read when a reply arrives (a reply's closure holds old ones).
  const now = useRef({ result, goal });
  useEffect(() => {
    let live = true;
    if (!db) { setAnswered(false); return; }
    kv(db).get(NOTIFICATIONS_KEY).then((v) => { if (live) setAnswered(v != null); }).catch(() => { if (live) setAnswered(false); });
    return () => { live = false; };
  }, [db]);
  const value = useMemo<OnboardingState>(() => ({
    goal, result, reveal, origin, setReveal, setOrigin, notificationsAnswered: answered,
    setGoal: (g) => { now.current = { ...now.current, goal: g }; setGoalState(g); setReveal(null); },
    setResult: (r) => { now.current = { ...now.current, result: r }; setResultState(r); setReveal(null); },
    current: (r, g) => now.current.result === r && now.current.goal === g,
    revealFor: (r, g, plan) => {
      if (now.current.result !== r || now.current.goal !== g) return false;
      setReveal(plan);
      return true;
    },
    rememberNotifications: async (answer) => {
      setAnswered(true);
      if (db) await kv(db).set(NOTIFICATIONS_KEY, { answer, at: Date.now() }).catch(() => {});
    },
    finish: async () => { if (db) await kv(db).set(ONBOARDED_KEY, true).catch(() => {}); },
  }), [goal, result, reveal, origin, answered, db]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useOnboarding(): OnboardingState {
  const c = useContext(Ctx);
  if (!c) throw new Error('useOnboarding outside OnboardingProvider');
  return c;
}

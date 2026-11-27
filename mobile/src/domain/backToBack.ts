// Two hard sessions on consecutive days: the twin of garmin_mcp/domain/backtoback.py,
// held to the same answers by garmin_mcp/domain/fixtures/backtoback.json. On the
// device so dragging a session warns at once; the server re-checks before writing.

import { addDays, DAYS, weekday } from './format';

export type PlanSession = { id: string; date: string; type: string; distance_km?: number | null };

const HARD_TYPES = new Set(['tempo', 'intervals', 'race']);
const LONG_HARD_KM = 25;
const TYPE_WORDS: Record<string, string> = { tempo: 'tempo', intervals: 'intervals', race: 'race', long: 'long run' };

export const isHard = (s: PlanSession) => HARD_TYPES.has(s.type) || (s.type === 'long' && (s.distance_km ?? 0) > LONG_HARD_KM);

export type Clash = { first: PlanSession; second: PlanSession; message: string };

const words = (s: PlanSession) => `${DAYS[weekday(s.date)]} ${TYPE_WORDS[s.type] ?? s.type}`;

export function clashes(sessions: PlanSession[]): Clash[] {
  const hard = new Map<string, PlanSession>();
  for (const s of [...sessions].sort((a, b) => (a.date === b.date ? (a.id < b.id ? -1 : 1) : a.date < b.date ? -1 : 1))) {
    if (isHard(s) && !hard.has(s.date)) hard.set(s.date, s);
  }
  return [...hard.keys()].sort().flatMap((d) => {
    const s = hard.get(d)!;
    const next = hard.get(addDays(d, 1));
    return next ? [{ first: s, second: next, message: `${words(s)} and ${words(next)} would be back to back` }] : [];
  });
}

/** The clashes moving one session to `to` would create that aren't there now. */
export function checkMove(sessions: PlanSession[], id: string, to: string): Clash[] {
  const before = new Set(clashes(sessions).map((c) => `${c.first.id}|${c.second.id}`));
  const moved = sessions.map((s) => (s.id === id ? { ...s, date: to } : s));
  return clashes(moved).filter((c) => !before.has(`${c.first.id}|${c.second.id}`) && (c.first.id === id || c.second.id === id));
}

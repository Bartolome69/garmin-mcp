// Onboarding's shapes (build prompt, section 5.13), as the server's onboarding engine
// gives them (garmin_mcp/domain/onboarding.py), and the four questions worked out on
// the device with the shared pace engine, so they answer before sign-up. That twin
// is held to the server's answer by the fixture.
import { mmss } from '../../domain/format';
import { paces } from '../../domain/paces';
import type { Basis } from '../plan/model';
import type { SessionType } from '../today/model';
import type { WorkoutV1 } from '../builder/model';

export type PaceRow = { key: 'race' | 'easy' | 'threshold' | 'week'; label: string; value: string; note: string };
export type PaceResult = {
  source: 'import' | 'questions';
  title: string;                 // "Found 56 runs since September"
  rows: PaceRow[];
  basis: Basis;
  answers: { avg_km: number; longest_km: number; days: number; level?: string };
};
export type RevealSession = {
  id: string; date: string; dow: string; day: number; type: SessionType; typeLabel: string; title: string;
  distanceKm: number; sub: string; workout: WorkoutV1;
};
export type Reveal = {
  kicker: string;                // "Your plan · 20 weeks"
  title: string;                 // "London Marathon, Sat 24 Apr"
  phases: { label: string; weeks: number }[];
  predicted: string | null;
  target: string | null;
  startKm: number;
  peakKm: number;
  starts: string;
  because: string;
  week1: RevealSession[];
  /** Coach on only, from the server: why this shape. Never a number of its own. */
  coachNote?: string | null;
};
export type Goal = { kind: 'race'; name: string; date: string; km: number; target: string | null } | { kind: 'fit' };

export type Experience = 'new' | 'regular' | 'exp';
export type Weekly = 'under30' | '30to50' | '50to70' | '70plus';
export const EXPERIENCE: { key: Experience; label: string }[] = [
  { key: 'new', label: 'New to it' }, { key: 'regular', label: 'Regular' }, { key: 'exp', label: 'Serious amateur' }];
export const WEEKLY: { key: Weekly; label: string }[] = [
  { key: 'under30', label: 'Under 30 km' }, { key: '30to50', label: '30 to 50' }, { key: '50to70', label: '50 to 70' }, { key: '70plus', label: '70+' }];
export const DISTANCES = [{ km: 5, label: '5K' }, { km: 10, label: '10K' }, { km: 21.0975, label: 'Half' }, { km: 42.195, label: 'Marathon' }];

// The twin of onboarding.py's constants. TODO(sports-science): placeholders, as config.py's are.
const EXPERIENCE_10K: Record<Experience, number> = { new: 3600, regular: 52 * 60, exp: 45 * 60 };
const WEEKLY_KM: Record<Weekly, number> = { under30: 20, '30to50': 40, '50to70': 60, '70plus': 75 };
const RACE_NAMES: [number, string][] = [[5, '5K'], [10, '10K'], [21.0975, 'Half marathon'], [42.195, 'Marathon']];

export function raceName(km: number): string {
  const [near, name] = RACE_NAMES.reduce((a, b) => (Math.abs(b[0] - km) < Math.abs(a[0] - km) ? b : a));
  return Math.abs(near - km) / near < 0.03 ? name : `${km} km`;
}

/** "3:15", "45:00", "1:52:30" → seconds; null when it isn't a time. */
export function parseTime(text: string): number | null {
  const m = /^\s*(\d{1,2}):(\d{2})(?::(\d{2}))?\s*$/.exec(text);
  if (!m) return null;
  return m[3] != null ? +m[1] * 3600 + +m[2] * 60 + +m[3] : +m[1] * 60 + +m[2];
}

/** A race time as entered: under 3 hours of h:mm for long races, mm:ss for short ones. */
export function raceSeconds(text: string, km: number): number | null {
  const s = parseTime(text);
  if (s == null) return null;
  // "3:15" for a marathon is 3 h 15, not 3 min 15.
  return km >= 21 && /^\s*\d{1,2}:\d{2}\s*$/.test(text) ? s * 60 : s;
}

/** The four questions, on the device: the twin of onboarding.py questions(). */
export function questions(experience: Experience, days: number, weekly: Weekly, race?: { km: number; secs: number } | null): PaceResult {
  const basis: [number, number] = race ? [race.km, race.secs] : [10, EXPERIENCE_10K[experience]];
  const z = paces(basis[0], basis[1]).zones;
  const km = WEEKLY_KM[weekly];
  const range = (r: [number, number]) => `${mmss(r[0])}–${mmss(r[1])} /km`;
  return {
    source: 'questions',
    title: 'From your answers',
    rows: [
      { key: 'race', label: 'Best recent race', value: race ? `${raceName(race.km)} · ${mmss(race.secs)}` : 'None given',
        note: race ? 'from your answer' : `using a 10K of ${mmss(basis[1])}` },
      { key: 'easy', label: 'Easy pace', value: range(z.easy), note: race ? 'from the race' : 'from the 10K above' },
      { key: 'threshold', label: 'Threshold', value: range(z.threshold), note: race ? `from your ${raceName(basis[0])}` : 'from the 10K above' },
      { key: 'week', label: 'Usual week', value: `${km} km · ${days} runs`, note: 'from your answers' },
    ],
    basis: { raceKm: basis[0], raceSeconds: basis[1], raceDate: null },
    answers: { avg_km: km, longest_km: Math.round(km * 0.3 * 10) / 10, days: Math.max(3, Math.min(7, days)), level: experience },
  };
}

/** The goal's words for a step: "London Marathon, 24 Apr 2027". */
export function goalValid(g: Goal | null): boolean {
  if (!g) return false;
  if (g.kind === 'fit') return true;
  return g.name.trim().length > 0 && /^\d{4}-\d{2}-\d{2}$/.test(g.date) && !Number.isNaN(Date.parse(g.date)) && g.km > 0;
}

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
  /** Runs read from the watch (import only): 0 says there was nothing to read. */
  runs: number | null;
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
/** Where week 1's first session is: the watch queue's state, in the words this screen needs. */
export type Landing =
  | { state: 'sending' }
  | { state: 'pending' }                 // Garmin has it; the watch gets it at its next sync
  | { state: 'landed'; at: string; device: string | null; rest: number; total: number }
  | { state: 'failed'; error: string; at: string | null; retrying: boolean };

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

// A time a person could run the distance in: 2:30 to 15:00 per km. Anything else is a typo.
const FASTEST_PER_KM = 150;
const SLOWEST_PER_KM = 900;

/**
 * A race time as entered, in seconds, or null when it isn't one. Two parts are h:mm
 * for a half or a marathon ("3:15" is 3 h 15, as plan_builder.goal_secs reads it)
 * and mm:ss for shorter races; three parts are always h:mm:ss.
 */
export function raceSeconds(text: string, km: number): number | null {
  const parsed = parseTime(text);
  if (parsed == null || km <= 0) return null;
  const s = km >= 21 && /^\s*\d{1,2}:\d{2}\s*$/.test(text) ? parsed * 60 : parsed;
  const perKm = s / km;
  return perKm >= FASTEST_PER_KM && perKm <= SLOWEST_PER_KM ? s : null;
}

const MONTH_WORDS = ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'];
const iso = (y: number, m: number, d: number) => `${y}-${String(m).padStart(2, '0')}-${String(d).padStart(2, '0')}`;

/** A race day as typed: "2027-04-24", "24/04/2027" (day first) or "24 Apr 2027". ISO, or null when it isn't a real day. */
export function parseRaceDate(text: string): string | null {
  // A weekday in front ("Sat 24 Apr 2027", as the goal step shows a date it read) is fine.
  const t = text.trim().toLowerCase().replace(/^(mon|tue|wed|thu|fri|sat|sun)[a-z]*,?\s+/, '');
  let y: number; let m: number; let d: number;
  let r: RegExpExecArray | null;
  if ((r = /^(\d{4})-(\d{1,2})-(\d{1,2})$/.exec(t))) [y, m, d] = [+r[1], +r[2], +r[3]];
  else if ((r = /^(\d{1,2})[/.](\d{1,2})[/.](\d{4})$/.exec(t))) [d, m, y] = [+r[1], +r[2], +r[3]];
  else if ((r = /^(\d{1,2})\s+([a-z]{3,9})\.?\s+(\d{4})$/.exec(t)) && MONTH_WORDS.includes(r[2].slice(0, 3))) {
    [d, m, y] = [+r[1], MONTH_WORDS.indexOf(r[2].slice(0, 3)) + 1, +r[3]];
  } else return null;
  const day = new Date(Date.UTC(y, m - 1, d));
  return day.getUTCFullYear() === y && day.getUTCMonth() === m - 1 && day.getUTCDate() === d ? iso(y, m, d) : null;
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
    runs: null,
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

/** Whether the goal can make a plan: a fitness goal, or a named race on a real day after today with a target that reads, if any. */
export function goalValid(g: Goal | null, today: string): boolean {
  if (!g) return false;
  if (g.kind === 'fit') return true;
  return g.name.trim().length > 0 && g.km > 0 && parseRaceDate(g.date) === g.date && g.date > today
    && (g.target == null || raceSeconds(g.target, g.km) != null);
}

/** Today on this phone, as YYYY-MM-DD in its own time zone. */
export function localToday(now: Date = new Date()): string {
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
}

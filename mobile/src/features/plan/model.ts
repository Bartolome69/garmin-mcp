// Plan's view model (build prompt, sections 5.3, 5.5 and 5.6): what the server
// sends, already worked out by the engines (garmin_mcp/domain/), with the words to
// show. The app draws it and never recomputes a plan number. Two things run on the
// device, from the shared engines held to the server's answers by fixtures: the
// back-to-back check while dragging (domain/backToBack.ts) and Easier / Harder pace
// steps on a session (domain/paces.ts).

import type { Zone } from '../../domain/paces';
import type { SessionType, StructurePart, WatchState } from '../today/model';

export type Watch = { state: WatchState; at: string | null };

/** A step's target. Pace ranges are seconds per km, fast to slow; zone lets Easier / Harder re-step it. */
export type Target =
  | { kind: 'pace'; zone: Zone | null; range: [number, number] }
  | { kind: 'hr'; range: [number, number] }
  | { kind: 'power'; range: [number, number] }
  | { kind: 'easy' }
  | { kind: 'none' };

export type StepRow = {
  kind: 'warmup' | 'work' | 'recovery' | 'cooldown' | 'rest';
  name: string;                  // "400 m", "Warm-up"
  detail: string;                // "Hard, controlled", "15 min easy"
  target: Target;
};
export type StepItem = StepRow | { kind: 'repeat'; times: number; steps: StepRow[] };

export type SessionDetail = {
  meta: string;                  // "Wed 2 Dec · 9.5 km · about 48 min"
  structure: StructurePart[];
  legend: string[];
  steps: StepItem[];
  adjustable: boolean;           // pace targets from the basis race, so Easier / Harder apply
  coachNote?: string | null;     // only sent with coach_enabled: why this session, never a cause
};

export type PlanSession = {
  id: string;
  date: string;
  type: SessionType;
  typeLabel: string;             // "Tempo"
  title: string;                 // "3 × 2 km at threshold"
  sub: string;                   // "Tempo · 10 km · 4:23–4:28 /km"
  distanceKm: number | null;     // for the back-to-back check (a long run over 25 km is hard)
  done: boolean;                 // recorded: opens the run, can't be moved
  watch: Watch | null;           // planned sessions only
  detail: SessionDetail | null;  // planned sessions with steps
};

export type PlanDay = {
  date: string;
  dow: string;                   // "MON"
  day: number;                   // 23
  today: boolean;
  sessions: PlanSession[];       // usually one; a rest day is a session of type rest
};

export type PlanWeek = {
  start: string;                 // the Monday
  number: number;
  title: string;                 // "Week 4 · Base"
  summary: string;               // "23 to 29 Nov · 28 of 54 km · 3 of 6 done"
  days: PlanDay[];
};

export type Phase = { name: string; from: number; to: number; starts: string; focus: string };

export type BlockVM = {
  title: string;                 // "21 weeks to London"
  raceDate: string;              // "Sat 24 Apr"
  totalWeeks: number;
  currentWeek: number;
  phases: Phase[];
  now: string;                   // "Week 4 of 21 · 54 km"
  peak: string;                  // "Peak week 17 · 98 km"
};

/** One week of the load chart, as garmin_mcp/domain/load.py chart() returns it. */
export type LoadWeek = {
  starts: string;
  number: number | null;
  load: number;
  low: number | null;
  high: number | null;
  state: 'done' | 'current' | 'planned';
  over: boolean;
  trim: string | null;           // "Trim week 7"
};

export type TrendsVM = {
  load: { weeks: LoadWeek[]; caption: string; warning: string | null };
  prediction: { race: string; today: string; target: string | null; basis: string } | null;
  /** Plus only: the server leaves it out for Free, and the app shows a locked preview instead. */
  trend: { points: { label: string; seconds: number }[]; target: number | null; start: string; projection: string } | null;
};

export type Basis = { raceKm: number; raceSeconds: number; raceDate: string | null };

export type PlanVM = {
  today: string;
  currentWeek: string;           // the Monday of this week
  weeks: PlanWeek[];             // a window around this week, oldest first
  block: BlockVM | null;
  trends: TrendsVM;
  basis: Basis | null;           // the race the pace targets come from
};

export function allSessions(vm: PlanVM): PlanSession[] {
  return vm.weeks.flatMap((w) => w.days.flatMap((d) => d.sessions));
}

export function findSession(vm: PlanVM, id: string): PlanSession | null {
  return allSessions(vm).find((s) => s.id === id) ?? null;
}

export function weekIndexOf(vm: PlanVM, start: string): number {
  const i = vm.weeks.findIndex((w) => w.start === start);
  return i < 0 ? 0 : i;
}

/** Numbers a runner reads on Plan, for the Coach on/off parity check. */
export function numbersOf(vm: PlanVM): unknown[] {
  return [
    vm.weeks.map((w) => [w.title, w.summary, w.days.map((d) => d.sessions.map((s) => [s.sub, s.distanceKm, s.detail?.meta,
      s.detail?.steps]))]),
    vm.block, vm.trends, vm.basis,
  ];
}

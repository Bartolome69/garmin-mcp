// Post-run's view model (build prompt, section 5.9): a recorded run as the server
// works it out (garmin_mcp/domain/postrun.py view()). Every number in it, the
// takeaways included, comes from there, so it's the same with the Coach on or off
// and the app never recomputes one. The app only draws it and reads it at the
// scrub line.
import type { SessionType } from '../today/model';

export type Takeaway = { bold: string; rest: string; text: string; tone: 'good' | 'note' };
export type Tick = { value: number; label: string };
export type Chart = {
  label: string;                 // "Pace"
  unit: string;                  // "/km"
  inverted: boolean;             // pace: faster is up
  domain: { min: number; max: number; ticks: Tick[] } | null;
  summary: string;               // for screen readers: "Pace by distance, 6 reps around 4:01 per km"
  takeaway: Takeaway | null;
};
export type Metric = 'pace' | 'hr' | 'cadence' | 'elevation';
export type Lap = { kind: 'warmup' | 'work' | 'recovery' | 'cooldown' | 'run'; label: string; fromKm: number; toKm: number };
export type PlannedRow = { label: string; plan: string; done: string; ok: boolean };

export type RunVM = {
  id: string;
  sessionId: string | null;
  date: string;
  when: string;                  // "Wed 25 Nov · 06:30"
  type: SessionType;
  typeLabel: string;
  title: string;
  totalKm: number;
  duration: string;              // "57:04"
  route: { aspect: number; points: [number, number][] } | null;
  planned: PlannedRow[] | null;
  /** The same points for every metric, by distance, so one scrub line reads them all. */
  series: { km: number[]; pace: number[]; hr: (number | null)[]; cadence: (number | null)[]; elevation: (number | null)[] };
  laps: Lap[];
  charts: Record<Metric, Chart>;
  zones: { label: string; minutes: number; share: number }[] | null;
  scrubKm: number;               // where the scrub line starts: halfway
  /** Coach on only, from the server: what the run showed, from its own numbers. Never a cause. */
  coach?: { debrief: string } | null;
};

/** The series point nearest a distance. */
export function pointAt(vm: RunVM, km: number): number {
  const xs = vm.series.km;
  let best = 0;
  for (let i = 1; i < xs.length; i++) if (Math.abs(xs[i] - km) < Math.abs(xs[best] - km)) best = i;
  return best;
}

/** The lap a distance falls in, if the run had laps. */
export function lapAt(vm: RunVM, km: number): Lap | null {
  return vm.laps.find((l) => km >= l.fromKm && km <= l.toKm) ?? null;
}

/** Numbers a runner reads on post-run, for the Coach on/off parity check. */
export function numbersOf(vm: RunVM): unknown[] {
  const { coach: _coach, ...rest } = vm;
  return [rest];
}

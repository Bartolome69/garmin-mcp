// The workout builder's model (build prompt, section 5.7): from what the parser
// read to everything the screen shows and sends. Pure, and on the device, so the
// builder works before sign-up and with the Coach off.
//
//   build()          steps → the type chip, title, "9.4 km · about 46 min", the step
//                    rows the session detail draws, and the daash.workout.v1 sent
//   adjustLine()     "Threshold from your 10K of 43:30", for Easier / Harder
//   stepsText()      steps → text the parser reads back to the same steps (Edit
//                    steps writes the text from its rows; the parser stays the only reader)
//   sessionText()    a planned session's step rows → that text, for Edit
//   dayChoices()     the day picker
//   buildProposal()  the watch confirm's one line: Add, or Replace when editing
//
// Targets come from the shared pace engine (domain/paces.ts, the server's twin): the
// runner's basis race, or a 10K of 45:00 until a watch is connected. Easier / Harder
// re-step the zones 2% a step; a pace or heart rate written out is kept as written.
// The estimate is only that: zones at their middle, a jog at the slow end of easy.

import { addDays, DAYS, mmss, parseDate, weekday } from '../../domain/format';
import { paces, ZONE_WORDS, type Paces, type Zone } from '../../domain/paces';
import { RECOVERY_WORDS, sizeWords, type Parsed, type Recovery, type Size, type Step, type Target as Written } from '../../domain/parser';
import type { Basis, PlanSession, PlanVM, SessionDetail, StepItem, StepRow, Target } from '../plan/model';
import type { SessionType, StructurePart } from '../today/model';
import type { DiffSession, OpDraft, Proposal } from '../watch/model';

// -- daash.workout.v1 (garmin_mcp/providers/workout.py) ------------------------------

export const WORKOUT_SCHEMA = 'daash.workout.v1';
type End = { duration_seconds: number } | { distance_meters: number };
export type WorkoutStep = { type: 'warmup' | 'interval' | 'recovery' | 'rest' | 'cooldown' } & End
  & { pace?: [string, string]; hr?: [number, number]; note?: string };
export type WorkoutRepeat = { type: 'repeat'; times: number; steps: WorkoutStep[] };
export type WorkoutV1 = { schema: typeof WORKOUT_SCHEMA; name: string; sport: 'running'; description?: string; steps: (WorkoutStep | WorkoutRepeat)[] };

// -- what build() gives the screen ---------------------------------------------------

export type Built = {
  type: SessionType;
  typeLabel: string;             // "Tempo"
  title: string;                 // "5 × 1 km at threshold": the session's name, on the plan and the watch
  sub: string;                   // "Tempo · 9.4 km · 4:22–4:30 /km", as Plan rows have it
  distanceKm: number;
  minutes: number;
  estimate: string;              // "9.4 km · about 46 min"
  targets: string;               // the confirm's Targets line: "4:22–4:30 /km" or "150 bpm"
  zone: Zone | null;             // the zone Easier / Harder speaks of, when the work has one
  detail: SessionDetail;         // the step rows, drawn like a planned session's
  workout: WorkoutV1;
};

const TYPE_LABEL: Partial<Record<SessionType, string>> = { easy: 'Easy', tempo: 'Tempo', intervals: 'Intervals', long: 'Long' };
const TITLE_ZONE: Record<Zone, string> = {
  easy: 'easy', steady: 'steady', marathon: 'marathon pace', threshold: 'threshold', tempo: 'tempo',
  '10k': '10K pace', '5k': '5K pace', vo2: 'VO2 pace',
};
const DETAIL_ZONE: Record<Zone, string> = {
  easy: 'Easy', steady: 'Steady', marathon: 'Marathon pace', threshold: 'Threshold', tempo: 'Tempo',
  '10k': '10K pace', '5k': '5K pace', vo2: 'VO2 pace',
};
const SUSTAINED: Zone[] = ['steady', 'marathon', 'threshold', 'tempo'];
const WALK_S_PER_KM = 600;
const LONG_KM = 18;

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
export const longDay = (iso: string) => { const { m, d } = parseDate(iso); return `${DAYS[weekday(iso)]} ${d} ${MONTHS[m - 1]}`; };
const shortDay = (iso: string) => `${DAYS[weekday(iso)]} ${parseDate(iso).d}`;

/** The zones in seconds per km for this runner, re-stepped Easier (−) or Harder (+). */
export function pacesFor(basis: Basis | null, step: number): Paces {
  return basis ? paces(basis.raceKm, basis.raceSeconds, { raceDate: basis.raceDate, step }) : paces(10, 45 * 60, { step });
}

type Run = Extract<Step, { kind: 'run' }>;
type Rec = Extract<Step, { kind: 'recovery' }>;
const mid = ([a, b]: [number, number]) => (a + b) / 2;

/** Seconds per km a step is likely run at, for the estimate only. */
function estimatePace(s: Step, z: Paces['zones']): number | null {
  switch (s.kind) {
    case 'warmup': case 'cooldown': return mid(z.easy);
    case 'recovery': return s.how === 'rest' ? null : s.how === 'walk' ? WALK_S_PER_KM : z.easy[1];
    case 'run':
      if (s.label === 'strides') return mid(z['5k']);
      if (!s.target) return mid(z.steady);
      if (s.target.kind === 'zone') return mid(z[s.target.zone]);
      if (s.target.kind === 'pace') return (s.target.fast + s.target.slow) / 2;
      return mid(z.steady);
    default: return null;
  }
}

/** [metres, seconds] for one pass of a step. A rest covers no ground. */
function measure(s: Step, z: Paces['zones']): [number, number] {
  if (s.kind === 'repeat') {
    const [wm, ws] = measure(s.work, z);
    const [rm, rs] = s.recovery ? measure(s.recovery, z) : [0, 0];
    return [s.times * (wm + rm), s.times * (ws + rs)];
  }
  const pace = estimatePace(s, z);
  if (s.distanceM != null) return [s.distanceM, pace ? (s.distanceM / 1000) * pace : 0];
  const secs = s.durationS ?? 0;
  return [pace ? (secs / pace) * 1000 : 0, secs];
}

export function durationWords(minutes: number): string {
  const m = Math.round(minutes);
  return m >= 60 ? `${Math.floor(m / 60)} h${m % 60 ? ` ${m % 60} min` : ''}` : `${m} min`;
}
const kmWords = (km: number) => `${+km.toFixed(1)} km`;

/** What the work is: its zone (or none), and how fast it is next to the zones. */
function workOf(steps: Step[]): Run[] {
  return steps.flatMap((s) => (s.kind === 'repeat' ? [s.work] : s.kind === 'run' ? [s] : []))
    .filter((r) => r.label !== 'strides' && !(r.target?.kind === 'zone' && r.target.zone === 'easy'));
}

function zoneNear(seconds: number, z: Paces['zones']): Zone {
  let best: Zone = 'easy';
  for (const name of Object.keys(z) as Zone[]) if (Math.abs(mid(z[name]) - seconds) < Math.abs(mid(z[best]) - seconds)) best = name;
  return best;
}

function typeOf(steps: Step[], km: number, z: Paces['zones']): SessionType {
  const work = workOf(steps);
  const repeats = steps.some((s) => s.kind === 'repeat' && s.work.label !== 'strides');
  const zones = work.map((r) => (r.target?.kind === 'zone' ? r.target.zone : r.target?.kind === 'pace' ? zoneNear((r.target.fast + r.target.slow) / 2, z) : null));
  const fast = zones.some((x) => x === '10k' || x === '5k' || x === 'vo2');
  if (fast || (repeats && zones.some((x) => x == null))) return 'intervals';
  const sustained = zones.some((x) => x != null && SUSTAINED.includes(x)) || work.some((r) => r.target?.kind === 'hr');
  if (steps.some((s) => s.kind === 'run' && s.label === 'long') || km >= LONG_KM) return 'long';
  return sustained ? 'tempo' : 'easy';
}

function writtenTarget(t: Written | undefined, z: Paces['zones']): Target {
  if (!t) return { kind: 'none' };
  if (t.kind === 'zone') return { kind: 'pace', zone: t.zone, range: z[t.zone] };
  if (t.kind === 'pace') return { kind: 'pace', zone: null, range: [t.fast, t.slow] };
  return { kind: 'hr', range: [t.low, t.high] };
}

function runDetail(r: Run): string {
  if (r.label === 'strides') return 'Strides, quick and relaxed';
  if (r.label === 'long') return 'Long run, easy';
  if (!r.target) return 'Your effort';
  if (r.target.kind === 'zone') return DETAIL_ZONE[r.target.zone];
  return r.target.kind === 'pace' ? 'Your pace' : 'Heart rate';
}

const RECOVERY_DETAIL: Record<Recovery, string> = { easy: 'Easy jog', jog: 'Jog', rest: 'Rest', walk: 'Walk' };

function row(s: Exclude<Step, { kind: 'repeat' }>, z: Paces['zones']): StepRow {
  switch (s.kind) {
    case 'warmup': case 'cooldown':
      return { kind: s.kind, name: s.kind === 'warmup' ? 'Warm-up' : 'Cool-down', detail: `${sizeWords(s)} easy${s.added ? ' (added)' : ''}`,
        target: { kind: 'pace', zone: 'easy', range: z.easy } };
    case 'recovery':
      return { kind: s.how === 'rest' ? 'rest' : 'recovery', name: sizeWords(s), detail: RECOVERY_DETAIL[s.how], target: { kind: 'none' } };
    case 'run':
      return { kind: 'work', name: sizeWords(s), detail: runDetail(s), target: writtenTarget(s.target, z) };
  }
}

const pace = (range: [number, number]): [string, string] => [mmss(range[0]), mmss(range[1])];
const end = (s: Size): End => (s.distanceM != null ? { distance_meters: s.distanceM } : { duration_seconds: s.durationS ?? 0 });

function workoutStep(s: Exclude<Step, { kind: 'repeat' }>, z: Paces['zones']): WorkoutStep {
  switch (s.kind) {
    case 'warmup': case 'cooldown': return { type: s.kind, ...end(s), pace: pace(z.easy) };
    case 'recovery':
      if (s.how === 'rest') return { type: 'rest', ...end(s) };
      return { type: 'recovery', ...end(s), ...(s.how === 'walk' ? { note: 'Walk' } : {}) };
    case 'run': {
      const t = s.target;
      const target = !t ? {} : t.kind === 'zone' ? { pace: pace(z[t.zone]) } : t.kind === 'pace' ? { pace: pace([t.fast, t.slow]) } : { hr: [t.low, t.high] as [number, number] };
      const note = s.label === 'strides' ? { note: 'Strides' } : s.label === 'long' ? { note: 'Long run' } : {};
      return { type: 'interval', ...end(s), ...target, ...note };
    }
  }
}

function targetText(t: Written | undefined, z: Paces['zones']): string | null {
  if (!t) return null;
  if (t.kind === 'zone') return `${mmss(z[t.zone][0])}–${mmss(z[t.zone][1])} /km`;
  if (t.kind === 'pace') return t.fast === t.slow ? `${mmss(t.fast)} /km` : `${mmss(t.fast)}–${mmss(t.slow)} /km`;
  return t.low === t.high ? `${t.low} bpm` : `${t.low}–${t.high} bpm`;
}

function pieceTitle(r: Run): string {
  const t = r.target;
  const at = !t ? '' : t.kind === 'zone' ? ` at ${TITLE_ZONE[t.zone]}`
    : t.kind === 'pace' ? ` at ${t.fast === t.slow ? mmss(t.fast) : `${mmss(t.fast)}–${mmss(t.slow)}`} /km`
    : ` at ${t.low === t.high ? t.low : `${t.low}–${t.high}`} bpm`;
  return `${sizeWords(r)}${at}`;
}

function titleOf(steps: Step[], type: SessionType, z: Paces['zones']): string {
  // The running written, without the warm-up and cool-down: "Long run 28 km", not 29.7 with the cool-down.
  const running = steps.filter((s) => s.kind === 'run' || s.kind === 'repeat');
  const km = running.reduce((m, s) => m + measure(s, z)[0], 0) / 1000;
  const pieces = steps.flatMap((s) => {
    if (s.kind === 'repeat') return s.work.label === 'strides' ? [] : [`${s.times} × ${pieceTitle(s.work)}`];
    if (s.kind === 'run' && s.label !== 'strides' && s.label !== 'long' && !(s.target?.kind === 'zone' && s.target.zone === 'easy')) return [pieceTitle(s)];
    return [];
  });
  const strides = steps.some((s) => (s.kind === 'repeat' ? s.work : s).kind === 'run' && ((s.kind === 'repeat' ? s.work : s) as Run).label === 'strides');
  const plus = strides ? ' + strides' : '';
  if (type === 'long') return `Long run ${kmWords(km)}${pieces.length ? `, ${pieces.join(' + ')}` : ''}${plus}`;
  const easyRuns = running.filter((s) => s.kind === 'run' && s.label !== 'strides');
  if (!pieces.length) return `Easy ${easyRuns.length === 1 ? sizeWords(easyRuns[0] as Run) : kmWords(km)}${plus}`;
  return `${pieces.join(' + ')}${plus}`;
}

function structureOf(steps: Step[], z: Paces['zones']): StructurePart[] {
  const parts: StructurePart[] = [];
  const easy = (s: Step) => s.kind === 'run' && (s.target?.kind === 'zone' && s.target.zone === 'easy');
  for (const s of steps) {
    const secs = (x: Step) => measure(x, z)[1];
    if (s.kind === 'warmup') parts.push({ part: 'wu', weight: secs(s) });
    else if (s.kind === 'cooldown') parts.push({ part: 'cd', weight: secs(s) });
    else if (s.kind === 'recovery') parts.push({ part: 'rec', weight: secs(s) });
    else if (s.kind === 'run') parts.push({ part: easy(s) ? 'easy' : 'work', weight: secs(s) });
    else if (s.kind === 'repeat') for (let i = 0; i < s.times; i++) {
      parts.push({ part: 'work', weight: secs(s.work) });
      if (s.recovery) parts.push({ part: 'rec', weight: secs(s.recovery) });
    }
  }
  return parts.filter((p) => p.weight > 0);
}

/** Everything the builder shows and sends, from what the parser read. null until it reads cleanly. */
export function build(parsed: Parsed, opts: { basis: Basis | null; step: number; date: string; keepType?: SessionType | null }): Built | null {
  if (!parsed.ok) return null;
  const { steps } = parsed;
  const z = pacesFor(opts.basis, opts.step).zones;
  const [metres, seconds] = steps.reduce(([m, s], st) => { const [a, b] = measure(st, z); return [m + a, s + b]; }, [0, 0]);
  const km = metres / 1000, minutes = seconds / 60;
  let type = typeOf(steps, km, z);
  // Editing a planned long run that reads as easy keeps it a long run.
  if (opts.keepType === 'long' && type === 'easy') type = 'long';
  const typeLabel = TYPE_LABEL[type] ?? 'Run';
  const title = titleOf(steps, type, z);
  const main = workOf(steps)[0] ?? steps.flatMap((s) => (s.kind === 'run' ? [s] : s.kind === 'repeat' ? [s.work] : []))[0];
  const targets = targetText(main?.target, z) ?? (main?.label === 'strides' ? 'Strides, no pace target' : 'No targets');
  const zone = main?.target?.kind === 'zone' ? main.target.zone : null;
  const estimate = `${kmWords(km)} · about ${durationWords(minutes)}`;
  const items: StepItem[] = steps.map((s) => (s.kind === 'repeat'
    ? { kind: 'repeat', times: s.times, steps: [row(s.work, z), ...(s.recovery ? [row(s.recovery, z)] : [])] }
    : row(s, z)));
  const detail: SessionDetail = {
    meta: `${longDay(opts.date)} · ${estimate}`,
    structure: structureOf(steps, z),
    legend: parsed.readAs.replace(/^Read as /, '').split(' · ').slice(0, 3),
    steps: items,
    adjustable: steps.some((s) => (s.kind === 'repeat' ? [s.work] : [s]).some((r) => r.kind === 'run' && r.target?.kind === 'zone')),
  };
  const workout: WorkoutV1 = {
    schema: WORKOUT_SCHEMA, name: title, sport: 'running', description: parsed.readAs.replace(/^Read as /, ''),
    steps: steps.map((s) => (s.kind === 'repeat'
      ? { type: 'repeat', times: s.times, steps: [workoutStep(s.work, z), ...(s.recovery ? [workoutStep(s.recovery, z)] : [])] }
      : workoutStep(s, z))),
  };
  const subTarget = targetText(main?.target, z);
  return {
    type, typeLabel, title, sub: [typeLabel, kmWords(km), subTarget].filter(Boolean).join(' · '),
    distanceKm: +km.toFixed(1), minutes: Math.round(minutes), estimate, targets, zone, detail, workout,
  };
}

/** "Threshold from your 10K of 43:30 · Harder by 2%"; before a watch is connected, from a 10K of 45:00. */
export function adjustLine(basis: Basis | null, zone: Zone | null, step: number): string {
  const what = zone ? ZONE_WORDS[zone] : 'Paces';
  const from = basis
    ? pacesFor(basis, 0).basis.replace(/^Based on/, `${what} from`).replace(/ \([^)]*\)$/, '')
    : `${what} from a 10K of 45:00 until a watch is connected`;
  return step ? `${from} · ${step < 0 ? 'Easier' : 'Harder'} by ${Math.abs(step) * 2}%` : from;
}

// -- text from steps (Edit steps, and Edit on a planned session) ------------------------

const ZONE_TEXT: Record<Zone, string> = {
  easy: 'easy', steady: 'steady', marathon: 'marathon', threshold: 'threshold', tempo: 'tempo', '10k': '10k', '5k': '5k', vo2: 'vo2',
};

function sizeText(s: Size): string {
  if (s.distanceM != null) return s.distanceM >= 1000 && s.distanceM % 100 === 0 ? `${s.distanceM / 1000} km` : `${s.distanceM} m`;
  const secs = s.durationS ?? 0;
  if (secs % 60 === 0) return `${secs / 60} min`;
  return secs < 120 ? `${secs} s` : mmss(secs);
}

function runText(r: Run): string {
  if (r.label === 'strides') return `${sizeText(r)} strides`;
  if (r.label === 'long') return `${sizeText(r)} long run`;
  const t = r.target;
  if (!t) return sizeText(r);
  if (t.kind === 'zone') return `${sizeText(r)} ${t.zone === 'easy' ? 'easy' : `at ${ZONE_TEXT[t.zone]}`}`;
  if (t.kind === 'pace') return `${sizeText(r)} at ${t.fast === t.slow ? mmss(t.fast) : `${mmss(t.fast)}-${mmss(t.slow)}`}/km`;
  return `${sizeText(r)} at ${t.low === t.high ? t.low : `${t.low}-${t.high}`} bpm`;
}

const recText = (r: Rec) => `${sizeText(r)} ${r.how}`;

/** One step as text the parser reads back to the same step. Added warm-ups aren't written. */
export function stepText(s: Step): string {
  switch (s.kind) {
    case 'warmup': return `${sizeText(s)} warm-up`;
    case 'cooldown': return `${sizeText(s)} cool-down`;
    case 'recovery': return recText(s);
    case 'run': return runText(s);
    case 'repeat': return `${s.times} x ${runText(s.work)}${s.recovery ? ` with ${recText(s.recovery)}` : ''}`;
  }
}

export function stepsText(steps: Step[]): string {
  return steps.filter((s) => !('added' in s && s.added)).map(stepText).join(', ');
}

/** A planned session's step rows as builder text, for Edit. null when they aren't steps the builder reads (strength). */
export function sessionText(detail: SessionDetail | null): string | null {
  if (!detail?.steps.length) return null;
  const size = (name: string) => (/^\d+(?:\.\d+)?\s*(km|m|mi|min|s)\b/.exec(name) ?? [null])[0];
  const target = (t: Target): string | null => {
    if (t.kind === 'pace') return t.zone ? (t.zone === 'easy' ? 'easy' : `at ${ZONE_TEXT[t.zone]}`) : `at ${mmss(t.range[0])}-${mmss(t.range[1])}/km`;
    if (t.kind === 'hr') return `at ${t.range[0]}-${t.range[1]} bpm`;
    if (t.kind === 'easy') return 'easy';
    return null;
  };
  const one = (r: StepRow): string | null => {
    if (r.kind === 'warmup' || r.kind === 'cooldown') {
      const q = size(r.detail);
      return q ? `${q} ${r.kind === 'warmup' ? 'warm-up' : 'cool-down'}` : null;
    }
    const q = size(r.name);
    if (!q) return null;
    if (r.kind === 'recovery' || r.kind === 'rest') {
      const how = /\b(jog|walk|rest|easy)\b/i.exec(r.name)?.[1]?.toLowerCase() ?? (r.kind === 'rest' ? 'rest' : r.target.kind === 'easy' ? 'easy' : 'jog');
      return `${q} ${how}`;
    }
    const t = target(r.target);
    return t ? `${q} ${t}` : null;
  };
  const parts: string[] = [];
  for (const item of detail.steps) {
    if (item.kind === 'repeat') {
      const [work, rec] = item.steps.map(one);
      if (!work || item.steps.length > 2 || (item.steps.length === 2 && !rec)) return null;
      parts.push(`${item.times} x ${work}${rec ? ` with ${rec}` : ''}`);
    } else {
      const t = one(item);
      if (!t) return null;
      parts.push(t);
    }
  }
  return parts.join(', ');
}

// -- the day, and the confirm ------------------------------------------------------------

export type DayChoice = { date: string; label: string; long: string; planned: string | null };

/** Today and the next 13 days, each with what's already planned there. */
export function dayChoices(vm: PlanVM | null, today: string, days = 14): DayChoice[] {
  const planned = new Map<string, string>();
  for (const w of vm?.weeks ?? []) for (const d of w.days) {
    const s = d.sessions.find((x) => !x.done && x.type !== 'rest');
    if (s) planned.set(d.date, s.title);
  }
  return Array.from({ length: days }, (_, i) => {
    const date = addDays(today, i);
    return { date, label: i === 0 ? 'Today' : i === 1 ? 'Tomorrow' : shortDay(date), long: longDay(date), planned: planned.get(date) ?? null };
  });
}

/** The first day from today with nothing planned on it. */
export function firstFreeDay(choices: DayChoice[]): string {
  return (choices.find((c) => !c.planned) ?? choices[1] ?? choices[0]).date;
}

/** The op a builder save sends: it carries the workout, so the watch gets exactly what was read. */
export type BuildOp = OpDraft & { workout: WorkoutV1 };

export function builtSession(b: Built, id: string, date: string): DiffSession & { detail: SessionDetail } {
  return { id, date, type: b.type, typeLabel: b.typeLabel, title: b.title, sub: b.sub, distanceKm: b.distanceKm, detail: b.detail };
}

/** Save and send: one line on the watch confirm. Add a new session, or Replace the one being edited. */
export function buildProposal(b: Built, opts: { id: string; date: string; replacing?: PlanSession | null }): Proposal {
  const { id, date, replacing } = opts;
  const after = builtSession(b, replacing?.id ?? id, date);
  const before: DiffSession | null = replacing
    ? { id: replacing.id, date: replacing.date, type: replacing.type, typeLabel: replacing.typeLabel, title: replacing.title, sub: replacing.sub, distanceKm: replacing.distanceKm }
    : null;
  const moved = replacing && replacing.date !== date;
  const ops: BuildOp[] = moved
    ? [
      { kind: 'remove', sessionId: replacing.id, date: replacing.date, dayLabel: shortDay(replacing.date), what: replacing.title, withWhat: null, before, after: null, workout: b.workout },
      { kind: 'add', sessionId: replacing.id, date, dayLabel: shortDay(date), what: b.title, withWhat: null, before: null, after, workout: b.workout },
    ]
    : [{
      kind: replacing ? 'replace' : 'add', sessionId: after.id, date, dayLabel: shortDay(date),
      what: replacing ? replacing.title : b.title, withWhat: replacing ? b.title : null, before, after, workout: b.workout,
    }];
  return {
    id: `build:${after.id}:${date}`, source: 'build', kicker: longDay(date), title: b.title, subtitle: b.targets,
    days: [], load: null, rule: null, disclaimer: null, ops,
  };
}

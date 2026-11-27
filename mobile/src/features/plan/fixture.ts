// The design's sample persona for Plan, for fixtures and development only (build
// prompt, section 16): London on Sat 24 Apr, target 3:15, a 10K of 43:30 on 12 Oct.
// Today is Fri 27 Nov, week 4 of 21. Pace targets are the shared engine's answers
// for that 10K (domain/paces.ts, the server's twin), as the server would send them.
import { addDays, DAYS, parseDate, weekday } from '../../domain/format';
import { paces } from '../../domain/paces';
import type { SessionType, StructurePart, WatchState } from '../today/model';
import type { PlanDay, PlanSession, PlanVM, PlanWeek, SessionDetail, StepItem, StepRow } from './model';

const TODAY = '2026-11-27';
const BASIS = { raceKm: 10, raceSeconds: 43 * 60 + 30, raceDate: '2026-10-12' };
const Z = paces(BASIS.raceKm, BASIS.raceSeconds, { raceDate: BASIS.raceDate }).zones;

const LABEL: Record<SessionType, string> = {
  easy: 'Easy', recovery: 'Recovery', tempo: 'Tempo', intervals: 'Intervals', long: 'Long', race: 'Race',
  strength: 'Strength', rest: 'Rest', other: 'Other',
};

type Spec = {
  type: SessionType; title: string; sub: string; km?: number | null; done?: boolean;
  watch?: WatchState; at?: string | null; detail?: SessionDetail | null;
};

function session(date: string, spec: Spec): PlanSession {
  return {
    id: `${date}-${spec.type}`,
    date,
    type: spec.type,
    typeLabel: LABEL[spec.type],
    title: spec.title,
    sub: spec.sub,
    distanceKm: spec.km ?? null,
    done: spec.done ?? false,
    watch: spec.done || spec.type === 'rest' ? null : { state: spec.watch ?? 'not_sent', at: spec.at ?? null },
    detail: spec.detail ?? null,
  };
}

function week(start: string, number: number, phase: string, summary: string, specs: (Spec | null)[]): PlanWeek {
  const days: PlanDay[] = specs.map((spec, i) => {
    const date = addDays(start, i);
    return {
      date,
      dow: DAYS[weekday(date)].toUpperCase(),
      day: parseDate(date).d,
      today: date === TODAY,
      sessions: spec ? [session(date, spec)] : [],
    };
  });
  return { start, number, title: `Week ${number} · ${phase}`, summary, days };
}

const warm = (min: number): StepRow => ({ kind: 'warmup', name: 'Warm-up', detail: `${min} min easy`, target: { kind: 'pace', zone: 'easy', range: Z.easy } });
const cool = (min: number): StepRow => ({ kind: 'cooldown', name: 'Cool-down', detail: `${min} min easy`, target: { kind: 'pace', zone: 'easy', range: Z.easy } });
const parts = (wu: number, reps: number, work: number, rec: number, cd: number): StructurePart[] => [
  { part: 'wu', weight: wu },
  ...Array.from({ length: reps }, (_, i) => [{ part: 'work' as const, weight: work }, ...(i < reps - 1 ? [{ part: 'rec' as const, weight: rec }] : [])]).flat(),
  { part: 'cd', weight: cd },
];

const TEMPO: SessionDetail = {
  meta: 'Fri 27 Nov · 10 km · about 52 min',
  structure: parts(15, 3, 9, 2, 10),
  legend: ['Warm-up 15 min', '3 × 2 km · 2 min jog', 'Cool 10'],
  steps: [
    warm(15),
    { kind: 'repeat', times: 3, steps: [
      { kind: 'work', name: '2 km', detail: 'Threshold, steady', target: { kind: 'pace', zone: 'threshold', range: Z.threshold } },
      { kind: 'recovery', name: '2 min jog', detail: 'Recover', target: { kind: 'easy' } },
    ] },
    cool(10),
  ] as StepItem[],
  adjustable: true,
};

const TEN_BY_400: SessionDetail = {
  meta: 'Wed 2 Dec · 9.5 km · about 48 min',
  structure: parts(15, 10, 1.6, 1.2, 10),
  legend: ['Warm-up 15 min', '10 × 400 m · 200 m jog', 'Cool 10'],
  steps: [
    warm(15),
    { kind: 'repeat', times: 10, steps: [
      { kind: 'work', name: '400 m', detail: 'Hard, controlled', target: { kind: 'pace', zone: 'vo2', range: Z.vo2 } },
      { kind: 'recovery', name: '200 m jog', detail: 'Recover', target: { kind: 'easy' } },
    ] },
    cool(10),
  ],
  adjustable: true,
};

const easyDetail = (date: string, km: number, min: number): SessionDetail => ({
  meta: `${DAYS[weekday(date)]} ${parseDate(date).d} ${date.slice(5, 7) === '12' ? 'Dec' : 'Nov'} · ${km} km · about ${min} min`,
  structure: [{ part: 'easy', weight: 1 }],
  legend: [`${km} km easy`],
  steps: [{ kind: 'work', name: `${km} km`, detail: 'Easy, conversational', target: { kind: 'pace', zone: 'easy', range: Z.easy } }],
  adjustable: true,
});

const LONG_18: SessionDetail = {
  meta: 'Sun 29 Nov · 18 km · about 1 h 45 min',
  structure: [{ part: 'easy', weight: 1 }],
  legend: ['18 km easy'],
  steps: [{ kind: 'work', name: '18 km', detail: 'Easy, the last 3 km steady', target: { kind: 'pace', zone: 'easy', range: Z.easy } }],
  adjustable: true,
};

const STRENGTH: SessionDetail = {
  meta: 'Sat 28 Nov · 27 min · 8 exercises',
  structure: [{ part: 'work', weight: 1 }],
  legend: ['8 exercises · 2 rounds'],
  steps: [{ kind: 'repeat', times: 2, steps: [
    { kind: 'work', name: 'Squat, lunge, bridge, calf raise', detail: '10 each', target: { kind: 'none' } },
    { kind: 'work', name: 'Plank, side plank, dead bug, bird dog', detail: '40 s each', target: { kind: 'none' } },
  ] }],
  adjustable: false,
};

const rest = (done = false): Spec => ({ type: 'rest', title: 'Rest day', sub: 'Rest · Recovery', done });

export const PERSONA_PLAN: PlanVM = {
  today: TODAY,
  currentWeek: '2026-11-23',
  basis: BASIS,
  weeks: [
    week('2026-11-16', 3, 'Base', '16 to 22 Nov · 49 of 50 km · 5 of 5 done', [
      rest(true),
      { type: 'easy', title: '8 km easy', sub: 'Easy · 8.0 km · 5:18 /km', km: 8, done: true },
      { type: 'intervals', title: '5 × 1 km', sub: 'Intervals · 10.4 km · reps 4:06 /km', km: 10.4, done: true },
      { type: 'easy', title: '8 km easy', sub: 'Easy · 8.2 km · 5:20 /km', km: 8.2, done: true },
      rest(true),
      { type: 'easy', title: '6 km easy', sub: 'Easy · 6.1 km · 5:25 /km', km: 6.1, done: true },
      { type: 'long', title: 'Long 16 km easy', sub: 'Long · 16.3 km · 5:31 /km', km: 16.3, done: true },
    ]),
    week('2026-11-23', 4, 'Base', '23 to 29 Nov · 28 of 54 km · 3 of 6 done', [
      rest(true),
      { type: 'easy', title: '8 km easy + strides', sub: 'Easy · 8.1 km · 5:12 /km', km: 8.1, done: true },
      { type: 'intervals', title: '6 × 800 m', sub: 'Intervals · 11.2 km · reps 4:01 /km', km: 11.2, done: true },
      { type: 'easy', title: '9 km run club', sub: 'Easy · 9.2 km · 5:14 /km', km: 9.2, done: true },
      { type: 'tempo', title: '3 × 2 km at threshold', sub: 'Tempo · 10 km · 4:23–4:28 /km', km: 10, watch: 'on_watch', at: '20:14', detail: TEMPO },
      { type: 'strength', title: 'Full body (runner)', sub: 'Strength · 27 min · 8 exercises', watch: 'on_watch', at: '20:14', detail: STRENGTH },
      { type: 'long', title: 'Long 18 km easy', sub: 'Long · 18 km · 5:27–6:10 /km', km: 18, watch: 'on_watch', at: '20:14', detail: LONG_18 },
    ]),
    week('2026-11-30', 5, 'Base', '30 Nov to 6 Dec · 55.5 km planned', [
      rest(),
      { type: 'easy', title: '8 km easy', sub: 'Easy · 8 km · 5:27–6:10 /km', km: 8, detail: easyDetail('2026-12-01', 8, 46) },
      { type: 'intervals', title: '10 × 400 m', sub: 'Intervals · 9.5 km · reps 4:03–4:10 /km', km: 9.5, detail: TEN_BY_400 },
      { type: 'easy', title: '10 km easy', sub: 'Easy · 10 km · 5:27–6:10 /km', km: 10, detail: easyDetail('2026-12-03', 10, 58) },
      rest(),
      { type: 'easy', title: '8 km easy', sub: 'Easy · 8 km · 5:27–6:10 /km', km: 8, detail: easyDetail('2026-12-05', 8, 46) },
      { type: 'long', title: 'Long 20 km easy', sub: 'Long · 20 km · 5:27–6:10 /km', km: 20 },
    ]),
    week('2026-12-07', 6, 'Base', '7 to 13 Dec · 60 km planned', [
      rest(),
      { type: 'easy', title: '8 km easy', sub: 'Easy · 8 km · 5:27–6:10 /km', km: 8 },
      { type: 'tempo', title: '4 × 2 km at threshold', sub: 'Tempo · 12 km · 4:23–4:28 /km', km: 12 },
      { type: 'easy', title: '10 km easy', sub: 'Easy · 10 km · 5:27–6:10 /km', km: 10 },
      rest(),
      { type: 'easy', title: '8 km easy', sub: 'Easy · 8 km · 5:27–6:10 /km', km: 8 },
      { type: 'long', title: 'Long 22 km easy', sub: 'Long · 22 km · 5:27–6:10 /km', km: 22 },
    ]),
  ],
  block: {
    title: '21 weeks to London',
    raceDate: 'Sat 24 Apr',
    totalWeeks: 21,
    currentWeek: 4,
    now: 'Week 4 of 21 · 54 km',
    peak: 'Peak week 17 · 98 km',
    phases: [
      { name: 'Base', from: 1, to: 7, starts: 'now', focus: 'Easy volume, one quality session a week, strength twice' },
      { name: 'Build', from: 8, to: 14, starts: '4 Jan', focus: 'Marathon-pace long runs, threshold on Wednesdays' },
      { name: 'Peak', from: 15, to: 18, starts: '15 Feb', focus: 'The two longest runs, a race rehearsal in week 18' },
      { name: 'Taper', from: 19, to: 21, starts: '5 Apr', focus: 'Volume down, sharpness kept' },
    ],
  },
  trends: {
    load: {
      caption: 'Next 5 weeks planned',
      warning: 'Week 7 is 18% above your target band.',
      weeks: [
        { starts: '2026-10-26', number: null, load: 300, low: 324, high: 396, state: 'done', over: false, trim: null },
        { starts: '2026-11-02', number: 1, load: 330, low: 324, high: 396, state: 'done', over: false, trim: null },
        { starts: '2026-11-09', number: 2, load: 365, low: 342, high: 418, state: 'done', over: false, trim: null },
        { starts: '2026-11-16', number: 3, load: 345, low: 342, high: 418, state: 'done', over: false, trim: null },
        { starts: '2026-11-23', number: 4, load: 395, low: 360, high: 440, state: 'current', over: false, trim: null },
        { starts: '2026-11-30', number: 5, load: 425, low: 378, high: 462, state: 'planned', over: false, trim: null },
        { starts: '2026-12-07', number: 6, load: 460, low: 396, high: 484, state: 'planned', over: false, trim: null },
        { starts: '2026-12-14', number: 7, load: 571, low: 396, high: 484, state: 'planned', over: true, trim: 'Trim week 7' },
        { starts: '2026-12-21', number: 8, load: 450, low: 414, high: 506, state: 'planned', over: false, trim: null },
        { starts: '2026-12-28', number: 9, load: 345, low: 324, high: 396, state: 'planned', over: false, trim: null },
      ],
    },
    prediction: { race: 'London', today: '3:20:40', target: '3:15', basis: 'from your runs' },
    trend: null,
  },
};

/** The same plan with Plus: the prediction trend and race-day projection. */
export const PERSONA_PLAN_PLUS: PlanVM = {
  ...PERSONA_PLAN,
  trends: {
    ...PERSONA_PLAN.trends,
    trend: {
      points: [
        { label: 'Sep', seconds: 3 * 3600 + 27 * 60 }, { label: '', seconds: 3 * 3600 + 26 * 60 }, { label: '', seconds: 3 * 3600 + 25 * 60 },
        { label: 'Oct', seconds: 3 * 3600 + 24 * 60 }, { label: '', seconds: 3 * 3600 + 23 * 60 }, { label: '', seconds: 3 * 3600 + 22 * 60 },
        { label: 'Nov', seconds: 3 * 3600 + 20 * 60 + 40 },
      ],
      target: 3 * 3600 + 15 * 60,
      start: 'Sep · 3:27',
      projection: 'Race day · 3:14 if on plan',
    },
  },
};

/** With the Coach on, a planned session carries one line on why it's there. Every number is the same. */
export const PERSONA_PLAN_COACH: PlanVM = {
  ...PERSONA_PLAN,
  weeks: PERSONA_PLAN.weeks.map((w) => ({
    ...w,
    days: w.days.map((d) => ({
      ...d,
      sessions: d.sessions.map((s) => (s.detail && s.type === 'intervals'
        ? { ...s, detail: { ...s.detail, coachNote: 'Short reps keep your speed ticking over while the volume builds. Easy days sit either side.' } }
        : s.detail && s.type === 'tempo'
          ? { ...s, detail: { ...s.detail, coachNote: 'Threshold reps at the pace you can hold for about an hour, with short jogs between.' } }
          : s)),
    })),
  })),
};

// The design's sample persona, for fixtures and development only (build prompt,
// section 16): London on Sat 24 Apr, target 3:15, a 10K of 43:30. Today is Fri 27 Nov,
// a Back off morning: two short signals, the tempo on the watch.
import type { TodayVM } from './model';

export const PERSONA_TODAY: TodayVM = {
  date: '2026-11-27',
  kicker: ['Fri 27 Nov', 'Week 4 of 21'],
  title: 'London in 148 days',
  readiness: {
    decision: 'back_off',
    word: 'Back off',
    garminScore: 26,
    summary: '2 of 3 below usual',
    recommended: { kind: 'easier', label: 'Make it easier' },
    signals: [
      { key: 'sleep', label: 'Sleep', value: '4:45', note: '2h under', status: 'low',
        sheet: { headline: 'Sleep · 4:45', badge: '2h under your usual', series: [7.3, 7.6, 7.1, 7.8, 6.9, 7.4, 7.5, 7.2, 6.8, 7.6, 7.0, 6.1, 5.4, 4.75],
          band: null, line: 7.25, rangeLabel: 'Your usual 7:15', lastLabel: 'Last night 4:45 · 2h under',
          explanation: 'From your watch overnight. Daash compares last night with your own last 28 nights, not a population average.' } },
      { key: 'hrv', label: 'HRV', value: '49 ms', note: 'below range', status: 'low',
        sheet: { headline: 'HRV · 49 ms', badge: 'Below your range', series: [60, 61, 59, 63, 60, 61, 59, 64, 61, 60, 58, 56, 52, 49],
          band: [54, 66], line: null, rangeLabel: 'Your range 54–66 ms', lastLabel: 'Last night 49 · below',
          explanation: 'Measured overnight by your watch. Daash compares it with your own last 60 days, not a population average.' } },
      { key: 'load', label: 'Load', value: '1.12', note: 'in range', status: 'usual',
        sheet: { headline: 'Load ratio · 1.12', badge: 'In range', series: [1.02, 1.04, 1.05, 1.03, 1.06, 1.08, 1.07, 1.1, 1.09, 1.11, 1.12, 1.1, 1.11, 1.12],
          band: [0.8, 1.3], line: null, rangeLabel: 'In range 0.8–1.3', lastLabel: 'Now 1.12 · in range',
          explanation: 'Your last 7 days of training against your last 28. Above 1.3 counts as a low signal.' } },
    ],
  },
  session: {
    id: '2026-11-27-tempo', date: '2026-11-27', type: 'tempo', typeLabel: 'Tempo', title: '3 × 2 km at threshold',
    meta: 'Today · 10 km · about 52 min · 4:23–4:28 /km',
    structure: [{ part: 'wu', weight: 15 }, { part: 'work', weight: 9 }, { part: 'rec', weight: 2 }, { part: 'work', weight: 9 },
      { part: 'rec', weight: 2 }, { part: 'work', weight: 9 }, { part: 'cd', weight: 10 }],
    legend: ['Warm-up 15 min', '3 × 2 km · 2 min jog', 'Cool 10'],
    watch: { state: 'on_watch', at: '20:14' },
  },
  week: {
    doneKm: 28, plannedKm: 54, activeWeeks: 11,
    days: [
      { date: '2026-11-23', letter: 'M', day: 23, type: 'rest', label: 'Rest', status: 'rest_done', today: false },
      { date: '2026-11-24', letter: 'T', day: 24, type: 'easy', label: 'Easy', status: 'done', today: false, runId: '2026-11-24-easy' },
      { date: '2026-11-25', letter: 'W', day: 25, type: 'intervals', label: 'Int', status: 'done', today: false, runId: '2026-11-25-intervals' },
      { date: '2026-11-26', letter: 'T', day: 26, type: 'easy', label: 'Easy', status: 'done', today: false, runId: '2026-11-26-easy' },
      { date: '2026-11-27', letter: 'F', day: 27, type: 'tempo', label: 'Tempo', status: 'planned', today: true },
      { date: '2026-11-28', letter: 'S', day: 28, type: 'strength', label: 'Str', status: 'planned', today: false },
      { date: '2026-11-29', letter: 'S', day: 29, type: 'long', label: 'Long', status: 'planned', today: false },
    ],
  },
  options: [
    { kind: 'as_planned', title: 'Go as planned', sub: '3 × 2 km at 4:23–4:28 /km, as it is' },
    { kind: 'swap', title: 'Swap a day', sub: 'Move the tempo, see the new week first' },
    { kind: 'rest', title: 'Rest and recover', sub: 'Counts toward your active week' },
  ],
};

/** The same morning with the Coach on: one observation line, and nothing else changes. */
export const PERSONA_TODAY_COACH: TodayVM = {
  ...PERSONA_TODAY,
  readiness: { ...PERSONA_TODAY.readiness,
    coachLine: 'Sleep was 2h under your usual and HRV is below your range. The tempo is your only hard day left this week.' },
};

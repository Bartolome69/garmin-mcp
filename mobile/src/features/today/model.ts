// Today's view model (build prompt, section 5.1): what the server sends, already
// worked out by the engines (garmin_mcp/domain/), with the words to show. The app
// draws it and never recomputes a number, so Coach on and off read the same numbers.

export type Decision = 'push' | 'hold' | 'back_off';
export type SignalKey = 'sleep' | 'hrv' | 'load';
export type SessionType = 'easy' | 'recovery' | 'tempo' | 'intervals' | 'long' | 'race' | 'strength' | 'rest' | 'other';
export type WatchState = 'not_sent' | 'confirming' | 'sending' | 'on_watch' | 'changed_pending_sync' | 'failed';

export type Signal = {
  key: SignalKey;
  label: string;                 // "Sleep"
  value: string | null;          // "4:45", "49 ms", "1.12"; null when missing
  note: string;                  // "2h under", "below range", "in range", "missing"
  status: 'low' | 'usual' | 'missing';
  sheet: {
    headline: string;            // "HRV · 49 ms"
    badge: string;               // "Below your range"
    series: (number | null)[];   // last 14 nights (or days), oldest first
    band: [number, number] | null;  // the person's own range, to shade
    line: number | null;         // or their usual (sleep's median), as a line
    rangeLabel: string;          // "Your range 54–66 ms"
    lastLabel: string;           // "Last night 49 · below"
    explanation: string;         // how the signal is read
  };
};

export type Readiness = {
  decision: Decision | null;
  word: string | null;           // "Back off"
  garminScore: number | null;    // shown labelled as Garmin's
  summary: string;               // "2 of 3 below usual"
  signals: Signal[];
  recommended: { kind: 'easier' | 'as_planned'; label: string };
  coachLine?: string | null;     // only sent with coach_enabled: an observation, never a cause
};

export type StructurePart = { part: 'wu' | 'work' | 'rec' | 'cd' | 'easy'; weight: number };

export type TodaySession = {
  id: string;
  date: string;
  type: SessionType;
  typeLabel: string;             // "Tempo"
  title: string;                 // "3 × 2 km at threshold"
  meta: string;                  // "Today · 10 km · about 52 min · 4:22–4:30 /km"
  structure: StructurePart[];
  legend: string[];              // ["Warm-up 15 min", "3 × 2 km · 2 min jog", "Cool 10"]
  watch: { state: WatchState; at: string | null };
};

export type WeekDay = {
  date: string;
  letter: string;                // "M"
  day: number;                   // 23
  type: SessionType;
  label: string;                 // "Easy"
  status: 'done' | 'planned' | 'rest' | 'rest_done';
  today: boolean;
};

export type TodayOption = { kind: 'as_planned' | 'swap' | 'rest'; title: string; sub: string };

export type TodayVM = {
  date: string;
  kicker: string[];              // phrases: ["Fri 27 Nov", "Week 4 of 21"]; wrapped only between them
  title: string;                 // "London in 148 days"
  readiness: Readiness;
  session: TodaySession | null;
  week: { doneKm: number; plannedKm: number; activeWeeks: number; days: WeekDay[] };
  options: TodayOption[];
};

/** Join phrases so a line breaks only between them, never inside one. */
export function kickerText(phrases: string[]): string {
  return phrases.map((p) => p.replace(/ /g, ' ')).join(' · ');
}

/** Numbers a runner reads on Today, for the Coach on/off parity check. */
export function numbersOf(vm: TodayVM): unknown[] {
  const r = vm.readiness;
  return [
    vm.title, vm.kicker, r.decision, r.garminScore, r.summary,
    r.signals.map((s) => [s.key, s.value, s.note, s.status, s.sheet.series, s.sheet.band, s.sheet.line]),
    vm.session && [vm.session.title, vm.session.meta, vm.session.structure, vm.session.watch],
    vm.week.doneKm, vm.week.plannedKm, vm.week.activeWeeks, vm.week.days.map((d) => [d.date, d.type, d.status]),
  ];
}

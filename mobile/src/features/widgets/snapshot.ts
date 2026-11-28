// What the home and lock screen widgets draw (build prompt, section 5.15), built
// from the phone's cached Today, the watch queue's state and the cached plan. Pure:
// no React, no clock of its own. The native widgets read this snapshot from shared
// storage and draw it; they never ask the server. Widgets are Plus: for Free the
// snapshot carries the Plus message instead of data. Never a blank: with nothing
// cached it says to open Daash, and when the data is old it says when it's from.
import { STALE_AFTER_MS } from '../../data/freshness';
import type { BlockVM } from '../plan/model';
import type { Decision, SessionType, TodayVM, WatchState } from '../today/model';

export const SNAPSHOT_VERSION = 1;

export type WidgetDay = { letter: string; day: number; type: SessionType; label: string; today: boolean; done: boolean };

export type WidgetSnapshot = {
  v: typeof SNAPSHOT_VERSION;
  writtenAt: number;
  /** When the phone last read this from Daash; the widget works out its own "Updated" from it too. */
  dataAt: number | null;
  /** "Updated 07:02", "Updated yesterday", "Updated 26 Nov" once the data is old; null while fresh. */
  updated: string | null;
  /** Free: the widget shows the Plus message, no data. */
  locked: boolean;
  /** What a widget says instead of data (locked, or nothing cached yet). Never empty when there's no data. */
  message: string | null;
  readiness: { score: number | null; word: string; decision: Decision } | null;
  session: { type: SessionType; typeLabel: string; line: string; title: string; watch: string; watchState: WatchState } | null;
  countdown: { race: string; days: number; date: string; raceDay: string } | null;
  week: { title: string; summary: string; days: WidgetDay[] } | null;
  lock: { circular: { score: number | null; word: string } | null; rectangular: { line1: string; line2: string } | null };
};

export const LOCKED_MESSAGE = 'Widgets come with Daash Plus. Open Daash to try it free.';
export const EMPTY_MESSAGE = 'Open Daash to load today.';

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
const DAY = 86400000;
const pad = (n: number) => String(n).padStart(2, '0');

/** The phone's own calendar date for a moment, as YYYY-MM-DD. */
export function localDate(t: number): string {
  const d = new Date(t);
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

const parseDay = (iso: string) => { const [y, m, d] = iso.split('-').map(Number); return Date.UTC(y, m - 1, d); };
const daysBetween = (from: string, to: string) => Math.round((parseDay(to) - parseDay(from)) / DAY);
const isoOf = (utc: number) => { const d = new Date(utc); return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}`; };
const dayLabel = (iso: string) => { const d = new Date(parseDay(iso)); return `${DAYS[d.getUTCDay()]} ${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]}`; };

/** "Updated 07:02" the same day, "Updated yesterday", else "Updated 26 Nov". Times, not "2h ago": a widget isn't redrawn every minute. */
export function updatedLabel(dataAt: number | null, now: number): string | null {
  if (dataAt == null || now - dataAt < STALE_AFTER_MS) return null;
  const at = new Date(dataAt);
  const gap = daysBetween(localDate(dataAt), localDate(now));
  if (gap <= 0) return `Updated ${pad(at.getHours())}:${pad(at.getMinutes())}`;
  if (gap === 1) return 'Updated yesterday';
  return `Updated ${at.getDate()} ${MONTHS[at.getMonth()]}`;
}

/** The widget's few words for the watch: "on watch", never a colour alone. */
export function widgetWatchWords(state: WatchState): string {
  switch (state) {
    case 'on_watch': return 'on watch';
    case 'changed_pending_sync': return 'changes at next sync';
    case 'sending': return 'sending';
    case 'failed': return 'not sent';
    default: return 'not on watch';
  }
}

const LONG_WATCH: Record<WatchState, string> = {
  on_watch: 'On your watch', changed_pending_sync: 'Changed · arrives at next sync', sending: 'Sending',
  failed: 'Not sent', confirming: 'Not on your watch', not_sent: 'Not on your watch',
};

/** "3 × 2 km at threshold" → "3 × 2 km": the widget's room is small; the app has the rest. */
const shortTitle = (title: string) => title.split(' at ')[0];

export function buildSnapshot(input: {
  /** Today as drawn (the queue's state applied), with when it was read. */
  today: { data: TodayVM; fetchedAt: number } | null;
  /** The cached plan's block, for the phase in the week title. Optional. */
  block?: BlockVM | null;
  plus: boolean;
  now: number;
}): WidgetSnapshot {
  const { today, block, plus, now } = input;
  const base: WidgetSnapshot = {
    v: SNAPSHOT_VERSION, writtenAt: now, dataAt: today?.fetchedAt ?? null, updated: null, locked: !plus, message: null,
    readiness: null, session: null, countdown: null, week: null, lock: { circular: null, rectangular: null },
  };
  if (!plus) return { ...base, dataAt: null, message: LOCKED_MESSAGE };
  if (!today) return { ...base, message: EMPTY_MESSAGE };

  const vm = today.data;
  const gap = Math.max(0, daysBetween(vm.date, localDate(now)));
  const r = vm.readiness;
  // Readiness is a word plus a number: no decision, no ring.
  const readiness = r.decision && r.word ? { score: r.garminScore, word: r.word, decision: r.decision } : null;

  const s = vm.session;
  const sessionDay = s ? (s.date === localDate(now) ? 'Today' : gap === 1 && s.date === vm.date ? 'Yesterday' : dayLabel(s.date).split(' ')[0]) : null;
  const session = s ? {
    type: s.type, typeLabel: s.typeLabel, line: `${sessionDay} · ${s.typeLabel}`, title: shortTitle(s.title),
    watch: widgetWatchWords(s.watch.state), watchState: s.watch.state,
  } : null;

  // "London in 148 days", counted down from the day Today was read.
  const m = /^(.+) in (\d+) days?$/.exec(vm.title);
  const countdown = m ? (() => {
    const raceDay = isoOf(parseDay(vm.date) + Number(m[2]) * DAY);
    return { race: m[1], days: Math.max(0, Number(m[2]) - gap), date: dayLabel(raceDay), raceDay };
  })() : null;

  const weekPhrase = vm.kicker.find((k) => /^Week \d+ of \d+$/.test(k.replace(/ /g, ' ')))?.replace(/ /g, ' ') ?? null;
  const phase = block?.phases.find((p) => block.currentWeek >= p.from && block.currentWeek <= p.to)?.name ?? null;
  const week = vm.week.days.length ? {
    title: [weekPhrase, phase].filter(Boolean).join(' · ') || 'This week',
    summary: `This week · ${vm.week.doneKm} of ${vm.week.plannedKm} km`,
    days: vm.week.days.map((d) => ({ letter: d.letter, day: d.day, type: d.type, label: d.label, today: d.date === localDate(now), done: d.status === 'done' || d.status === 'rest_done' })),
  } : null;

  const km = s ? s.meta.split(' · ').find((p) => /\bkm$/.test(p)) : undefined;
  const rectangular = s ? {
    line1: [readiness?.word, `${s.typeLabel} ${shortTitle(s.title)}`].filter(Boolean).join(' · '),
    line2: [LONG_WATCH[s.watch.state], km].filter(Boolean).join(' · '),
  } : readiness ? { line1: readiness.word, line2: 'Rest day' } : null;

  return {
    ...base,
    updated: updatedLabel(today.fetchedAt, now),
    readiness, session, countdown, week,
    lock: { circular: readiness ? { score: readiness.score, word: readiness.word } : null, rectangular },
  };
}

/** Whether two snapshots draw the same (so the widgets aren't reloaded for nothing). */
export function sameDrawing(a: WidgetSnapshot | null, b: WidgetSnapshot): boolean {
  if (!a) return false;
  return JSON.stringify({ ...a, writtenAt: 0 }) === JSON.stringify({ ...b, writtenAt: 0 });
}

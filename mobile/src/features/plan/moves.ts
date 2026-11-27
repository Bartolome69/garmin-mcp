// Moving a session (build prompt, section 5.3). Pure: no React, no network.
//
// A drop is checked on the device at once with the shared back-to-back engine
// (domain/backToBack.ts, the twin of the server's): two hard sessions on
// consecutive days warn before anything changes, with "Move anyway" or "Keep it
// on <day>". A move that passes becomes a proposal, drawn in the week as
// "Waiting for your confirm". Nothing reaches the watch until the confirm sheet
// (milestone 5) sends it; the server checks again before writing.

import { checkMove, type Clash } from '../../domain/backToBack';
import { DAYS, weekday } from '../../domain/format';
import { allSessions, type PlanSession, type PlanVM } from './model';

export type Proposal = { id: string; from: string; to: string };

export type DropResult =
  | { kind: 'none' }
  | { kind: 'refused'; message: string }
  | { kind: 'warn'; proposal: Proposal; title: string; words: string; keepLabel: string }
  | { kind: 'propose'; proposal: Proposal };

const TYPE_WORDS: Record<string, string> = { tempo: 'tempo', intervals: 'intervals', race: 'race', long: 'the long run' };

const dayName = (iso: string) => DAYS[weekday(iso)];
const capital = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

/** "Intervals on Wed and tempo on Thu leave no easy day between." */
export function clashWords(clash: Clash): string {
  const word = (s: { type: string }) => TYPE_WORDS[s.type] ?? s.type;
  return `${capital(word(clash.first))} on ${dayName(clash.first.date)} and ${word(clash.second)} on ${dayName(clash.second.date)} leave no easy day between.`;
}

export function canMove(s: PlanSession, today: string): boolean {
  return !s.done && s.type !== 'rest' && s.date >= today;
}

/** What dropping session `id` on `to` would do. */
export function drop(vm: PlanVM, id: string, to: string): DropResult {
  const sessions = allSessions(vm);
  const s = sessions.find((x) => x.id === id);
  if (!s || s.date === to) return { kind: 'none' };
  if (!canMove(s, vm.today)) return { kind: 'refused', message: s.done ? 'A run you’ve done stays on its day.' : 'This one can’t move.' };
  if (to < vm.today) return { kind: 'refused', message: 'Sessions can’t move into the past.' };
  const proposal = { id, from: s.date, to };
  const clashes = checkMove(sessions.filter((x) => x.type !== 'rest').map((x) => ({ id: x.id, date: x.date, type: x.type, distance_km: x.distanceKm })), id, to);
  if (clashes.length) {
    return { kind: 'warn', proposal, title: 'Back-to-back hard days.', words: clashWords(clashes[0]), keepLabel: `Keep it on ${dayName(s.date)}` };
  }
  return { kind: 'propose', proposal };
}

/** The plan as it would look after `p`: the session on its new day, waiting for the confirm. */
export function withProposal(vm: PlanVM, p: Proposal | null): PlanVM {
  if (!p) return vm;
  const moving = allSessions(vm).find((s) => s.id === p.id);
  if (!moving) return vm;
  const moved: PlanSession = { ...moving, date: p.to, watch: { state: 'confirming', at: null } };
  return {
    ...vm,
    weeks: vm.weeks.map((w) => ({
      ...w,
      days: w.days.map((d) => {
        const kept = d.sessions.filter((s) => s.id !== p.id);
        return d.date === p.to ? { ...d, sessions: [...kept.filter((s) => s.type !== 'rest' || s.done), moved] } : { ...d, sessions: kept };
      }),
    })),
  };
}

/** "Moving tempo from Fri to Thu." */
export function proposalWords(vm: PlanVM, p: Proposal): string {
  const s = allSessions(vm).find((x) => x.id === p.id);
  const what = s ? s.typeLabel.toLowerCase() : 'session';
  return `Moving ${what} from ${dayName(p.from)} to ${dayName(p.to)}.`;
}

/** The days a session could move to this week, for the Move sheet and the accessibility actions. */
export function moveTargets(vm: PlanVM, id: string): { date: string; label: string; disabled: boolean }[] {
  const week = vm.weeks.find((w) => w.days.some((d) => d.sessions.some((s) => s.id === id)));
  if (!week) return [];
  const s = allSessions(vm).find((x) => x.id === id)!;
  return week.days.map((d) => ({
    date: d.date,
    label: `${DAYS[weekday(d.date)]} ${d.day}`,
    disabled: d.date === s.date || d.date < vm.today,
  }));
}

// Where proposals come from.
//
// The ones that need the engines (Life happened, Make it easier, Rest and recover,
// Swap a day, Trim week N) come from the server, made by garmin_mcp/domain/
// proposal.py. TODO(server): POST /app/api/v1/plan/propose. Until it exists, a
// development build reads proposals.fixture.json, which scripts/domain_fixtures.py
// writes from those same engines for the design's persona.
//
// The simple ones the app builds itself, from the plan it already has: moving a
// session, skipping one, sending one, new targets, Remove from watch. No number
// is worked out here: a skip says the session's own kilometres, nothing more.
import { postJson } from '../../api/client';
import { clashes } from '../../domain/backToBack';
import { addDays, DAYS, parseDate, weekday } from '../../domain/format';
import type { PlanSession, PlanVM } from '../plan/model';
import type { DiffSession, OpDraft, Proposal } from './model';
import FIXTURE from './proposals.fixture.json';
import { fixturesOn } from '../../data/sample';

export type Reason = 'unwell' | 'niggle' | 'travelling' | 'race_moved' | 'busy';
export const REASONS: { key: Reason; title: string; sub: string }[] = [
  { key: 'unwell', title: 'Not feeling 100%', sub: 'Cold, tired, run down' },
  { key: 'niggle', title: 'Niggle or injury', sub: 'Something hurts when you run' },
  { key: 'travelling', title: 'Travelling', sub: 'Fewer days, maybe a treadmill' },
  { key: 'race_moved', title: 'Race moved', sub: 'New date or distance' },
  { key: 'busy', title: 'Busy week', sub: 'Fewer days free' },
];
// The prompt's words, as the server's engine has them (garmin_mcp/domain/adjust.py MEDICAL).
export const MEDICAL = "Daash doesn't give medical advice. If you're unwell or hurt, check with a doctor or physio.";

export type ProposalRequest =
  | { kind: 'life'; reason: Reason; since: string }
  | { kind: 'easier' | 'rest' | 'swap' }
  | { kind: 'trim'; week: string };

const CASES = (FIXTURE as unknown as { cases: Record<string, Proposal> }).cases;

// Development only: what the server's Coach would add, one observation per kind, never a number of its own.
const COACH_NOTES: Record<string, string> = {
  unwell: 'Dropping the intensity for a few days is enough here. Next week stays as it was, so the plan to London holds.',
  niggle: 'Shorter, easier runs for five days keep you moving without loading the sore spot. The long run comes back down.',
  travelling: 'The treadmill tempo keeps the session’s purpose without needing a track. The long run stays.',
  busy: 'The long run and one quality session are the ones worth keeping in a short week.',
  easier: 'An easy run today keeps the week’s rhythm. Tomorrow and Sunday stay as planned.',
  rest: 'A rest day counts toward your active week. The weekend stays as planned.',
  swap: 'Strength today and the tempo tomorrow keep both sessions in the week.',
  trim: 'Taking the extra from the easy runs keeps the long run and the intervals as they are.',
};

export function fixtureKey(req: ProposalRequest): string {
  if (req.kind === 'life') return `life:${req.reason}:${req.since}`;
  if (req.kind === 'trim') return `trim:${req.week}`;
  return req.kind;
}

export function fixtureProposal(req: ProposalRequest, coach: boolean): Proposal | null {
  const p = CASES[fixtureKey(req)];
  if (!p) return null;
  return coach ? { ...p, coachNote: COACH_NOTES[p.source] ?? null } : p;
}

export function proposalSource(coach: boolean): (req: ProposalRequest) => Promise<Proposal> {
  return async (req) => {
    try {
      return await postJson<Proposal>(`/app/api/v1/plan/propose${coach ? '?coach=1' : ''}`, req);
    } catch (err) {
      const p = fixturesOn() ? fixtureProposal(req, coach) : null;
      if (p) return p;
      throw err;
    }
  };
}

// -- Built on the device ------------------------------------------------------

const dayLabel = (iso: string) => `${DAYS[weekday(iso)]} ${parseDate(iso).d}`;
const rowLabel = (iso: string) => dayLabel(iso).toUpperCase();

/** What a proposal needs of a session: a plan row, or a session from an earlier proposal. */
type SessionLike = Pick<PlanSession, 'id' | 'date' | 'type' | 'typeLabel' | 'title' | 'sub' | 'distanceKm'>;

export function diffSession(s: SessionLike, date = s.date): DiffSession {
  return { id: s.id, date, type: s.type, typeLabel: s.typeLabel, title: s.title, sub: s.sub, distanceKm: s.distanceKm };
}

const op = (kind: OpDraft['kind'], s: SessionLike, date: string, after: DiffSession | null = null, withWhat: string | null = null): OpDraft => ({
  kind, sessionId: s.id, date, dayLabel: dayLabel(date), what: s.title, withWhat,
  before: kind === 'add' ? null : diffSession(s), after: kind === 'remove' ? null : after ?? diffSession(s, date),
});

/** Moving a session from one day to another: off its old day, onto the new one. */
export function moveProposal(vm: PlanVM, s: PlanSession, to: string, warned = false): Proposal {
  const from = s.date;
  const moved = diffSession(s, to);
  const days = [from, to].sort().map((d) => ({
    date: d, label: rowLabel(d), changed: true,
    before: d === from ? diffSession(s) : null, after: d === to ? moved : null,
  }));
  const sameWeek = vm.weeks.some((w) => w.days.some((d) => d.date === from) && w.days.some((d) => d.date === to));
  return {
    id: `move:${s.id}:${to}`, source: 'move', kicker: `Move · ${dayLabel(from)} to ${dayLabel(to)}`,
    title: '1 session moves',
    subtitle: sameWeek ? `${s.typeLabel} moves from ${DAYS[weekday(from)]} to ${DAYS[weekday(to)]}. This week’s kilometres stay the same.`
      : `${s.typeLabel} moves from ${dayLabel(from)} to ${dayLabel(to)}.`,
    days, load: null,
    rule: warned ? 'Two hard days in a row: you chose to move it anyway.' : 'Checked: no two hard days in a row.',
    disclaimer: null,
    ops: [op('remove', s, from), op('add', s, to, moved)],
  };
}

/** Skipping a session: it comes off the plan and the watch. */
export function skipProposal(s: PlanSession): Proposal {
  return {
    id: `skip:${s.id}`, source: 'skip', kicker: `Skip · ${dayLabel(s.date)}`, title: '1 session comes off',
    subtitle: s.distanceKm ? `${s.distanceKm} km less this week.` : 'The rest of the week stays.',
    days: [{ date: s.date, label: rowLabel(s.date), before: diffSession(s), after: null, changed: true }],
    load: null, rule: 'Skipped days count toward your active week like rest.', disclaimer: null,
    ops: [op('remove', s, s.date)],
  };
}

/** Two sessions trade days. Checked with the shared back-to-back engine. */
export function swapProposal(vm: PlanVM, a: PlanSession, b: PlanSession): Proposal {
  const all = vm.weeks.flatMap((w) => w.days.flatMap((d) => d.sessions)).filter((s) => s.type !== 'rest');
  const after = all.map((s) => ({ id: s.id, type: s.type, distance_km: s.distanceKm,
    date: s.id === a.id ? b.date : s.id === b.id ? a.date : s.date }));
  const clash = clashes(after).some((c) => [c.first.id, c.second.id].some((id) => id === a.id || id === b.id));
  const aThere = diffSession(a, b.date), bHere = diffSession(b, a.date);
  const days = [a.date, b.date].sort().map((d) => ({
    date: d, label: rowLabel(d), changed: true,
    before: diffSession(d === a.date ? a : b), after: d === a.date ? bHere : aThere,
  }));
  return {
    id: `swap:${a.id}:${b.id}`, source: 'swap', kicker: `Swap · ${dayLabel(a.date)} and ${dayLabel(b.date)}`,
    title: '2 sessions change', subtitle: `${a.typeLabel} and ${b.typeLabel.toLowerCase()} swap days. This week’s kilometres stay the same.`,
    days, load: null, disclaimer: null,
    rule: clash ? 'This leaves two hard days in a row. You can still send it.' : 'Checked: no two hard days in a row.',
    ops: [op('remove', a, a.date), op('remove', b, b.date), op('add', a, b.date, aThere), op('add', b, a.date, bHere)]
      .sort((x, y) => (x.date === y.date ? (x.kind === 'remove' ? -1 : 1) : x.date < y.date ? -1 : 1)),
  };
}

/** Sending one session to the watch, or its new targets (Easier / Harder). The plan itself stays. */
export function sendProposal(s: PlanSession, step = 0, targets = 'as planned'): Proposal {
  const adjusted = step !== 0;
  const change = `${Math.abs(step) * 2}% ${step < 0 ? 'easier' : 'harder'}`;
  const what = adjusted ? `${s.title}, ${change}` : s.title;
  const after = { ...diffSession(s), title: what };
  return {
    id: `${adjusted ? 'adjust' : 'send'}:${s.id}:${step}`, source: adjusted ? 'adjust' : 'send',
    kicker: dayLabel(s.date), title: s.title, subtitle: adjusted ? `${targets}, ${change}` : targets,
    days: [], load: null, rule: null, disclaimer: null,
    ops: [adjusted ? op('replace', s, s.date, after, what) : op('add', s, s.date)], changesPlan: false,
  };
}

/** Remove from watch: off the watch, still in the plan, marked Not on your watch. */
export function unwatchProposal(sessions: SessionLike[], batch?: string): Proposal {
  return {
    id: `unwatch:${batch ?? sessions.map((s) => s.id).join(',')}`, source: 'unwatch',
    kicker: '', title: 'Remove from your watch?', subtitle: '', days: [], load: null, rule: null, disclaimer: null,
    ops: sessions.map((s) => op('remove', s, s.date)), changesPlan: false,
  };
}

/** The Life happened sheet's "Since" choices: today, yesterday, or a day this past week. */
export function sinceChoices(today: string): { date: string; label: string }[] {
  return Array.from({ length: 7 }, (_, i) => {
    const d = addDays(today, -i);
    return { date: d, label: i === 0 ? 'Today' : i === 1 ? 'Yesterday' : dayLabel(d) };
  });
}

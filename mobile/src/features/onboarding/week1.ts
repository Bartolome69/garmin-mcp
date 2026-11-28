// "Send week 1 to my watch": one Add per session, each carrying its workout
// (daash.workout.v1), through the same watch confirm as every other write.
import { DAYS, parseDate, weekday } from '../../domain/format';
import type { Proposal } from '../watch/model';
import type { WatchOp } from '../watch/queue';
import type { Landing, Reveal, RevealSession } from './model';

export function weekOneProposal(r: Reveal): Proposal {
  return {
    id: `onboarding:${r.starts}`, source: 'onboarding', kicker: r.kicker, title: 'Week 1', subtitle: '',
    days: [], load: null, rule: null, disclaimer: null,
    // The plan itself is saved with it on the server (POST /app/api/v1/onboarding/plan); the watch gets the week.
    changesPlan: false,
    ops: r.week1.map((s) => {
      const dayLabel = `${DAYS[weekday(s.date)]} ${parseDate(s.date).d}`;
      const after = { id: s.id, date: s.date, type: s.type, typeLabel: s.typeLabel, title: s.title, sub: s.sub, distanceKm: s.distanceKm };
      return { kind: 'add' as const, sessionId: s.id, date: s.date, dayLabel, what: s.title, withWhat: null, before: null, after, workout: s.workout };
    }),
  };
}

const hhmm = (t: number) => { const d = new Date(t); return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`; };

/**
 * Where week 1 is, for the landing screen: its first session's watch state, and once
 * that's on the watch, how many of the others aren't yet. Null: not sent at all.
 */
export function landingFor(week: RevealSession[], opFor: (sessionId: string) => WatchOp | null, device: string | null): Landing | null {
  const first = week[0];
  const op = first ? opFor(first.id) : null;
  if (!op) return null;
  if (op.state === 'on_watch') {
    const rest = week.slice(1).filter((s) => opFor(s.id)?.state !== 'on_watch').length;
    return { state: 'landed', at: hhmm(op.onWatchAt ?? op.updatedAt), device, rest, total: week.length };
  }
  if (op.state === 'failed') {
    return { state: 'failed', error: op.error ?? 'Not sent.', at: hhmm(op.updatedAt), retrying: op.nextAttemptAt != null };
  }
  return op.state === 'changed_pending_sync' ? { state: 'pending' } : { state: 'sending' };
}

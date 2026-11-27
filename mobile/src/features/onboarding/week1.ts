// "Send week 1 to my watch": one Add per session, each carrying its workout
// (daash.workout.v1), through the same watch confirm as every other write.
import { DAYS, parseDate, weekday } from '../../domain/format';
import type { Proposal } from '../watch/model';
import type { Reveal } from './model';

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

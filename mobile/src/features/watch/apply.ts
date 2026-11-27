// Confirmed changes, drawn on Plan and Today before the server's next answer has
// them, and each session's watch status as the queue last left it. Pure.
import type { PlanSession, PlanVM } from '../plan/model';
import type { TodayVM } from '../today/model';
import { changesPlan, type DiffSession, type Proposal } from './model';
import { watchFor, type WatchOp } from './queue';

function toSession(d: DiffSession, was: PlanSession | undefined): PlanSession {
  const same = was && was.type === d.type && was.title === d.title;
  return {
    id: d.id, date: d.date, type: d.type, typeLabel: d.typeLabel, title: d.title, sub: d.sub, distanceKm: d.distanceKm,
    done: false, watch: was?.watch ?? { state: 'not_sent', at: null },
    // The builder's sessions bring their steps; otherwise the steps changed and come back with the server's plan.
    detail: d.detail ?? (same ? was!.detail : null),
  };
}

/** The plan with confirmed changes applied, and every planned session's watch status from the queue. */
export function applyToPlan(vm: PlanVM, applied: Proposal[], ops: WatchOp[]): PlanVM {
  const all = new Map(vm.weeks.flatMap((w) => w.days.flatMap((d) => d.sessions)).map((s) => [s.id, s]));
  let days = new Map(vm.weeks.flatMap((w) => w.days).map((d) => [d.date, [...d.sessions]]));
  for (const p of applied.filter(changesPlan)) {
    for (const o of p.ops) {
      const list = days.get(o.date);
      if (!list) continue;
      if (o.kind === 'remove') days.set(o.date, list.filter((s) => s.id !== o.sessionId));
      else if (o.kind === 'replace' && o.after) days.set(o.date, list.map((s) => (s.id === o.sessionId ? toSession(o.after!, s) : s)));
      else if (o.kind === 'add' && o.after) {
        const added = toSession(o.after, all.get(o.sessionId));
        days.set(o.date, [...list.filter((s) => s.id !== o.sessionId && !(s.type === 'rest' && !s.done)), added]);
      }
    }
  }
  const watched = (s: PlanSession): PlanSession => (s.done || s.type === 'rest' ? s : { ...s, watch: watchFor(ops, s.id, s.watch) });
  return {
    ...vm,
    weeks: vm.weeks.map((w) => ({ ...w, days: w.days.map((d) => ({ ...d, sessions: (days.get(d.date) ?? []).map(watched) })) })),
  };
}

/** Today with confirmed changes to today's session, and its watch status from the queue. */
export function applyToToday(vm: TodayVM, applied: Proposal[], ops: WatchOp[]): TodayVM {
  let session = vm.session;
  for (const p of applied.filter(changesPlan)) {
    for (const o of p.ops.filter((x) => x.date === vm.date)) {
      if (o.kind === 'remove' && session?.id === o.sessionId) session = null;
      else if ((o.kind === 'replace' || o.kind === 'add') && o.after && (o.kind === 'add' || session?.id === o.sessionId)) {
        session = {
          id: o.after.id, date: vm.date, type: o.after.type, typeLabel: o.after.typeLabel, title: o.after.title,
          meta: `Today · ${o.after.sub.split(' · ').slice(1).join(' · ') || o.after.typeLabel}`,
          structure: [{ part: o.after.type === 'easy' || o.after.type === 'recovery' ? 'easy' : 'work', weight: 1 }],
          legend: [o.after.sub],
          watch: { state: 'not_sent', at: null },
        };
      }
    }
  }
  if (!session) return { ...vm, session: null };
  const w = watchFor(ops, session.id, session.watch);
  return { ...vm, session: { ...session, watch: w ?? session.watch } };
}

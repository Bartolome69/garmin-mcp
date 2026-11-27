// The builder's state: the text, the parse, the steps, Easier / Harder, the day and
// Edit steps. The route adds navigation and the watch; tests use it bare.
import { useMemo, useState } from 'react';

import { clashes } from '../../domain/backToBack';
import { weekday } from '../../domain/format';
import { parseWorkout } from '../../domain/parser';
import { allSessions, type PlanSession, type PlanVM } from '../plan/model';
import { rowsText, type EditState } from './EditSteps';
import { adjustLine, build, dayChoices, firstFreeDay, sessionText, stepText, type Built } from './model';

const LONG_DAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];

/** Two hard days in a row on the chosen day: said, never blocked. */
export function ruleFor(vm: PlanVM | null, b: Built | null, date: string, replacing: PlanSession | null): string | null {
  if (!vm || !b) return null;
  const others = allSessions(vm).filter((s) => s.type !== 'rest' && s.id !== replacing?.id)
    .map((s) => ({ id: s.id, date: s.date, type: s.type, distance_km: s.distanceKm }));
  const mine = { id: '__built__', date, type: b.type, distance_km: b.distanceKm };
  const clash = clashes([...others, mine]).find((c) => c.first.id === mine.id || c.second.id === mine.id);
  return clash ? `Two hard days in a row: ${clash.message}. You can still save it.` : null;
}

/**
 * Development only: what the server's Coach would add, from the days around it.
 * TODO(server): the Coach's note for a built session comes from the server with coach_enabled.
 */
export function devCoachNote(vm: PlanVM | null, b: Built | null, date: string): string | null {
  if (!vm || !b) return null;
  const away = (iso: string) => Math.round((Date.parse(date) - Date.parse(iso)) / 864e5);
  const near = allSessions(vm).filter((s) => !s.done && s.type !== 'rest' && s.date !== date && Math.abs(away(s.date)) <= 3);
  const before = near.filter((s) => s.date < date && (s.type === 'intervals' || s.type === 'tempo')).pop();
  const after = near.find((s) => s.date > date && s.type === 'long');
  const bits = [
    before ? `${LONG_DAYS[weekday(before.date)]}'s ${before.typeLabel.toLowerCase()} is ${away(before.date)} days earlier` : null,
    after ? `${LONG_DAYS[weekday(after.date)]}'s long run stays easy` : null,
  ].filter(Boolean);
  return [`Fits ${LONG_DAYS[weekday(date)]}.`, bits.length ? `${bits.join(' and ')}.` : ''].join(' ').trim();
}

/** The text Edit opens with: the session's steps, or its title when that reads. */
export function initialText(replacing: PlanSession | null): string {
  if (!replacing) return '';
  return sessionText(replacing.detail) ?? (parseWorkout(replacing.title).ok ? replacing.title : '');
}

export function useBuilder({ vm, today, replacing = null, initial }: { vm: PlanVM | null; today: string; replacing?: PlanSession | null; initial?: string }) {
  const days = useMemo(() => dayChoices(vm, today), [vm, today]);
  const [text, setText] = useState(() => initial ?? initialText(replacing));
  const [add, setAdd] = useState({ warmup: true, cooldown: true });
  const [step, setStep] = useState(0);
  const [day, setDay] = useState(() => replacing?.date ?? firstFreeDay(days));
  const [edit, setEdit] = useState<EditState | null>(null);
  const basis = vm?.basis ?? null;

  const parsed = useMemo(() => parseWorkout(text, { add }), [text, add]);
  const built = useMemo(() => build(parsed, { basis, step, date: day, keepType: replacing?.type }), [parsed, basis, step, day, replacing?.type]);

  return {
    built,
    view: {
      text, onText: setText, parsed, built, basis, step, onStep: setStep,
      adjust: adjustLine(basis, built?.zone ?? null, step),
      rule: ruleFor(vm, built, day, replacing),
      days, day, onDay: setDay,
      edit,
      onEditOpen: () => setEdit({ rows: parsed.steps.filter((s) => !('added' in s && s.added)).map(stepText), add }),
      onEdit: (e: EditState) => { setEdit(e); setText(rowsText(e.rows)); setAdd(e.add); },
      onEditDone: () => setEdit(null),
      editing: !!replacing,
    },
    day,
  };
}

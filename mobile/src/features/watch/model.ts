// A proposed change to the plan, and the watch operations it sends (build prompt,
// sections 5.4 and 5.8). The server proposes the changes that need the engines
// (Life happened, Make it easier, Rest, Swap, Trim: garmin_mcp/domain/proposal.py
// makes exactly this shape); the app builds the simple ones itself (a move, a
// skip, sending one session, Remove from watch). Every one goes through the same
// diff and the same watch confirm, and nothing reaches the watch without it.

import type { SessionType } from '../today/model';

export type OpKind = 'add' | 'replace' | 'remove';

export type DiffSession = {
  id: string;
  date: string;
  type: SessionType;
  typeLabel: string;
  title: string;
  sub: string;                   // "Easy · 6 km"
  distanceKm: number | null;
};

export type DiffDay = {
  date: string;
  label: string;                 // "FRI 27"
  before: DiffSession | null;    // null: a rest day
  after: DiffSession | null;
  changed: boolean;
};

export type LoadPair = {
  label: string;                 // "This week", "Next week", "Week 6"
  before: number;                // km
  after: number;
  changed: boolean;
  low: number | null;            // the target band, from the week as planned
  high: number | null;
};

/** One line of the watch confirm, and one watch operation once confirmed. */
export type OpDraft = {
  kind: OpKind;
  sessionId: string;
  date: string;
  dayLabel: string;              // "Fri 27"
  what: string;                  // the session it acts on, as titled
  withWhat: string | null;       // a replace: what it becomes
  before: DiffSession | null;
  after: DiffSession | null;
};

export type Proposal = {
  id: string;
  source: string;                // unwell, niggle, travelling, busy, race_moved, easier, rest, swap, trim, move, skip, send, adjust, unwatch
  kicker: string;                // "Not feeling 100% · since today"
  title: string;                 // "3 sessions change"
  subtitle: string;              // "54 → 37 km this week. Next week unchanged."
  days: DiffDay[];
  load: LoadPair[] | null;
  rule: string | null;           // shown with the Coach off
  disclaimer: string | null;     // the medical line, for unwell and niggle
  ops: OpDraft[];
  /** Coach on only: one observation in place of the rule. Never a cause, never a number of its own. */
  coachNote?: string | null;
  /** false for watch-only changes (send, new targets, Remove from watch): the plan itself stays. */
  changesPlan?: boolean;
};

export const VERB: Record<OpKind, string> = { add: 'Add', replace: 'Replace', remove: 'Remove' };

/** "Replace · Sat 28 · Full body (runner) with Easy 5 km" */
export function opWords(o: Pick<OpDraft, 'kind' | 'dayLabel' | 'what' | 'withWhat'>): string {
  return `${VERB[o.kind]} · ${o.dayLabel} · ${o.what}${o.withWhat ? ` with ${o.withWhat}` : ''}`;
}

export function changesPlan(p: Proposal): boolean {
  return p.changesPlan ?? !['send', 'adjust', 'unwatch'].includes(p.source);
}

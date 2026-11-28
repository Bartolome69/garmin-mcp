// The Coach's thread (build prompt, sections 5.10 and 16): one continuous
// conversation, no "New chat". Messages from Today, a run or a session carry a
// context tag and land in the same thread; older days collapse; replies carry
// source chips and, where the server sends one, a chart drawn by the app from the
// server's numbers. Plan changes are proposals that open the shared diff and the
// watch confirm. Pure: no React, no network, no clock of its own.
import { guardKinds, type GuardKind } from '../../domain/replyGuard';
import { DAYS, parseDate, weekday } from '../../domain/format';
import type { Proposal } from '../watch/model';
import type { ProposalRequest } from '../watch/proposals';

export type Role = 'user' | 'assistant' | 'action' | 'memory';
export type MessageStatus =
  | 'sending' | 'sent' | 'not_sent'                         // the runner's message
  | 'streaming' | 'done' | 'partial' | 'failed'             // a reply
  | 'pending' | 'running' | 'declined' | 'expired';         // a proposed change (done/failed too)

export type ContextKind = 'today' | 'readiness' | 'run' | 'session' | 'proposal';
/** Where a question was asked from: "About · Wed 25 · 6 × 800 m", "From Today · Back off". */
export type ContextTag = { kind: ContextKind; id: string | null; label: string };

/**
 * A chart in a reply. Built by the server from the numbers its tools returned (never
 * from the model's words), and drawn by the app; the summary is what a screen reader says.
 */
export type InlineChart = {
  kind: 'line' | 'bars';
  title: string;                 // "HRV, last 7 nights"
  unit: string;                  // "ms"
  caption: string | null;        // "range 54–66"
  labels: string[];              // one per value: "Sat", "Rep 1"
  values: (number | null)[];
  band: [number, number] | null; // the runner's own range, shaded
  highlight: number[];           // indexes drawn as marked points (also said in the summary)
  summary: string;
};

export type CoachMessage = {
  id: string;
  role: Role;
  text: string;
  status: MessageStatus;
  /**
   * When it was written (ms). null: not known. The pilot sends no time, so a message
   * this phone didn't write or see written has none; it collapses into "Earlier" and
   * never counts toward today's limit (never "now", which would put a whole web thread
   * under Today and use up the free messages on first open).
   */
  at: number | null;
  context?: ContextTag | null;
  sources?: string[];
  chart?: InlineChart | null;
  feedback?: 'up' | 'down' | null;
  /** A proposed change: the tool the server stored, and how to open the shared diff. */
  tool?: string | null;
  proposal?: Proposal | null;
  request?: ProposalRequest | null;
  /** The proposal the card staged on the phone, followed through the watch queue by its id. */
  proposalId?: string | null;
  /** A reply that failed or was cut off: what the server said, in words. */
  error?: string | null;
  /** While a reply is being written: what the Coach is reading ("Checking readiness"). */
  working?: string | null;
  /** The phone's second-line check on a finished reply (domain/replyGuard). */
  flags?: GuardKind[];
  /** "Saved to memory": the memory item it made. */
  memoryId?: string | null;
};

/** A message as the server's GET /app/api/chat sends it (chat_pilot._view), plus what the v1 adds. */
export type ServerMessage = {
  id: number | string;
  role: string;
  text: string;
  status: string;
  feedback?: 'up' | 'down';
  tool?: string;
  // TODO(server): the v1 thread adds these; the pilot sends none of them yet.
  at?: number;                   // seconds
  context?: ContextTag | null;
  sources?: string[];
  chart?: InlineChart | null;
  proposal?: Proposal | null;
  request?: ProposalRequest | null;
};

const ROLES: Role[] = ['user', 'assistant', 'action', 'memory'];

/** The server's thread over what the phone has: the server's words and statuses win, the phone keeps what only it knows. */
export function mergeThread(local: CoachMessage[], server: ServerMessage[]): CoachMessage[] {
  const byId = new Map(local.map((m) => [m.id, m]));
  const fromServer = server.filter((s) => ROLES.includes(s.role as Role)).map((s): CoachMessage => {
    const id = String(s.id);
    const mine = byId.get(id);
    const role = s.role as Role;
    const text = s.text ?? '';
    return {
      ...mine,
      id, role, text,
      status: (s.status as MessageStatus) ?? 'done',
      at: s.at != null ? s.at * 1000 : mine?.at ?? null,
      feedback: s.feedback ?? mine?.feedback ?? null,
      tool: s.tool ?? mine?.tool ?? null,
      context: s.context ?? mine?.context ?? null,
      sources: s.sources ?? mine?.sources ?? [],
      chart: s.chart ?? mine?.chart ?? null,
      proposal: s.proposal ?? mine?.proposal ?? null,
      request: s.request ?? mine?.request ?? null,
      working: null,
      // The phone's second-line check, on every reply it shows.
      flags: role === 'assistant' ? guardKinds(text) : undefined,
    };
  });
  const seen = new Set(fromServer.map((m) => m.id));
  // Kept from the phone: memory notes, and messages that never reached the server.
  const onlyHere = local.filter((m) => !seen.has(m.id) && (m.role === 'memory' || m.status === 'not_sent'));
  // The server's order stands (its messages may have no time); each phone-only message goes
  // before the first message written after it, else at the end.
  const out = [...fromServer];
  for (const m of onlyHere) {
    const j = m.at == null ? -1 : out.findIndex((x) => x.at != null && x.at > (m.at as number));
    if (j < 0) out.push(m);
    else out.splice(j, 0, m);
  }
  return out;
}

// -- retention, days, the collapsed past ------------------------------------------------

export const RETAIN_DAYS = 14;
const DAY_MS = 86_400_000;

/**
 * The phone keeps the last 14 days of the thread (build prompt, section 16's default); memory carries the rest.
 * A message with no time can't be judged here: it stays while the server still sends it (TODO(server): `at`).
 */
export function retain(messages: CoachMessage[], now: number, days = RETAIN_DAYS): CoachMessage[] {
  return messages.filter((m) => m.at == null || m.at >= now - days * DAY_MS);
}

/** Written before today, or at a time the phone doesn't know. */
const isEarlier = (m: CoachMessage, today: string) => m.at == null || dayOf(m.at) < today;

/** Local calendar day, as YYYY-MM-DD. */
export function dayOf(ms: number): string {
  const d = new Date(ms);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

export const hhmm = (ms: number) => {
  const d = new Date(ms);
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
};

const dayWords = (ms: number) => { const iso = dayOf(ms); return `${DAYS[weekday(iso)]} ${parseDate(iso).d}`; };

export type ThreadRow =
  | { kind: 'earlier'; count: number; label: string }
  | { kind: 'day'; label: string }
  | { kind: 'message'; m: CoachMessage };

/**
 * The thread as drawn: days before today collapse into "Earlier · 14 messages since
 * Mon · Show"; today's (or every day's, once shown) open with a separator.
 */
export function threadRows(messages: CoachMessage[], now: number, showEarlier: boolean): ThreadRow[] {
  const today = dayOf(now);
  const earlier = messages.filter((m) => isEarlier(m, today));
  const rows: ThreadRow[] = [];
  if (earlier.length && !showEarlier) {
    const first = earlier.find((m) => m.at != null);
    // "since Mon" only when every earlier message has a time: an undated one may be older.
    const since = first?.at != null && earlier.every((m) => m.at != null) ? ` since ${DAYS[weekday(dayOf(first.at))]}` : '';
    rows.push({ kind: 'earlier', count: earlier.length, label: `Earlier · ${earlier.length} message${earlier.length === 1 ? '' : 's'}${since}` });
  }
  let day: string | null = null;
  for (const m of messages) {
    if (!showEarlier && isEarlier(m, today)) continue;
    const d = m.at == null ? '' : dayOf(m.at);
    if (d !== day) {
      day = d;
      rows.push({ kind: 'day', label: m.at == null ? 'Earlier' : `${d === today ? 'Today' : dayWords(m.at)} · ${hhmm(m.at)}` });
    }
    rows.push({ kind: 'message', m });
  }
  return rows;
}

// -- the daily limit ------------------------------------------------------------------------

/** Messages in the thread known to be sent today (one that never reached the server, or with no time, doesn't count). */
export function sentToday(messages: CoachMessage[], now: number): number {
  const today = dayOf(now);
  return messages.filter((m) => m.role === 'user' && m.status !== 'not_sent' && m.at != null && dayOf(m.at) === today).length;
}

/**
 * Today's sends from this phone, counted apart from the thread (kv), so clearing the
 * conversation doesn't hand back the free messages. Cleared only by signing out.
 */
export type SendCount = { day: string; count: number };

/** One more send counted (the server stored the question: its `start` event). */
export function countSend(c: SendCount | null, now: number): SendCount {
  const day = dayOf(now);
  return { day, count: c && c.day === day ? c.count + 1 : 1 };
}

export const countToday = (c: SendCount | null, now: number) => (c && c.day === dayOf(now) ? c.count : 0);

export type Limit = { limit: number | null; remaining: number | null; reached: boolean };

/**
 * Free: the daily limit (10 by default, from the server's flags); Plus: none. Used today is
 * the larger of this phone's own count and the thread's dated messages from today (which,
 * once the server sends `at`, include another device's). The server's 429 also counts.
 */
export function limitFor({ plus, dailyLimit, messages, now, capHit, counted = null }: {
  plus: boolean; dailyLimit: number; messages: CoachMessage[]; now: number; capHit: boolean; counted?: SendCount | null;
}): Limit {
  if (plus) return { limit: null, remaining: null, reached: false };
  const used = Math.max(sentToday(messages, now), countToday(counted, now));
  const remaining = Math.max(0, dailyLimit - used);
  return { limit: dailyLimit, remaining: capHit ? 0 : remaining, reached: capHit || remaining === 0 };
}

// -- search and filter ----------------------------------------------------------------------

/** Messages whose words contain the query (any case), in order. */
export function searchThread(messages: CoachMessage[], query: string): CoachMessage[] {
  const q = query.trim().toLowerCase();
  if (!q) return messages;
  return messages.filter((m) => m.text.toLowerCase().includes(q));
}

export const tagKey = (t: ContextTag) => `${t.kind}:${t.id ?? t.label}`;

/** The runs and sessions questions were asked about, newest first, each once. */
export function tagsOf(messages: CoachMessage[]): ContextTag[] {
  const out = new Map<string, ContextTag>();
  for (const m of [...messages].reverse()) {
    if (m.context && (m.context.kind === 'run' || m.context.kind === 'session') && !out.has(tagKey(m.context))) out.set(tagKey(m.context), m.context);
  }
  return [...out.values()];
}

/** The exchanges about one run or session: each tagged question and what followed it, up to the next question. */
export function filterByTag(messages: CoachMessage[], key: string): CoachMessage[] {
  const out: CoachMessage[] = [];
  let on = false;
  for (const m of messages) {
    if (m.role === 'user') on = !!m.context && tagKey(m.context) === key;
    if (on) out.push(m);
  }
  return out;
}

// -- sources -----------------------------------------------------------------------------

/** What each of the server's tools reads, as a source chip (the pilot streams the tool names). */
export const TOOL_SOURCES: Record<string, string> = {
  get_plan: 'Plan · Daash', get_session: 'Plan · Daash', get_activities: 'Runs · Garmin',
  get_activity_details: 'Run · Garmin', get_readiness: 'Readiness · Garmin', get_sleep_data: 'Sleep · Garmin',
  get_daily_summary: 'Days · Garmin', get_workout: 'Workouts · Garmin', list_workouts: 'Workouts · Garmin',
  find_exercises: 'Exercises · Daash', get_fitness: 'Fitness · Garmin',
};

export function addSource(sources: string[] | undefined, tool: string): string[] {
  const chip = TOOL_SOURCES[tool];
  const list = sources ?? [];
  return chip && !list.includes(chip) ? [...list, chip] : list;
}

// -- finishing a proposal in Plan ----------------------------------------------------------

/**
 * Where "Do it in Plan" goes for a proposal the phone can't stage (the pilot's `pending`
 * carries only the tool's name and a summary), and what to do there. TODO(server): the v1's
 * `pending` carries `request` or `proposal`, and the card opens the shared diff instead.
 */
export function inPlanFor(tool: string | null | undefined): { href: '/plan' | '/plan/builder'; label: string; hint: string } {
  if (tool === 'create_workout' || tool === 'create_strength_workout' || tool === 'update_workout') {
    return { href: '/plan/builder', label: 'Build it in Plan', hint: 'Build this workout in Plan, with the change above. It goes through the same review there.' };
  }
  if (tool === 'move_session' || tool === 'schedule_workout' || tool === 'unschedule_workout' || tool === 'delete_workout') {
    return { href: '/plan', label: 'Do it in Plan', hint: 'In Plan, open the session above and use Move, Swap or Skip. It goes through the same review there.' };
  }
  return { href: '/plan', label: 'Do it in Plan', hint: 'Finish this in Plan: it goes through the same review there.' };
}

// -- context tags from the entry points ----------------------------------------------------

/** The tag a question carries when the Coach opens from somewhere: /coach?run=…&label=…, ?about=readiness, ?session=…. */
export function contextFrom(params: { about?: string; run?: string; session?: string; label?: string }): ContextTag | null {
  const label = params.label?.trim();
  if (params.run) return { kind: 'run', id: params.run, label: `About · ${label || 'this run'}` };
  if (params.session) return { kind: 'session', id: params.session, label: `About · ${label || 'this session'}` };
  if (params.about === 'readiness') return { kind: 'readiness', id: null, label: `From Today${label ? ` · ${label}` : ''}` };
  if (params.about === 'today') return { kind: 'today', id: null, label: 'From Today' };
  if (params.about) return { kind: 'proposal', id: params.about, label: `About · ${label || 'this change'}` };
  return null;
}

// The Coach's reply as it streams (POST /app/api/chat/send, server-sent events; see
// chat_pilot.py on main): the frames parsed from the bytes as they arrive, the
// events they carry, and what each does to the thread. Pure, so the whole stream is
// tested without a network.
//
//   start   {user_id, id}             the server stored the question and opened the reply
//   delta   {text}                    more of the reply
//   tool    {name, label}             the Coach is reading something (a source chip)
//   pending {id, tool, text, status}  a proposed change, stored, waiting for the runner
//   memory  {id, kind, text}          TODO(server): saved to memory from this message
//   chart   {chart}                   TODO(server): a chart built from a tool's numbers
//   done    {id, status}              finished (status "partial": cut off)
//   error   {code, message}           the turn failed; what was written stays
import { guardKinds } from '../../domain/coachGuard';
import type { Proposal } from '../watch/model';
import type { ProposalRequest } from '../watch/proposals';
import { addSource, type CoachMessage, type InlineChart } from './model';

export type SseFrame = { event: string; data: string };

/** Frames from a stream fed in arbitrary pieces: a frame ends at a blank line; comments are skipped. */
export function createSseParser() {
  let buffer = '';
  let event = '';
  let data: string[] = [];
  const take = (out: SseFrame[]) => {
    if (data.length) out.push({ event: event || 'message', data: data.join('\n') });
    event = '';
    data = [];
  };
  const line = (l: string, out: SseFrame[]) => {
    if (l === '') take(out);
    else if (l.startsWith(':')) return;
    else {
      const colon = l.indexOf(':');
      const field = colon < 0 ? l : l.slice(0, colon);
      const value = colon < 0 ? '' : l.slice(colon + 1).replace(/^ /, '');
      if (field === 'event') event = value;
      else if (field === 'data') data.push(value);
    }
  };
  return {
    feed(chunk: string): SseFrame[] {
      const out: SseFrame[] = [];
      buffer += chunk;
      let nl = buffer.search(/\r\n|\r|\n/);
      while (nl >= 0) {
        const eol = buffer.startsWith('\r\n', nl) ? 2 : 1;
        // A lone \r at the very end may be the first half of \r\n: wait for more.
        if (buffer[nl] === '\r' && eol === 1 && nl === buffer.length - 1) break;
        line(buffer.slice(0, nl), out);
        buffer = buffer.slice(nl + eol);
        nl = buffer.search(/\r\n|\r|\n/);
      }
      return out;
    },
    /** The stream closed: a last frame without its blank line still counts. */
    end(): SseFrame[] {
      const out: SseFrame[] = [];
      if (buffer) { line(buffer.replace(/\r$/, ''), out); buffer = ''; }
      take(out);
      return out;
    },
  };
}

/**
 * The app's words for the pilot's error codes. The pilot says "The AI service isn't
 * answering"; the app says "AI" only in the Coach's name and settings (section 12).
 */
export const ERROR_WORDS: Record<string, string> = {
  timeout: 'That took too long. Try again?',
  provider: 'The Coach isn’t answering right now. Try again in a minute.',
  config: 'The Coach isn’t set up yet.',
  bad_request: 'The Coach couldn’t take that message. Try rewording it.',
  internal: 'Something went wrong on our side. Try again.',
};

export type CoachEvent =
  | { type: 'start'; userId: string; id: string }
  | { type: 'delta'; text: string }
  | { type: 'tool'; name: string; label: string }
  | { type: 'pending'; id: string; tool: string; text: string; status: string; proposal: Proposal | null; request: ProposalRequest | null }
  | { type: 'memory'; id: string; kind: string; text: string }
  | { type: 'chart'; chart: InlineChart }
  | { type: 'done'; id: string; status: 'done' | 'partial' }
  | { type: 'error'; code: string; message: string };

/** A frame as an event; null for anything unknown or unreadable (skipped, never fatal). */
export function toEvent(f: SseFrame): CoachEvent | null {
  let d: Record<string, unknown>;
  try { d = JSON.parse(f.data) as Record<string, unknown>; } catch { return null; }
  if (!d || typeof d !== 'object') return null;
  const s = (v: unknown) => (v == null ? '' : String(v));
  switch (f.event) {
    case 'start': return { type: 'start', userId: s(d.user_id), id: s(d.id) };
    case 'delta': return { type: 'delta', text: s(d.text) };
    case 'tool': return { type: 'tool', name: s(d.name), label: s(d.label) };
    case 'pending': return { type: 'pending', id: s(d.id), tool: s(d.tool), text: s(d.text), status: s(d.status) || 'pending',
      proposal: (d.proposal as Proposal | undefined) ?? null, request: (d.request as ProposalRequest | undefined) ?? null };
    case 'memory': return { type: 'memory', id: s(d.id), kind: s(d.kind), text: s(d.text) };
    case 'chart': return d.chart ? { type: 'chart', chart: d.chart as InlineChart } : null;
    case 'done': return { type: 'done', id: s(d.id), status: d.status === 'partial' ? 'partial' : 'done' };
    case 'error': {
      const code = s(d.code) || 'internal';
      return { type: 'error', code, message: ERROR_WORDS[code] ?? ERROR_WORDS.internal };
    }
    default: return null;
  }
}

/** One turn as it streams: the runner's message and the reply, by the ids the phone gave them. */
export type Turn = { userId: string; replyId: string };

/**
 * What an event does to the thread. The reply's words are never changed here; a
 * finished reply gets the phone's guard check (flags), which only marks it.
 */
export function applyEvent(thread: CoachMessage[], turn: Turn, e: CoachEvent, now: number): { thread: CoachMessage[]; turn: Turn } {
  const patch = (id: string, f: (m: CoachMessage) => CoachMessage) => thread.map((m) => (m.id === id ? f(m) : m));
  switch (e.type) {
    case 'start': {
      const next = { userId: e.userId || turn.userId, replyId: e.id || turn.replyId };
      return { turn: next, thread: thread.map((m) => (m.id === turn.userId ? { ...m, id: next.userId, status: 'sent' }
        : m.id === turn.replyId ? { ...m, id: next.replyId } : m)) };
    }
    case 'delta':
      return { turn, thread: patch(turn.replyId, (m) => ({ ...m, text: m.text + e.text, working: null })) };
    case 'tool':
      return { turn, thread: patch(turn.replyId, (m) => ({ ...m, working: e.label || 'Reading your data', sources: addSource(m.sources, e.name) })) };
    case 'chart':
      return { turn, thread: patch(turn.replyId, (m) => ({ ...m, chart: e.chart })) };
    case 'pending': {
      const card: CoachMessage = { id: e.id, role: 'action', text: e.text, status: e.status as CoachMessage['status'], at: now,
        tool: e.tool, proposal: e.proposal, request: e.request };
      return { turn, thread: [...thread.filter((m) => m.id !== e.id), card] };
    }
    case 'memory': {
      const note: CoachMessage = { id: `memory:${e.id}`, role: 'memory', text: e.text, status: 'done', at: now, memoryId: e.id };
      return { turn, thread: [...thread, note] };
    }
    case 'done':
      return { turn, thread: patch(turn.replyId, (m) => {
        const text = m.text.trim();
        return { ...m, id: e.id || m.id, text, status: e.status, working: null, flags: guardKinds(text) };
      }) };
    case 'error':
      return { turn, thread: patch(turn.replyId, (m) => ({ ...m, status: m.text ? 'partial' : 'failed', working: null, error: e.message,
        flags: guardKinds(m.text) })) };
  }
}

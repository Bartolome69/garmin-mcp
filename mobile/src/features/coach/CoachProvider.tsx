// The Coach's state (build prompt, section 5.10): the one thread, cached on the phone
// so it opens offline; sending, with the reply streaming in; the daily limit; the
// context a question carries from Today, a run or a session; search and filter.
// Lives over the tabs, so the thread and a reply being written survive switching tabs.
// Does nothing while coach_enabled is off: no request, nothing drawn.
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';

import { Offline, TimedOut } from '../../api/client';
import { useDb } from '../../db/DbContext';
import { guardKinds } from '../../domain/replyGuard';
import { kv } from '../../db/kv';
import { failureKind, type FailureKind } from '../../data/resource';
import { useFlags } from '../../flags/FlagsProvider';
import { useEntitlement } from '../plus/EntitlementsProvider';
import { CoachLimitReached, CoachUnavailable, type CoachApi } from './api';
import { useMemory } from './MemoryProvider';
import type { MemoryKind } from './memory';
import { countSend, dayOf, limitFor, mergeThread, retain, type CoachMessage, type ContextTag, type Limit, type SendCount } from './model';
import { CoachApiContext, defaultCoachApi } from './source';
import { applyEvent, ERROR_WORDS, type Turn } from './sse';

export const THREAD_KEY = 'coach:thread';
/** Today's sends from this phone, apart from the thread: Clear conversation keeps it; signing out clears it (kv). */
export const SENT_KEY = 'coach:sent';
type Cached = { messages: CoachMessage[]; starters: string[] };

export type CoachContext = {
  messages: CoachMessage[];
  starters: string[];
  loaded: boolean;
  /** Why the last request failed: offline shows the banner and turns sending off. */
  failure: FailureKind | null;
  error: string | null;
  streaming: boolean;
  limit: Limit;
  /** The tag the next message carries ("About · Wed 25 · 6 × 800 m"), until sent or removed. */
  context: ContextTag | null;
  setContext: (c: ContextTag | null) => void;
  refresh: () => Promise<void>;
  /** One try; a failure leaves the message marked "Not sent" with "Send again". */
  send: (text: string, opts?: { starter?: boolean; context?: ContextTag | null }) => Promise<void>;
  sendAgain: (id: string) => Promise<void>;
  decline: (id: string) => Promise<void>;
  feedback: (id: string, value: 'up' | 'down') => Promise<void>;
  /** Clears the thread once the server has; false (and the thread kept) if it couldn't. */
  clear: () => Promise<boolean>;
  /** A proposal card's staged proposal, so the card can follow it through the watch queue. */
  linkProposal: (messageId: string, proposalId: string) => void;
  searching: boolean;
  setSearching: (v: boolean) => void;
  query: string;
  setQuery: (q: string) => void;
  filter: ContextTag | null;
  setFilter: (t: ContextTag | null) => void;
  showEarlier: boolean;
  setShowEarlier: (v: boolean) => void;
};

const Ctx = createContext<CoachContext | null>(null);

export function CoachProvider({ children, clock = Date.now }: { children: ReactNode; clock?: () => number }) {
  const given = useContext(CoachApiContext);
  const api: CoachApi = useMemo(() => given ?? defaultCoachApi(), [given]);
  const db = useDb();
  const memory = useMemory();
  const { flags } = useFlags();
  const entitlement = useEntitlement();
  const on = flags.coach_enabled;

  const [messages, setMessages] = useState<CoachMessage[]>([]);
  const ref = useRef<CoachMessage[]>([]);
  const [starters, setStarters] = useState<string[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [failure, setFailure] = useState<FailureKind | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [streaming, setStreaming] = useState(false);
  const [capHit, setCapHit] = useState<string | null>(null);
  const [counted, setCounted] = useState<SendCount | null>(null);
  const countedRef = useRef<SendCount | null>(null);
  const [context, setContext] = useState<ContextTag | null>(null);
  const [query, setQuery] = useState('');
  const [searching, setSearching] = useState(false);
  const [filter, setFilter] = useState<ContextTag | null>(null);
  const [showEarlier, setShowEarlier] = useState(false);
  const local = useRef(0);
  const abort = useRef<AbortController | null>(null);

  const commit = useCallback((next: CoachMessage[], persist = true) => {
    ref.current = next;
    setMessages(next);
    if (persist && db) kv(db).set(THREAD_KEY, { messages: next, starters } satisfies Cached).catch(() => {});
  }, [db, starters]);

  const refresh = useCallback(async () => {
    try {
      const answer = await api.thread();
      setFailure(null);
      setError(null);
      if (answer.starters?.length) setStarters(answer.starters);
      const merged = retain(mergeThread(ref.current, answer.messages ?? []), clock());
      ref.current = merged;
      setMessages(merged);
      if (db) await kv(db).set(THREAD_KEY, { messages: merged, starters: answer.starters ?? [] } satisfies Cached).catch(() => {});
    } catch (err) {
      setFailure(err instanceof CoachUnavailable ? 'server' : failureKind(err));
      setError(err instanceof Error ? err.message : 'Couldn’t reach Daash');
    } finally {
      setLoaded(true);
    }
  }, [api, db, clock]);

  // The cached thread first, then the server's; only with the Coach on.
  useEffect(() => {
    if (!on) return;
    let live = true;
    (async () => {
      const sent = db ? await kv(db).get<SendCount>(SENT_KEY).catch(() => null) : null;
      if (live && sent) {
        const mine = countedRef.current;
        const next = mine && mine.day === sent.day ? { day: sent.day, count: Math.max(mine.count, sent.count) } : mine ?? sent;
        countedRef.current = next;
        setCounted(next);
      }
      const cached = db ? await kv(db).get<Cached>(THREAD_KEY).catch(() => null) : null;
      if (live && cached && !ref.current.length) {
        const kept = retain(cached.messages ?? [], clock());
        ref.current = kept;
        setMessages(kept);
        setStarters(cached.starters ?? []);
        setLoaded(true);
      }
      if (live) await refresh();
    })();
    return () => { live = false; };
  }, [on, db, refresh]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => () => abort.current?.abort(), []);

  // Plus or a running free week: no daily limit (build prompt, sections 1 and 5.10).
  // Until the cached entitlement is read none is applied either, so a Plus runner never
  // sees the limit flash up; the server's 429 still stops a free runner's eleventh.
  const limit = limitFor({ plus: entitlement.plus || !entitlement.ready, dailyLimit: flags.coach_daily_limit, messages, now: clock(), capHit: capHit === dayOf(clock()), counted });

  /** The server stored a question from this phone: one more of today's messages, kept through Clear conversation. */
  const countOne = useCallback(() => {
    const next = countSend(countedRef.current, clock());
    countedRef.current = next;
    setCounted(next);
    if (db) kv(db).set(SENT_KEY, next).catch(() => {});
  }, [clock, db]);

  const send = useCallback(async (text: string, opts?: { starter?: boolean; context?: ContextTag | null }) => {
    const words = text.trim();
    if (!words || streaming || limit.reached || failure === 'offline') return;
    const tag = opts && 'context' in opts ? opts.context ?? null : context;
    const now = clock();
    const n = local.current++;
    let turn: Turn = { userId: `local:${n}`, replyId: `local:${n}:reply` };
    commit([...ref.current,
      { id: turn.userId, role: 'user', text: words, status: 'sending', at: now, context: tag },
      { id: turn.replyId, role: 'assistant', text: '', status: 'streaming', at: now, sources: [], working: 'Reading your data' }], false);
    setContext(null);
    setStreaming(true);
    const controller = new AbortController();
    abort.current = controller;
    let started = false;
    try {
      await api.send({ text: words, starter: !!opts?.starter, context: tag }, (e) => {
        const r = applyEvent(ref.current, turn, e, clock());
        turn = r.turn;
        commit(r.thread, false);
        if (e.type === 'start') { started = true; countOne(); }
        if (e.type === 'memory') memory.add((e.kind as MemoryKind) || 'note', e.text, 'chat', e.id).catch(() => {});
      }, controller.signal);
      // A stream that ended without "done" or "error": what was written stays, marked cut off.
      commit(ref.current.map((m) => (m.id === turn.replyId && m.status === 'streaming'
        ? { ...m, status: m.text ? 'partial' : 'failed', working: null, error: m.text ? null : 'The reply didn’t arrive. Try again.' } : m)));
      setFailure(null);
    } catch (err) {
      const kind = failureKind(err);
      if (started) {
        // The server has the question (its `start` arrived), so it was sent; the stream broke after.
        // What was written stays, marked cut off, with Try again, as the server's own `error` event does.
        const why = err instanceof TimedOut ? ERROR_WORDS.timeout
          : kind === 'offline' ? 'The connection dropped before the reply finished. Try again.'
            : 'The reply stopped before it finished. Try again.';
        commit(ref.current.map((m) => (m.id === turn.replyId && m.status === 'streaming'
          ? { ...m, status: m.text ? 'partial' : 'failed', working: null, error: why, text: m.text.trim(), flags: guardKinds(m.text) }
          : m)));
        if (err instanceof Offline) setFailure('offline');
      } else {
        // Nothing reached the Coach (or it said no): the question stays, marked, with Send again. No retry here.
        const why = err instanceof CoachLimitReached ? err.message : kind === 'offline' ? 'No connection.'
          : err instanceof Error ? err.message : '';
        commit(ref.current.filter((m) => m.id !== turn.replyId)
          .map((m) => (m.id === turn.userId ? { ...m, status: 'not_sent', error: why } : m)));
        if (err instanceof CoachLimitReached) setCapHit(dayOf(clock()));
        else if (kind === 'offline' || kind === 'signedOut' || kind === 'reconnect') setFailure(kind);
      }
    } finally {
      setStreaming(false);
      abort.current = null;
    }
  }, [api, clock, commit, context, countOne, failure, limit.reached, memory, streaming]);

  const value = useMemo<CoachContext>(() => ({
    messages, starters, loaded, failure, error, streaming, limit, context, setContext, refresh,
    send: (text, opts) => send(text, opts),
    sendAgain: async (id) => {
      const m = ref.current.find((x) => x.id === id && x.status === 'not_sent');
      if (!m || streaming || limit.reached) return;
      commit(ref.current.filter((x) => x.id !== id));
      await send(m.text, { context: m.context ?? null });
    },
    decline: async (id) => {
      const before = ref.current;
      commit(before.map((m) => (m.id === id ? { ...m, status: 'declined' } : m)));
      try { await api.decide(id, 'decline'); } catch { commit(before); }
    },
    feedback: async (id, value) => {
      commit(ref.current.map((m) => (m.id === id ? { ...m, feedback: value } : m)));
      await api.feedback(id, value).catch(() => {});
    },
    clear: async () => {
      // Clear conversation: the thread goes once the server's has (else the next load brings it back);
      // memory, the plan and today's message count stay.
      try { await api.clear(); } catch (err) {
        if (failureKind(err) === 'offline') setFailure('offline');
        return false;
      }
      commit([]);
      setFilter(null);
      setQuery('');
      setSearching(false);
      return true;
    },
    linkProposal: (messageId, proposalId) => {
      commit(ref.current.map((m) => (m.id === messageId ? { ...m, proposalId } : m)));
    },
    searching, setSearching, query, setQuery, filter, setFilter, showEarlier, setShowEarlier,
  }), [messages, starters, loaded, failure, error, streaming, limit, context, refresh, send, commit, api, searching, query, filter, showEarlier]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useCoach(): CoachContext {
  const c = useContext(Ctx);
  if (!c) throw new Error('useCoach outside CoachProvider');
  return c;
}

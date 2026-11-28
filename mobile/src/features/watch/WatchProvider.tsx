// The watch queue, running (build prompt, sections 5.8 and 7). The only code that
// calls a transport: every write to the watch is confirmed here, sent in order,
// retried with backoff when it may pass, and followed until the watch has it.
// Stored on the device, so a send survives the app closing.
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { AppState } from 'react-native';

import { announce } from '../../ui/motion';

import { useDb } from '../../db/DbContext';
import { useFlags } from '../../flags/FlagsProvider';
import type { Watch } from '../plan/model';
import { changesPlan, type Proposal } from './model';
import { proposalSource, type ProposalRequest } from './proposals';
import { accepted, batchWords, confirmOps, failed, isDue, isSettled, latestFor, onWatch, resend as resendOp, watchFor, type WatchOp } from './queue';
import { memoryStore, sqlStore, type WatchStore } from './store';
import { FakeGarmin, SEND_TIMEOUT_MS, SendFailure, serverTransport, withTimeout, type Connection, type WatchTransport } from './transport';
import { fixturesOn } from '../../data/sample';

export type WatchContext = {
  ops: WatchOp[];
  connection: Connection;
  /** Confirmed plan changes, drawn until the server's plan has them. */
  applied: Proposal[];
  stage: (p: Proposal) => void;
  draft: (id: string) => Proposal | null;
  propose: (req: ProposalRequest) => Promise<Proposal>;
  /** The confirm sheet's "Confirm and send": the proposal's lines become operations. Returns the batch. */
  confirm: (id: string) => string | null;
  resend: (opId: string) => void;
  pump: () => Promise<void>;
  watchFor: (sessionId: string, base: Watch | null) => Watch | null;
  opFor: (sessionId: string) => WatchOp | null;
  /** "Changed your mind? · Remove from watch", after a batch that put something on the watch went through. */
  toast: { batch: string } | null;
  dismissToast: () => void;
};

const Ctx = createContext<WatchContext | null>(null);

const DEFAULT_CONNECTION: Connection = { provider: 'Garmin', device: null, syncedAt: null };
export const PUMP_EVERY_MS = 5000;
export const POLL_SYNC_EVERY_MS = 30000;

function defaultTransport(): WatchTransport {
  // A development build sends to a stand-in Garmin; EXPO_PUBLIC_DAASH_WATCH=fail makes the first try fail.
  if (fixturesOn()) return new FakeGarmin(process.env.EXPO_PUBLIC_DAASH_WATCH === 'fail' ? ['timeout'] : [], 'ok', 2);
  return serverTransport(DEFAULT_CONNECTION);
}

export function WatchProvider({ children, transport: given, store: givenStore, clock = Date.now, auto = true, pollEveryMs = POLL_SYNC_EVERY_MS, source }: {
  children: ReactNode;
  transport?: WatchTransport;
  store?: WatchStore;
  clock?: () => number;
  /** Pump on a timer and when the app comes back (off in tests, which pump by hand). */
  auto?: boolean;
  pollEveryMs?: number;
  source?: (req: ProposalRequest) => Promise<Proposal>;
}) {
  const db = useDb();
  const { flags } = useFlags();
  const transport = useMemo(() => given ?? defaultTransport(), [given]);
  const store = useMemo(() => givenStore ?? (db ? sqlStore(db, clock) : memoryStore()), [givenStore, db]); // eslint-disable-line react-hooks/exhaustive-deps
  const fetchProposal = useMemo(() => source ?? proposalSource(flags.coach_enabled), [source, flags.coach_enabled]);

  const [ops, setOps] = useState<WatchOp[]>([]);
  const opsRef = useRef<WatchOp[]>([]);
  const [applied, setApplied] = useState<Proposal[]>([]);
  const drafts = useRef(new Map<string, Proposal>());
  const [, bump] = useState(0);
  const [toast, setToast] = useState<{ batch: string } | null>(null);
  const toasted = useRef(new Set<string>());
  const pumping = useRef(false);
  const again = useRef(false);
  const lastPoll = useRef(0);
  // What was last said about each batch: a send is announced here, once per change, however
  // many screens show its status (and on iOS, which has no live regions).
  const said = useRef(new Map<string, string>());

  useEffect(() => { store.load().then((loaded) => { opsRef.current = loaded; setOps(loaded); }).catch(() => {}); }, [store]);

  /** `quiet`: an automatic retry starting, not said (only what it comes to, if that's new). */
  const update = useCallback(async (changed: WatchOp[], { quiet = false } = {}) => {
    if (!changed.length) return;
    const byId = new Map(opsRef.current.map((o) => [o.id, o]));
    for (const o of changed) byId.set(o.id, o);
    opsRef.current = [...byId.values()];
    setOps(opsRef.current);
    for (const batch of new Set(changed.map((o) => o.batch))) {
      const all = opsRef.current.filter((o) => o.batch === batch);
      // A batch that put something on the watch, all through: offer the way back once.
      const offer = !toasted.current.has(batch) && all.every(isSettled) && all.some((o) => o.kind !== 'remove');
      if (offer) {
        toasted.current.add(batch);
        setToast({ batch });
      }
      const words = quiet ? null : batchWords(all, transport.connection().device);
      if (words && words !== said.current.get(batch)) {
        said.current.set(batch, words);
        announce(offer ? `${words}. Changed your mind? Remove from watch.` : words);
      }
    }
    await store.save(changed).catch(() => {});
  }, [store, transport]);

  const pump = useCallback(async () => {
    if (pumping.current) { again.current = true; return; }
    pumping.current = true;
    try {
      do {
        again.current = false;
        for (const due of opsRef.current.filter((o) => isDue(o, clock()))) {
          let op = due.state === 'failed' ? resendOp(due, clock()) : due;
          if (op !== due) await update([op], { quiet: true });
          try {
            const r = await withTimeout(transport.send(op), SEND_TIMEOUT_MS);
            op = accepted(op, r.synced, clock());
          } catch (err) {
            op = err instanceof SendFailure ? failed(op, err.kind, clock(), err.detail) : failed(op, 'offline', clock());
          }
          await update([op]);
        }
        const waiting = opsRef.current.filter((o) => o.state === 'changed_pending_sync');
        if (waiting.length && clock() - lastPoll.current >= pollEveryMs) {
          lastPoll.current = clock();
          const ids = new Set(await transport.synced(waiting).catch(() => [] as string[]));
          await update(waiting.filter((o) => ids.has(o.id)).map((o) => onWatch(o, clock())));
        }
      } while (again.current);
    } finally {
      pumping.current = false;
    }
  }, [transport, update, clock, pollEveryMs]);

  useEffect(() => {
    if (!auto) return;
    const busy = ops.some((o) => o.state === 'sending' || o.state === 'changed_pending_sync' || (o.state === 'failed' && o.nextAttemptAt != null));
    const sub = AppState.addEventListener('change', (s) => { if (s === 'active') pump(); });
    const timer = busy ? setInterval(pump, PUMP_EVERY_MS) : null;
    return () => { sub.remove(); if (timer) clearInterval(timer); };
  }, [auto, ops, pump]);

  const value = useMemo<WatchContext>(() => ({
    ops,
    connection: transport.connection(),
    applied,
    stage: (p) => { drafts.current.set(p.id, p); bump((n) => n + 1); },
    draft: (id) => drafts.current.get(id) ?? null,
    propose: async (req) => { const p = await fetchProposal(req); drafts.current.set(p.id, p); bump((n) => n + 1); return p; },
    confirm: (id) => {
      const p = drafts.current.get(id);
      if (!p || !p.ops.length) return null;
      const batch = `${id}@${clock()}`;
      update(confirmOps(p.ops, batch, clock())).then(() => { if (auto) pump(); });
      if (changesPlan(p)) setApplied((a) => [...a, p]);
      drafts.current.delete(id);
      return batch;
    },
    resend: (opId) => {
      const op = opsRef.current.find((o) => o.id === opId);
      if (op) update([resendOp(op, clock())]).then(() => { if (auto) pump(); });
    },
    pump,
    watchFor: (sessionId, base) => watchFor(ops, sessionId, base),
    opFor: (sessionId) => latestFor(ops, sessionId),
    toast,
    dismissToast: () => setToast(null),
  }), [ops, transport, applied, fetchProposal, update, pump, auto, toast, clock]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useWatch(): WatchContext {
  const c = useContext(Ctx);
  if (!c) throw new Error('useWatch outside WatchProvider');
  return c;
}

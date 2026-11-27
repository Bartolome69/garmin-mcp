// The one way to the watch (build prompt, section 7): only the watch queue calls
// a transport, and nothing else in the app may write to Garmin.
import { postJson, TimedOut } from '../../api/client';
import type { FailureKind, WatchOp } from './queue';

export class SendFailure extends Error {
  constructor(public kind: FailureKind, public detail?: string) { super(detail ?? kind); }
}

export type Connection = { provider: string; device: string | null; syncedAt: string | null };

export interface WatchTransport {
  /** Send one operation. Resolves when Garmin accepted it (synced: the watch has it already); throws SendFailure. */
  send(op: WatchOp): Promise<{ synced: boolean }>;
  /** Of these accepted operations, the ids the watch now has (it synced since). */
  synced(ops: WatchOp[]): Promise<string[]>;
  connection(): Connection;
}

export const SEND_TIMEOUT_MS = 20000;

/** A promise that gives up after `ms` as a timeout. */
export function withTimeout<T>(p: Promise<T>, ms: number): Promise<T> {
  return new Promise<T>((ok, fail) => {
    const t = setTimeout(() => fail(new SendFailure('timeout')), ms);
    p.then((v) => { clearTimeout(t); ok(v); }, (e) => { clearTimeout(t); fail(e); });
  });
}

/**
 * The Daash server, which writes to Garmin and watches the watch's sync.
 * TODO(server): POST /app/api/v1/watch/ops and /app/api/v1/watch/synced, on the
 * server's existing Garmin writes (schedule, unschedule, update); see
 * docs/native-app/DECISIONS.md, milestone 5.
 */
export function serverTransport(connection: Connection): WatchTransport {
  return {
    async send(op) {
      try {
        const r = await postJson<{ synced?: boolean; refused?: string }>('/app/api/v1/watch/ops',
          { id: op.id, kind: op.kind, session_id: op.sessionId, date: op.date }, { timeoutMs: SEND_TIMEOUT_MS });
        if (r.refused) throw new SendFailure('refused', r.refused);
        return { synced: !!r.synced };
      } catch (err) {
        if (err instanceof SendFailure) throw err;
        if (err instanceof TimedOut) throw new SendFailure('timeout');
        throw new SendFailure('offline');
      }
    },
    async synced(ops) {
      const r = await postJson<{ synced: string[] }>('/app/api/v1/watch/synced', { ids: ops.map((o) => o.id) });
      return r.synced ?? [];
    },
    connection: () => connection,
  };
}

export type Outcome = 'ok' | 'synced' | 'timeout' | 'offline' | 'refused';

/**
 * A stand-in Garmin for development builds and tests: answers each send with the
 * next scripted outcome (then `then` for the rest), and has the watch sync
 * whatever it accepted once `syncAfter` polls have passed.
 */
export class FakeGarmin implements WatchTransport {
  sent: WatchOp[] = [];
  private polls = new Map<string, number>();
  constructor(private script: Outcome[] = [], private then: Outcome = 'ok', private syncAfter = 1,
    private conn: Connection = { provider: 'Garmin', device: 'Forerunner 965', syncedAt: '07:02' }) {}

  async send(op: WatchOp) {
    const outcome = this.script.length ? this.script.shift()! : this.then;
    if (outcome === 'timeout' || outcome === 'offline') throw new SendFailure(outcome);
    if (outcome === 'refused') throw new SendFailure('refused', 'that workout is gone from Garmin Connect');
    this.sent.push(op);
    return { synced: outcome === 'synced' };
  }

  async synced(ops: WatchOp[]) {
    return ops.filter((o) => {
      const n = (this.polls.get(o.id) ?? 0) + 1;
      this.polls.set(o.id, n);
      return n >= this.syncAfter;
    }).map((o) => o.id);
  }

  connection() { return this.conn; }
}

// Watch operations on the device (the watch_operations table, build prompt
// section 7), so a send in flight survives the app closing and still ends in a
// state you can see.
import type { Db } from '../../db/types';
import type { WatchOp } from './queue';

export interface WatchStore {
  load(): Promise<WatchOp[]>;
  save(ops: WatchOp[]): Promise<void>;
}

type Row = {
  id: string; type: WatchOp['kind']; target: string; session_id: string | null; state: WatchOp['state'];
  sent_at: number | null; on_watch_at: number | null; attempts: number; next_attempt_at: number | null;
  error: string | null; created_at: number; updated_at: number;
};

/** Operations kept: everything still moving, and settled ones for a day (the rows read them). */
const KEEP_SETTLED_MS = 24 * 3600 * 1000;

export function sqlStore(db: Db, now: () => number = Date.now): WatchStore {
  return {
    async load() {
      const rows = await db.all<Row>('SELECT * FROM watch_operations WHERE state NOT IN (\'on_watch\') OR updated_at > ? ORDER BY created_at, id',
        [now() - KEEP_SETTLED_MS]);
      return rows.map((r) => {
        const t = JSON.parse(r.target) as Omit<WatchOp, 'id' | 'kind' | 'sessionId' | 'state' | 'attempts' | 'nextAttemptAt' | 'error' | 'createdAt' | 'updatedAt' | 'sentAt' | 'onWatchAt'>;
        return { ...t, id: r.id, kind: r.type, sessionId: r.session_id ?? '', state: r.state, attempts: r.attempts,
          nextAttemptAt: r.next_attempt_at, error: r.error, createdAt: r.created_at, updatedAt: r.updated_at,
          sentAt: r.sent_at, onWatchAt: r.on_watch_at };
      });
    },
    async save(ops) {
      for (const op of ops) {
        const { id, kind, sessionId, state, attempts, nextAttemptAt, error, createdAt, updatedAt, sentAt, onWatchAt, ...target } = op;
        await db.run(
          'INSERT INTO watch_operations (id, type, target, session_id, state, confirmed_at, sent_at, on_watch_at, attempts, '
          + 'next_attempt_at, error, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) '
          + 'ON CONFLICT(id) DO UPDATE SET state = excluded.state, sent_at = excluded.sent_at, on_watch_at = excluded.on_watch_at, '
          + 'attempts = excluded.attempts, next_attempt_at = excluded.next_attempt_at, error = excluded.error, updated_at = excluded.updated_at',
          [id, kind, JSON.stringify(target), sessionId, state, createdAt, sentAt, onWatchAt, attempts, nextAttemptAt, error, createdAt, updatedAt]);
      }
    },
  };
}

/** No database (the web build, tests): kept in memory for the session. */
export function memoryStore(initial: WatchOp[] = []): WatchStore {
  let ops = initial;
  return {
    async load() { return ops; },
    async save(changed) {
      const byId = new Map(ops.map((o) => [o.id, o]));
      for (const op of changed) byId.set(op.id, op);
      ops = [...byId.values()];
    },
  };
}

/**
 * @jest-environment node
 */
// The schema migrates on Node's own SQLite, through the same Db interface the app uses.
import { describe, expect, it } from '@jest/globals';
import { kv } from './kv';
import { migrate } from './migrate';
import { MIGRATIONS } from './schema';
import type { Db } from './types';

type NodeSqlite = { DatabaseSync: new (path: string) => {
  exec(sql: string): void;
  prepare(sql: string): { run(...p: unknown[]): unknown; get(...p: unknown[]): unknown; all(...p: unknown[]): unknown[] };
} };

function nodeDb(): Db {
  const { DatabaseSync } = (process as unknown as { getBuiltinModule(m: string): NodeSqlite }).getBuiltinModule('node:sqlite');
  const db = new DatabaseSync(':memory:');
  return {
    exec: async (sql) => { db.exec(sql); },
    run: async (sql, params = []) => { db.prepare(sql).run(...params); },
    get: async <T,>(sql: string, params: unknown[] = []) => (db.prepare(sql).get(...params) as T) ?? null,
    all: async <T,>(sql: string, params: unknown[] = []) => db.prepare(sql).all(...params) as T[],
  };
}

const TABLES = ['kv', 'sync_state', 'users', 'goals', 'plans', 'weeks', 'sessions', 'steps', 'watch_operations',
  'activities', 'splits', 'streams', 'sleep_nights', 'hrv_nights', 'connections', 'merge_records', 'coach_messages',
  'memory_items', 'entitlements'];

describe('local database', () => {
  it('creates every entity of section 7, once', async () => {
    const db = nodeDb();
    expect(await migrate(db)).toBe(MIGRATIONS.length);
    expect(await migrate(db)).toBe(MIGRATIONS.length);
    const names = (await db.all<{ name: string }>("SELECT name FROM sqlite_master WHERE type = 'table'")).map((r) => r.name);
    expect(names.sort()).toEqual([...TABLES].sort());
  });

  it('lets a watch operation hold only the known types and states, starting at not_sent', async () => {
    const db = nodeDb();
    await migrate(db);
    await db.run("INSERT INTO watch_operations (id, type, target, created_at, updated_at) VALUES ('w1', 'add', '{}', 1, 1)");
    expect((await db.get<{ state: string }>("SELECT state FROM watch_operations WHERE id = 'w1'"))?.state).toBe('not_sent');
    await expect(db.run("INSERT INTO watch_operations (id, type, target, created_at, updated_at) VALUES ('w2', 'delete', '{}', 1, 1)"))
      .rejects.toThrow();
    await expect(db.run("UPDATE watch_operations SET state = 'done' WHERE id = 'w1'")).rejects.toThrow();
  });

  it('keeps small values as JSON', async () => {
    const db = nodeDb();
    await migrate(db);
    const store = kv(db);
    expect(await store.get('flags')).toBeNull();
    await store.set('flags', { coach: true });
    await store.set('flags', { coach: false });
    expect(await store.get('flags')).toEqual({ coach: false });
  });

  it('rolls a failed migration back', async () => {
    const db = nodeDb();
    await migrate(db);
    await expect(migrate(db, [...MIGRATIONS, { version: 99, sql: 'CREATE TABLE ok (a); NOT SQL;' }])).rejects.toThrow();
    expect((await db.get<{ user_version: number }>('PRAGMA user_version'))?.user_version).toBe(MIGRATIONS.length);
    expect(await db.get("SELECT name FROM sqlite_master WHERE name = 'ok'")).toBeNull();
  });
});

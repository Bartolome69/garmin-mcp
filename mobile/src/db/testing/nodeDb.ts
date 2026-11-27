// Tests only: the app's Db interface on Node's own SQLite (node:sqlite), migrated
// like the device's, so stores are tested against real SQL.
import { migrate } from '../migrate';
import type { Db } from '../types';

type NodeSqlite = { DatabaseSync: new (path: string) => {
  exec(sql: string): void;
  prepare(sql: string): { run(...p: unknown[]): unknown; get(...p: unknown[]): unknown; all(...p: unknown[]): unknown[] };
} };

export function nodeDb(): Db {
  const { DatabaseSync } = (process as unknown as { getBuiltinModule(m: string): NodeSqlite }).getBuiltinModule('node:sqlite');
  const db = new DatabaseSync(':memory:');
  return {
    exec: async (sql) => { db.exec(sql); },
    run: async (sql, params = []) => { db.prepare(sql).run(...params); },
    get: async <T,>(sql: string, params: unknown[] = []) => (db.prepare(sql).get(...params) as T) ?? null,
    all: async <T,>(sql: string, params: unknown[] = []) => db.prepare(sql).all(...params) as T[],
  };
}

export async function migratedNodeDb(): Promise<Db> {
  const db = nodeDb();
  await migrate(db);
  return db;
}

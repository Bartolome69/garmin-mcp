// The app's database on the device: expo-sqlite, behind the Db interface.
import { openDatabaseAsync, type SQLiteDatabase } from 'expo-sqlite';

import { migrate } from './migrate';
import type { Db, SqlParams } from './types';

export function fromExpo(sqlite: SQLiteDatabase): Db {
  return {
    exec: (sql) => sqlite.execAsync(sql),
    run: async (sql, params: SqlParams = []) => { await sqlite.runAsync(sql, params); },
    get: (sql, params: SqlParams = []) => sqlite.getFirstAsync(sql, params),
    all: (sql, params: SqlParams = []) => sqlite.getAllAsync(sql, params),
  };
}

let opened: Promise<Db> | null = null;

/** The one database, opened and migrated once per app run. */
export function openDb(): Promise<Db> {
  opened ??= (async () => {
    const db = fromExpo(await openDatabaseAsync('daash.db'));
    await db.exec('PRAGMA journal_mode = WAL;');
    await migrate(db);
    return db;
  })();
  return opened;
}

import { MIGRATIONS } from './schema';
import type { Db } from './types';

/** Bring the database up to the latest schema. Safe to call on every start. Returns the version. */
export async function migrate(db: Db, migrations = MIGRATIONS): Promise<number> {
  await db.exec('PRAGMA foreign_keys = ON;');
  const row = await db.get<{ user_version: number }>('PRAGMA user_version;');
  let current = row?.user_version ?? 0;
  for (const m of [...migrations].sort((a, b) => a.version - b.version)) {
    if (m.version <= current) continue;
    await db.exec('BEGIN;');
    try {
      await db.exec(m.sql);
      await db.exec(`PRAGMA user_version = ${m.version};`);
      await db.exec('COMMIT;');
    } catch (err) {
      await db.exec('ROLLBACK;');
      throw err;
    }
    current = m.version;
  }
  return current;
}

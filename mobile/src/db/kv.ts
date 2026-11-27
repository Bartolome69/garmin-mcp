import type { Db } from './types';

/** Small JSON values kept on the device: flags, appearance, last answers. */
export function kv(db: Db) {
  return {
    async get<T>(key: string): Promise<T | null> {
      const row = await db.get<{ value: string }>('SELECT value FROM kv WHERE key = ?', [key]);
      if (!row) return null;
      try { return JSON.parse(row.value) as T; } catch { return null; }
    },
    async set(key: string, value: unknown): Promise<void> {
      await db.run('INSERT INTO kv (key, value, updated_at) VALUES (?, ?, ?) '
        + 'ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at',
      [key, JSON.stringify(value), Date.now()]);
    },
  };
}

// Signing out: the token goes, and so does everything Daash keeps on the phone for
// this runner (cached screens, the watch queue, runs, plans), so the next person to
// sign in on this phone sees nothing of theirs. The device's own settings stay.
import type { Db } from '../db/types';
import { setAccessToken } from './client';

/** kv keys that belong to the device, not the runner. */
export const DEVICE_KEYS = ['appearance'];

const RUNNER_TABLES = ['sync_state', 'users', 'goals', 'plans', 'weeks', 'sessions', 'steps', 'watch_operations', 'activities',
  'splits', 'streams', 'sleep_nights', 'hrv_nights', 'connections', 'merge_records', 'coach_messages', 'memory_items', 'entitlements'];

export async function clearRunnerData(db: Db): Promise<void> {
  await db.run(`DELETE FROM kv WHERE key NOT IN (${DEVICE_KEYS.map(() => '?').join(', ')})`, DEVICE_KEYS);
  for (const table of RUNNER_TABLES) await db.run(`DELETE FROM ${table}`);
}

export async function signOut(db: Db | null): Promise<void> {
  await setAccessToken(null);
  if (db) await clearRunnerData(db);
}

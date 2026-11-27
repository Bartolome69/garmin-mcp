// The app doesn't ship on the web. A web build exists only to measure layouts in a
// browser (scripts/layout-check.js), so there's no local database there: the app
// runs without one, as it does before the database opens on a phone.
import type { Db } from './types';

export function openDb(): Promise<Db> {
  return Promise.reject(new Error('No local database on the web'));
}

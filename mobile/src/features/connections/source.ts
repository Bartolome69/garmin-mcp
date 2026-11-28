import { Platform } from 'react-native';

import { getJson, postJson } from '../../api/client';
import FIXTURE from './connections.fixture.json';
import type { ConnectionsVM } from './model';
import { fixturesOn } from '../../data/sample';

/**
 * Connections from the server. TODO(server): GET /app/api/v1/connections returns
 * garmin_mcp/domain/connections.py view(...); POST /app/api/v1/connections/priority
 * {order} and /app/api/v1/connections/keep-apart {pairs} store the runner's changes.
 * Until they exist, a development build reads connections.fixture.json, written by
 * scripts/domain_fixtures.py from that engine for the design's persona.
 */
const CASES = (FIXTURE as unknown as { cases: Record<'ios' | 'android', ConnectionsVM> }).cases;

export function fixtureConnections(os: string = Platform.OS): ConnectionsVM {
  return os === 'ios' ? CASES.ios : CASES.android;
}

export async function fetchConnections(): Promise<ConnectionsVM> {
  try {
    return await getJson<ConnectionsVM>('/app/api/v1/connections');
  } catch (err) {
    if (fixturesOn()) return fixtureConnections();
    throw err;
  }
}

/** Best effort: the device keeps the change either way, and sends it again next time. */
export async function sendChanges(change: { priority?: string[]; keptApart?: [string, string][] }): Promise<boolean> {
  try {
    if (change.priority) await postJson('/app/api/v1/connections/priority', { order: change.priority });
    if (change.keptApart) await postJson('/app/api/v1/connections/keep-apart', { pairs: change.keptApart });
    return true;
  } catch {
    return false;
  }
}

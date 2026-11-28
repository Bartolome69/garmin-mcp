import { getJson } from '../../api/client';
import { PERSONA_TODAY, PERSONA_TODAY_COACH } from './fixture';
import type { TodayVM } from './model';
import { fixturesOn } from '../../data/sample';

/**
 * Today from the server. TODO(server): GET /app/api/v1/today, built from the
 * engines in garmin_mcp/domain/ (docs/native-app/DECISIONS.md). Until it exists, and
 * until sign-in lands, development builds and sample data (data/sample.ts) show the persona.
 */

export function todaySource(coach: boolean): () => Promise<TodayVM> {
  return async () => {
    try {
      return await getJson<TodayVM>(`/app/api/v1/today${coach ? '?coach=1' : ''}`);
    } catch (err) {
      if (fixturesOn()) return coach ? PERSONA_TODAY_COACH : PERSONA_TODAY;
      throw err;
    }
  };
}

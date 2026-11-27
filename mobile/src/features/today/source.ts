import { getJson } from '../../api/client';
import { PERSONA_TODAY, PERSONA_TODAY_COACH } from './fixture';
import type { TodayVM } from './model';

/**
 * Today from the server. TODO(server): GET /app/api/v1/today, built from the
 * engines in garmin_mcp/domain/ (docs/native-app/DECISIONS.md). Until it exists, and
 * until sign-in lands, a development build shows the design's persona.
 */
/** Development builds, and the layout check's web build (EXPO_PUBLIC_DAASH_FIXTURE=1); never a store build. */
const USE_FIXTURE = __DEV__ || process.env.EXPO_PUBLIC_DAASH_FIXTURE === '1';

export function todaySource(coach: boolean): () => Promise<TodayVM> {
  return async () => {
    try {
      return await getJson<TodayVM>(`/app/api/v1/today${coach ? '?coach=1' : ''}`);
    } catch (err) {
      if (USE_FIXTURE) return coach ? PERSONA_TODAY_COACH : PERSONA_TODAY;
      throw err;
    }
  };
}

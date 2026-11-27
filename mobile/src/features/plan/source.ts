import { getJson } from '../../api/client';
import { PERSONA_PLAN, PERSONA_PLAN_COACH, PERSONA_PLAN_PLUS } from './fixture';
import type { PlanVM } from './model';

/**
 * Plan from the server: a window of weeks around this one, the block and the
 * trends, each session with its steps so the detail opens offline. TODO(server):
 * GET /app/api/v1/plan, built from garmin_mcp/domain/ (docs/native-app/DECISIONS.md).
 * Until it exists, and until sign-in lands, a development build shows the persona.
 */
/** Development builds, and the layout check's web build (EXPO_PUBLIC_DAASH_FIXTURE=1); never a store build. */
const USE_FIXTURE = __DEV__ || process.env.EXPO_PUBLIC_DAASH_FIXTURE === '1';

export function planSource(coach: boolean, plus: boolean): () => Promise<PlanVM> {
  return async () => {
    try {
      const q = [coach ? 'coach=1' : '', plus ? 'plus=1' : ''].filter(Boolean).join('&');
      return await getJson<PlanVM>(`/app/api/v1/plan${q ? `?${q}` : ''}`);
    } catch (err) {
      if (!USE_FIXTURE) throw err;
      const base = coach ? PERSONA_PLAN_COACH : PERSONA_PLAN;
      return plus ? { ...base, trends: PERSONA_PLAN_PLUS.trends } : base;
    }
  };
}

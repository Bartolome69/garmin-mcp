import { getJson } from '../../api/client';
import type { RunVM } from './model';
import FIXTURE from './runs.fixture.json';

/**
 * A recorded run, worked out by the server's post-run engine. TODO(server):
 * GET /app/api/v1/runs/{sessionId} returns garmin_mcp/domain/postrun.py view(...),
 * with coach: {debrief} when coach_enabled. Until it exists, a development build
 * reads runs.fixture.json, which scripts/domain_fixtures.py writes from that engine
 * for the design's persona.
 */
const USE_FIXTURE = __DEV__ || process.env.EXPO_PUBLIC_DAASH_FIXTURE === '1';
const CASES = (FIXTURE as unknown as { cases: Record<string, RunVM> }).cases;

// Development only: what the server's Coach would say, from the run's own numbers. Never a cause.
const DEBRIEFS: Record<string, string> = {
  '2026-11-25-intervals': 'Reps were even, 3:58 to 4:04, and your heart rate fell under 130 in every recovery. Rep 6 peaked at 172 bpm, 4 above rep 5.',
  '2026-11-26-easy': 'The second half was 6 s/km quicker than the first, and every kilometre was faster than your easy range.',
};

export function fixtureRun(id: string, coach: boolean): RunVM | null {
  const vm = CASES[id];
  if (!vm) return null;
  return coach ? { ...vm, coach: DEBRIEFS[id] ? { debrief: DEBRIEFS[id] } : null } : vm;
}

export function runSource(id: string, coach: boolean): () => Promise<RunVM> {
  return async () => {
    try {
      return await getJson<RunVM>(`/app/api/v1/runs/${encodeURIComponent(id)}${coach ? '?coach=1' : ''}`);
    } catch (err) {
      const vm = USE_FIXTURE ? fixtureRun(id, coach) : null;
      if (vm) return vm;
      throw err;
    }
  };
}

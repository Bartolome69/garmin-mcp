// Onboarding's server calls. TODO(server): POST /app/api/v1/onboarding/import (the
// signed-in runner's last 12 weeks, through onboarding.import_summary) and
// /app/api/v1/onboarding/plan ({answers, basis, goal} → onboarding.reveal, which also
// saves the plan). Until they exist, a development build reads onboarding.fixture.json,
// written by scripts/domain_fixtures.py from those engines for the design's persona.
import { postJson } from '../../api/client';
import FIXTURE from './onboarding.fixture.json';
import type { Goal, PaceResult, Reveal } from './model';

const USE_FIXTURE = __DEV__ || process.env.EXPO_PUBLIC_DAASH_FIXTURE === '1';
export const CASES = (FIXTURE as unknown as {
  cases: { import: PaceResult; import_empty: PaceResult; questions: PaceResult; questions_race: PaceResult; reveal: Reveal; reveal_fit: Reveal; reveal_empty: Reveal; reveal_questions: Reveal };
}).cases;

// Development only: what the server's Coach would say about the shape. Never a number of its own.
const WHY = 'Base is long because your weeks have been steady. Threshold work comes in with Build.';

export async function importRuns(): Promise<PaceResult> {
  try {
    return await postJson<PaceResult>('/app/api/v1/onboarding/import', {});
  } catch (err) {
    if (USE_FIXTURE) return CASES.import;
    throw err;
  }
}

export async function makePlan(result: PaceResult, goal: Goal, coach: boolean): Promise<Reveal> {
  const body = { answers: result.answers, basis: result.basis, goal: goal.kind === 'race' ? goal : null, coach };
  try {
    return await postJson<Reveal>('/app/api/v1/onboarding/plan', body);
  } catch (err) {
    if (!USE_FIXTURE) throw err;
    const r = goal.kind === 'race' ? CASES.reveal : CASES.reveal_fit;
    return coach ? { ...r, coachNote: WHY } : r;
  }
}

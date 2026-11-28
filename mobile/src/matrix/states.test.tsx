// The snapshot matrix (src/testing/matrix.tsx) for the states that aren't routes: Today and
// Plan loading, offline, with an expired Garmin sign-in and empty; the Coach empty, offline
// and streaming (STATE_SCREENS). Today's and Plan's sources answer as testing/dataMode says.
import { jest } from '@jest/globals';

import { runMatrix } from '../testing/matrix';

jest.mock('../features/today/source', () => {
  const real = jest.requireActual('../features/today/source') as typeof import('../features/today/source');
  const { answering } = jest.requireActual('../testing/dataMode') as typeof import('../testing/dataMode');
  return { ...real, todaySource: (coach: boolean) => answering(real.todaySource(coach), (v) => ({ ...v, session: null })) };
});
jest.mock('../features/plan/source', () => {
  const real = jest.requireActual('../features/plan/source') as typeof import('../features/plan/source');
  const { answering } = jest.requireActual('../testing/dataMode') as typeof import('../testing/dataMode');
  return { ...real, planSource: (coach: boolean, plus: boolean) => answering(real.planSource(coach, plus), (v) => ({ ...v, weeks: [], block: null })) };
});

runMatrix('states');

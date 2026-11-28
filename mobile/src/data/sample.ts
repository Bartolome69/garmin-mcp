// Sample data: the London persona's fixtures, in place of a server that doesn't have
// the native endpoints yet (TODO(server) across the app) and a sign-in that can't hand
// the app a token yet. Used:
//   - in development builds (__DEV__), as before: a fallback when the server can't answer;
//   - when the bundle was made with EXPO_PUBLIC_DAASH_FIXTURE=1;
//   - in the store Expo Go app when nobody is signed in on this phone (the owner's test
//     setup: an EAS update opened in Expo Go, which is a production bundle).
// In the last two the app says so ("Sample data" on Today and You), so test numbers are
// never mistaken for real ones, and the onboarding's sign-in wall is skipped. A signed-in
// phone in Expo Go uses the real paths, as a store build does.
import { isRunningInExpoGo } from 'expo';

import { accessToken } from '../api/client';

const FIXTURE_ENV = process.env.EXPO_PUBLIC_DAASH_FIXTURE === '1';

/**
 * True in the store Expo Go app (Constants.executionEnvironment is 'storeClient' and no
 * development client), where only Expo Go's own native modules exist.
 */
export function inExpoGo(): boolean {
  if (expoGoForTests != null) return expoGoForTests;
  // jest-expo reports Expo Go; the tests are not Expo Go unless they say so.
  if (process.env.JEST_WORKER_ID !== undefined) return false;
  try { return isRunningInExpoGo(); } catch { return false; }
}
let expoGoForTests: boolean | null = null;
/** Tests only: pretend to be (or not be) in Expo Go; null goes back to asking. */
export function setExpoGoForTests(on: boolean | null): void { expoGoForTests = on; }

// Whether a token was on this phone at launch, once read. Unknown counts as not signed in.
let signedInAtLaunch: boolean | null = null;
// Tests set the mode directly.
let forced: boolean | null = null;

/** The app is showing sample data, and says so. */
export function sampleData(): boolean {
  if (forced != null) return forced;
  return FIXTURE_ENV || (inExpoGo() && signedInAtLaunch !== true);
}

/** Sources may fall back to the fixtures: development builds, and sample data. */
export function fixturesOn(): boolean {
  return __DEV__ || sampleData();
}

/** Read once at launch, before the providers mount (the root layout waits for it). */
export async function resolveSampleData(read: () => Promise<string | null> = accessToken): Promise<boolean> {
  signedInAtLaunch = !!(await read().catch(() => null));
  return sampleData();
}

/** Tests only: force sample data on or off; null goes back to the real rule. */
export function setSampleDataForTests(on: boolean | null, signedIn: boolean | null = null): void {
  forced = on;
  signedInAtLaunch = signedIn;
}

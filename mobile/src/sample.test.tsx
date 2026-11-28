// Sample data (data/sample.ts): in Expo Go with nobody signed in, or with
// EXPO_PUBLIC_DAASH_FIXTURE=1, the app runs on the persona's fixtures, skips the sign-in
// wall and says "Sample data" on Today and You. A signed-in phone uses the real paths.
import { afterEach, describe, expect, it } from '@jest/globals';
import { renderRouter, screen, within } from 'expo-router/testing-library';

import Index from './app/index';
import { fixturesOn, inExpoGo, resolveSampleData, sampleData, setExpoGoForTests, setSampleDataForTests } from './data/sample';
import { pushToken } from './features/settings/push';
import { todaySource } from './features/today/source';
import { PERSONA_TODAY } from './features/today/fixture';
import { app, openScreen, SCREENS } from './testing/screens';

afterEach(() => { setSampleDataForTests(null); setExpoGoForTests(null); });

describe('when the app shows sample data', () => {
  it('not in Expo Go and no fixture switch: real data only (development builds still fall back, unlabelled)', () => {
    expect(inExpoGo()).toBe(false);
    expect(sampleData()).toBe(false);
    expect(fixturesOn()).toBe(true); // __DEV__ in Jest
  });

  it('in Expo Go: sample data unless a token was on the phone at launch', async () => {
    setExpoGoForTests(true);
    expect(inExpoGo()).toBe(true);
    expect(await resolveSampleData(async () => null)).toBe(true);
    expect(await resolveSampleData(async () => 'token')).toBe(false);
    expect(await resolveSampleData(async () => { throw new Error('no keychain'); })).toBe(true);
  });

  it('Today falls back to the persona without a token in sample mode', async () => {
    setSampleDataForTests(true);
    expect(await todaySource(false)()).toEqual(PERSONA_TODAY);
  });

  it('never asks Expo Go for a push token', async () => {
    expect(await pushToken(true)).toBeNull();
  });
});

describe('the "Sample data" label', () => {
  const today = SCREENS.find((c) => c.name === 'Today')!;
  const you = SCREENS.find((c) => c.name === 'You')!;

  it('shows on Today and You in sample mode, with words a screen reader says', async () => {
    setSampleDataForTests(true);
    await openScreen(today);
    // Native tabs draw every tab in Jest, so Today's and You's labels are both there.
    expect(screen.getAllByTestId('sample-data')[0].props.accessibilityLabel).toBe('Sample data. These numbers are from a sample runner, not yours.');
    await openScreen(you);
    expect(screen.getAllByTestId('sample-data').length).toBeGreaterThan(0);
    expect(screen.getByTestId('button-Try onboarding')).toBeTruthy();
  });

  it('on Today it sits in the kicker row inside the scroll view (safe area on iOS, no height above the fold)', async () => {
    setSampleDataForTests(true);
    await openScreen(today);
    const header = within(screen.getByTestId('today-header'));
    expect(header.getByTestId('sample-data')).toBeTruthy();
    // It takes the place of the time the day was fetched, which means nothing for sample data.
    expect(header.queryByTestId('freshness')).toBeNull();
    expect(within(screen.getByTestId('today')).getByTestId('sample-data')).toBeTruthy();
  });

  it('isn’t there with real data', async () => {
    setSampleDataForTests(false);
    await openScreen(today);
    expect(screen.queryAllByTestId('sample-data')).toEqual([]);
    await openScreen(you);
    expect(screen.queryAllByTestId('sample-data')).toEqual([]);
    expect(screen.queryByTestId('button-Try onboarding')).toBeNull();
  });
});

describe('the first screen', () => {
  it('skips the sign-in wall with sample data: straight to Today', async () => {
    setSampleDataForTests(true);
    await renderRouter({ ...app('dark', false), index: Index }, { initialUrl: '/' });
    expect(await screen.findByTestId('today')).toBeTruthy();
  });

  it('a new phone with real data starts at the welcome', async () => {
    setSampleDataForTests(false);
    await renderRouter({ ...app('dark', false), index: Index }, { initialUrl: '/' });
    expect(await screen.findByTestId('welcome')).toBeTruthy();
  });
});

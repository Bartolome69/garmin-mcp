// You's settings (milestone 12): Sign out behind a confirm, the appearance override, the four
// notification kinds and their copy (Notifications boards; no "Missed"), and the sketched rows.
import { describe, expect, it } from '@jest/globals';
import { fireEvent, screen, within } from 'expo-router/testing-library';

import { DEFAULT_PREFS, headsUp, NOTIFICATION_KINDS, planUpdated, readPrefs, runSummary, sentToWatch } from './features/settings/notifications';
import { openScreen, SCREENS } from './testing/screens';

const open = (name: string) => openScreen(SCREENS.find((c) => c.name === name)!);

describe('notifications', () => {
  it('the four kinds of section 8, all on by default, and no streak or missed-session kind', () => {
    expect(NOTIFICATION_KINDS.map((k) => k.title)).toEqual(['Session heads-up', 'Sent to your watch', 'Run summary', 'Plan updated']);
    expect(DEFAULT_PREFS).toEqual({ headsUp: true, sent: true, runSummary: true, planUpdated: true });
    expect(JSON.stringify(NOTIFICATION_KINDS)).not.toMatch(/miss|streak/i);
    expect(readPrefs({ sent: false, junk: 1 })).toEqual({ ...DEFAULT_PREFS, sent: false });
    expect(readPrefs('nonsense')).toEqual(DEFAULT_PREFS);
  });

  it('says what the boards say', () => {
    expect(runSummary({ title: '9km run club easy', km: '9.2', pace: '5:14', onPlan: true }))
      .toEqual({ title: 'Run done · 9.2 km at 5:14 /km', body: 'On plan. Tap for the summary.' });
    expect(sentToWatch({ title: 'Long 18km easy', day: 'Sunday', device: 'Forerunner 965' }))
      .toEqual({ title: 'Sent to your watch', body: 'Long 18km easy is on Forerunner 965 for Sunday.' });
    expect(planUpdated({ what: 'Jo moved Thursday’s session to Friday.', sentToWatch: false }))
      .toEqual({ title: 'Plan updated', body: 'Jo moved Thursday’s session to Friday. Nothing sent to the watch yet.' });
    expect(headsUp({ title: '3 × 2 km at threshold', onWatch: true, device: 'Forerunner 965' }).body).toBe('It’s on Forerunner 965.');
    // With the Coach on, the debrief is the line; the title and its numbers are the same.
    const on = runSummary({ title: '', km: '9.2', pace: '5:14', onPlan: true, debrief: 'Easy means easy, and this was.' });
    expect(on.title).toBe('Run done · 9.2 km at 5:14 /km');
    expect(on.body).toBe('Easy means easy, and this was.');
  });

  it('the screen has a switch per kind, each named', async () => {
    await open('Notifications');
    for (const k of NOTIFICATION_KINDS) expect(screen.getByTestId(`notify-${k.kind}`).props.accessibilityLabel).toBe(`${k.title}. ${k.sub}`);
  });
});

describe('Sign out', () => {
  it('asks first; Stay signed in goes back, Sign out ends at the welcome', async () => {
    await open('You');
    await fireEvent.press(screen.getByTestId('button-Sign out'));
    expect(await screen.findByTestId('sign-out-sheet')).toBeTruthy();
    expect(screen.getByText('Nothing changes on your watch.')).toBeTruthy();
    await fireEvent.press(screen.getByTestId('button-Stay signed in'));
    expect(screen.queryByTestId('sign-out-sheet')).toBeNull();
    await fireEvent.press(screen.getByTestId('button-Sign out'));
    const sheet = await screen.findByTestId('sign-out-sheet');
    const confirm = within(sheet).getByTestId('button-Sign out');
    await fireEvent.press(confirm);
    expect(await screen.findByTestId('welcome', {}, { timeout: 5000 })).toBeTruthy();
  });
});

describe('appearance', () => {
  it('the phone’s setting, light or dark, one selected', async () => {
    await open('Appearance');
    expect(screen.getByTestId('appearance-dark').props.accessibilityState).toMatchObject({ selected: true });
    await fireEvent.press(screen.getByTestId('appearance-light'));
    expect(screen.getByTestId('appearance-light').props.accessibilityState).toMatchObject({ selected: true });
    expect(screen.getByTestId('appearance-dark').props.accessibilityState).toMatchObject({ selected: false });
  });
});

describe('You lists every setting', () => {
  it('Activities, units and zones, notifications, appearance, privacy, help, and the independence line', async () => {
    await open('You');
    for (const id of ['row-activities', 'row-training', 'row-notifications', 'row-appearance', 'row-privacy', 'row-help']) expect(screen.getByTestId(id)).toBeTruthy();
    expect(screen.getAllByText('Daash is independent and not endorsed by any watch maker.').length).toBeGreaterThan(0);
  });

  it('Activities lists runs that open their post-run screen', async () => {
    await open('Activities');
    await fireEvent.press(screen.getByTestId('activity-2026-11-25-intervals'));
    expect(await screen.findByTestId('run')).toBeTruthy();
  });
});

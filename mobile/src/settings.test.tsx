// You's settings (milestone 12): Sign out behind a confirm, the appearance override, the four
// notification kinds and their copy (Notifications boards; no "Missed"), and the sketched rows.
import { describe, expect, it } from '@jest/globals';
import { act, fireEvent, renderRouter, screen, within } from 'expo-router/testing-library';

import { headsUp, planUpdated, runSummary, sentToWatch } from './features/settings/notificationCopy';
import { DEFAULT_PREFS, NOTIFICATION_KINDS, readPrefs } from './features/settings/notifications';
import { memoryStore, type WatchStore } from './features/watch/store';
import { FakeGarmin, type WatchTransport } from './features/watch/transport';
import type { WatchOp } from './features/watch/queue';
import { app, openScreen, SCREENS, stagedProposal, watchQueue } from './testing/screens';

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

  it('the screen has a switch per kind, each named; the whole row is the switch', async () => {
    await open('Notifications');
    for (const k of NOTIFICATION_KINDS) {
      const row = screen.getByTestId(`notify-${k.kind}-row`);
      expect([row.props.accessibilityRole, row.props.accessibilityLabel, row.props.accessibilityState.checked]).toEqual(['switch', `${k.title}. ${k.sub}`, true]);
    }
    await fireEvent.press(screen.getByTestId('notify-headsUp-row'));
    expect(screen.getByTestId('notify-headsUp-row').props.accessibilityState.checked).toBe(false);
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

  it('forgets the watch queue: a send under way when signing out writes nothing back', async () => {
    // Garmin answers only when told to, so the send is still under way at sign out.
    const fake = new FakeGarmin();
    let answer: () => void = () => {};
    const garmin: WatchTransport = {
      connection: () => fake.connection(),
      synced: (ops) => fake.synced(ops),
      send: (op) => new Promise((resolve, reject) => { answer = () => { fake.send(op).then(resolve, reject); }; }),
    };
    const saved: WatchOp[][] = [];
    const inner = memoryStore();
    const store: WatchStore = { load: () => inner.load(), save: async (ops) => { saved.push(ops); await inner.save(ops); } };
    await renderRouter(app('dark', true, { garmin, watchStore: store }), { initialUrl: '/you' });
    await screen.findByTestId('you');
    const p = stagedProposal();
    await act(async () => { watchQueue().stage(p); watchQueue().confirm(p.id); });
    expect(watchQueue().ops.map((o) => o.state)).toEqual(['sending']);
    let pumped!: Promise<void>;
    await act(async () => { pumped = watchQueue().pump(); });

    await fireEvent.press(screen.getByTestId('button-Sign out'));
    await fireEvent.press(within(await screen.findByTestId('sign-out-sheet')).getByTestId('button-Sign out'));
    await screen.findByTestId('welcome', {}, { timeout: 5000 });
    const before = saved.length;
    await act(async () => { answer(); await pumped; });
    expect(watchQueue().ops).toEqual([]);
    expect(watchQueue().toast).toBeNull();
    expect(saved.length).toBe(before); // nothing written back after sign out
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

describe('copy that says the same thing says it the same way', () => {
  it('Help’s medical line is the shared one (Life happened, the Coach and memory use it too)', async () => {
    const { MEDICAL } = require('./features/watch/proposals') as typeof import('./features/watch/proposals');
    await open('Help');
    expect(within(screen.getByTestId('help')).getByTestId('help-medical')).toHaveTextContent(MEDICAL);
  });
});

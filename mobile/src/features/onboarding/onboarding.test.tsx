// Onboarding (milestone 9): the device's twin of the four questions against the
// server's answers, the goal checks, week 1 for the watch, where week 1 is once
// sent, the provider, and each step's states, on iOS and Android (this suite runs
// once per platform).
import { describe, expect, it, jest } from '@jest/globals';
import { act, fireEvent, render, screen } from '@testing-library/react-native';
import { StyleSheet } from 'react-native';

import { DbContext } from '../../db/DbContext';
import { kv } from '../../db/kv';
import { migratedNodeDb } from '../../db/testing/nodeDb';
import { ThemeProvider } from '../../theme/ThemeProvider';
import { colors } from '../../theme/tokens';
import {
  goalValid, localToday, parseRaceDate, questions, raceName, raceSeconds, type Goal, type PaceResult, type Reveal,
} from './model';
import { NOTIFICATIONS_KEY, ONBOARDED_KEY, OnboardingProvider, useOnboarding, type OnboardingState } from './OnboardingProvider';
import { CASES } from './source';
import { GoalStep, LandedView, PacesStep, QuestionsStep, ResultStep, RevealView, RevealWait, restLine } from './views';
import { landingFor, weekOneProposal } from './week1';
import type { WatchOp } from '../watch/queue';

const TODAY = '2026-11-27';
const LONDON: Goal = { kind: 'race', name: 'London Marathon', date: '2027-04-24', km: 42.195, target: '3:15' };
const wrap = (ui: React.ReactElement, scheme: 'dark' | 'light' = 'dark') => <ThemeProvider initial={scheme}>{ui}</ThemeProvider>;

type Node = { type: string; props: Record<string, any>; children: (Node | string)[] | null };
const nodes = (tree: unknown): Node[] => {
  const out: Node[] = [];
  const walk = (n: unknown) => {
    if (!n || typeof n !== 'object') return;
    if (Array.isArray(n)) { n.forEach(walk); return; }
    out.push(n as Node);
    ((n as Node).children ?? []).forEach(walk);
  };
  walk(tree);
  return out;
};
const brandFills = (scheme: 'dark' | 'light' = 'dark') =>
  nodes(screen.toJSON()).filter((n) => StyleSheet.flatten(n.props?.style)?.backgroundColor === colors[scheme].brand).length;

describe('the four questions on the phone: the server\'s answers, exactly', () => {
  it('no race: the experience\'s 10K, said as such', () => {
    expect(questions('exp', 5, '50to70')).toEqual(CASES.questions);
  });
  it('a recent race sets the paces', () => {
    expect(questions('regular', 4, '30to50', { km: 21.0975, secs: 6300 })).toEqual(CASES.questions_race);
  });
  it('days a week stay between 3 and 7 for the plan builder, the usual week says what was answered', () => {
    const q = questions('new', 7, 'under30');
    expect(q.answers).toEqual({ avg_km: 20, longest_km: 6, days: 7, level: 'new' });
    expect(q.rows.find((r) => r.key === 'week')!.value).toBe('20 km · 7 runs');
    expect(q.runs).toBeNull();
  });
  it('race names: the standard distances by name, anything else in km', () => {
    expect([raceName(5), raceName(10), raceName(21.0975), raceName(42.195), raceName(15)]).toEqual(['5K', '10K', 'Half marathon', 'Marathon', '15 km']);
  });
  it('inside a sentence, in sentence case', () => {
    expect([raceName(10, true), raceName(21.0975, true), raceName(42.195, true)]).toEqual(['10K', 'half marathon', 'marathon']);
    expect(CASES.questions_race.rows.find((r) => r.key === 'threshold')!.note).toBe('from your half marathon');
  });
});

describe('the server\'s import, as the app reads it', () => {
  it('the persona: 56 runs, the 10K of 43:30, a usual week', () => {
    expect(CASES.import.runs).toBe(56);
    expect(CASES.import.rows.map((r) => r.key)).toEqual(['race', 'easy', 'threshold', 'week']);
    expect(CASES.import.basis).toEqual({ raceKm: 10, raceSeconds: 2610, raceDate: '2026-10-12', easy: [310, 320], assumed: false });
  });
  it('the plan\'s easy runs use the easy pace the runner confirmed', () => {
    const row = CASES.import.rows.find((r) => r.key === 'easy')!.value.replace(' /km', '');
    const easy = CASES.reveal.week1.filter((s) => s.type === 'easy' || s.type === 'long');
    expect(easy.length).toBeGreaterThan(0);
    for (const s of easy) expect(s.sub.split(' · ').pop()).toBe(row);
  });
  it.each(['reveal_empty', 'reveal_questions'] as const)('%s: no race to go on, so no prediction, and the assumed 10K isn\'t called theirs', (key) => {
    const r = CASES[key];
    expect(r.predicted).toBeNull();
    expect(r.because).toContain('No prediction without a recent race; paces use a 10K of 45:00 for now.');
    expect(r.because).not.toMatch(/your 10K| 0 km/);
  });
  it('nothing to read: zero runs, the default 10K, no usual week', () => {
    expect(CASES.import_empty.runs).toBe(0);
    expect(CASES.import_empty.title).toBe('No runs in the last 12 weeks');
    expect(CASES.import_empty.rows.map((r) => r.key)).toEqual(['race', 'easy', 'threshold']);
    expect(CASES.import_empty.basis.raceSeconds).toBe(2700);
  });
});

describe('race times', () => {
  it('two parts are h:mm for a half or a marathon, mm:ss for shorter races', () => {
    expect(raceSeconds('3:15', 42.195)).toBe(11700);
    expect(raceSeconds('1:45', 21.0975)).toBe(6300);
    expect(raceSeconds('45:00', 10)).toBe(2700);
    expect(raceSeconds('22:30', 5)).toBe(1350);
  });
  it('three parts are always h:mm:ss', () => {
    expect(raceSeconds('3:15:00', 42.195)).toBe(11700);
    expect(raceSeconds('1:02:10', 10)).toBe(3730);
  });
  it('times no one could run, and words, are not times', () => {
    expect(raceSeconds('45:00', 21.0975)).toBeNull();     // 45 hours
    expect(raceSeconds('0:30', 5)).toBeNull();            // 30 seconds
    expect(raceSeconds('11:30', 42.195)).toBeNull();      // 11 h 30 is slower than 15:00 /km
    expect(raceSeconds('10:15', 42.195)).toBeNull();      // the server reads two parts of 10 or more as mm:ss
    expect(raceSeconds('10:15:00', 42.195)).toBe(36900);
    expect(raceSeconds('fast', 10)).toBeNull();
    expect(raceSeconds('', 10)).toBeNull();
    expect(raceSeconds('45:00', 0)).toBeNull();
  });
});

describe('race days', () => {
  it('ISO, day first with slashes, or words, with or without the weekday', () => {
    for (const t of ['2027-04-24', '24/04/2027', '24.4.2027', '24 Apr 2027', '24 april 2027', 'Sat 24 Apr 2027', ' saturday, 24 Apr 2027 ']) {
      expect([t, parseRaceDate(t)]).toEqual([t, '2027-04-24']);
    }
  });
  it('days that don\'t exist, and other words, are not days', () => {
    for (const t of ['2027-02-31', '31/02/2027', '30 Feb 2027', '24 Apz 2027', 'next April', '04/24', '']) {
      expect([t, parseRaceDate(t)]).toEqual([t, null]);
    }
  });
  it('a leap day only in a leap year', () => {
    expect(parseRaceDate('29 Feb 2028')).toBe('2028-02-29');
    expect(parseRaceDate('29 Feb 2027')).toBeNull();
  });
  it('today on the phone is its own calendar day', () => {
    expect(localToday(new Date(2026, 10, 27, 23, 59))).toBe('2026-11-27');
    expect(localToday(new Date(2027, 0, 1, 0, 1))).toBe('2027-01-01');
  });
});

describe('the goal can make a plan', () => {
  it('just get fitter always can', () => {
    expect(goalValid({ kind: 'fit' }, TODAY)).toBe(true);
  });
  it('a named race on a day after today, with a target that reads or none', () => {
    expect(goalValid(LONDON, TODAY)).toBe(true);
    expect(goalValid({ ...LONDON, target: null }, TODAY)).toBe(true);
  });
  it('not without a name, a real future day, a distance, or with a target that doesn\'t read', () => {
    expect(goalValid(null, TODAY)).toBe(false);
    expect(goalValid({ ...LONDON, name: '  ' }, TODAY)).toBe(false);
    expect(goalValid({ ...LONDON, date: TODAY }, TODAY)).toBe(false);
    expect(goalValid({ ...LONDON, date: '2026-01-01' }, TODAY)).toBe(false);
    expect(goalValid({ ...LONDON, date: '2027-02-31' }, TODAY)).toBe(false);
    expect(goalValid({ ...LONDON, date: '24 Apr 2027' }, TODAY)).toBe(false);   // the step stores ISO
    expect(goalValid({ ...LONDON, km: 0 }, TODAY)).toBe(false);
    expect(goalValid({ ...LONDON, target: '45:00' }, TODAY)).toBe(false);
  });
});

describe('week 1 for the watch', () => {
  const p = weekOneProposal(CASES.reveal);
  it('one Add per session, each with its day, title and workout, through the same confirm as every write', () => {
    expect(p.source).toBe('onboarding');
    expect(p.ops).toHaveLength(CASES.reveal.week1.length);
    expect(p.ops.map((o) => o.kind)).toEqual(CASES.reveal.week1.map(() => 'add'));
    expect(p.ops.map((o) => o.sessionId)).toEqual(CASES.reveal.week1.map((s) => s.id));
    expect(p.ops.map((o) => o.workout)).toEqual(CASES.reveal.week1.map((s) => s.workout));
    expect(p.ops.every((o) => o.workout?.schema === 'daash.workout.v1' && o.workout.steps.length > 0)).toBe(true);
    expect(p.ops.every((o) => /^[A-Z][a-z]{2} \d{1,2}$/.test(o.dayLabel) && o.after?.title === o.what)).toBe(true);
  });
  it('the plan is the server\'s to save; the watch only gets the week', () => {
    expect(p.changesPlan).toBe(false);
    expect(p.id).toBe(`onboarding:${CASES.reveal.starts}`);
  });
});

describe('where week 1 is', () => {
  const week = CASES.reveal.week1;
  const op = (sessionId: string, over: Partial<WatchOp>): WatchOp => ({
    id: `b:${sessionId}`, batch: 'b', kind: 'add', sessionId, date: '2026-12-01', dayLabel: 'Tue 1', what: 'x', withWhat: null, before: null, after: null,
    state: 'sending', attempts: 0, nextAttemptAt: null, error: null, createdAt: 0, updatedAt: 0, sentAt: null, onWatchAt: null, ...over,
  } as WatchOp);
  const at = new Date(2026, 10, 30, 20, 14).getTime();
  it('not sent: nothing to say', () => {
    expect(landingFor(week, () => null, 'Forerunner 965')).toBeNull();
  });
  it('sending, then Garmin has it, then on the watch with the time it landed', () => {
    expect(landingFor(week, (id) => (id === week[0].id ? op(id, {}) : null), null)).toEqual({ state: 'sending' });
    expect(landingFor(week, (id) => op(id, { state: 'changed_pending_sync' }), null)).toEqual({ state: 'pending' });
    expect(landingFor(week, (id) => op(id, { state: id === week[0].id ? 'on_watch' : 'changed_pending_sync', onWatchAt: at }), 'Forerunner 965'))
      .toEqual({ state: 'landed', at: '20:14', device: 'Forerunner 965', rest: week.length - 1, total: week.length });
    expect(landingFor(week, (id) => op(id, { state: 'on_watch', onWatchAt: at }), null)).toMatchObject({ rest: 0 });
  });
  it('failed: the queue\'s words, and whether Daash tries again on its own', () => {
    expect(landingFor(week, (id) => op(id, { state: 'failed', error: 'Not sent. Garmin didn\'t answer.', nextAttemptAt: 5, updatedAt: at }), null))
      .toEqual({ state: 'failed', error: 'Not sent. Garmin didn\'t answer.', at: '20:14', retrying: true });
  });
  it('the rest of the week, in words', () => {
    expect(restLine(3, 4)).toBe(' The other 3 sessions of week 1 follow as the watch syncs.');
    expect(restLine(1, 2)).toBe(' The other session of week 1 follows as the watch syncs.');
    expect(restLine(0, 4)).toBe(' The rest of week 1 is on it too.');
    expect(restLine(0, 1)).toBe('');
  });
});

describe('the provider', () => {
  let ob: OnboardingState | null = null;
  function Probe() { ob = useOnboarding(); return null; }
  it('changing the goal or the paces drops the plan made from the old ones', async () => {
    await render(<OnboardingProvider initial={{ goal: LONDON, result: CASES.import, reveal: CASES.reveal }}><Probe /></OnboardingProvider>);
    expect(ob!.reveal).toBe(CASES.reveal);
    await act(async () => { ob!.setGoal({ kind: 'fit' }); });
    expect(ob!.goal).toEqual({ kind: 'fit' });
    expect(ob!.reveal).toBeNull();
    await act(async () => { ob!.setReveal(CASES.reveal_fit); });
    await act(async () => { ob!.setResult(CASES.questions); });
    expect(ob!.result).toBe(CASES.questions);
    expect(ob!.reveal).toBeNull();
  });
  it('finishing marks the device onboarded; the notification answer is kept', async () => {
    const db = await migratedNodeDb();
    await render(<DbContext.Provider value={db}><OnboardingProvider><Probe /></OnboardingProvider></DbContext.Provider>);
    expect(ob!.origin).toBe('start');
    expect(await kv(db).get(ONBOARDED_KEY)).toBeNull();
    await act(async () => { await ob!.rememberNotifications('granted'); await ob!.finish(); });
    expect(await kv(db).get(ONBOARDED_KEY)).toBe(true);
    expect(await kv(db).get<{ answer: string }>(NOTIFICATIONS_KEY)).toMatchObject({ answer: 'granted' });
  });
  it('without a database (it failed to open) finishing still resolves', async () => {
    await render(<OnboardingProvider><Probe /></OnboardingProvider>);
    await expect(ob!.finish()).resolves.toBeUndefined();
  });
  it('outside the provider is a mistake, said plainly', async () => {
    jest.spyOn(console, 'error').mockImplementation(() => {});
    await expect(render(<Probe />)).rejects.toThrow('useOnboarding outside OnboardingProvider');
    (console.error as unknown as { mockRestore: () => void }).mockRestore();
  });
});

describe('the goal step', () => {
  const type = async (id: string, t: string) => { await fireEvent.changeText(screen.getByTestId(id), t); };
  it('Continue waits for a name, a real day after today, and a target that reads', async () => {
    const onContinue = jest.fn();
    await render(wrap(<GoalStep initial={null} today={TODAY} brand onContinue={onContinue} />));
    const go = () => screen.getByTestId('button-Continue');
    expect(go()).toBeDisabled();
    await type('race-name', 'London Marathon');
    await type('race-date', '31 Feb 2027');
    expect(screen.getByLabelText('Write the date as day, month and year, like 24 Apr 2027.')).toBeTruthy();
    await type('race-date', '24 Nov 2026');
    expect(screen.getByLabelText('Pick a race day after today.')).toBeTruthy();
    await type('race-date', '24 Apr 2027');
    expect(screen.getByTestId('race-date-read')).toHaveTextContent('Sat 24 Apr 2027');
    await type('race-target', '45:00');
    expect(screen.getByLabelText('Write the target as h:mm, like 3:15.')).toBeTruthy();
    expect(go()).toBeDisabled();
    await type('race-target', '3:15');
    expect(go()).toBeEnabled();
    await fireEvent.press(go());
    expect(onContinue).toHaveBeenCalledWith(LONDON);
  });
  it('a shorter race reads its target as mm:ss; just get fitter needs nothing more', async () => {
    const onContinue = jest.fn();
    await render(wrap(<GoalStep initial={null} today={TODAY} brand onContinue={onContinue} />));
    await fireEvent.press(screen.getByTestId('distance-10K'));
    await type('race-target', '3:15');
    expect(screen.getByLabelText('Write the target as mm:ss, like 45:00.')).toBeTruthy();
    await fireEvent.press(screen.getByTestId('goal-fit'));
    expect(screen.queryByTestId('race-name')).toBeNull();
    await fireEvent.press(screen.getByTestId('button-Continue'));
    expect(onContinue).toHaveBeenCalledWith({ kind: 'fit' });
  });
  it('coming back keeps what was entered', async () => {
    await render(wrap(<GoalStep initial={LONDON} today={TODAY} brand onContinue={() => {}} />));
    expect(screen.getByTestId('race-name').props.value).toBe('London Marathon');
    expect(screen.getByTestId('race-date').props.value).toBe('Sat 24 Apr 2027');
    expect(screen.getByTestId('button-Continue')).toBeEnabled();
  });
});

describe('the paces step', () => {
  it('connecting first when not signed in, importing when signed in; one brand fill', async () => {
    const { rerender } = await render(wrap(<PacesStep brand busy={false} connected={false} problem={null} onImport={() => {}} onQuestions={() => {}} />));
    expect(screen.getByTestId('button-Connect Garmin')).toBeTruthy();
    expect(brandFills()).toBe(1);
    await rerender(wrap(<PacesStep brand busy={false} connected problem={null} onImport={() => {}} onQuestions={() => {}} />));
    expect(screen.getByTestId('button-Import my runs')).toBeTruthy();
    await rerender(wrap(<PacesStep brand busy connected problem={null} onImport={() => {}} onQuestions={() => {}} />));
    expect(screen.getByTestId('button-Reading your runs…')).toBeDisabled();
  });
  it.each([
    ['offline', 'You’re offline. Reading your runs needs a connection, or answer 4 questions instead.'],
    ['signedOut', 'Sign in on Garmin’s page, then come back here and import.'],
    ['reconnect', 'Garmin needs you to sign in again. Sign in on Garmin’s page, then import.'],
    ['server', 'Couldn’t read your runs just now. Try again, or answer 4 questions instead.'],
  ] as const)('%s: says what happened and what to do, with an icon, never colour alone', async (kind, words) => {
    await render(wrap(<PacesStep brand busy={false} connected={false} problem={kind} onImport={() => {}} onQuestions={() => {}} />));
    expect(screen.getByTestId('import-problem').props.accessibilityLabel).toBe(words);
    expect(words).not.toMatch(/!/);
  });
});

describe('the four questions step', () => {
  it('Continue once the three are answered; a race time is optional but must read', async () => {
    const onContinue = jest.fn();
    await render(wrap(<QuestionsStep brand onContinue={onContinue} />));
    expect(screen.getByTestId('button-Continue')).toBeDisabled();
    for (const id of ['exp-exp', 'days-5', 'weekly-50to70']) await fireEvent.press(screen.getByTestId(id));
    expect(screen.getByTestId('exp-exp').props.accessibilityState).toEqual({ selected: true });
    expect(screen.getByTestId('button-Continue')).toBeEnabled();
    await fireEvent.press(screen.getByTestId('qrace-Half'));
    await fireEvent.changeText(screen.getByTestId('race-time'), '45:00');
    expect(screen.getByLabelText('Write the time as h:mm:ss, like 1:45:00.')).toBeTruthy();
    expect(screen.getByTestId('button-Continue')).toBeDisabled();
    await fireEvent.changeText(screen.getByTestId('race-time'), '1:45:00');
    await fireEvent.press(screen.getByTestId('button-Continue'));
    expect(onContinue).toHaveBeenCalledWith({ experience: 'exp', days: 5, weekly: '50to70', race: { km: 21.0975, secs: 6300 } });
  });
  it('tapping the chosen race again clears it', async () => {
    const onContinue = jest.fn();
    await render(wrap(<QuestionsStep brand onContinue={onContinue} />));
    for (const id of ['exp-new', 'days-3', 'weekly-under30', 'qrace-5K', 'qrace-5K']) await fireEvent.press(screen.getByTestId(id));
    expect(screen.queryByTestId('race-time')).toBeNull();
    await fireEvent.press(screen.getByTestId('button-Continue'));
    expect(onContinue).toHaveBeenCalledWith({ experience: 'new', days: 3, weekly: 'under30', race: null });
  });
});

describe('the result', () => {
  it('each row read out whole; the import says where it came from', async () => {
    await render(wrap(<ResultStep result={CASES.import} device="Forerunner 965" brand onAdjust={() => {}} onRight={() => {}} />));
    expect(screen.getByLabelText('Best recent race, 10K · 43:30, 12 Oct')).toBeTruthy();
    expect(screen.getByTestId('result-connection')).toHaveTextContent('Connected to Garmin · Forerunner 965');
    expect(screen.queryByTestId('result-empty')).toBeNull();
    expect(brandFills()).toBe(1);
  });
  it('nothing to read: says so and what Adjust does', async () => {
    await render(wrap(<ResultStep result={CASES.import_empty} device={null} brand onAdjust={() => {}} onRight={() => {}} />));
    expect(screen.getByText('No runs in the last 12 weeks')).toBeTruthy();
    expect(screen.getByTestId('result-empty')).toHaveTextContent(/use a 10K of 45:00\. Adjust to answer 4 questions/);
    expect(screen.queryByTestId('result-week')).toBeNull();
    expect(screen.getByTestId('result-connection')).toHaveTextContent('Connected to Garmin');
  });
  it('answers have no connection line', async () => {
    await render(wrap(<ResultStep result={CASES.questions as PaceResult} device="Forerunner 965" brand onAdjust={() => {}} onRight={() => {}} />));
    expect(screen.queryByTestId('result-connection')).toBeNull();
  });
});

describe('the plan reveal', () => {
  const words = () => ['phases', 'predicted', 'target', 'volume'].map((id) => screen.getByTestId(id).props.accessibilityLabel);
  const days = () => nodes(screen.getByTestId('week1')).filter((n) => n.props.accessible).map((n) => n.props.accessibilityLabel);
  it('phases, predicted against target, week 1; the numbers are the same with the Coach on or off', async () => {
    const { rerender } = await render(wrap(<RevealView r={CASES.reveal} coach={false} brand onSend={() => {}} />));
    const off = [words(), days()];
    expect(off[0]).toEqual([`Phases: ${CASES.reveal.phases.map((p) => `${p.label} ${p.weeks} weeks`).join(', ')}`, 'Predicted now, 3:20', 'Target, 3:15',
      `km in week 1 · peak ${CASES.reveal.peakKm}, ${CASES.reveal.startKm}`]);
    expect(off[1]).toHaveLength(CASES.reveal.week1.length);
    expect(screen.getByTestId('because')).toHaveTextContent(CASES.reveal.because);
    expect(brandFills()).toBe(1);
    await rerender(wrap(<RevealView r={{ ...CASES.reveal, coachNote: 'Base is long because your weeks have been steady.' }} coach brand onSend={() => {}} />));
    expect([words(), days()]).toEqual(off);
    expect(screen.getByTestId('coach-note')).toHaveTextContent(/Coach · why this shape/);
  });
  it('no race: no prediction or target', async () => {
    await render(wrap(<RevealView r={CASES.reveal_fit} coach={false} brand onSend={() => {}} />));
    expect(screen.getByText('Your fitness plan')).toBeTruthy();
    expect(screen.queryByTestId('predicted')).toBeNull();
    expect(screen.queryByTestId('target')).toBeNull();
  });
  it('a Coach note never shows with the Coach off', async () => {
    await render(wrap(<RevealView r={{ ...CASES.reveal, coachNote: 'x' } as Reveal} coach={false} brand onSend={() => {}} />));
    expect(screen.queryByTestId('coach-note')).toBeNull();
  });
  it.each([
    ['loading', 'Making your plan…', []],
    ['signedOut', 'Connect Garmin to make your plan', ['button-Connect Garmin', 'button-I’ve signed in']],
    ['reconnect', 'Garmin needs you to sign in again', ['button-Reconnect']],
    ['offline', 'Your plan isn’t made yet', ['button-Try again']],
    ['server', 'Your plan isn’t made yet', ['button-Try again']],
  ] as const)('before the plan, %s: says so, with the one thing to do', async (failure, title, buttons) => {
    const onRetry = jest.fn(); const onConnect = jest.fn();
    await render(wrap(<RevealWait failure={failure} brand onRetry={onRetry} onConnect={onConnect} />));
    expect(screen.getByText(title)).toBeTruthy();
    for (const b of buttons) await fireEvent.press(screen.getByTestId(b));
    if (failure === 'signedOut') expect([onConnect.mock.calls.length, onRetry.mock.calls.length]).toEqual([1, 1]);
    if (failure === 'offline') expect(screen.getByText(/You’re offline/)).toBeTruthy();
  });
});

describe('the landing', () => {
  const first = CASES.reveal.week1[0];
  const day = ({ MON: 'Monday', TUE: 'Tuesday', WED: 'Wednesday', THU: 'Thursday', FRI: 'Friday', SAT: 'Saturday', SUN: 'Sunday' } as Record<string, string>)[first.dow];
  const handlers = () => ({ onResend: jest.fn(), onTrouble: jest.fn(), onAllow: jest.fn(), onNotNow: jest.fn(), onLeave: jest.fn() });
  it('landed: the filled success check, 56 pt, labelled; only now the notification ask', async () => {
    const h = handlers();
    await render(wrap(<LandedView first={first} landing={{ state: 'landed', at: '20:14', device: 'Forerunner 965', rest: 2, total: 4 }} brand device="Forerunner 965" {...h} />));
    const check = screen.getByTestId('success-check');
    expect([check.props.width, check.props.height, check.props.accessibilityLabel]).toEqual([56, 56, 'Done']);
    expect(screen.getByText(`${day}’s run is on your watch.`)).toBeTruthy();
    expect(screen.getByTestId('landed-detail')).toHaveTextContent(`${first.title} landed on Forerunner 965 at 20:14. The other 2 sessions of week 1 follow as the watch syncs.`);
    await fireEvent.press(screen.getByTestId('button-Allow notifications'));
    await fireEvent.press(screen.getByTestId('button-Not now'));
    expect([h.onAllow.mock.calls.length, h.onNotNow.mock.calls.length]).toEqual([1, 1]);
    expect(brandFills()).toBe(1);
  });
  it.each(['sending', 'pending'] as const)('%s: no check and no ask yet; going on is fine', async (state) => {
    const h = handlers();
    await render(wrap(<LandedView first={first} landing={{ state }} brand device={null} home="Plan" {...h} />));
    expect(screen.queryByTestId('success-check')).toBeNull();
    expect(screen.queryByTestId('notify-ask')).toBeNull();
    expect(screen.getByTestId('landed-waiting').props.accessibilityLabel).toBe(state === 'pending' ? `${day}’s run arrives at the next sync` : `Sending ${day}’s run to your watch`);
    await fireEvent.press(screen.getByTestId('button-Go to Plan'));
    expect(h.onLeave).toHaveBeenCalled();
  });
  it('failed: the send state\'s words, Resend, Trouble connecting?, and that nothing changed', async () => {
    const h = handlers();
    await render(wrap(<LandedView first={first} landing={{ state: 'failed', error: 'Not sent. Garmin didn\'t answer.', at: '20:14', retrying: true }} brand device="Forerunner 965" {...h} />));
    expect(screen.getByText(`${day}’s run isn’t on your watch yet.`)).toBeTruthy();
    expect(screen.getByText('Nothing changed on your watch. Daash retries when Garmin is back.')).toBeTruthy();
    await fireEvent.press(screen.getByTestId('button-Resend'));
    await fireEvent.press(screen.getByTestId('button-Trouble connecting?'));
    expect([h.onResend.mock.calls.length, h.onTrouble.mock.calls.length]).toEqual([1, 1]);
    expect(screen.queryByTestId('notify-ask')).toBeNull();
  });
});

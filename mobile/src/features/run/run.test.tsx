// Post-run (milestone 7), rendered from the server's view model on iOS and Android
// (this suite runs once per platform), dark and light, Coach off and on.
import { describe, expect, it, jest } from '@jest/globals';
import { fireEvent, render, screen, within } from '@testing-library/react-native';
import { Platform, StyleSheet } from 'react-native';

import { ThemeProvider } from '../../theme/ThemeProvider';
import { colors } from '../../theme/tokens';
import { unmatchedNumbers } from '../../domain/replyGuard';
import { numbersOf, type RunVM } from './model';
import { RunView, scrubWords, shareText, stepScrub } from './RunView';
import { fixtureRun } from './source';

const os = Platform.OS;
const REPS = fixtureRun('2026-11-25-intervals', false)!;
const REPS_COACH = fixtureRun('2026-11-25-intervals', true)!;
const CLUB = fixtureRun('2026-11-26-easy', false)!;

async function draw(vm: RunVM, { coach = false, scheme = 'dark' as 'dark' | 'light' } = {}) {
  const onAsk = jest.fn();
  const utils = await render(<ThemeProvider initial={scheme}><RunView vm={vm} coach={coach} onAsk={onAsk} /></ThemeProvider>);
  return { ...utils, onAsk };
}

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
// Words only: Android's symbols draw as glyph text, which isn't something a runner reads.
const texts = (tree: unknown) => nodes(tree).filter((n) => n.type === 'Text')
  .flatMap((n) => (n.children ?? []).filter((c): c is string => typeof c === 'string' && /[A-Za-z0-9]/.test(c) && !/^[a-z_]+$/.test(c)));
/** What the scrub line reads, per metric shown: the screen reader's value, the same words as the chart labels. */
const scrub = (i: number) => (screen.getByTestId('scrub-area').props.accessibilityValue.text as string).split(', ')[i];
const testIDs = (tree: unknown) => nodes(tree).map((n) => n.props?.testID).filter(Boolean) as string[];

describe.each(['dark', 'light'] as const)(`Post-run on ${os}, %s`, (scheme) => {
  it('in the prompt\'s order: map, title, planned vs actual, the charts, zones', async () => {
    const { toJSON } = await draw(REPS, { scheme });
    const order = testIDs(toJSON()).filter((t) => ['route-map', 'debrief', 'planned', 'charts', 'zones'].includes(t));
    expect(order).toEqual(['route-map', 'planned', 'charts', 'zones']);
    expect(screen.getByRole('header')).toHaveTextContent('6 × 800 m');
    expect(screen.getByText('Intervals')).toBeTruthy();
    expect(screen.getByText('Wed 25 Nov · 06:30')).toBeTruthy();
  });

  it('planned vs actual: each row says on plan or off plan, with an icon as well as the colour', async () => {
    await draw(REPS, { scheme });
    expect(screen.getByLabelText('Distance, plan 11.0 km, done 11.2 km, on plan')).toBeTruthy();
    expect(screen.getByLabelText('Rep pace, plan 3:58 to 4:08, done 4:01 avg, on plan')).toBeTruthy();
    expect(screen.getByLabelText('Reps, plan 6 × 800 m, done 6 of 6, on plan')).toBeTruthy();
    const done = within(screen.getByTestId('planned-Distance')).getByText('11.2 km');
    expect(StyleSheet.flatten(done.props.style).color).toBe(colors[scheme].success);
  });

  it('no brand fill: post-run has no primary action', async () => {
    const { toJSON } = await draw(REPS, { scheme });
    expect(nodes(toJSON()).filter((n) => StyleSheet.flatten(n.props?.style)?.backgroundColor === colors[scheme].brand)).toHaveLength(0);
  });
});

describe(`Post-run on ${os}: charts`, () => {
  it('stacked Pace and HR, each with its axis, units and one takeaway under it', async () => {
    await draw(REPS);
    expect(screen.getByText('Pace · /km')).toBeTruthy();
    expect(screen.getByText('faster ↑')).toBeTruthy();
    expect(screen.getByText('Heart rate · bpm')).toBeTruthy();
    expect(screen.getByTestId('takeaway-pace').props.accessibilityLabel).toBe('Even reps. Fastest and slowest 6 s apart.');
    expect(screen.getByTestId('takeaway-hr').props.accessibilityLabel).toBe('HR drifted 4 bpm across the 6 reps.');
    expect(screen.getByLabelText('Even reps. Fastest and slowest 6 s apart.')).toBeTruthy();
    expect(screen.getByTestId('chart-pace')).toBeTruthy();
    expect(screen.getByTestId('chart-hr')).toBeTruthy();
  });

  it('one scrub line across the metrics: it starts on rep 4, and moving it moves both', async () => {
    await draw(REPS);
    expect(scrub(0)).toBe('Rep 4 · 4:00 /km');
    expect(scrub(1)).toMatch(/^Rep 4 · \d+ bpm$/);
    const area = screen.getByTestId('scrub-area');
    await fireEvent(area, 'layout', { nativeEvent: { layout: { width: 320, height: 300, x: 0, y: 0 } } });
    // 2.4 km in: the middle of rep 1 (2.0 to 2.8 km).
    const x = 38 + (2.4 / REPS.totalKm) * (316 - 38);
    await fireEvent(area, 'responderGrant', { nativeEvent: { locationX: x, locationY: 40 } });
    expect(scrub(0)).toMatch(/^Rep 1 · 3:5\d \/km$/);
    expect(scrub(1)).toMatch(/^Rep 1 · \d+ bpm$/);
    await fireEvent(area, 'responderMove', { nativeEvent: { locationX: 40, locationY: 40 } });
    expect(scrub(0)).toMatch(/^Warm-up · 5:\d\d \/km$/);
  });

  it('for screen readers: a text summary of each chart, and the scrub steps lap by lap', async () => {
    await draw(REPS);
    const area = screen.getByTestId('scrub-area');
    expect(area.props.accessibilityLabel).toBe('Pace by distance, 6 reps around 4:01 per km. Heart rate by distance, from 118 to 172 bpm');
    expect(area.props.accessibilityValue.text).toMatch(/^Rep 4 · 4:00 \/km, Rep 4 · \d+ bpm$/);
    await fireEvent(area, 'accessibilityAction', { nativeEvent: { actionName: 'increment' } });
    expect(scrub(0)).toMatch(/^Recovery · /);
    await fireEvent(area, 'accessibilityAction', { nativeEvent: { actionName: 'decrement' } });
    await fireEvent(area, 'accessibilityAction', { nativeEvent: { actionName: 'decrement' } });
    expect(scrub(0)).toMatch(/^Recovery · /);
    expect(stepScrub(CLUB, 4.6, 1)).toBe(5.6); // no laps: a kilometre at a time
  });

  it('Cadence and Elevation are further tabs, each with its takeaway', async () => {
    await draw(REPS);
    await fireEvent.press(screen.getByTestId('metric-cadence'));
    expect(screen.queryByTestId('chart-pace')).toBeNull();
    expect(screen.getByTestId('chart-cadence')).toBeTruthy();
    expect(screen.getByTestId('takeaway-cadence').props.accessibilityLabel).toMatch(/^Cadence steady, around \d+ spm\.$/);
    await fireEvent.press(screen.getByTestId('metric-elevation'));
    expect(screen.getByTestId('takeaway-elevation').props.accessibilityLabel).toMatch(/^\d+ m up, \d+ m down\.$/);
    expect(screen.getByTestId('metric-elevation').props.accessibilityState).toEqual({ selected: true });
  });

  it('a steady run: no laps, so the scrub reads the distance; its takeaways are splits and drift', async () => {
    await draw(CLUB);
    expect(scrub(0)).toMatch(/^4\.\d km · 5:\d\d \/km$/);
    expect(screen.getByTestId('takeaway-pace').props.accessibilityLabel).toBe('Negative split. Second half 6 s/km faster.');
    expect(screen.getByLabelText('Pace, plan 5:27 to 6:10, done 5:14 avg, off plan')).toBeTruthy();
  });

  it('what wasn\'t recorded is said, not drawn', async () => {
    const bare: RunVM = { ...CLUB, route: null, zones: null, planned: null,
      charts: { ...CLUB.charts, hr: { ...CLUB.charts.hr, domain: null, takeaway: null, summary: 'No heart rate recorded' } } };
    await draw(bare);
    expect(screen.queryByTestId('route-map')).toBeNull();
    expect(screen.queryByTestId('zones')).toBeNull();
    expect(screen.queryByTestId('planned')).toBeNull();
    expect(screen.queryByTestId('chart-hr')).toBeNull();
    expect(screen.getByText('No heart rate recorded')).toBeTruthy();
  });
});

describe(`Post-run on ${os}: zones, route, share`, () => {
  it('HR zones: labelled rows on a text-opacity ramp', async () => {
    await draw(REPS);
    const zones = within(screen.getByTestId('zones'));
    expect(zones.getByText('Heart rate zones')).toBeTruthy();
    REPS.zones!.forEach((z, i) => expect(zones.getByLabelText(`Zone ${i + 1}, ${z.minutes} minutes`)).toBeTruthy());
  });
  it('the route map says what its dots mean', async () => {
    await draw(REPS);
    expect(screen.getByLabelText('Route map, 11.2 km. Hollow dot: start. Solid dot: finish.')).toBeTruthy();
  });
  it('Share sends the run in words, with the screen\'s numbers', () => {
    expect(shareText(REPS)).toBe('6 × 800 m · Wed 25 Nov · 06:30\n11.2 km in 57:04\nEven reps. Fastest and slowest 6 s apart.\nHR drifted 4 bpm across the 6 reps.');
  });
  it('scrub words: the lap, else the distance', () => {
    expect(scrubWords(REPS, 0.5, 'pace')).toMatch(/^Warm-up · /);
    expect(scrubWords(CLUB, 2, 'hr')).toMatch(/^2(\.0)? km · \d+ bpm$/);
  });
});

describe(`Post-run on ${os}: the Coach`, () => {
  it('on: a collapsible debrief after the map, thumbs, and "Ask about this run"', async () => {
    const { toJSON, onAsk } = await draw(REPS_COACH, { coach: true });
    const order = testIDs(toJSON()).filter((t) => ['route-map', 'debrief', 'planned'].includes(t));
    expect(order).toEqual(['route-map', 'debrief', 'planned']);
    expect(screen.getByTestId('debrief-text')).toHaveTextContent(/^Reps averaged 4:01, the fastest and slowest 6 s apart/);
    await fireEvent.press(screen.getByTestId('thumb-up'));
    expect(screen.getByTestId('thumb-up').props.accessibilityState).toEqual({ selected: true });
    await fireEvent.press(screen.getByTestId('ask-run'));
    expect(onAsk).toHaveBeenCalled();
    await fireEvent.press(screen.getByTestId('debrief-toggle'));
    expect(screen.queryByTestId('debrief-text')).toBeNull();
  });

  it('on and off: every number is the same; on adds only the debrief', async () => {
    expect(numbersOf(REPS_COACH)).toEqual(numbersOf(REPS));
    const off = await draw(REPS);
    const offTexts = texts(off.toJSON());
    const on = await draw(REPS_COACH, { coach: true });
    const onTexts = texts(on.toJSON()).filter((t) => !['Coach debrief', 'Ask about this run', REPS_COACH.coach!.debrief].includes(t));
    expect(onTexts).toEqual(offTexts);
  });

  it('the debrief\'s numbers are the run\'s own: no number of its own', () => {
    // The Coach's numbers rule (domain/replyGuard): every number in it is in what the server sent for the run.
    expect(unmatchedNumbers(REPS_COACH.coach!.debrief, REPS)).toEqual([]);
    const series = REPS.series;
    expect(Math.max(...series.hr.filter((h): h is number => h != null))).toBe(172);
  });

  it('off: no debrief, even when the server sent one', async () => {
    await draw(REPS_COACH, { coach: false });
    expect(screen.queryByTestId('debrief')).toBeNull();
  });
});

it(`snapshot: post-run on ${os}`, async () => {
  const { toJSON } = await draw(REPS);
  expect(toJSON()).toMatchSnapshot(`run-${os}`);
});

it(`snapshot: post-run with the Coach on ${os}`, async () => {
  const { toJSON } = await draw(REPS_COACH, { coach: true });
  expect(toJSON()).toMatchSnapshot(`run-coach-${os}`);
});

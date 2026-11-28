// Today (milestone 3), rendered from its view model on iOS and on Android (this
// suite runs once per platform), in dark and light, Coach off and on, at 100% and 200%.
import { describe, expect, it, jest } from '@jest/globals';
import { fireEvent, render, screen, within } from '@testing-library/react-native';
import { Platform, StyleSheet } from 'react-native';

import { DEFAULT_FLAGS } from '../../flags/flags';
import { FlagsProvider } from '../../flags/FlagsProvider';
import { ThemeProvider } from '../../theme/ThemeProvider';
import { colors } from '../../theme/tokens';
import { PERSONA_TODAY, PERSONA_TODAY_COACH } from './fixture';
import { kickerText, numbersOf, type TodayVM } from './model';
import { OptionsSheet, ReadinessSheet, SAME_EITHER_WAY, FIXED_RULES } from './Sheets';
import { TodayView, type TodayActions } from './TodayView';

const os = Platform.OS;
const actions = (): TodayActions => ({
  signal: jest.fn(), recommended: jest.fn(), options: jest.fn(), how: jest.fn(), askWhy: jest.fn(),
  openSession: jest.fn(), refresh: jest.fn(),
});

async function draw(vm: TodayVM, { scheme = 'dark' as 'dark' | 'light', coach = false, brand = true, fontScale = 1, a = actions() } = {}) {
  const utils = await render(
    <ThemeProvider initial={scheme}>
      <FlagsProvider initial={{ ...DEFAULT_FLAGS, coach_enabled: coach }}>
        <TodayView vm={vm} coach={coach} brand={brand} fontScale={fontScale} fresh={{ stale: false, label: '07:02' }}
          refreshing={false} error={null} actions={a} />
      </FlagsProvider>
    </ThemeProvider>,
  );
  return { ...utils, a };
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

/** Every element filled with the brand colour: at most one per screen (build prompt, section 13). */
function brandFills(tree: unknown, scheme: 'dark' | 'light'): number {
  return nodes(tree).filter((n) => StyleSheet.flatten(n.props.style)?.backgroundColor === colors[scheme].brand).length;
}

/** The words a runner reads, in order. */
function texts(tree: unknown): string[] {
  return nodes(tree).filter((n) => n.type === 'Text')
    .flatMap((n) => (n.children ?? []).filter((c): c is string => typeof c === 'string' && c.trim() !== ''));
}

describe.each(['dark', 'light'] as const)(`Today on ${os}, %s`, (scheme) => {
  it('draws the header, readiness, the session and the week', async () => {
    await draw(PERSONA_TODAY, { scheme });
    expect(screen.getByTestId('kicker')).toHaveTextContent(kickerText(['Fri 27 Nov', 'Week 4 of 21']));
    expect(screen.getByText('London in 148 days')).toBeTruthy();
    expect(screen.getByTestId('readiness-word')).toHaveTextContent('Back off');
    expect(screen.getByText('Garmin readiness 26 · 2 of 3 below usual')).toBeTruthy();
    expect(screen.getByText('3 × 2 km at threshold')).toBeTruthy();
    expect(screen.getByLabelText('On your watch · 20:14')).toBeTruthy();
    expect(screen.getByTestId('week')).toBeTruthy();
    expect(screen.getAllByLabelText(/^(Rest|Easy|Intervals|Tempo|Strength|Long run), \d+, (rest day|done|planned)/)).toHaveLength(7);
  });

  it('Back off is the warning word on the warning tint', async () => {
    await draw(PERSONA_TODAY, { scheme });
    const card = StyleSheet.flatten(screen.getByTestId('readiness').props.style);
    expect(card.backgroundColor).toBe(colors[scheme].warningTint);
    expect(StyleSheet.flatten(screen.getByTestId('readiness-word').props.style).color).toBe(colors[scheme].warning);
  });

  it('has exactly one brand fill, the recommended action, and none behind an open sheet', async () => {
    const { toJSON } = await draw(PERSONA_TODAY, { scheme });
    expect(brandFills(toJSON(), scheme)).toBe(1);
    expect(StyleSheet.flatten(screen.getByTestId('button-Make it easier').props.style).backgroundColor).toBe(colors[scheme].brand);
    const behind = await draw(PERSONA_TODAY, { scheme, brand: false });
    expect(brandFills(behind.toJSON(), scheme)).toBe(0);
  });

  it("today's date is an outline, not a fill", async () => {
    const { toJSON } = await draw(PERSONA_TODAY, { scheme });
    const today = within(screen.getByTestId('day-2026-11-27'));
    expect(today.getByText('27')).toBeTruthy();
    expect(brandFills(toJSON(), scheme)).toBe(1);
  });

  it('each signal opens its reasons; the buttons do what they say', async () => {
    const { a } = await draw(PERSONA_TODAY, { scheme });
    await fireEvent.press(screen.getByLabelText('HRV 49 ms, below range. Opens details'));
    expect(a.signal).toHaveBeenCalledWith('hrv');
    await fireEvent.press(screen.getByTestId('button-Make it easier'));
    await fireEvent.press(screen.getByTestId('button-Other options'));
    await fireEvent.press(screen.getByText('3 × 2 km at threshold'));
    expect([a.recommended, a.options, a.openSession].map((f) => (f as jest.Mock).mock.calls.length)).toEqual([1, 1, 1]);
  });
});

describe(`Coach on and off on ${os}`, () => {
  it('Coach off: nothing mentions a Coach', async () => {
    const { toJSON } = await draw(PERSONA_TODAY_COACH, { coach: false });
    expect(screen.queryByTestId('coach-line')).toBeNull();
    expect(texts(toJSON()).join(' ')).not.toMatch(/coach|ask why/i);
  });
  it('Coach on: one observation line with "Ask why", and nothing else changes', async () => {
    const off = texts((await draw(PERSONA_TODAY, { coach: false })).toJSON());
    const { toJSON, a } = await draw(PERSONA_TODAY_COACH, { coach: true });
    expect(screen.getByTestId('coach-line')).toBeTruthy();
    await fireEvent.press(screen.getByText('Ask why'));
    expect(a.askWhy).toHaveBeenCalled();
    const on = texts(toJSON()).filter((t) => t !== 'Ask why' && t !== PERSONA_TODAY_COACH.readiness.coachLine);
    expect(on).toEqual(off);
  });
  it('every number is identical with the Coach on or off', async () => {
    expect(numbersOf(PERSONA_TODAY_COACH)).toEqual(numbersOf(PERSONA_TODAY));
  });
});

describe(`Today at 200% text on ${os}`, () => {
  it('stacks the signals, clamps the explanation to two lines, keeps one brand fill', async () => {
    const { toJSON } = await draw(PERSONA_TODAY_COACH, { coach: true, fontScale: 2 });
    expect(StyleSheet.flatten(screen.getByTestId('signals').props.style).flexDirection).toBe('column');
    expect(screen.getByTestId('coach-line').props.numberOfLines).toBe(2);
    expect(screen.getByTestId('button-How it works')).toBeTruthy();
    expect(brandFills(toJSON(), 'dark')).toBe(1);
    expect(toJSON()).toMatchSnapshot(`today-200-${os}`);
  });
  it('the kicker only wraps between phrases', async () => {
    expect(kickerText(['Fri 27 Nov', 'Week 4 of 21'])).toBe('Fri 27 Nov · Week 4 of 21');
  });
  it('every text scales up to 200% and no further', async () => {
    const { toJSON } = await draw(PERSONA_TODAY, { fontScale: 2 });
    const caps = nodes(toJSON()).filter((n) => n.type === 'Text').map((n) => n.props.maxFontSizeMultiplier).filter((m) => m !== undefined);
    expect(caps.every((m: number) => m <= 2 && m >= 1)).toBe(true);
  });
});

describe(`Today's sheets on ${os}`, () => {
  const hrv = PERSONA_TODAY.readiness.signals[1];
  it('reasons: the signal, its 14 nights against your range, the rule table, the same-either-way line', async () => {
    const done = jest.fn();
    await render(<ThemeProvider initial="dark"><ReadinessSheet signal={hrv} onDone={done} /></ThemeProvider>);
    expect(screen.getByText('HRV · 49 ms')).toBeTruthy();
    expect(screen.getByText('Below your range')).toBeTruthy();
    expect(screen.getByLabelText(/HRV over the last 14 nights\. Your range 54–66 ms\. Last night 49 · below\./)).toBeTruthy();
    for (const [when, word] of [['2 or 3 signals below usual', 'Back off'], ['1 signal below usual', 'Hold'], ['None below, load in range', 'Push']]) {
      expect(screen.getByText(when)).toBeTruthy();
      expect(screen.getByText(word)).toBeTruthy();
    }
    expect(screen.getByText(SAME_EITHER_WAY)).toBeTruthy();
    expect(SAME_EITHER_WAY).toBe('Readiness is worked out the same way with or without the Coach. Not medical advice.');
    // With the Coach off nothing mentions it.
    await render(<ThemeProvider initial="dark"><ReadinessSheet signal={hrv} coach={false} onDone={done} /></ThemeProvider>);
    expect(screen.queryByText(SAME_EITHER_WAY)).toBeNull();
    expect(screen.getByText(FIXED_RULES)).toBeTruthy();
    await fireEvent.press(screen.getByTestId('button-Done'));
    expect(done).toHaveBeenCalled();
  });
  it('"Done" is a secondary button: no brand fill in the sheet', async () => {
    const { toJSON } = await render(<ThemeProvider initial="light"><ReadinessSheet signal={hrv} onDone={jest.fn()} /></ThemeProvider>);
    expect(brandFills(toJSON(), 'light')).toBe(0);
  });
  it('other options: go as planned, swap a day, rest and recover (which counts)', async () => {
    const choose = jest.fn();
    await render(<ThemeProvider initial="dark"><OptionsSheet options={PERSONA_TODAY.options} onChoose={choose} onCancel={jest.fn()} /></ThemeProvider>);
    expect(screen.getByText('Go as planned')).toBeTruthy();
    expect(screen.getByText('Swap a day')).toBeTruthy();
    expect(screen.getByText('Counts toward your active week')).toBeTruthy();
    expect(screen.getByText('Your call. Any change to the watch is confirmed first.')).toBeTruthy();
    await fireEvent.press(screen.getByTestId('option-rest'));
    expect(choose).toHaveBeenCalledWith(PERSONA_TODAY.options[2]);
  });
});

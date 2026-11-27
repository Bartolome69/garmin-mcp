// The workout builder (milestone 6), rendered on iOS and on Android (this suite runs
// once per platform), in dark and light, Coach off and on.
import { describe, expect, it, jest } from '@jest/globals';
import { fireEvent, render, screen, within } from '@testing-library/react-native';
import { Platform, StyleSheet } from 'react-native';

import { ThemeProvider } from '../../theme/ThemeProvider';
import { colors } from '../../theme/tokens';
import { PERSONA_PLAN } from '../plan/fixture';
import { BuilderView } from './BuilderView';
import { devCoachNote, useBuilder } from './useBuilder';

const os = Platform.OS;
const DESIGN = '5x1km at threshold, 2 min easy';

async function draw({ text = DESIGN, coach = false, scheme = 'dark' as 'dark' | 'light', brand = true, vm = PERSONA_PLAN as typeof PERSONA_PLAN | null } = {}) {
  const onSave = jest.fn();
  function Harness() {
    const b = useBuilder({ vm, today: '2026-11-27', initial: text });
    return <BuilderView {...b.view} coach={coach} coachNote={coach ? devCoachNote(vm, b.built, b.day) : null} onSave={onSave} brand={brand} />;
  }
  const utils = await render(<ThemeProvider initial={scheme}><Harness /></ThemeProvider>);
  return { ...utils, onSave };
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
const brandFills = (tree: unknown, scheme: 'dark' | 'light') =>
  nodes(tree).filter((n) => StyleSheet.flatten(n.props.style)?.backgroundColor === colors[scheme].brand).length;
const texts = (tree: unknown) => nodes(tree).filter((n) => n.type === 'Text')
  .flatMap((n) => (n.children ?? []).filter((c): c is string => typeof c === 'string' && c.trim() !== ''));

describe.each(['dark', 'light'] as const)(`Builder on ${os}, %s`, (scheme) => {
  it('the design\'s session: read as, the type chip and estimate, the steps, the adjust row and the day', async () => {
    await draw({ scheme });
    expect(screen.getByTestId('read-as')).toHaveTextContent('Read as 5 repeats · 1 km at threshold · 2 min easy jog · parsed on your phone');
    const steps = within(screen.getByTestId('builder-steps'));
    expect(steps.getByText('Tempo')).toBeTruthy();
    expect(steps.getByText('10.1 km · about 52 min')).toBeTruthy();
    expect(steps.getByText('5 ×')).toBeTruthy();
    expect(steps.getByText('4:23–4:28')).toBeTruthy();
    expect(steps.getAllByText('5:27–6:10')).toHaveLength(2);
    expect(screen.getByText('Threshold from your 10K of 43:30')).toBeTruthy();
    expect(screen.getByText('Day')).toBeTruthy();
    expect(screen.getByTestId('day-pick')).toHaveTextContent('Pick');
  });

  it('"Save and send" is the one brand fill, and gives it up under a sheet', async () => {
    const { toJSON } = await draw({ scheme });
    expect(brandFills(toJSON(), scheme)).toBe(1);
    expect(StyleSheet.flatten(screen.getByTestId('button-Save and send').props.style).backgroundColor).toBe(colors[scheme].brand);
    const behind = await draw({ scheme, brand: false });
    expect(brandFills(behind.toJSON(), scheme)).toBe(0);
  });

  it('the chosen day is a text fill, not brand', async () => {
    await draw({ scheme });
    const chip = screen.getByRole('radio', { selected: true });
    const fill = nodes(chip).map((n) => StyleSheet.flatten(n.props?.style)?.backgroundColor).find(Boolean);
    expect(fill ?? StyleSheet.flatten(chip.props.style)?.backgroundColor).not.toBe(colors[scheme].brand);
  });
});

describe(`Builder on ${os}: states`, () => {
  it('empty: the hint and a placeholder, nothing to save', async () => {
    await draw({ text: '' });
    expect(screen.getByText('Type it how you’d say it. Daash turns it into steps.')).toBeTruthy();
    expect(screen.getByTestId('builder-input').props.placeholder).toBe(DESIGN);
    expect(screen.queryByTestId('button-Save and send')).toBeNull();
    expect(screen.queryByTestId('builder-steps')).toBeNull();
  });

  it('refused: the fix-it, no steps, no save', async () => {
    const { onSave } = await draw({ text: '5km parkrun' });
    expect(screen.getByText('For a race effort give its pace, like "5 km at 5K pace"')).toBeTruthy();
    expect(screen.queryByTestId('builder-steps')).toBeNull();
    expect(screen.getByTestId('button-Save and send')).toBeDisabled();
    await fireEvent.press(screen.getByTestId('button-Save and send'));
    expect(onSave).not.toHaveBeenCalled();
  });

  it('a day with a session already planned says so; a day after hard says that too', async () => {
    await draw();
    await fireEvent.press(screen.getByTestId('day-pick'));
    await fireEvent.press(screen.getByTestId('day-2026-12-03'));
    expect(screen.getByTestId('day-planned')).toHaveTextContent(/is already planned that day\. This adds a second session\.$/);
    expect(screen.getByTestId('rule')).toHaveTextContent(/^Two hard days in a row: Wed intervals and Thu tempo would be back to back\. You can still save it\.$/);
  });

  it('before a watch is connected: paces from a 10K of 45:00, and it says so', async () => {
    await draw({ vm: null });
    expect(screen.getByText('Threshold from a 10K of 45:00 until a watch is connected')).toBeTruthy();
    expect(screen.getByTestId('button-Save and send')).not.toBeDisabled();
  });
});

it(`Coach on and off on ${os}: the same numbers; on drops "parsed on your phone" and adds one card`, async () => {
  const off = await draw();
  const offTexts = texts(off.toJSON());
  expect(screen.queryByTestId('coach-note')).toBeNull();
  const on = await draw({ coach: true });
  expect(screen.getByTestId('coach-note')).toHaveTextContent(/^CoachFits /);
  const onTexts = texts(on.toJSON()).filter((t) => t !== 'Coach' && !t.startsWith('Fits '));
  expect(onTexts).toEqual(offTexts.filter((t) => t !== ' · parsed on your phone'));
});

it(`snapshot: the builder on ${os}`, async () => {
  const { toJSON } = await draw();
  expect(toJSON()).toMatchSnapshot(`builder-${os}`);
});

it(`snapshot: a refusal on ${os}`, async () => {
  const { toJSON } = await draw({ text: '5x1km at thresold, 2 min easy' });
  expect(toJSON()).toMatchSnapshot(`builder-refused-${os}`);
});

// The shared diff, the watch confirm in its three shapes, the Life happened sheet
// and the send states (milestone 5), on iOS and Android (this suite runs once per platform).
import { describe, expect, it, jest } from '@jest/globals';
import { fireEvent, render, screen, within } from '@testing-library/react-native';
import { Platform, processColor, StyleSheet } from 'react-native';

import { ThemeProvider } from '../../theme/ThemeProvider';
import { colors } from '../../theme/tokens';
import { PERSONA_PLAN } from '../plan/fixture';
import { findSession, type Watch } from '../plan/model';
import { DiffView } from './DiffView';
import type { Proposal } from './model';
import { fixtureProposal, MEDICAL, sendProposal, unwatchProposal } from './proposals';
import { ConfirmSheet, LifeSheet } from './Sheets';
import type { Connection } from './transport';
import { WatchCard } from './WatchCard';

const os = Platform.OS;
const CONN: Connection = { provider: 'Garmin', device: 'Forerunner 965', syncedAt: '07:02' };
const UNWELL = fixtureProposal({ kind: 'life', reason: 'unwell', since: '2026-11-27' }, false)!;
const TEMPO = findSession(PERSONA_PLAN, '2026-11-27-tempo')!;

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
const brandFills = (tree: unknown) => nodes(tree).filter((n) => StyleSheet.flatten(n.props.style)?.backgroundColor === colors.dark.brand).length;
const paint = (c: string) => ({ type: 0, payload: processColor(c) });

const drawDiff = (p: Proposal, { coach = false, brand = true } = {}) => {
  const a = { onReview: jest.fn(), onBack: jest.fn(), onAsk: jest.fn() };
  return render(<ThemeProvider initial="dark"><DiffView p={p} coach={coach} brand={brand} {...a} /></ThemeProvider>).then((u) => ({ ...u, ...a }));
};

describe(`The shared diff on ${os}`, () => {
  it('says what changes and why, day by day, before struck through', async () => {
    await drawDiff(UNWELL);
    expect(screen.getByTestId('diff-kicker')).toHaveTextContent('Not feeling 100% · since today');
    expect(screen.getByTestId('diff-title')).toHaveTextContent('2 sessions change');
    expect(screen.getByTestId('diff-subtitle')).toHaveTextContent('54 → 38 km this week. Next week unchanged.');
    const fri = screen.getByTestId('diff-2026-11-27');
    expect(fri.props.accessibilityLabel).toBe('FRI 27: 3 × 2 km at threshold, becomes Easy 3 km, Easy · 3 km');
    const before = within(fri).getByText('3 × 2 km at threshold');
    expect(StyleSheet.flatten(before.props.style)).toMatchObject({ textDecorationLine: 'line-through', color: colors.dark.muted });
    const sat = screen.getByTestId('diff-2026-11-28');
    expect(StyleSheet.flatten(sat.props.style).opacity).toBe(0.55); // unchanged, faded
    expect(sat.props.accessibilityLabel).toBe('SAT 28: Full body (runner), unchanged');
    expect(screen.getByTestId('diff-rule')).toHaveTextContent(/^Rule used/);
    expect(screen.getByTestId('diff-disclaimer')).toHaveTextContent(MEDICAL);
  });

  it('kilometres: before an outline, after solid; unchanged weeks identical; labels only where it changes', async () => {
    await drawDiff(UNWELL);
    const before = (i: number) => screen.getByTestId(`diff-before-${i}`).props;
    const after = (i: number) => screen.getByTestId(`diff-after-${i}`).props;
    expect(before(0).stroke).toEqual(paint(colors.dark.muted));
    expect(before(0).fill).toBeFalsy();
    expect(after(0).fill).toEqual(paint(colors.dark.text));
    expect(before(0).height).toBeGreaterThan(after(0).height); // 54 → 38
    for (const i of [1, 2]) expect([before(i).y, before(i).height]).toEqual([after(i).y, after(i).height]);
    expect(screen.getByLabelText('Kilometres a week. This week: 54 before, 38 after. Next week: unchanged at 55.5. Week 6: unchanged at 60.')).toBeTruthy();
  });

  it('one brand fill, "Review watch changes", lost behind the confirm sheet', async () => {
    const { toJSON, onReview } = await drawDiff(UNWELL);
    expect(brandFills(toJSON())).toBe(1);
    await fireEvent.press(screen.getByTestId('button-Review watch changes'));
    expect(onReview).toHaveBeenCalled();
    const behind = await drawDiff(UNWELL, { brand: false });
    expect(brandFills(behind.toJSON())).toBe(0);
  });

  it('Coach on: one observation in place of the rule, with "Ask about this"; every number the same', async () => {
    const off = await drawDiff(UNWELL);
    const offSubtitle = screen.getByTestId('diff-subtitle').props.children;
    const coached = fixtureProposal({ kind: 'life', reason: 'unwell', since: '2026-11-27' }, true)!;
    const { onAsk } = await drawDiff(coached, { coach: true });
    expect(screen.queryByTestId('diff-rule')).toBeNull();
    expect(within(screen.getByTestId('diff-coach')).getByText(coached.coachNote!)).toBeTruthy();
    expect(screen.getByTestId('diff-subtitle').props.children).toBe(offSubtitle);
    await fireEvent.press(screen.getByText('Ask about this'));
    expect(onAsk).toHaveBeenCalled();
    expect(off).toBeTruthy();
  });

  it('nothing to change says so, and offers only the way back', async () => {
    const { onBack } = await drawDiff(fixtureProposal({ kind: 'life', reason: 'busy', since: '2026-11-27' }, false)!);
    expect(screen.getByTestId('diff-title')).toHaveTextContent('Nothing needs to change');
    expect(screen.queryByTestId('button-Review watch changes')).toBeNull();
    await fireEvent.press(screen.getByTestId('button-Back to the plan'));
    expect(onBack).toHaveBeenCalled();
  });

  it(`snapshot on ${os}`, async () => {
    const { toJSON } = await drawDiff(UNWELL);
    expect(toJSON()).toMatchSnapshot(`diff-${os}`);
  });
});

const drawConfirm = (p: Proposal) => {
  const a = { onConfirm: jest.fn(), onBack: jest.fn() };
  return render(<ThemeProvider initial="dark"><ConfirmSheet p={p} connection={CONN} {...a} /></ThemeProvider>).then((u) => ({ ...u, ...a }));
};

describe(`The watch confirm on ${os}`, () => {
  it('a batch: exactly what the watch gets, the connection, one brand fill', async () => {
    const { toJSON, onConfirm, onBack } = await drawConfirm(UNWELL);
    expect(screen.getByText('2 changes on your watch')).toBeTruthy();
    expect(screen.getByLabelText('Replace, Fri 27, 3 × 2 km at threshold, with Easy 3 km')).toBeTruthy();
    expect(screen.getByLabelText('Replace, Sun 29, Long 18 km easy, with Long 9 km easy')).toBeTruthy();
    expect(screen.getByLabelText('Connected to Garmin · synced 07:02')).toBeTruthy();
    expect(brandFills(toJSON())).toBe(1);
    await fireEvent.press(screen.getByTestId('button-Confirm and send 2 changes'));
    expect(onConfirm).toHaveBeenCalled();
    await fireEvent.press(screen.getByTestId('button-Back'));
    expect(onBack).toHaveBeenCalled();
  });

  it('one session: "Send to Forerunner 965?", what it replaces, when it arrives', async () => {
    await drawConfirm(sendProposal(TEMPO, 1, 'pace /km'));
    expect(screen.getByText('Send to Forerunner 965?')).toBeTruthy();
    expect(screen.getByLabelText('Targets, pace /km, 2% harder')).toBeTruthy();
    expect(screen.getByLabelText('Replaces, the version on your watch')).toBeTruthy();
    expect(screen.getByText('Connected to Garmin. It shows up on the watch next time it syncs with your phone.')).toBeTruthy();
    expect(screen.getByTestId('button-Not now')).toBeTruthy();
  });

  it('Remove from watch: it stays in your plan', async () => {
    const { toJSON } = await drawConfirm(unwatchProposal([TEMPO]));
    expect(screen.getByText('Remove from your watch?')).toBeTruthy();
    expect(screen.getByLabelText('Remove, Fri 27, 3 × 2 km at threshold')).toBeTruthy();
    expect(screen.getByText('It stays in your plan, marked Not on watch. You can send it again any time.')).toBeTruthy();
    expect(screen.getByTestId('button-Keep it')).toBeTruthy();
    expect(brandFills(toJSON())).toBe(1);
  });

  it('no watch name known: "your watch"', async () => {
    await render(<ThemeProvider initial="dark"><ConfirmSheet p={UNWELL} connection={{ provider: 'Garmin', device: null, syncedAt: null }}
      onConfirm={jest.fn()} onBack={jest.fn()} /></ThemeProvider>);
    expect(screen.getByText('This is exactly what your watch gets. Nothing else on the watch changes.')).toBeTruthy();
    expect(screen.getByLabelText('Connected to Garmin')).toBeTruthy();
  });

  it(`snapshot on ${os}`, async () => {
    const { toJSON } = await drawConfirm(UNWELL);
    expect(toJSON()).toMatchSnapshot(`confirm-${os}`);
  });
});

describe(`Life happened on ${os}`, () => {
  it('five reasons, since today by default, the medical line, one brand fill', async () => {
    const onSee = jest.fn();
    const { toJSON } = await render(<ThemeProvider initial="dark"><LifeSheet today="2026-11-27" busy={false} onSee={onSee} /></ThemeProvider>);
    expect(screen.getAllByRole('radio').filter((r) => r.props.testID?.startsWith('reason-')).map((r) => r.props.accessibilityLabel)).toEqual([
      'Not feeling 100%. Cold, tired, run down', 'Niggle or injury. Something hurts when you run', 'Travelling. Fewer days, maybe a treadmill',
      'Race moved. New date or distance', 'Busy week. Fewer days free']);
    expect(screen.getByText(MEDICAL)).toBeTruthy();
    expect(brandFills(toJSON())).toBe(1);
    await fireEvent.press(screen.getByTestId('reason-travelling'));
    await fireEvent.press(screen.getByTestId('since-2026-11-26'));
    await fireEvent.press(screen.getByTestId('button-See the new week'));
    expect(onSee).toHaveBeenCalledWith('travelling', '2026-11-26');
  });
});

describe(`Send states on ${os}`, () => {
  const draw = (watch: Watch, extra: Partial<Parameters<typeof WatchCard>[0]> = {}) => {
    const a = { onSend: jest.fn(), onResend: jest.fn(), onRemove: jest.fn(), onTrouble: jest.fn() };
    return render(<ThemeProvider initial="dark"><WatchCard watch={watch} device="Forerunner 965" error={null} retrying={false} adjusted={false} {...a} {...extra} /></ThemeProvider>)
      .then((u) => ({ ...u, ...a }));
  };

  it('not sent: "Send to watch", the brand fill', async () => {
    const { onSend, toJSON } = await draw({ state: 'not_sent', at: null });
    expect(screen.getByLabelText('Not on your watch')).toBeTruthy();
    await fireEvent.press(screen.getByTestId('button-Send to watch'));
    expect(onSend).toHaveBeenCalled();
    expect(brandFills(toJSON())).toBe(1);
  });

  it('sending: muted, with a spinner, naming the watch', async () => {
    const { toJSON } = await draw({ state: 'sending', at: null });
    expect(screen.getByLabelText('Sending to Forerunner 965')).toBeTruthy();
    expect(screen.getByText('now')).toBeTruthy();
    expect(brandFills(toJSON())).toBe(0);
  });

  it('accepted: "Changed · arrives at next sync" in warning', async () => {
    await draw({ state: 'changed_pending_sync', at: null });
    const words = screen.getByText('Changed · arrives at next sync');
    expect(StyleSheet.flatten(words.props.style).color).toBe(colors.dark.warning);
  });

  it('on your watch: text-2, with the time, and "Remove from watch" as an outline', async () => {
    const { onRemove, toJSON } = await draw({ state: 'on_watch', at: '20:14' });
    expect(StyleSheet.flatten(screen.getByText('On your watch · 20:14').props.style).color).toBe(colors.dark.text2);
    await fireEvent.press(screen.getByTestId('button-Remove from watch'));
    expect(onRemove).toHaveBeenCalled();
    expect(brandFills(toJSON())).toBe(0);
  });

  it('failed: icon and words in brand text, "Resend", "Trouble connecting?", nothing changed on the watch', async () => {
    const { onResend, onTrouble } = await draw({ state: 'failed', at: '20:14' }, { error: 'Not sent. Garmin didn’t answer.', retrying: true });
    expect(StyleSheet.flatten(screen.getByText('Not sent. Garmin didn’t answer.').props.style).color).toBe(colors.dark.brandText);
    expect(screen.getByText('Nothing changed on your watch. Daash retries when Garmin is back.')).toBeTruthy();
    await fireEvent.press(screen.getByTestId('button-Resend'));
    await fireEvent.press(screen.getByTestId('button-Trouble connecting?'));
    expect(onResend).toHaveBeenCalled();
    expect(onTrouble).toHaveBeenCalled();
  });
});

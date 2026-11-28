// Plus screens (milestone 10), on iOS and Android (this suite runs once per
// platform), dark and light: the paywall and trial sheet against the boards, one
// brand fill, Plus in You, and the widgets setup.
import { describe, expect, it, jest } from '@jest/globals';
import { fireEvent, render, screen, within } from '@testing-library/react-native';
import { Platform, StyleSheet } from 'react-native';

import { ThemeProvider } from '../../theme/ThemeProvider';
import { colors } from '../../theme/tokens';
import { PERSONA_PLAN } from '../plan/fixture';
import { PERSONA_TODAY } from '../today/fixture';
import { buildSnapshot } from '../widgets/snapshot';
import { WidgetsView, smallWords } from '../widgets/WidgetsView';
import { FREE, plusStatus } from './entitlement';
import { ALWAYS_FREE, PaywallView, PlusSection, TrialSheet, plusItems } from './PaywallView';
import { PRODUCT_IDS, type Product } from './store';

const os = Platform.OS;
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
  nodes(tree).filter((n) => StyleSheet.flatten(n.props?.style)?.backgroundColor === colors[scheme].brand).length;

const paywallActions = () => ({ start: jest.fn(), close: jest.fn(), restore: jest.fn(), manage: jest.fn() });
const PRODUCT: Product = { id: PRODUCT_IDS.monthly, price: '4,99 €', period: 'month', trialDays: 7, title: 'Daash Plus' };
const READ = new Date(2026, 10, 27, 7, 2).getTime();

describe.each(['dark', 'light'] as const)(`Paywall on ${os}, %s`, (scheme) => {
  it('value first: kicker, headline, faded trend, Plus items, Always free, one brand fill', async () => {
    const a = paywallActions();
    const { toJSON } = await render(<ThemeProvider initial={scheme}>
      <PaywallView coach={false} coachLimit={10} plus={false} trialOffered={null} brand os={os} actions={a} />
    </ThemeProvider>);
    expect(screen.getByText('Daash Plus')).toBeTruthy();
    expect(screen.getByText('See where your training is heading.')).toBeTruthy();
    expect(screen.getByTestId('paywall-preview').props.accessibilityLabel).toBe('A preview of the prediction trend and race-day projection');
    for (const i of plusItems(false, 10)) expect(screen.getByLabelText(`${i.title}, ${i.sub}`)).toBeTruthy();
    for (const t of ALWAYS_FREE) expect(within(screen.getByTestId('always-free')).getByLabelText(t)).toBeTruthy();
    expect(screen.queryByText(/Coach/)).toBeNull();
    expect(brandFills(toJSON(), scheme)).toBe(1);
    await fireEvent.press(screen.getByTestId('button-Try Plus free for 7 days'));
    expect(a.start).toHaveBeenCalled();
    await fireEvent.press(screen.getByTestId('paywall-restore'));
    expect(a.restore).toHaveBeenCalled();
    await fireEvent.press(screen.getByTestId('paywall-close'));
    expect(a.close).toHaveBeenCalled();
  });

  it('behind the trial sheet it loses its brand fill; Plus already: manage, no offer', async () => {
    const { toJSON, rerender } = await render(<ThemeProvider initial={scheme}>
      <PaywallView coach={false} coachLimit={10} plus={false} trialOffered brand={false} os={os} actions={paywallActions()} />
    </ThemeProvider>);
    expect(brandFills(toJSON(), scheme)).toBe(0);
    await rerender(<ThemeProvider initial={scheme}>
      <PaywallView coach coachLimit={10} plus trialOffered brand os={os} actions={paywallActions()} />
    </ThemeProvider>);
    expect(screen.getByTestId('paywall-have')).toHaveTextContent('You have Daash Plus.');
    expect(screen.queryByTestId('button-Try Plus free for 7 days')).toBeNull();
    expect(screen.getByTestId('button-Manage subscription')).toBeTruthy();
  });
});

describe(`Paywall copy on ${os}`, () => {
  it('Coach on: the daily limit line, with the flags’ number', () => {
    expect(plusItems(true, 12).at(-1)).toEqual({ title: 'Coach without a daily limit', sub: 'Free has 12 messages a day' });
    expect(plusItems(false, 12).map((i) => i.title)).toEqual(
      ['Race prediction trend', 'Advanced plan styles', 'Advanced merge rules', 'Home and lock screen widgets']);
  });

  it('cancel words name the platform’s store', async () => {
    await render(<ThemeProvider initial="dark"><PaywallView coach={false} coachLimit={10} plus={false} trialOffered brand os={os} actions={paywallActions()} /></ThemeProvider>);
    expect(screen.getByText(`Plus renews until you cancel. Cancel anytime in ${os === 'android' ? 'Google Play' : 'the App Store'}.`)).toBeTruthy();
  });
});

describe.each(['dark', 'light'] as const)(`Free week sheet on ${os}, %s`, (scheme) => {
  it('the timeline with the store’s price, the promise, one brand fill', async () => {
    const actions = { start: jest.fn(), notNow: jest.fn(), retry: jest.fn() };
    const { toJSON } = await render(<ThemeProvider initial={scheme}>
      <TrialSheet product={PRODUCT} loading={false} error={null} busy={false} outcome={null} brand actions={actions} />
    </ThemeProvider>);
    expect(screen.getByText('How your free week works')).toBeTruthy();
    expect(screen.getByLabelText('Today: Everything in Plus unlocks')).toBeTruthy();
    expect(screen.getByLabelText('Day 5: We remind you the trial is ending')).toBeTruthy();
    expect(screen.getByLabelText('Day 7: 4,99 € a month starts, unless you cancel')).toBeTruthy();
    expect(screen.getByText('No payment due today. Cancel anytime.')).toBeTruthy();
    expect(screen.getByText('Plans, Today and sending to your watch stay free either way.')).toBeTruthy();
    expect(brandFills(toJSON(), scheme)).toBe(1);
    await fireEvent.press(screen.getByTestId('button-Start free week'));
    expect(actions.start).toHaveBeenCalled();
    await fireEvent.press(screen.getByTestId('button-Not now'));
    expect(actions.notNow).toHaveBeenCalled();
  });

  it('asking the store: a spinner in words, Start waits', async () => {
    await render(<ThemeProvider initial={scheme}>
      <TrialSheet product={null} loading error={null} busy={false} outcome={null} brand actions={{ start: jest.fn(), notNow: jest.fn(), retry: jest.fn() }} />
    </ThemeProvider>);
    expect(screen.getByLabelText('Asking the store for the price')).toBeTruthy();
    expect(screen.getByTestId('button-Start free week')).toBeDisabled();
  });
});

describe(`Plus in You on ${os}`, () => {
  const sectionActions = () => ({ open: jest.fn(), widgets: jest.fn(), restore: jest.fn(), manage: jest.fn() });
  it('Free: Get Plus opens the paywall; Widgets locked; Restore purchases', async () => {
    const a = sectionActions();
    await render(<ThemeProvider initial="dark"><PlusSection status={plusStatus({ ...FREE, lapsed: null }, READ, os, false)} plus={false} actions={a} /></ThemeProvider>);
    expect(screen.getByTestId('row-plus').props.accessibilityLabel).toBe('Daash Plus, Prediction trend, plan styles, widgets');
    expect(screen.getByTestId('row-widgets').props.accessibilityLabel).toBe('Widgets, Plus. Home and lock screen');
    await fireEvent.press(screen.getByTestId('row-plus'));
    expect(a.open).toHaveBeenCalled();
    await fireEvent.press(screen.getByTestId('row-restore'));
    expect(a.restore).toHaveBeenCalled();
    await fireEvent.press(screen.getByTestId('row-widgets'));
    expect(a.widgets).toHaveBeenCalled();
  });

  it('On Plus (the YouPlus board): Active, since when, manage where', async () => {
    const a = sectionActions();
    const status = plusStatus({ tier: 'plus', expiresAt: null, source: 'web', since: new Date(2026, 10, 3).getTime(), willRenew: true, productId: null }, READ, os, false);
    await render(<ThemeProvider initial="dark"><PlusSection status={status} plus actions={a} /></ThemeProvider>);
    expect(screen.getByTestId('plus-status')).toHaveTextContent('Since Nov · manage on daash.run');
    expect(screen.getByTestId('plus-badge')).toHaveTextContent('Active');
    await fireEvent.press(screen.getByTestId('row-manage'));
    expect(a.manage).toHaveBeenCalled();
    expect(screen.queryByTestId('row-restore')).toBeNull();
  });
});

describe.each(['dark', 'light'] as const)(`Widgets setup on ${os}, %s`, (scheme) => {
  const snapshot = buildSnapshot({ today: { data: PERSONA_TODAY, fetchedAt: READ }, block: PERSONA_PLAN.block, plus: true, now: READ + 60000 });

  it('Plus: the previews, as the widgets will draw them, and how to add one', async () => {
    await render(<ThemeProvider initial={scheme}><WidgetsView snapshot={snapshot} plus os={os} onPlus={jest.fn()} /></ThemeProvider>);
    expect(screen.getByTestId('widget-small').props.accessibilityLabel).toBe('Readiness 26 Back off. Today · Tempo, 3 × 2 km, on watch');
    expect(screen.getByTestId('widget-countdown').props.accessibilityLabel).toBe('London, 148 days, Sat 24 Apr');
    expect(screen.getByTestId('widget-medium').props.accessibilityLabel).toMatch(/^Week 4 of 21 · Base\. This week · 28 of 54 km\. M 23 Rest done/);
    expect(screen.getByTestId('widget-lock').props.accessibilityLabel).toBe('Lock screen. 26 Back off. Back off · Tempo 3 × 2 km, On your watch · 10 km');
    expect(screen.getByText('Add a widget')).toBeTruthy();
    expect(screen.queryByTestId('widgets-locked')).toBeNull();
  });

  it('Free: the same previews faded behind a lock that opens Plus', async () => {
    const onPlus = jest.fn();
    await render(<ThemeProvider initial={scheme}><WidgetsView snapshot={snapshot} plus={false} os={os} onPlus={onPlus} /></ThemeProvider>);
    expect(screen.getByTestId('widgets-locked')).toBeTruthy();
    await fireEvent.press(screen.getByTestId('widgets-see-plus'));
    expect(onPlus).toHaveBeenCalled();
    expect(screen.getByText('Once you have Plus')).toBeTruthy();
  });

  it('old data shows when it’s from; nothing cached says so', async () => {
    const old = buildSnapshot({ today: { data: PERSONA_TODAY, fetchedAt: READ }, plus: true, now: READ + 3 * 3600000 });
    expect(smallWords(old)).toMatch(/Updated 07:02$/);
    const empty = buildSnapshot({ today: null, plus: true, now: READ });
    await render(<ThemeProvider initial={scheme}><WidgetsView snapshot={empty} plus os={os} onPlus={jest.fn()} /></ThemeProvider>);
    expect(screen.getByTestId('widget-message').props.accessibilityLabel).toBe('Open Daash to load today.');
  });
});

it(`snapshot: paywall and free week sheet on ${os}`, async () => {
  const paywall = await render(<ThemeProvider initial="dark"><PaywallView coach coachLimit={10} plus={false} trialOffered brand os={os} actions={paywallActions()} /></ThemeProvider>);
  expect(paywall.toJSON()).toMatchSnapshot(`paywall-${os}`);
  const sheet = await render(<ThemeProvider initial="dark">
    <TrialSheet product={PRODUCT} loading={false} error={null} busy={false} outcome={null} brand actions={{ start: jest.fn(), notNow: jest.fn(), retry: jest.fn() }} />
  </ThemeProvider>);
  expect(sheet.toJSON()).toMatchSnapshot(`trial-sheet-${os}`);
});

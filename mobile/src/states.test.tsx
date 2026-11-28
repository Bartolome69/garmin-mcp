// Every screen's states over the real routes (hardening): no network, an expired
// Garmin login, signed out, a slow first answer, and empty data. And the rule that
// matters most: nothing reaches the watch without the confirm sheet, retries included.
import { beforeEach, describe, expect, it, jest } from '@jest/globals';
import { act, fireEvent, renderRouter, screen, within } from 'expo-router/testing-library';
import { Stack } from 'expo-router';
import type { ReactNode } from 'react';
import { Linking } from 'react-native';

// What the server does in each test: answer from the fixtures, fail in a given way, or never answer.
let mockMode: 'ok' | 'offline' | 'reconnect' | 'signedOut' | 'hang' | 'empty' = 'ok';
function mockAnswer<T>(value: () => T, empty?: (v: T) => T): () => Promise<T> {
  return async () => {
    const c = jest.requireActual('./api/client') as typeof import('./api/client');
    if (mockMode === 'offline') throw new c.Offline();
    if (mockMode === 'reconnect') throw new c.ReconnectNeeded('Garmin');
    if (mockMode === 'signedOut') throw new c.NotSignedIn();
    if (mockMode === 'hang') return new Promise<T>(() => {});
    return mockMode === 'empty' && empty ? empty(value()) : value();
  };
}
jest.mock('./features/today/source', () => ({
  todaySource: () => mockAnswer(() => (jest.requireActual('./features/today/fixture') as { PERSONA_TODAY: Record<string, unknown> }).PERSONA_TODAY,
    (v) => ({ ...v, session: null })),
}));
jest.mock('./features/plan/source', () => ({
  planSource: () => mockAnswer(() => (jest.requireActual('./features/plan/fixture') as { PERSONA_PLAN: Record<string, unknown> }).PERSONA_PLAN,
    (v) => ({ ...v, weeks: [], block: null })),
}));
jest.mock('./features/run/source', () => {
  const real = jest.requireActual('./features/run/source') as typeof import('./features/run/source');
  return { ...real, runSource: (id: string, coach: boolean) => mockAnswer(() => real.fixtureRun(id, coach)!) };
});
jest.mock('./features/connections/source', () => {
  const real = jest.requireActual('./features/connections/source') as typeof import('./features/connections/source');
  return { ...real, fetchConnections: mockAnswer(() => real.fixtureConnections(),
    (v) => ({ ...v, providers: [], priority: [], activities: [], merges: [], keptApart: [] })), sendChanges: async () => true };
});

import TabsLayout from './app/(tabs)/_layout';
import CoachIndex from './app/(tabs)/coach/index';
import CrewIndex from './app/(tabs)/crew/index';
import PlanLayout from './app/(tabs)/plan/_layout';
import PlanIndex from './app/(tabs)/plan/index';
import PlanRun from './app/(tabs)/plan/run/[id]';
import PlanSession from './app/(tabs)/plan/session/[id]';
import TodayLayout from './app/(tabs)/today/_layout';
import TodayIndex from './app/(tabs)/today/index';
import YouLayout from './app/(tabs)/you/_layout';
import YouConnections from './app/(tabs)/you/connections';
import YouIndex from './app/(tabs)/you/index';
import WatchConfirm from './app/watch/confirm';
import { PERSONA_PLAN } from './features/plan/fixture';
import { findSession } from './features/plan/model';
import { skipProposal } from './features/watch/proposals';
import { memoryStore } from './features/watch/store';
import { FakeGarmin } from './features/watch/transport';
import { useWatch, WatchProvider, type WatchContext } from './features/watch/WatchProvider';
import { DEFAULT_FLAGS } from './flags/flags';
import { FlagsProvider } from './flags/FlagsProvider';
import { ThemeProvider } from './theme/ThemeProvider';

let queue: WatchContext | null = null;
function Probe() { queue = useWatch(); return null; }

function app(garmin = new FakeGarmin(), store = memoryStore()) {
  const Root = ({ children }: { children?: ReactNode }) => (
    <ThemeProvider initial="dark">
      <FlagsProvider initial={DEFAULT_FLAGS}>
        <WatchProvider transport={garmin} store={store} auto={false} pollEveryMs={0}>
          <Probe />
          {children ?? <Stack screenOptions={{ headerShown: false }} />}
        </WatchProvider>
      </FlagsProvider>
    </ThemeProvider>
  );
  return {
    _layout: () => <Root />, '(tabs)/_layout': TabsLayout,
    '(tabs)/today/_layout': TodayLayout, '(tabs)/today/index': TodayIndex,
    '(tabs)/plan/_layout': PlanLayout, '(tabs)/plan/index': PlanIndex, '(tabs)/plan/session/[id]': PlanSession, '(tabs)/plan/run/[id]': PlanRun,
    '(tabs)/coach/index': CoachIndex, '(tabs)/crew/index': CrewIndex,
    '(tabs)/you/_layout': YouLayout, '(tabs)/you/index': YouIndex, '(tabs)/you/connections': YouConnections,
    'watch/confirm': WatchConfirm,
  };
}

/** Spinners on screen, read from what's drawn. */
const spinners = () => {
  const out: unknown[] = [];
  const walk = (n: unknown) => {
    if (!n || typeof n !== 'object') return;
    if (Array.isArray(n)) { n.forEach(walk); return; }
    const node = n as { type: string; children: unknown[] | null };
    if (/ActivityIndicator/.test(node.type)) out.push(node);
    (node.children ?? []).forEach(walk);
  };
  walk(screen.toJSON());
  return out;
};

beforeEach(() => { mockMode = 'ok'; });

describe.each([
  ['Today', '/today', 'today'],
  ['Plan', '/plan', 'plan'],
  ['Post-run', '/plan/run/2026-11-25-intervals', 'run-missing'],
  ['Connections', '/you/connections', 'connections-missing'],
])('%s', (_, url, id) => {
  it('slow first answer: a skeleton, never a blank screen or a spinner', async () => {
    mockMode = 'hang';
    await renderRouter(app(), { initialUrl: url });
    expect(await screen.findByTestId(`${id}-loading`)).toBeTruthy();
    expect(spinners()).toHaveLength(0);
  });
  it('no network and nothing on the phone: says so, offers Try again, no crash', async () => {
    mockMode = 'offline';
    await renderRouter(app(), { initialUrl: url });
    const state = within(await screen.findByTestId(`${id}-error`));
    expect(state.getByText('You’re offline. It loads as soon as you’re back online.')).toBeTruthy();
    expect(state.getByTestId('button-Try again')).toBeTruthy();
    expect(spinners()).toHaveLength(0);
  });
  it('expired Garmin login: a clear Reconnect, which opens Garmin\'s sign-in', async () => {
    mockMode = 'reconnect';
    const open = jest.spyOn(Linking, 'openURL').mockResolvedValue(undefined as never);
    await renderRouter(app(), { initialUrl: url });
    const state = within(await screen.findByTestId(`${id}-error`));
    expect(state.getByText('Garmin needs you to sign in again')).toBeTruthy();
    await fireEvent.press(state.getByTestId('button-Reconnect'));
    expect(open).toHaveBeenCalledWith(expect.stringMatching(/\/connect\?app=native$/));
    expect(spinners()).toHaveLength(0);
    open.mockRestore();
  });
  it('signed out: says so, with Sign in', async () => {
    mockMode = 'signedOut';
    await renderRouter(app(), { initialUrl: url });
    const state = within(await screen.findByTestId(`${id}-error`));
    expect(state.getByText('You’re signed out')).toBeTruthy();
    expect(state.getByTestId('button-Sign in')).toBeTruthy();
  });
});

describe('empty: a new runner', () => {
  it('Plan with no plan: says so, and offers planning week by week, not a blank week', async () => {
    mockMode = 'empty';
    await renderRouter(app(), { initialUrl: '/plan' });
    const empty = within(await screen.findByTestId('plan-empty'));
    expect(empty.getByText('No plan yet')).toBeTruthy();
    expect(empty.getByTestId('button-Or plan week by week')).toBeTruthy();
  });
  it('Today with nothing planned: a rest day, said kindly, no crash', async () => {
    mockMode = 'empty';
    await renderRouter(app(), { initialUrl: '/today' });
    const card = within(await screen.findByTestId('session-card'));
    expect(card.getByText('Rest day')).toBeTruthy();
    expect(card.getByText('Nothing planned today. Rest counts toward your active week.')).toBeTruthy();
  });
  it('Connections with nothing connected: says so, with Connect Garmin; You says so too', async () => {
    mockMode = 'empty';
    await renderRouter(app(), { initialUrl: '/you/connections' });
    expect(within(await screen.findByTestId('connections-empty')).getByTestId('button-Connect Garmin')).toBeTruthy();
    expect(screen.getByLabelText('Source priority, Nothing connected yet')).toBeTruthy();
  });
});

describe('cached content when the login expires', () => {
  it('Today keeps showing the day, with a Reconnect banner over it', async () => {
    await renderRouter(app(), { initialUrl: '/today' });
    await screen.findByTestId('session-card');
    mockMode = 'reconnect';
    // Pull to refresh: the refresh fails, the day stays, and the banner says what to do.
    await act(async () => { screen.getByTestId('today').props.refreshControl.props.onRefresh(); });
    const banner = within(await screen.findByTestId('failure-banner'));
    expect(banner.getByText('Garmin needs you to sign in again')).toBeTruthy();
    expect(banner.getByText('Showing what’s on your phone.')).toBeTruthy();
    expect(screen.getByTestId('session-card')).toBeTruthy();
  });
  it('offline over cached content: no banner (the "Updated …" label says it), no crash', async () => {
    await renderRouter(app(), { initialUrl: '/today' });
    await screen.findByTestId('session-card');
    mockMode = 'offline';
    await act(async () => { screen.getByTestId('today').props.refreshControl.props.onRefresh(); });
    expect(screen.queryByTestId('failure-banner')).toBeNull();
    expect(screen.getByTestId('session-card')).toBeTruthy();
  });
});

describe('nothing reaches the watch without the confirm sheet, retries included', () => {
  it('a staged change sends nothing until confirmed, however often the queue runs', async () => {
    const g = new FakeGarmin();
    await renderRouter(app(g), { initialUrl: '/plan' });
    await screen.findByTestId('week-title');
    await act(async () => { queue!.stage(skipProposal(findSession(PERSONA_PLAN, '2026-12-02-intervals')!)); });
    for (let i = 0; i < 3; i++) await act(async () => { await queue!.pump(); });
    expect(g.sent).toHaveLength(0);
    expect(queue!.ops).toHaveLength(0);
  });
  it('a failed send retries only that confirmed operation: same id, nothing new', async () => {
    const g = new FakeGarmin(['offline', 'offline'], 'ok', 1);
    await renderRouter(app(g), { initialUrl: '/plan' });
    await screen.findByTestId('week-title');
    const p = skipProposal(findSession(PERSONA_PLAN, '2026-12-02-intervals')!);
    await act(async () => { queue!.stage(p); queue!.confirm(p.id); });
    await act(async () => { await queue!.pump(); });
    const [op] = queue!.ops;
    expect(op.state).toBe('failed');
    await act(async () => { queue!.resend(op.id); await queue!.pump(); });
    await act(async () => { queue!.resend(op.id); await queue!.pump(); });
    expect(new Set(g.sent.map((o) => o.id))).toEqual(new Set([op.id]));
    expect(queue!.ops).toHaveLength(1);
  });
  it('after a restart, the queue resumes only what was confirmed before it', async () => {
    const store = memoryStore();
    const g1 = new FakeGarmin(['offline'], 'ok', 1);
    await renderRouter(app(g1, store), { initialUrl: '/plan' });
    await screen.findByTestId('week-title');
    const p = skipProposal(findSession(PERSONA_PLAN, '2026-12-02-intervals')!);
    await act(async () => { queue!.stage(p); queue!.confirm(p.id); });
    await act(async () => { await queue!.pump(); });
    const confirmed = queue!.ops.map((o) => o.id);
    // Staged but never confirmed: must not survive.
    await act(async () => { queue!.stage(skipProposal(findSession(PERSONA_PLAN, '2026-12-03-easy')!)); });
    const g2 = new FakeGarmin([], 'ok', 1);
    await renderRouter(app(g2, store), { initialUrl: '/plan' });
    await screen.findByTestId('week-title');
    await act(async () => { queue!.ops.filter((o) => o.state === 'failed').forEach((o) => queue!.resend(o.id)); await queue!.pump(); });
    expect(g2.sent.map((o) => o.id)).toEqual(confirmed);
  });
  it('in the code: only the queue sends, and only confirm makes operations', () => {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const fs = require('fs') as typeof import('fs');
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const path = require('path') as typeof import('path');
    const files: string[] = [];
    const walk = (d: string) => { for (const f of fs.readdirSync(d)) { const p = path.join(d, f); if (fs.statSync(p).isDirectory()) walk(p); else if (/\.tsx?$/.test(f) && !/\.test\./.test(f)) files.push(p); } };
    walk(path.join(__dirname));
    const where = (re: RegExp) => files.filter((f) => re.test(fs.readFileSync(f, 'utf8'))).map((f) => path.relative(__dirname, f));
    expect(where(/\.send\(op\)|transport\.send\(/)).toEqual(['features/watch/WatchProvider.tsx']);
    expect(where(/confirmOps\(p\.ops/)).toEqual(['features/watch/WatchProvider.tsx']);
    expect(where(/\/app\/api\/v1\/watch\/ops/)).toEqual(['features/watch/transport.ts']);
    // The Coach: only "Not now" reaches the pilot's /action (its Confirm writes to Garmin
    // itself, past the confirm sheet and the queue); a Coach proposal opens the shared diff.
    expect(where(/\/action/)).toEqual(['features/coach/api.ts']);
    expect(where(/decision:\s*['"]confirm['"]|['"]confirm['"]\s*\)|Decision = [^;]*confirm/)).toEqual([]);
    expect(where(/watch\.confirm\(/)).toEqual(['app/watch/confirm.tsx']);
  });
});

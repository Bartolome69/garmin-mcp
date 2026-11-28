// Performance pins (milestone 12; docs/native-app/PERFORMANCE.md): the screens don't redraw
// when nothing they show changed.
//   - Today: a watch-queue tick (idle, or a send for another week's session) doesn't redraw
//     TodayView; a change to today's session does.
//   - The Coach: each streamed token redraws the reply it belongs to, never the messages
//     before it.
//   - Long lists are virtualised: the Coach thread and the post-run laps.
import { beforeEach, describe, expect, it } from '@jest/globals';
import { act, fireEvent, renderRouter, screen } from 'expo-router/testing-library';
import { Platform } from 'react-native';

import type { CoachApi } from './features/coach/api';
import { FakeCoach } from './features/coach/fake';
import type { CoachEvent } from './features/coach/sse';
import { PERSONA_PLAN } from './features/plan/fixture';
import { findSession } from './features/plan/model';
import { skipProposal } from './features/watch/proposals';
import { app, COACH_MESSAGES, openScreen, SCREENS, watchQueue } from './testing/screens';
import { linePoints } from './features/run/RunView';
import { renderCounts } from './ui/hooks';

beforeEach(() => renderCounts.clear());
const count = (name: string) => renderCounts.get(name) ?? 0;

describe(`Today and the watch queue on ${Platform.OS}`, () => {
  it('an idle tick and a send for another week leave TodayView alone; a change to today redraws it', async () => {
    await openScreen(SCREENS.find((c) => c.name === 'Today')!);
    const settled = count('TodayView');
    expect(settled).toBeGreaterThan(0);

    await act(async () => { await watchQueue().pump(); });
    expect(count('TodayView')).toBe(settled);

    // Next week's Wednesday: staged, confirmed and sent. Not on Today's screen.
    const later = skipProposal(findSession(PERSONA_PLAN, '2026-12-02-intervals')!);
    await act(async () => { watchQueue().stage(later); });
    await act(async () => { watchQueue().confirm(later.id); });
    await act(async () => { await watchQueue().pump(); });
    expect(watchQueue().ops.some((o) => o.batch.startsWith(later.id))).toBe(true);
    expect(count('TodayView')).toBe(settled);

    // Today's tempo skipped: Today shows it, so it redraws.
    const today = skipProposal(findSession(PERSONA_PLAN, '2026-11-27-tempo')!);
    await act(async () => { watchQueue().stage(today); });
    await act(async () => { watchQueue().confirm(today.id); });
    await act(async () => { await watchQueue().pump(); });
    expect(count('TodayView')).toBeGreaterThan(settled);
  });
});

/** A Coach whose reply streams one token at a time, when the test says. */
class TokenCoach implements CoachApi {
  private fake = new FakeCoach({ messages: COACH_MESSAGES() });
  private emit: ((e: CoachEvent) => void) | null = null;
  private finish: (() => void) | null = null;
  thread = () => this.fake.thread();
  decide = (id: string, d: string) => this.fake.decide(id, d);
  feedback = (id: string, v: 'up' | 'down') => this.fake.feedback(id, v);
  clear = () => this.fake.clear();
  send(_body: unknown, onEvent: (e: CoachEvent) => void): Promise<void> {
    this.emit = onEvent;
    onEvent({ type: 'start', userId: '900', id: '901' });
    return new Promise<void>((r) => { this.finish = r; });
  }
  token(text: string) { this.emit?.({ type: 'delta', text }); }
  done() { this.emit?.({ type: 'done', id: '901', status: 'done' }); this.finish?.(); }
}

describe(`post-run charts on ${Platform.OS}`, () => {
  it('scrubbing moves the marker without redrawing the map or working out the lines again', async () => {
    await openScreen(SCREENS.find((c) => c.name === 'Post-run (Plan)')!);
    const maps = count('RouteMap');
    const lines = linePoints.calls;
    const area = screen.getByTestId('scrub-area');
    for (const dir of ['increment', 'increment', 'decrement']) {
      await act(async () => { area.props.onAccessibilityAction({ nativeEvent: { actionName: dir } }); });
    }
    expect(count('RouteMap')).toBe(maps);
    expect(linePoints.calls).toBe(lines);
  });
});

describe(`the Coach streaming on ${Platform.OS}`, () => {
  it('each token redraws only the reply it belongs to', async () => {
    const api = new TokenCoach();
    await renderRouter(app('dark', true, { api }), { initialUrl: '/coach' });
    await screen.findByTestId('coach-reply-2');
    await fireEvent.changeText(screen.getByTestId('composer'), 'How was my week?');
    await fireEvent.press(screen.getByTestId('coach-send'));
    const earlier = ['coach-row:1', 'coach-row:2', 'coach-row:3'];
    const before = earlier.map(count);
    const words = ['Your ', 'week ', 'had ', 'three ', 'runs ', 'so ', 'far.'];
    for (const w of words) await act(async () => { api.token(w); });
    expect(earlier.map(count)).toEqual(before);
    expect(count('coach-row:901') + count('coach-row:local:0:reply')).toBeGreaterThanOrEqual(words.length);
    await act(async () => { api.done(); });
    expect(screen.getByText('Your week had three runs so far.')).toBeTruthy();
    expect(earlier.map(count)).toEqual(before);
  });

  it('the thread is a virtualised list', async () => {
    await openScreen(SCREENS.find((c) => c.name === 'Coach')!);
    const thread = screen.getByTestId('coach-thread');
    expect(thread.props.data).toBeDefined(); // a FlatList's rows, drawn as they scroll into view
    expect(thread.props.initialNumToRender).toBe(20);
  });
});

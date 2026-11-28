// The Coach end to end (build prompt, sections 5.10 and 5.11, milestone 11), over the
// real routes, the real watch queue and a scripted Coach with the pilot's API shape:
//
//   1. Ask, watch the reply stream in, with its chart and source chips; the count goes down
//   2. From a run: the question carries the run's tag, and the reply its rep chart
//   3. A proposal goes through the shared diff and the watch confirm, and nothing before
//   4. "Not now" declines it on the server; nothing reaches the watch
//   5. The free limit: 10 a day, the banner, the sheet, See Plus; Plus has none; the server's 429
//   6. Offline: the banner, sending off; a send that never left is marked, and Send again
//   7. A reply that fails part way keeps its words, says why, and Try again asks again
//   8. The phone's guard marks a reply that states a cause; it never rewrites it
//   9. The ⋯ menu: search, filter by run, clear with a confirm (memory stays)
//  10. Saved to memory from the chat, edited in You
//  11. You: Coach memory on, Training preferences off; edit, delete, add, clear
//  12. Turning the Coach off in You hides the tab and the Coach blocks; on again brings them back
//  13. Every number the Coach tab shows is one the server sent
import { describe, expect, it } from '@jest/globals';
import { router, Stack } from 'expo-router';
import { act, fireEvent, renderRouter, screen, within } from 'expo-router/testing-library';
import type { ReactNode } from 'react';

import TabsLayout from './app/(tabs)/_layout';
import CoachClear from './app/(tabs)/coach/clear';
import CoachFilter from './app/(tabs)/coach/filter';
import CoachIndex from './app/(tabs)/coach/index';
import CoachLayout from './app/(tabs)/coach/_layout';
import CoachLimit from './app/(tabs)/coach/limit';
import CoachMenu from './app/(tabs)/coach/menu';
import CoachNewWeek from './app/(tabs)/coach/new-week';
import CrewIndex from './app/(tabs)/crew/index';
import PlanLayout from './app/(tabs)/plan/_layout';
import PlanIndex from './app/(tabs)/plan/index';
import PlanBuilder from './app/(tabs)/plan/builder';
import PlanNext from './app/(tabs)/plan/next';
import PlanRun from './app/(tabs)/plan/run/[id]';
import PlanSession from './app/(tabs)/plan/session/[id]';
import TodayLayout from './app/(tabs)/today/_layout';
import TodayIndex from './app/(tabs)/today/index';
import YouLayout from './app/(tabs)/you/_layout';
import YouIndex from './app/(tabs)/you/index';
import YouMemory from './app/(tabs)/you/memory';
import YouMemoryClear from './app/(tabs)/you/memory-clear';
import YouMemoryEdit from './app/(tabs)/you/memory-edit';
import WatchConfirm from './app/watch/confirm';
import { NotSignedIn } from './api/client';
import { numbersIn, numbersOfPayload } from './domain/replyGuard';
import type { CoachApi } from './features/coach/api';
import { FakeCoach } from './features/coach/fake';
import type { ServerMessage } from './features/coach/model';
import { CoachApiContext } from './features/coach/source';
import PROPOSALS from './features/watch/proposals.fixture.json';
import { memoryStore } from './features/watch/store';
import { FakeGarmin } from './features/watch/transport';
import { useWatch, WatchProvider, type WatchContext } from './features/watch/WatchProvider';
import { DEFAULT_FLAGS, type Flags } from './flags/flags';
import { FlagsProvider } from './flags/FlagsProvider';
import { ThemeProvider } from './theme/ThemeProvider';

const ON: Flags = { ...DEFAULT_FLAGS, coach_enabled: true };
let queue: WatchContext | null = null;
function Probe() { queue = useWatch(); return null; }

function app(coach: CoachApi, garmin = new FakeGarmin(), flags: Flags = ON) {
  const Root = ({ children }: { children?: ReactNode }) => (
    <ThemeProvider initial="dark">
      <FlagsProvider initial={flags}>
        <WatchProvider transport={garmin} store={memoryStore()} auto={false} pollEveryMs={0}>
          <CoachApiContext.Provider value={coach}>
            <Probe />
            {children ?? <Stack screenOptions={{ headerShown: false }} />}
          </CoachApiContext.Provider>
        </WatchProvider>
      </FlagsProvider>
    </ThemeProvider>
  );
  return {
    _layout: () => <Root />,
    '(tabs)/_layout': TabsLayout,
    '(tabs)/today/_layout': TodayLayout, '(tabs)/today/index': TodayIndex,
    '(tabs)/plan/_layout': PlanLayout, '(tabs)/plan/index': PlanIndex, '(tabs)/plan/next': PlanNext, '(tabs)/plan/builder': PlanBuilder,
    '(tabs)/plan/run/[id]': PlanRun, '(tabs)/plan/session/[id]': PlanSession,
    '(tabs)/coach/_layout': CoachLayout, '(tabs)/coach/index': CoachIndex, '(tabs)/coach/menu': CoachMenu,
    '(tabs)/coach/filter': CoachFilter, '(tabs)/coach/clear': CoachClear, '(tabs)/coach/limit': CoachLimit,
    '(tabs)/coach/new-week': CoachNewWeek,
    '(tabs)/crew/index': CrewIndex,
    '(tabs)/you/_layout': YouLayout, '(tabs)/you/index': YouIndex, '(tabs)/you/memory': YouMemory,
    '(tabs)/you/memory-edit': YouMemoryEdit, '(tabs)/you/memory-clear': YouMemoryClear,
    'watch/confirm': WatchConfirm,
  };
}

async function openCoach(coach: CoachApi, garmin?: FakeGarmin, flags?: Flags) {
  await renderRouter(app(coach, garmin, flags), { initialUrl: '/coach' });
  return screen.findByTestId('coach');
}

async function ask(text: string) {
  await fireEvent.changeText(screen.getByTestId('composer'), text);
  await fireEvent.press(screen.getByTestId('coach-send'));
}

type Node = { props?: Record<string, unknown>; children?: (Node | string)[] | null };
/** Every word on screen: text, and what screen readers are told. */
function words(tree: unknown): string[] {
  const out: string[] = [];
  const walk = (n: unknown) => {
    if (typeof n === 'string') { out.push(n); return; }
    if (!n || typeof n !== 'object') return;
    if (Array.isArray(n)) { n.forEach(walk); return; }
    const node = n as Node;
    if (typeof node.props?.accessibilityLabel === 'string') out.push(node.props.accessibilityLabel);
    (node.children ?? []).forEach(walk);
  };
  walk(tree);
  return out;
}

const lastReply = () => screen.getAllByTestId(/^coach-reply-/).at(-1)!;
/** Now, in the server's seconds: a thread written today (the pilot sends no time; the v1 does). */
const nowS = () => Math.floor(Date.now() / 1000);

describe('1. Ask', () => {
  it('the reply streams in, then shows its chart and source chips; the free count goes down', async () => {
    const fake = new FakeCoach({ hold: true });
    await openCoach(fake);
    expect(await screen.findByTestId('coach-empty')).toBeTruthy();
    expect(screen.getByTestId('coach-remaining')).toHaveTextContent('10 left today');
    expect(screen.getByTestId('coach-send').props.accessibilityState).toEqual({ disabled: true });
    await ask('Should I still do the tempo?');
    expect(await screen.findByTestId('streaming')).toBeTruthy();
    expect(screen.getByTestId('coach-send').props.accessibilityState).toEqual({ disabled: true });
    await act(async () => { fake.release(); });
    const reply = within(lastReply());
    expect(screen.queryByTestId('streaming')).toBeNull();
    expect(reply.getByText(/^Your call\. HRV has been under your range of 54–66 ms for two nights: 52 and 49 ms/)).toBeTruthy();
    expect(reply.getByLabelText('HRV, last 7 nights: 64, 61, 60, 58, 56, 52, 49 ms. Below your range of 54 to 66 on the last 2.')).toBeTruthy();
    expect(reply.getByLabelText('Sources: Readiness · Garmin, Sleep · Garmin')).toBeTruthy();
    expect(screen.getByTestId('coach-remaining')).toHaveTextContent('9 left today');
    expect(fake.sent).toEqual([{ text: 'Should I still do the tempo?', starter: false, context: null }]);
  });
  it('a prompt chip sends itself, marked as a starter; thumbs reach the server', async () => {
    const fake = new FakeCoach();
    await openCoach(fake);
    await fireEvent.press(await screen.findByTestId('chip-How’s my week?'));
    expect(within(lastReply()).getByText(/^This week you’ve run 28 of 54 km planned/)).toBeTruthy();
    expect(fake.sent[0].starter).toBe(true);
    await fireEvent.press(within(lastReply()).getByTestId('thumb-up'));
    expect(fake.feedbacks).toEqual([[expect.any(String), 'up']]);
    expect(within(lastReply()).getByTestId('thumb-up').props.accessibilityState).toEqual({ selected: true });
  });
});

describe('2. From a run', () => {
  it('"Ask about this run" opens the Coach with the run’s tag; the question carries it', async () => {
    const fake = new FakeCoach();
    await renderRouter(app(fake), { initialUrl: '/plan' });
    await screen.findByTestId('week-title');
    await act(async () => { router.push({ pathname: '/plan/run/[id]', params: { id: '2026-11-25-intervals' } }); });
    await fireEvent.press(await screen.findByTestId('ask-run'));
    await screen.findByTestId('coach');
    expect(screen.getByTestId('context-tag').props.accessibilityLabel).toBe('About · Wed 25 · 6 × 800 m');
    await ask('Why did rep 6 feel harder?');
    expect(fake.sent[0].context).toEqual({ kind: 'run', id: '2026-11-25-intervals', label: 'About · Wed 25 · 6 × 800 m' });
    expect(screen.getAllByTestId('context-tag')).toHaveLength(1); // on the question now, gone from the composer
    expect(within(lastReply()).getByLabelText('Peak heart rate per rep: 164, 165, 166, 168, 168, 172 bpm.')).toBeTruthy();
    expect(screen.queryByTestId('context-remove')).toBeNull();
  });
  it('a run with no debrief and a session with no note still open the Coach with their tag', async () => {
    const fake = new FakeCoach();
    await renderRouter(app(fake), { initialUrl: '/plan' });
    await screen.findByTestId('week-title');
    await act(async () => { router.push({ pathname: '/plan/run/[id]', params: { id: '2026-11-24-easy' } }); });
    await screen.findByTestId('run');
    expect(screen.queryByTestId('debrief')).toBeNull();
    await fireEvent.press(within(screen.getByTestId('coach-ask')).getByTestId('ask-run'));
    expect((await screen.findByTestId('context-tag')).props.accessibilityLabel).toMatch(/^About · Tue 24 · /);
    await act(async () => { router.push({ pathname: '/plan/session/[id]', params: { id: '2026-11-29-long' } }); });
    await screen.findByTestId('coach-ask');
    expect(screen.queryByTestId('coach-note')).toBeNull();
    await fireEvent.press(within(screen.getByTestId('coach-ask')).getByTestId('button-Ask about this session'));
    expect((await screen.findByTestId('context-tag')).props.accessibilityLabel).toMatch(/^About · .*Sun 29.*Long/i);
    await ask('Can I do this one slower?');
    expect(fake.sent[0].context).toMatchObject({ kind: 'session', id: '2026-11-29-long' });
  });
  it('a tag can be removed before sending', async () => {
    const fake = new FakeCoach();
    await renderRouter(app(fake), { initialUrl: '/coach?about=readiness&label=Back%20off' });
    expect((await screen.findByTestId('context-tag')).props.accessibilityLabel).toBe('From Today · Back off');
    await fireEvent.press(screen.getByTestId('context-remove'));
    await ask('Hello');
    expect(fake.sent[0].context).toBeNull();
  });
});

describe('3. A proposal', () => {
  it('goes through the shared diff and the watch confirm; nothing reaches the watch before Confirm', async () => {
    const fake = new FakeCoach();
    const garmin = new FakeGarmin([], 'ok', 1);
    await openCoach(fake, garmin);
    await ask('Make it easier then');
    const card = within(await screen.findByTestId(/^proposal-\d+$/));
    expect(card.getByLabelText('FRI 27: 3 × 2 km at threshold, becomes Easy 6 km')).toBeTruthy();
    await fireEvent.press(card.getByTestId('button-Review 1 watch change'));
    expect(await screen.findByTestId('diff')).toBeTruthy();
    expect(garmin.sent).toHaveLength(0);
    await fireEvent.press(screen.getByTestId('button-Review watch changes'));
    expect(await screen.findByTestId('confirm-sheet')).toBeTruthy();
    expect(screen.getByLabelText('Replace, Fri 27, 3 × 2 km at threshold, with Easy 6 km')).toBeTruthy();
    expect(garmin.sent).toHaveLength(0);
    await fireEvent.press(screen.getByTestId('button-Confirm and send 1 change'));
    await act(async () => { await queue!.pump(); });
    expect(garmin.sent).toHaveLength(1);
    const sent = within(await screen.findByTestId(/^proposal-sent-/));
    expect(sent.getByTestId(/^watch-(changed_pending_sync|on_watch)$/)).toBeTruthy();
    // The pilot's Confirm (which writes to Garmin itself) is never called.
    expect(fake.decisions).toEqual([]);
  });
  it('the card’s Review is the screen’s one brand fill; a proposal the server can’t describe is finished in Plan', async () => {
    const fake = new FakeCoach({ messages: [
      { id: 1, role: 'user', text: 'Move my long run', status: 'sent', at: nowS() },
      { id: 2, role: 'action', text: 'Move “Long 18 km easy” from Sun 29 Nov to Sat 28 Nov. The watch follows.', status: 'pending', tool: 'move_session', at: nowS() },
    ] });
    await openCoach(fake);
    const card = within(await screen.findByTestId('proposal-2'));
    expect(card.getByTestId('proposal-hint-2')).toHaveTextContent('In Plan, open the session above and use Move, Swap or Skip. It goes through the same review there.');
    await fireEvent.press(card.getByTestId('button-Do it in Plan'));
    expect(await screen.findByTestId('week-title')).toBeTruthy();
  });
  it('a new workout the server can’t describe is built in Plan’s builder', async () => {
    await openCoach(new FakeCoach({ messages: [
      { id: 1, role: 'user', text: 'Add strides on Saturday', status: 'sent', at: nowS() },
      { id: 2, role: 'action', text: 'Create “Easy 6 km + strides” on Sat 28 Nov.', status: 'pending', tool: 'create_workout', at: nowS() },
    ] }));
    const card = within(await screen.findByTestId('proposal-2'));
    expect(card.getByTestId('proposal-hint-2')).toHaveTextContent(/^Build this workout in Plan/);
    await fireEvent.press(card.getByTestId('button-Build it in Plan'));
    expect(await screen.findByTestId('builder')).toBeTruthy();
  });
});

describe('4. Not now', () => {
  it('declines on the server and changes nothing', async () => {
    const fake = new FakeCoach();
    const garmin = new FakeGarmin();
    await openCoach(fake, garmin);
    await ask('Make it easier');
    const card = await screen.findByTestId(/^proposal-\d+$/);
    await fireEvent.press(within(card).getByTestId('button-Not now'));
    expect(within(card).getByTestId(/^proposal-status-/)).toHaveTextContent('Not now · nothing changed');
    expect(fake.decisions).toEqual([[expect.any(String), 'decline']]);
    expect(garmin.sent).toHaveLength(0);
  });
});

const today10 = (): ServerMessage[] => Array.from({ length: 10 }, (_, i) => ({ id: i + 1, role: 'user', text: `Question ${i + 1}`, status: 'sent', at: nowS() }));

describe('5. The free limit', () => {
  it('10 a day: the banner, no chips, no sending; the sheet; See Plus', async () => {
    const fake = new FakeCoach({ messages: today10() });
    await openCoach(fake);
    expect(await screen.findByTestId('coach-limit')).toBeTruthy();
    expect(screen.queryByTestId('coach-chips')).toBeNull();
    expect(screen.getByTestId('composer').props.editable).toBe(false);
    await fireEvent.press(screen.getByTestId('coach-limit'));
    expect(await screen.findByTestId('coach-limit-sheet')).toHaveTextContent(/That’s today’s 10/);
    await fireEvent.press(screen.getByTestId('button-See Plus'));
    expect(await screen.findByTestId('plan-next')).toBeTruthy();
  });
  it('Plus: no limit and no count', async () => {
    await openCoach(new FakeCoach({ messages: today10() }), undefined, { ...ON, plus: true });
    await screen.findByTestId('coach-thread');
    expect(screen.queryByTestId('coach-limit')).toBeNull();
    expect(screen.queryByTestId('coach-remaining')).toBeNull();
  });
  it('the server’s 429 counts: the question is marked and the banner shows', async () => {
    const fake = new FakeCoach({ failures: ['cap'] });
    await openCoach(fake);
    await ask('One more');
    expect(await screen.findByTestId(/^not-sent-/)).toHaveTextContent('Not sent. That’s today’s limit of 10 messages. It resets at midnight.');
    expect(screen.getByTestId('coach-limit')).toBeTruthy();
    expect(screen.queryByTestId('button-Send again')).toBeNull();
  });
  it('a web thread with no times (the pilot’s) uses none of today’s messages and folds into Earlier', async () => {
    const web: ServerMessage[] = Array.from({ length: 12 }, (_, i) => [
      { id: 2 * i + 1, role: 'user', text: `Web question ${i + 1}`, status: 'sent' },
      { id: 2 * i + 2, role: 'assistant', text: `Web answer ${i + 1}`, status: 'done' },
    ]).flat();
    await openCoach(new FakeCoach({ messages: web }));
    expect(await screen.findByTestId('coach-earlier')).toHaveTextContent(/Earlier · 24 messages/);
    expect(screen.queryByTestId('coach-limit')).toBeNull();
    expect(screen.getByTestId('coach-remaining')).toHaveTextContent('10 left today');
    expect(screen.queryByTestId('question-1')).toBeNull();
  });
  it('clearing the conversation doesn’t give back today’s messages', async () => {
    const fake = new FakeCoach();
    await openCoach(fake);
    for (let i = 0; i < 10; i++) await ask(`How’s my week, part ${i + 1}?`);
    expect(await screen.findByTestId('coach-limit')).toBeTruthy();
    await fireEvent.press(screen.getByTestId('coach-more'));
    await fireEvent.press(await screen.findByTestId('menu-clear'));
    await fireEvent.press(await screen.findByTestId('button-Clear conversation'));
    expect(await screen.findByTestId('coach-empty')).toBeTruthy();
    expect(fake.cleared).toBe(1);
    expect(screen.getByTestId('coach-limit')).toBeTruthy();
    expect(screen.getByTestId('composer').props.editable).toBe(false);
  });
});

describe('6. Offline', () => {
  it('the thread can’t load: the banner, and sending is off until it’s back', async () => {
    const fake = new FakeCoach({ offlineThread: true });
    await openCoach(fake);
    expect(await screen.findByTestId('coach-offline')).toBeTruthy();
    await fireEvent.changeText(screen.getByTestId('composer'), 'Hello');
    expect(screen.getByTestId('coach-send').props.accessibilityState).toEqual({ disabled: true });
    fake.offlineThread = false;
    await fireEvent.press(within(screen.getByTestId('coach-offline')).getByTestId('button-Try again'));
    expect(screen.queryByTestId('coach-offline')).toBeNull();
    expect(screen.getByTestId('coach-send').props.accessibilityState).toEqual({ disabled: false });
  });
  it('a send that never left is marked "Not sent", never retried by itself; Send again once back', async () => {
    const fake = new FakeCoach({ failures: ['offline'] });
    await openCoach(fake);
    await ask('Am I recovered?');
    expect(await screen.findByTestId(/^not-sent-/)).toHaveTextContent('Not sent. No connection.');
    expect(fake.sent).toHaveLength(1);
    await fireEvent.press(within(screen.getByTestId('coach-offline')).getByTestId('button-Try again'));
    await fireEvent.press(screen.getByTestId('button-Send again'));
    expect(fake.sent).toHaveLength(2);
    expect(screen.queryByTestId(/^not-sent-/)).toBeNull();
    expect(within(lastReply()).getByText(/^This week you’ve run 28 of 54 km planned/)).toBeTruthy();
  });
});

describe('7. A reply that fails part way', () => {
  it('keeps its words, says why, and Try again asks again', async () => {
    const fake = new FakeCoach({ failures: ['error'] });
    await openCoach(fake);
    await ask('How’s my week?');
    const reply = within(lastReply());
    expect(reply.getByTestId(/^reply-error-/)).toHaveTextContent('The Coach isn’t answering right now. Try again in a minute.');
    expect(reply.getByText(/^This week you’ve run 28 of 54 km planned\.$/)).toBeTruthy();
    await fireEvent.press(reply.getByTestId('button-Try again'));
    expect(fake.sent.map((s) => s.text)).toEqual(['How’s my week?', 'How’s my week?']);
    expect(within(lastReply()).queryByTestId(/^reply-error-/)).toBeNull();
  });
  it('the connection drops after the server stored the question: the question was sent, the words so far stay', async () => {
    const fake = new FakeCoach({ failures: ['drop'] });
    await openCoach(fake);
    await ask('How’s my week?');
    const reply = within(lastReply());
    expect(reply.getByTestId(/^reply-error-/)).toHaveTextContent('The connection dropped before the reply finished. Try again.');
    expect(reply.getByText(/^This week you’ve run 28 of 54 km/)).toBeTruthy();
    expect(screen.queryByTestId(/^not-sent-/)).toBeNull();
    expect(screen.queryByTestId('button-Send again')).toBeNull();
    expect(screen.getByTestId('coach-remaining')).toHaveTextContent('9 left today');
    expect(fake.sent).toHaveLength(1);
  });
});

describe('7b. Signed out', () => {
  it('a send that finds the sign-in gone says so with Sign in; no Send again, sending off', async () => {
    const fake = new FakeCoach({ messages: [
      { id: 1, role: 'user', text: 'How’s my week?', status: 'sent', at: nowS() },
      { id: 2, role: 'assistant', text: 'This week you’ve run 28 of 54 km planned.', status: 'done', at: nowS() },
    ] });
    const api: CoachApi = { thread: () => fake.thread(), decide: (i, d) => fake.decide(i, d), feedback: (i, v) => fake.feedback(i, v),
      clear: () => fake.clear(), send: async () => { throw new NotSignedIn(); } };
    await openCoach(api);
    await screen.findByTestId('question-1');
    await ask('And the tempo?');
    expect(await screen.findByTestId('failure-banner')).toHaveTextContent(/You’re signed out/);
    expect(screen.getByTestId('question-1')).toBeTruthy();
    expect(screen.getByTestId(/^not-sent-/)).toBeTruthy();
    expect(screen.queryByTestId('button-Send again')).toBeNull();
    await fireEvent.changeText(screen.getByTestId('composer'), 'Hello');
    expect(screen.getByTestId('coach-send').props.accessibilityState).toEqual({ disabled: true });
  });
});

describe('8. The guard', () => {
  it('a reply stating a cause is marked as an observation, its words unchanged; a medical claim gets the medical line', async () => {
    await openCoach(new FakeCoach({ messages: [
      { id: 1, role: 'user', text: 'Why was rep 6 slow?', status: 'sent', at: nowS() },
      { id: 2, role: 'assistant', text: 'Rep 6 was slower, in line with your short sleep.', status: 'done', at: nowS() },
      { id: 3, role: 'user', text: 'My achilles hurts', status: 'sent', at: nowS() },
      { id: 4, role: 'assistant', text: 'It sounds like tendinitis. Ice it after runs.', status: 'done', at: nowS() },
      { id: 5, role: 'user', text: 'And the tempo?', status: 'sent', at: nowS() },
      { id: 6, role: 'assistant', text: 'Rep 6 peaked at 172 bpm, 4 above rep 5.', status: 'done', at: nowS() },
    ] }));
    expect(await screen.findByText('Rep 6 was slower, in line with your short sleep.')).toBeTruthy();
    expect(screen.getByTestId('flag-2').props.accessibilityLabel).toBe('Read this as an observation. Daash can’t tell what caused it, or what will happen.');
    expect(screen.getByTestId('flag-4').props.accessibilityLabel).toBe("Daash doesn't give medical advice. If you're unwell or hurt, check with a doctor or physio.");
    expect(screen.queryByTestId('flag-6')).toBeNull();
  });
});

describe('9. The ⋯ menu', () => {
  const thread: ServerMessage[] = [
    { id: 1, role: 'user', text: 'Why did rep 6 feel harder?', status: 'sent', at: nowS(), context: { kind: 'run', id: 'r1', label: 'About · Wed 25 · 6 × 800 m' } },
    { id: 2, role: 'assistant', text: 'Rep 6 peaked at 172 bpm.', status: 'done', at: nowS() },
    { id: 3, role: 'user', text: 'Should I still do the tempo?', status: 'sent', at: nowS() },
    { id: 4, role: 'assistant', text: 'Your call.', status: 'done', at: nowS() },
  ];
  it('search finds words; filter keeps one run’s questions; clear asks first, and memory stays', async () => {
    const fake = new FakeCoach({ messages: thread });
    await openCoach(fake);
    await fireEvent.press(await screen.findByTestId('coach-more'));
    expect(await screen.findByTestId('coach-menu')).toHaveTextContent(/5 things the Coach keeps in mind/);
    await fireEvent.press(screen.getByTestId('menu-search'));
    await fireEvent.changeText(await screen.findByTestId('coach-search-input'), 'tempo');
    expect(screen.getByTestId('question-3')).toBeTruthy();
    expect(screen.queryByTestId('question-1')).toBeNull();
    await fireEvent.press(screen.getByTestId('button-Done'));

    await fireEvent.press(screen.getByTestId('coach-more'));
    await fireEvent.press(await screen.findByTestId('menu-filter'));
    await fireEvent.press(await screen.findByTestId('filter-r1'));
    expect(await screen.findByTestId('coach-filter')).toHaveTextContent('About · Wed 25 · 6 × 800 m');
    expect(screen.getByTestId('question-1')).toBeTruthy();
    expect(screen.queryByTestId('question-3')).toBeNull();
    await fireEvent.press(screen.getByTestId('button-Show all'));

    await fireEvent.press(screen.getByTestId('coach-more'));
    await fireEvent.press(await screen.findByTestId('menu-clear'));
    await fireEvent.press(await screen.findByTestId('button-Keep it'));
    expect(screen.getByTestId('question-1')).toBeTruthy();
    expect(fake.cleared).toBe(0);
    await fireEvent.press(screen.getByTestId('coach-more'));
    await fireEvent.press(await screen.findByTestId('menu-clear'));
    await fireEvent.press(await screen.findByTestId('button-Clear conversation'));
    expect(await screen.findByTestId('coach-empty')).toBeTruthy();
    expect(fake.cleared).toBe(1);
    await act(async () => { router.push('/you/memory'); });
    expect(within(await screen.findByTestId('memory-list')).getByText('London Marathon · Sat 24 Apr · 3:15')).toBeTruthy();
  });
  it('a clear the server couldn’t make keeps the thread and says so', async () => {
    const fake = new FakeCoach({ messages: thread, clearFails: true });
    await openCoach(fake);
    await fireEvent.press(await screen.findByTestId('coach-more'));
    await fireEvent.press(await screen.findByTestId('menu-clear'));
    await fireEvent.press(await screen.findByTestId('button-Clear conversation'));
    expect(await screen.findByTestId('coach-clear-error')).toHaveTextContent(/Couldn’t clear the conversation\. It’s still here\. Try again in a moment\./);
    await fireEvent.press(screen.getByTestId('button-Keep it'));
    expect(await screen.findByTestId('question-1')).toBeTruthy();
    expect(fake.messages).toHaveLength(4);
  });
});

describe('10. Saved to memory from the chat', () => {
  it('the thread says so, with Edit into You', async () => {
    await openCoach(new FakeCoach());
    await ask('I’m travelling 1 to 3 Dec');
    expect(await screen.findByText('Saved to memory: Travelling 1 to 3 Dec')).toBeTruthy();
    await fireEvent.press(screen.getByLabelText('Edit memory: Travelling 1 to 3 Dec'));
    const input = await screen.findByTestId('memory-text');
    expect(input.props.value).toBe('Travelling 1 to 3 Dec');
    await fireEvent.changeText(input, 'Travelling 1 to 3 Dec · treadmill only');
    await fireEvent.press(screen.getByTestId('button-Save'));
    await act(async () => { router.push('/you/memory'); });
    expect(await screen.findByLabelText('Travel: Travelling 1 to 3 Dec · treadmill only · you said in chat')).toBeTruthy();
  });
});

describe('11. You: memory and preferences', () => {
  it('Coach on: Coach memory, with tone and Clear memory; edit, delete, add, clear', async () => {
    await renderRouter(app(new FakeCoach()), { initialUrl: '/you' });
    await fireEvent.press(await screen.findByTestId('row-memory'));
    const list = within(await screen.findByTestId('memory-list'));
    expect(list.getByLabelText('Injuries you mentioned: Left achilles · 12 Nov · you said in chat')).toBeTruthy();
    expect(list.getByLabelText('Tone: Short answers, numbers first')).toBeTruthy();
    await fireEvent.press(screen.getByLabelText('Edit Goal'));
    await fireEvent.changeText(await screen.findByTestId('memory-text'), 'London Marathon · Sat 24 Apr · 3:10');
    await fireEvent.press(screen.getByTestId('button-Save'));
    expect(await screen.findByLabelText('Goal: London Marathon · Sat 24 Apr · 3:10')).toBeTruthy();
    await fireEvent.press(screen.getByLabelText('Edit Tone'));
    await fireEvent.press(await screen.findByTestId('button-Delete'));
    expect(screen.queryByLabelText(/^Tone:/)).toBeNull();
    await fireEvent.press(screen.getByTestId('button-Add something to remember'));
    await fireEvent.press(await screen.findByTestId('kind-travel'));
    await fireEvent.changeText(screen.getByTestId('memory-text'), '1 to 3 Dec · treadmill only');
    await fireEvent.press(screen.getByTestId('button-Save'));
    expect(await screen.findByLabelText('Travel: 1 to 3 Dec · treadmill only')).toBeTruthy();
    await fireEvent.press(screen.getByTestId('memory-clear'));
    await fireEvent.press(await screen.findByTestId('button-Clear memory'));
    expect(await screen.findByTestId('memory-empty')).toBeTruthy();
  });
  it('Coach off: the same items as Training preferences; niggles, no tone, no Clear memory, no Coach anywhere', async () => {
    await renderRouter(app(new FakeCoach(), undefined, DEFAULT_FLAGS), { initialUrl: '/you' });
    expect(await screen.findByTestId('row-memory')).toHaveTextContent(/Training preferences/);
    expect(screen.queryByTestId('coach-switch')).toBeNull();
    await fireEvent.press(screen.getByTestId('row-memory'));
    const view = await screen.findByTestId('memory');
    expect(within(view).getByLabelText('Niggles to plan around: Left achilles · 12 Nov')).toBeTruthy();
    expect(within(view).queryByLabelText(/^Tone:/)).toBeNull();
    expect(screen.queryByTestId('memory-clear')).toBeNull();
    expect(words(screen.toJSON()).join(' ')).not.toMatch(/Coach/);
  });
});

describe('12. The Coach switch in You', () => {
  it('off hides the tab and every Coach block; on brings them back', async () => {
    await renderRouter(app(new FakeCoach()), { initialUrl: '/today' });
    expect(await screen.findByTestId('coach-line')).toBeTruthy();
    await act(async () => { router.push('/you'); });
    await fireEvent(await screen.findByTestId('coach-switch'), 'valueChange', false);
    expect(await screen.findByTestId('row-memory')).toHaveTextContent(/Training preferences/);
    await act(async () => { router.push('/today'); });
    await screen.findByTestId('today');
    expect(screen.queryByTestId('coach-line')).toBeNull();
    await act(async () => { router.push('/coach'); });
    expect(screen.queryByTestId('coach')).toBeNull();
    await act(async () => { router.push('/you'); });
    await fireEvent(await screen.findByTestId('coach-switch'), 'valueChange', true);
    await act(async () => { router.push('/coach'); });
    expect(await screen.findByTestId('coach')).toBeTruthy();
  });
});

describe('13. The Coach tab shows only the server’s numbers', () => {
  it('every number in a reply, chart, proposal card or memory note is in what the server sent', async () => {
    const fake = new FakeCoach();
    const events: unknown[] = [];
    const recorded: CoachApi = {
      thread: () => fake.thread(), decide: (i, d) => fake.decide(i, d), feedback: (i, v) => fake.feedback(i, v), clear: () => fake.clear(),
      send: (b, on) => fake.send(b, (e) => { events.push(e); on(e); }),
    };
    await openCoach(recorded);
    for (const q of ['Should I still do the tempo?', 'Why was rep 6 hard on Wednesday?', 'Make it easier', 'I’m travelling 1 to 3 Dec', 'How’s my week?']) await ask(q);
    await screen.findByTestId(/^proposal-days-/);
    const payload = numbersOfPayload([fake.messages, events, PROPOSALS]);
    const shown: string[] = [];
    const walk = (n: unknown, on: boolean) => {
      if (typeof n === 'string') { if (on) shown.push(...numbersIn(n)); return; }
      if (!n || typeof n !== 'object') return;
      if (Array.isArray(n)) { n.forEach((c) => walk(c, on)); return; }
      const node = n as Node;
      const id = typeof node.props?.testID === 'string' ? node.props.testID : '';
      const here = on || /^(coach-reply-|proposal-|memory-note-)/.test(id);
      if (here && typeof node.props?.accessibilityLabel === 'string') shown.push(...numbersIn(node.props.accessibilityLabel));
      (node.children ?? []).forEach((c) => walk(c, here));
    };
    walk(screen.toJSON(), false);
    expect(shown.length).toBeGreaterThan(20);
    expect([...new Set(shown.filter((n) => !payload.has(n)))]).toEqual([]);
  });
});

// The Coach's pure parts: the thread model, the stream parser and its events, and the
// API client against the live pilot's real shapes (chat_pilot.py on main), with a
// stand-in fetch whose body streams in pieces.
import { beforeEach, describe, expect, it, jest } from '@jest/globals';

const mockStore = new Map<string, string>();
jest.mock('expo-secure-store', () => ({
  getItemAsync: async (k: string) => mockStore.get(k) ?? null,
  setItemAsync: async (k: string, v: string) => { mockStore.set(k, v); },
  deleteItemAsync: async (k: string) => { mockStore.delete(k); },
}));

import { ACCESS_KEY, ApiError, NotSignedIn, Offline } from '../../api/client';
import { CoachLimitReached, CoachUnavailable, readStream, serverCoachApi, type StreamResponse } from './api';
import { FakeCoach, HRV_CHART, REP_HR_CHART, scriptFor } from './fake';
import { contextFrom, countSend, dayOf, filterByTag, limitFor, mergeThread, retain, searchThread, sentToday, tagKey, tagsOf, threadRows,
  type CoachMessage, type ServerMessage } from './model';
import { applyEvent, createSseParser, toEvent, type CoachEvent, type Turn } from './sse';
import { itemWords, kindLabel, kindsFor, visibleItems, personaMemory } from './memory';
import { defaultCoachApi } from './source';
import { setSampleDataForTests } from '../../data/sample';

const NOW = new Date(2026, 10, 27, 9, 0).getTime();   // Fri 27 Nov, 09:00 local
const H = 3_600_000;
const msg = (id: string, role: CoachMessage['role'], text: string, at: number, extra: Partial<CoachMessage> = {}): CoachMessage =>
  ({ id, role, text, status: role === 'user' ? 'sent' : 'done', at, ...extra });

describe('the thread', () => {
  const run = { kind: 'run' as const, id: '2026-11-25-intervals', label: 'About · Wed 25 · 6 × 800 m' };
  const thread = [
    msg('1', 'user', 'Why did rep 6 feel harder?', NOW - 48 * H, { context: run }),
    msg('2', 'assistant', 'Rep 6 peaked at 172 bpm.', NOW - 48 * H + 1000),
    msg('3', 'user', 'How is my week?', NOW - 24 * H),
    msg('4', 'assistant', '28 of 54 km.', NOW - 24 * H + 1000),
    msg('5', 'user', 'Should I still do the tempo?', NOW - 2 * H),
    msg('6', 'assistant', 'Your call.', NOW - 2 * H + 1000),
  ];

  it('older days collapse into "Earlier · N messages since <day>"; today opens with its separator', () => {
    const rows = threadRows(thread, NOW, false);
    expect(rows[0]).toEqual({ kind: 'earlier', count: 4, label: 'Earlier · 4 messages since Wed' });
    expect(rows[1]).toEqual({ kind: 'day', label: 'Today · 07:00' });
    expect(rows.filter((r) => r.kind === 'message').map((r) => r.kind === 'message' && r.m.id)).toEqual(['5', '6']);
  });
  it('shown, every day has its own separator', () => {
    const days = threadRows(thread, NOW, true).filter((r) => r.kind === 'day').map((r) => r.kind === 'day' && r.label);
    expect(days).toEqual(['Wed 25 · 09:00', 'Thu 26 · 09:00', 'Today · 07:00']);
  });
  it('keeps the last 14 days (section 16’s default); memory carries the rest', () => {
    const old = msg('0', 'user', 'old', NOW - 15 * 24 * H);
    expect(retain([old, ...thread], NOW).map((m) => m.id)).toEqual(thread.map((m) => m.id));
  });
  it('search finds words in any case; filter keeps the exchanges about one run', () => {
    expect(searchThread(thread, 'TEMPO').map((m) => m.id)).toEqual(['5']);
    expect(searchThread(thread, '  ')).toBe(thread);
    expect(tagsOf(thread)).toEqual([run]);
    expect(filterByTag(thread, tagKey(run)).map((m) => m.id)).toEqual(['1', '2']);
  });
  it('the server’s words and statuses win; the phone keeps tags, charts and sources it saw, and unsent messages', () => {
    const local = [msg('2', 'assistant', 'old words', NOW - H, { chart: HRV_CHART, sources: ['HRV · Garmin'] }),
      msg('local:1', 'user', 'never sent', NOW, { status: 'not_sent' })];
    const merged = mergeThread(local, [{ id: 2, role: 'assistant', text: 'Because your HRV dropped, you are tired.', status: 'done', feedback: 'up' },
      { id: 9, role: 'action', text: 'Move it', status: 'expired', tool: 'move_session' }, { id: 10, role: 'system', text: 'x', status: 'done' }]);
    expect(merged.map((m) => [m.id, m.status])).toEqual([['2', 'done'], ['9', 'expired'], ['local:1', 'not_sent']]);
    expect(merged[0]).toMatchObject({ text: 'Because your HRV dropped, you are tired.', chart: HRV_CHART, sources: ['HRV · Garmin'], feedback: 'up', flags: ['cause'] });
  });
});

describe('the daily limit', () => {
  const sent = (n: number, at = NOW) => Array.from({ length: n }, (_, i) => msg(`u${i}`, 'user', 'q', at));
  it('free: 10 a day, counted today, unsent messages not counted', () => {
    expect(limitFor({ plus: false, dailyLimit: 10, messages: sent(7), now: NOW, capHit: false })).toEqual({ limit: 10, remaining: 3, reached: false });
    expect(limitFor({ plus: false, dailyLimit: 10, messages: sent(10), now: NOW, capHit: false }).reached).toBe(true);
    expect(sentToday([...sent(4, NOW - 24 * H), ...sent(2), msg('x', 'user', 'q', NOW, { status: 'not_sent' })], NOW)).toBe(2);
  });
  it('the server’s 429 counts as reached; Plus has no limit', () => {
    expect(limitFor({ plus: false, dailyLimit: 10, messages: [], now: NOW, capHit: true })).toEqual({ limit: 10, remaining: 0, reached: true });
    expect(limitFor({ plus: true, dailyLimit: 10, messages: sent(40), now: NOW, capHit: true })).toEqual({ limit: null, remaining: null, reached: false });
  });
  it('local days', () => { expect(dayOf(NOW)).toBe('2026-11-27'); });

  // The pilot's GET /app/api/chat sends no time. A runner with a web thread opening the
  // phone for the first time hasn't used today's messages, and their thread isn't "Today".
  const pilot: ServerMessage[] = Array.from({ length: 12 }, (_, i) => [
    { id: 2 * i + 1, role: 'user', text: `q${i}`, status: 'sent' },
    { id: 2 * i + 2, role: 'assistant', text: `a${i}`, status: 'done' },
  ]).flat();
  it('a pilot thread with no times: nothing counts toward today, it all collapses into Earlier, and retention keeps it', () => {
    const merged = mergeThread([], pilot);
    expect(merged.every((m) => m.at == null)).toBe(true);
    expect(merged.map((m) => m.id)).toEqual(pilot.map((m) => String(m.id)));
    expect(limitFor({ plus: false, dailyLimit: 10, messages: merged, now: NOW, capHit: false })).toEqual({ limit: 10, remaining: 10, reached: false });
    expect(threadRows(merged, NOW, false)).toEqual([{ kind: 'earlier', count: 24, label: 'Earlier · 24 messages' }]);
    expect(threadRows(merged, NOW, true)[0]).toEqual({ kind: 'day', label: 'Earlier' });
    expect(retain(merged, NOW)).toHaveLength(24);
  });
  it('messages this phone sent keep their time through a merge; undated ones come first', () => {
    const local = [msg('25', 'user', 'today’s', NOW - H)];
    const merged = mergeThread(local, [...pilot, { id: 25, role: 'user', text: 'today’s', status: 'sent' }]);
    expect(merged[merged.length - 1]).toMatchObject({ id: '25', at: NOW - H });
    expect(sentToday(merged, NOW)).toBe(1);
    const rows = threadRows(merged, NOW, false);
    expect(rows.map((r) => r.kind)).toEqual(['earlier', 'day', 'message']);
  });
  it('today’s sends are counted apart from the thread: clearing the thread doesn’t give them back; a new day does', () => {
    let c = null as ReturnType<typeof countSend> | null;
    for (let i = 0; i < 10; i++) c = countSend(c, NOW);
    expect(c).toEqual({ day: '2026-11-27', count: 10 });
    expect(limitFor({ plus: false, dailyLimit: 10, messages: [], now: NOW, capHit: false, counted: c })).toEqual({ limit: 10, remaining: 0, reached: true });
    expect(limitFor({ plus: false, dailyLimit: 10, messages: [], now: NOW + 24 * H, capHit: false, counted: c }).remaining).toBe(10);
    expect(countSend(c, NOW + 24 * H)).toEqual({ day: '2026-11-28', count: 1 });
    // The larger of the two counts: the thread's dated messages (another device, once the server sends times) or this phone's.
    expect(limitFor({ plus: false, dailyLimit: 10, messages: sent(6), now: NOW, capHit: false, counted: { day: '2026-11-27', count: 2 } }).remaining).toBe(4);
  });
});

describe('context tags from the entry points', () => {
  it('a run, a session, Today, a proposal', () => {
    expect(contextFrom({ run: 'r1', label: 'Wed 25 · 6 × 800 m' })).toEqual({ kind: 'run', id: 'r1', label: 'About · Wed 25 · 6 × 800 m' });
    expect(contextFrom({ session: 's1', label: 'Fri 27 · 3 × 2 km at threshold' })?.label).toBe('About · Fri 27 · 3 × 2 km at threshold');
    expect(contextFrom({ about: 'readiness', label: 'Back off' })).toEqual({ kind: 'readiness', id: null, label: 'From Today · Back off' });
    expect(contextFrom({ about: 'easier', label: 'Make it easier · today' })?.label).toBe('About · Make it easier · today');
    expect(contextFrom({})).toBeNull();
  });
});

describe('the stream', () => {
  const STREAM = 'event: start\ndata: {"user_id":41,"id":42}\n\n'
    + ': a comment\n'
    + 'event: tool\ndata: {"name":"get_readiness","label":"Checking readiness"}\n\n'
    + 'event: delta\ndata: {"text":"HRV is 49 ms, "}\n\n'
    + 'event: delta\ndata: {"text":"below your range."}\n\n'
    + 'event: pending\ndata: {"id":43,"tool":"easier","text":"Make today easier","status":"pending","request":{"kind":"easier"}}\n\n'
    + 'event: done\ndata: {"id":42,"status":"done","usage":{"model":"x"}}\n\n';

  it('frames come out the same however the bytes are cut, CRLF or LF', () => {
    const whole = createSseParser().feed(STREAM);
    for (const size of [1, 3, 7, 50]) {
      const p = createSseParser();
      const out = [];
      for (let i = 0; i < STREAM.length; i += size) out.push(...p.feed(STREAM.slice(i, i + size)));
      out.push(...p.end());
      expect(out).toEqual(whole);
    }
    const crlf = createSseParser();
    expect([...crlf.feed(STREAM.replace(/\n/g, '\r\n')), ...crlf.end()]).toEqual(whole);
    expect(whole.map((f) => f.event)).toEqual(['start', 'tool', 'delta', 'delta', 'pending', 'done']);
  });
  it('a last frame without its blank line still counts; multi-line data joins; junk is skipped', () => {
    const p = createSseParser();
    expect(p.feed('event: delta\ndata: {"text":\ndata: "a"}')).toEqual([]);
    expect(p.end()).toEqual([{ event: 'delta', data: '{"text":\n"a"}' }]);
    expect(toEvent({ event: 'delta', data: 'not json' })).toBeNull();
    expect(toEvent({ event: 'mystery', data: '{}' })).toBeNull();
    // The pilot's own error words say "AI service"; the app uses its own, by code.
    expect(toEvent({ event: 'error', data: '{"code":"provider","message":"The AI service isn\u2019t answering right now."}' }))
      .toEqual({ type: 'error', code: 'provider', message: 'The Coach isn’t answering right now. Try again in a minute.' });
    expect(toEvent({ event: 'error', data: '{"code":"new_thing","message":"x"}' })).toMatchObject({ message: 'Something went wrong on our side. Try again.' });
  });

  it('events build the reply: the question gets the server’s id, words stream in, sources and the proposal land, the guard checks the end', () => {
    let thread: CoachMessage[] = [msg('local:0', 'user', 'Should I?', NOW, { status: 'sending' }),
      msg('local:0:reply', 'assistant', '', NOW, { status: 'streaming', sources: [] })];
    let turn: Turn = { userId: 'local:0', replyId: 'local:0:reply' };
    const p = createSseParser();
    for (const f of [...p.feed(STREAM), ...p.end()]) {
      const e = toEvent(f)!;
      ({ thread, turn } = applyEvent(thread, turn, e, NOW));
      if (e.type === 'tool') expect(thread[1].working).toBe('Checking readiness');
    }
    expect(thread.map((m) => [m.id, m.role, m.status])).toEqual([['41', 'user', 'sent'], ['42', 'assistant', 'done'], ['43', 'action', 'pending']]);
    expect(thread[1]).toMatchObject({ text: 'HRV is 49 ms, below your range.', sources: ['Readiness · Garmin'], working: null, flags: [] });
    expect(thread[2]).toMatchObject({ request: { kind: 'easier' }, text: 'Make today easier' });
  });
  it('an error keeps what was written, marks it, and says what happened; a cause in it is flagged, never rewritten', () => {
    const start: CoachMessage[] = [msg('r', 'assistant', 'Because your sleep was short', NOW, { status: 'streaming' })];
    const { thread } = applyEvent(start, { userId: 'u', replyId: 'r' }, toEvent({ event: 'error', data: '{"code":"timeout"}' })!, NOW);
    expect(thread[0]).toMatchObject({ status: 'partial', error: 'That took too long. Try again?', text: 'Because your sleep was short', flags: ['cause'] });
  });
});

// -- the API client --------------------------------------------------------------------------

type Call = { url: string; init: { method: string; headers: Record<string, string>; body: string } };
function streamOf(chunks: string[], status = 200, body: unknown = {}): StreamResponse {
  const enc = new TextEncoder();
  let i = 0;
  return {
    status, ok: status < 400, json: async () => body, text: async () => chunks.join(''),
    body: { getReader: () => ({ read: async () => (i < chunks.length ? { done: false, value: enc.encode(chunks[i++]) } : { done: true }) }) },
  };
}

describe('serverCoachApi against the pilot’s shapes', () => {
  let calls: Call[];
  beforeEach(() => { mockStore.clear(); mockStore.set(ACCESS_KEY, 'tok'); calls = []; });
  const api = (res: StreamResponse | 'network') => serverCoachApi({ base: 'https://d', fetch: async (url, init) => {
    calls.push({ url, init });
    if (res === 'network') throw new TypeError('Network request failed');
    return res;
  } });

  it('sends {text, starter, context} once, signed in, and streams the events as they arrive', async () => {
    const events: CoachEvent[] = [];
    await api(streamOf(['event: start\ndata: {"user_id":1,"id":2}\n\nevent: del', 'ta\ndata: {"text":"Hi"}\n\n', 'event: done\ndata: {"id":2,"status":"done"}\n\n']))
      .send({ text: 'Hello', starter: false, context: null }, (e) => events.push(e));
    expect(calls).toHaveLength(1);
    expect(calls[0].url).toBe('https://d/app/api/chat/send');
    expect(calls[0].init.headers.Authorization).toBe('Bearer tok');
    expect(JSON.parse(calls[0].init.body)).toEqual({ text: 'Hello', starter: false, context: null });
    expect(events.map((e) => e.type)).toEqual(['start', 'delta', 'done']);
  });
  it('no retry on send: offline once is one call and an Offline error', async () => {
    await expect(api('network').send({ text: 'Hi' }, () => {})).rejects.toBeInstanceOf(Offline);
    expect(calls).toHaveLength(1);
  });
  it('maps the pilot’s answers: 401 signed out, 429 today’s limit (its words), 404 no Coach, 400 its words', async () => {
    await expect(api(streamOf([], 401)).send({ text: 'Hi' }, () => {})).rejects.toBeInstanceOf(NotSignedIn);
    const cap = api(streamOf([], 429, { error: 'That’s today’s limit of 10 messages. It resets at midnight.', code: 'cap' })).send({ text: 'Hi' }, () => {});
    await expect(cap).rejects.toBeInstanceOf(CoachLimitReached);
    await expect(api(streamOf([], 429, { error: 'x', code: 'cap' })).send({ text: 'Hi' }, () => {})).rejects.toThrow('x');
    await expect(api(streamOf([], 404, { error: 'Not found' })).send({ text: 'Hi' }, () => {})).rejects.toBeInstanceOf(CoachUnavailable);
    const empty = api(streamOf([], 400, { error: 'Type a message first.', code: 'empty' })).send({ text: '' }, () => {});
    await expect(empty).rejects.toEqual(new ApiError('Type a message first.', 400, false));
    mockStore.clear();
    await expect(api(streamOf([])).send({ text: 'Hi' }, () => {})).rejects.toBeInstanceOf(NotSignedIn);
  });
  it('reads a body that can’t stream as one piece', async () => {
    const events: CoachEvent[] = [];
    await readStream({ ...streamOf(['event: delta\ndata: {"text":"a"}\n\n']), body: null }, (e) => events.push(e));
    expect(events).toEqual([{ type: 'delta', text: 'a' }]);
  });
});

describe('the stand-in Coach', () => {
  it('its numbers are the persona fixtures’: HRV from Today, peak heart rate per rep from Wednesday’s run', () => {
    expect(HRV_CHART.values).toEqual([64, 61, 60, 58, 56, 52, 49]);
    expect(HRV_CHART.highlight).toEqual([5, 6]);
    expect(REP_HR_CHART.values).toEqual([164, 165, 166, 168, 168, 172]);
    expect(scriptFor({ text: 'why?', context: { kind: 'run', id: 'r', label: 'x' } }).text).toBe('Rep pace averaged 4:01. Your peak heart rate on rep 6 was 172, 4 bpm above rep 5.');
  });
  it('fails as told, in order', async () => {
    const fake = new FakeCoach({ failures: ['offline', 'cap'] });
    await expect(fake.send({ text: 'a' }, () => {})).rejects.toBeInstanceOf(Offline);
    await expect(fake.send({ text: 'a' }, () => {})).rejects.toBeInstanceOf(CoachLimitReached);
    const events: CoachEvent[] = [];
    await fake.send({ text: 'a' }, (e) => events.push(e));
    expect(events.at(-1)).toEqual({ type: 'done', id: expect.any(String), status: 'done' });
  });
});

describe('memory and preferences', () => {
  const items = personaMemory(NOW);
  it('the same items both ways; injuries are "niggles to plan around" with the Coach off, tone only with it on', () => {
    expect(kindLabel('injury', true)).toBe('Injuries you mentioned');
    expect(kindLabel('injury', false)).toBe('Niggles to plan around');
    expect(kindsFor(false)).not.toContain('tone');
    expect(visibleItems(items, true).map((i) => i.kind)).toEqual(['goal', 'days', 'injury', 'preferences', 'tone']);
    expect(visibleItems(items, false).map((i) => i.kind)).toEqual(['goal', 'days', 'injury', 'preferences']);
  });
  it('where an item came from is said only with the Coach on', () => {
    const chat = items.find((i) => i.source === 'chat')!;
    expect(itemWords(chat, true)).toBe('Left achilles · 12 Nov · you said in chat');
    expect(itemWords(chat, false)).toBe('Left achilles · 12 Nov');
  });
});

describe('which Coach the app talks to', () => {
  it('with sample data showing, only the stand-in, even with a token on the phone (never the real account)', async () => {
    mockStore.set(ACCESS_KEY, 'token');
    const realFetch = global.fetch;
    const fetchSpy = jest.fn(async () => { throw new Error('the server must not be asked'); });
    global.fetch = fetchSpy as unknown as typeof fetch;
    try {
      setSampleDataForTests(true);
      const api = defaultCoachApi();
      expect(api).toBeInstanceOf(FakeCoach);
      expect((await api.thread()).messages.length).toBeGreaterThan(0);
      expect(fetchSpy).not.toHaveBeenCalled();
      // Real data: the server first (a development build falls back to the stand-in).
      setSampleDataForTests(false);
      expect(defaultCoachApi()).not.toBeInstanceOf(FakeCoach);
    } finally {
      global.fetch = realFetch;
      setSampleDataForTests(null);
      mockStore.delete(ACCESS_KEY);
    }
  });
});

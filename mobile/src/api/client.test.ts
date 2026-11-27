// The API client: auth, retries and error mapping, against a stand-in fetch.
import { afterEach, beforeEach, describe, expect, it, jest } from '@jest/globals';

const mockStore = new Map<string, string>();
jest.mock('expo-secure-store', () => ({
  getItemAsync: async (k: string) => mockStore.get(k) ?? null,
  setItemAsync: async (k: string, v: string) => { mockStore.set(k, v); },
  deleteItemAsync: async (k: string) => { mockStore.delete(k); },
}));

import { migratedNodeDb } from '../db/testing/nodeDb';
import { kv } from '../db/kv';
import { ACCESS_KEY, ApiError, accessToken, clientConfig, getJson, NotSignedIn, Offline, postJson, ReconnectNeeded, setAccessToken, TimedOut } from './client';
import { clearRunnerData, signOut } from './session';

type Reply = { status?: number; body?: unknown } | 'network' | 'hang';
let replies: Reply[] = [];
let calls: { url: string; init: RequestInit }[] = [];
const slept: number[] = [];

beforeEach(() => {
  mockStore.clear();
  mockStore.set(ACCESS_KEY, 'tok-123');
  replies = [];
  calls = [];
  slept.length = 0;
  clientConfig.sleep = async (ms) => { slept.push(ms); };
  (globalThis as { fetch: unknown }).fetch = jest.fn(async (url: string, init: RequestInit) => {
    calls.push({ url, init });
    const r = replies.shift() ?? { status: 200, body: {} };
    if (r === 'network') throw new TypeError('Network request failed');
    if (r === 'hang') {
      return new Promise((_, reject) => init.signal?.addEventListener('abort', () => reject(new Error('aborted'))));
    }
    return { status: r.status ?? 200, ok: (r.status ?? 200) < 400, json: async () => r.body ?? {} } as Response;
  });
});
afterEach(() => { jest.useRealTimers(); });

describe('auth', () => {
  it('sends the stored token as a bearer, and says which layout asked', async () => {
    replies = [{ body: { ok: 1 } }];
    await expect(getJson('/app/api/v1/today')).resolves.toEqual({ ok: 1 });
    const h = calls[0].init.headers as Record<string, string>;
    expect(h.Authorization).toBe('Bearer tok-123');
    expect(h['X-Daash-Layout']).toBe('native');
    expect(calls[0].url).toMatch(/\/app\/api\/v1\/today$/);
  });
  it('no token: NotSignedIn, and nothing is sent', async () => {
    mockStore.clear();
    await expect(getJson('/x')).rejects.toBeInstanceOf(NotSignedIn);
    expect(calls).toHaveLength(0);
  });
  it('a 401: NotSignedIn, never retried', async () => {
    replies = [{ status: 401 }];
    await expect(getJson('/x')).rejects.toBeInstanceOf(NotSignedIn);
    expect(calls).toHaveLength(1);
  });
  it('an expired Garmin login: ReconnectNeeded, naming the provider, never retried', async () => {
    replies = [{ status: 409, body: { code: 'reconnect_needed', provider: 'Garmin', error: 'x' } }];
    const err = (await getJson('/x').catch((e: unknown) => e)) as ApiError;
    expect(err).toBeInstanceOf(ReconnectNeeded);
    expect(err.message).toBe('Garmin needs you to sign in again.');
    expect(calls).toHaveLength(1);
  });
  it('setAccessToken stores and clears the token', async () => {
    await setAccessToken('new');
    expect(await accessToken()).toBe('new');
    await setAccessToken(null);
    expect(await accessToken()).toBeNull();
  });
});

describe('retries', () => {
  it('a read retries a network failure with backoff, then succeeds', async () => {
    replies = ['network', { status: 503 }, { body: { ok: 1 } }];
    await expect(getJson('/x')).resolves.toEqual({ ok: 1 });
    expect(calls).toHaveLength(3);
    expect(slept).toEqual(clientConfig.retryDelaysMs);
  });
  it('a read gives up after its tries and says why', async () => {
    replies = ['network', 'network', 'network'];
    await expect(getJson('/x')).rejects.toBeInstanceOf(Offline);
    expect(calls).toHaveLength(clientConfig.retryDelaysMs.length + 1);
  });
  it('429 is retried', async () => {
    replies = [{ status: 429 }, { body: { ok: 1 } }];
    await expect(getJson('/x')).resolves.toEqual({ ok: 1 });
  });
  it('a 4xx error is not retried', async () => {
    replies = [{ status: 400, body: { error: 'Bad date.' } }];
    const err = (await getJson('/x').catch((e: unknown) => e)) as ApiError;
    expect(err).toBeInstanceOf(ApiError);
    expect([err.message, err.status, err.retryable]).toEqual(['Bad date.', 400, false]);
    expect(calls).toHaveLength(1);
  });
  it('a write is never retried here: the watch queue owns retries, after the confirm', async () => {
    replies = ['network'];
    await expect(postJson('/app/api/v1/watch/ops', { id: 'a' })).rejects.toBeInstanceOf(Offline);
    expect(calls).toHaveLength(1);
    expect(calls[0].init.method).toBe('POST');
    expect((calls[0].init.headers as Record<string, string>)['Content-Type']).toBe('application/json');
    expect(calls[0].init.body).toBe(JSON.stringify({ id: 'a' }));
  });
  it('a hung request times out', async () => {
    replies = ['hang'];
    await expect(postJson('/x', {}, { timeoutMs: 20 })).rejects.toBeInstanceOf(TimedOut);
  });
});

describe('error mapping', () => {
  it('5xx: retryable, in plain words', async () => {
    replies = [{ status: 500 }, { status: 502 }, { status: 504 }];
    const err = (await getJson('/x').catch((e: unknown) => e)) as ApiError;
    expect(err).toBeInstanceOf(ApiError);
    expect(err.retryable).toBe(true);
    expect(err.message).toBe('Daash didn’t answer. Try again in a moment.');
  });
  it('a 200 carrying an error is an error', async () => {
    replies = [{ status: 200, body: { error: 'No plan yet.' } }];
    await expect(getJson('/x')).rejects.toThrow('No plan yet.');
  });
  it('an answer that isn\'t JSON reads as an empty answer, not a crash', async () => {
    (globalThis as { fetch: unknown }).fetch = jest.fn(async () => ({ status: 200, ok: true, json: async () => { throw new SyntaxError('x'); } }));
    await expect(getJson('/x')).resolves.toEqual({});
  });
});

describe('sign-out', () => {
  it('removes the token and everything kept for the runner; the device\'s appearance stays', async () => {
    const db = await migratedNodeDb();
    await kv(db).set('appearance', 'dark');
    await kv(db).set('today:plain', { data: { x: 1 }, fetchedAt: 1 });
    await kv(db).set('plan:plain:free', { data: {}, fetchedAt: 1 });
    await kv(db).set('run:a:plain', { data: {}, fetchedAt: 1 });
    await kv(db).set('connections:changes', { priority: ['garmin'] });
    await db.run("INSERT INTO watch_operations (id, type, target, session_id, state, created_at, updated_at) VALUES ('o1', 'add', '{}', 's1', 'sending', 1, 1)");
    await signOut(db);
    expect(await accessToken()).toBeNull();
    expect(await kv(db).get('appearance')).toBe('dark');
    for (const k of ['today:plain', 'plan:plain:free', 'run:a:plain', 'connections:changes']) expect(await kv(db).get(k)).toBeNull();
    expect(await db.all('SELECT * FROM watch_operations')).toEqual([]);
  });
  it('without a database it still signs out', async () => {
    await signOut(null);
    expect(await accessToken()).toBeNull();
  });
  it('clears every runner table the schema has', async () => {
    const db = await migratedNodeDb();
    await expect(clearRunnerData(db)).resolves.toBeUndefined();
  });
});

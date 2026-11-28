// The Coach's API client (build prompt, section 5.10), against the live chat pilot's
// real shapes (garmin_mcp/chat_pilot.py on main):
//
//   GET  /app/api/chat            {messages: [{id, role, text, status, feedback?, tool?}], starters}
//   POST /app/api/chat/send       {text, starter?, context?}: a server-sent event stream (sse.ts);
//                                 400 {code: "empty"}, 429 {code: "cap"} (the daily limit), 404 off
//   POST /app/api/chat/action     {id, decision: "decline"} (see below)
//   POST /app/api/chat/feedback   {id, value: "up" | "down"}
//   POST /app/api/chat/new        the pilot's fresh thread: the app's "Clear conversation"
//
// The conventions are api/client.ts's: the same token, the same typed errors. Reads retry
// with backoff; nothing here that writes retries, and a send is never retried by the
// app: a second try could ask the model twice.
//
// The phone never confirms a proposed change through /action: the pilot's Confirm runs
// the stored call on Garmin directly, which would skip the watch confirm sheet and the
// watch queue. A Coach proposal opens the shared diff and the watch confirm instead
// (CoachScreen), and only "Not now" reaches /action, as a decline. The type below has
// no "confirm" on purpose; src/states.test.tsx checks the source for it.
// TODO(server): /action takes {decision: "applied", batch} once the app has sent it.
import { accessToken, ApiError, API_BASE, getJson, NotSignedIn, Offline, postJson, TimedOut } from '../../api/client';
import type { ContextTag, ServerMessage } from './model';
import { createSseParser, toEvent, type CoachEvent } from './sse';

export const CHAT = '/app/api/chat';

export type ThreadAnswer = { messages: ServerMessage[]; starters: string[] };
export type SendBody = { text: string; starter?: boolean; context?: ContextTag | null };
export type Decision = 'decline';

export interface CoachApi {
  thread(): Promise<ThreadAnswer>;
  /** One try. Events arrive as the reply is written; resolves when the stream ends. */
  send(body: SendBody, onEvent: (e: CoachEvent) => void, signal?: AbortSignal): Promise<void>;
  decide(id: string, decision: Decision): Promise<{ status: string; message?: string }>;
  feedback(id: string, value: 'up' | 'down'): Promise<void>;
  clear(): Promise<void>;
}

/** Today's limit is used up (the server's 429). */
export class CoachLimitReached extends ApiError {
  constructor(message: string) { super(message, 429, false); }
}

/** The server says this person has no Coach (the same 404 as an unknown route). */
export class CoachUnavailable extends ApiError {
  constructor() { super('The Coach isn’t available on this account.', 404, false); }
}

type StreamBody = { getReader(): { read(): Promise<{ done: boolean; value?: Uint8Array | string }> } };
export type StreamResponse = { status: number; ok: boolean; body?: StreamBody | null; json(): Promise<unknown>; text(): Promise<string> };
export type StreamFetch = (url: string, init: { method: string; headers: Record<string, string>; body: string; signal?: AbortSignal }) => Promise<StreamResponse>;

// expo/fetch streams a response body on iOS and Android; React Native's own fetch doesn't.
// Loaded when a message is first sent, so nothing native loads in tests that don't send.
const expoFetch: StreamFetch = (url, init) => {
  const { fetch } = require('expo/fetch') as { fetch: StreamFetch };
  return fetch(url, init);
};

export const SEND_TIMEOUT_MS = 120_000;

/** The stream's events, from a response, as they arrive. Exported for the tests. */
export async function readStream(res: StreamResponse, onEvent: (e: CoachEvent) => void): Promise<void> {
  const parser = createSseParser();
  const emit = (frames: ReturnType<typeof parser.feed>) => frames.forEach((f) => { const e = toEvent(f); if (e) onEvent(e); });
  const reader = res.body?.getReader?.();
  if (!reader) { emit(parser.feed(await res.text())); emit(parser.end()); return; }
  const decoder = typeof TextDecoder !== 'undefined' ? new TextDecoder() : null;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    if (value == null) continue;
    emit(parser.feed(typeof value === 'string' ? value : decoder ? decoder.decode(value, { stream: true }) : String.fromCharCode(...value)));
  }
  emit(parser.end());
}

export function serverCoachApi({ fetch = expoFetch, base = API_BASE }: { fetch?: StreamFetch; base?: string } = {}): CoachApi {
  return {
    thread: () => getJson<ThreadAnswer>(CHAT),
    async send(body, onEvent, signal) {
      const token = await accessToken();
      if (!token) throw new NotSignedIn();
      const controller = new AbortController();
      const abort = () => controller.abort();
      signal?.addEventListener?.('abort', abort);
      const timer = setTimeout(abort, SEND_TIMEOUT_MS);
      try {
        const res = await fetch(`${base}${CHAT}/send`, {
          method: 'POST',
          headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json', Accept: 'text/event-stream', 'X-Daash-Layout': 'native' },
          body: JSON.stringify(body),
          signal: controller.signal,
        }).catch(() => { throw controller.signal.aborted ? new TimedOut() : new Offline(); });
        if (res.status === 401) throw new NotSignedIn();
        if (!res.ok) {
          const answer = await res.json().catch(() => ({})) as { error?: string; code?: string };
          if (res.status === 429 || answer.code === 'cap') throw new CoachLimitReached(answer.error ?? 'That’s today’s limit.');
          if (res.status === 404) throw new CoachUnavailable();
          throw new ApiError(answer.error ?? 'Daash didn’t answer. Try again in a moment.', res.status, false);
        }
        await readStream(res, onEvent).catch((err) => { throw controller.signal.aborted ? new TimedOut() : err instanceof Error ? err : new Offline(); });
      } finally {
        clearTimeout(timer);
        signal?.removeEventListener?.('abort', abort);
      }
    },
    decide: (id, decision) => postJson(`${CHAT}/action`, { id: Number(id), decision }),
    feedback: async (id, value) => { await postJson(`${CHAT}/feedback`, { id: Number(id), value }); },
    clear: async () => { await postJson(`${CHAT}/new`, {}); },
  };
}

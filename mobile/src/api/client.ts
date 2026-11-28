// The signed-in API client (build prompt, section 0: the existing Daash backend).
//
// Errors come back typed, so each screen can say the right thing:
//   NotSignedIn      no token, or the server said 401: sign in again
//   ReconnectNeeded  Daash is fine but Garmin wants the runner to sign in again
//   Offline          the request never reached Daash (no network)
//   TimedOut         Daash didn't answer in time
//   ApiError         Daash answered with an error, with its status and whether a retry may help
//
// Reads (GET) retry by themselves with backoff when a retry may help (offline, timed
// out, 429, 5xx). Writes (POST) never retry here: the one write that reaches the watch
// goes through the watch queue, which retries the confirmed operation itself, so a
// retry can never send something the runner didn't confirm.
// Sign-in lands with onboarding (milestone 9); setAccessToken is its hook.
import * as SecureStore from 'expo-secure-store';

export const API_BASE = process.env.EXPO_PUBLIC_DAASH_API ?? 'https://mcp.garmin.daash.run';
export const ACCESS_KEY = 'daash.access';

export class NotSignedIn extends Error {
  constructor() { super('Not signed in'); }
}

/** Garmin needs the runner to sign in again; Daash itself is reachable. */
export class ReconnectNeeded extends Error {
  constructor(public provider = 'Garmin') { super(`${provider} needs you to sign in again.`); }
}

/** The server didn't answer in time (the request was abandoned). */
export class TimedOut extends Error {
  constructor() { super('Daash took too long to answer. Try again in a moment.'); }
}

/** The request never reached Daash. */
export class Offline extends Error {
  constructor() { super('You’re offline. Showing what’s on your phone.'); }
}

export class ApiError extends Error {
  constructor(message: string, public status: number, public retryable: boolean) { super(message); }
}

const SERVER_TROUBLE = 'Daash didn’t answer. Try again in a moment.';

/** Delays between a read's tries. Tests swap sleep for a stand-in. */
export const clientConfig = {
  retryDelaysMs: [400, 1200],
  sleep: (ms: number) => new Promise<void>((r) => setTimeout(r, ms)),
};

export async function accessToken(): Promise<string | null> {
  try { return await SecureStore.getItemAsync(ACCESS_KEY); } catch { return null; }
}

export async function setAccessToken(token: string | null): Promise<void> {
  if (token) await SecureStore.setItemAsync(ACCESS_KEY, token);
  else await SecureStore.deleteItemAsync(ACCESS_KEY).catch(() => {});
}

export async function getJson<T>(path: string, init?: { timeoutMs?: number }): Promise<T> {
  return request<T>(path, { timeoutMs: init?.timeoutMs });
}

export async function postJson<T>(path: string, body: unknown, init?: { timeoutMs?: number }): Promise<T> {
  return request<T>(path, { method: 'POST', body, timeoutMs: init?.timeoutMs });
}

export const mayRetry = (err: unknown) => err instanceof Offline || err instanceof TimedOut || (err instanceof ApiError && err.retryable);

async function request<T>(path: string, opts: { method?: string; body?: unknown; timeoutMs?: number }): Promise<T> {
  const tries = (opts.method ?? 'GET') === 'GET' ? clientConfig.retryDelaysMs.length + 1 : 1;
  for (let i = 0; ; i++) {
    try {
      return await once<T>(path, opts);
    } catch (err) {
      if (i + 1 >= tries || !mayRetry(err)) throw err;
      await clientConfig.sleep(clientConfig.retryDelaysMs[i]);
    }
  }
}

async function once<T>(path: string, { method = 'GET', body, timeoutMs = 15000 }: { method?: string; body?: unknown; timeoutMs?: number }): Promise<T> {
  const token = await accessToken();
  if (!token) throw new NotSignedIn();
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(`${API_BASE}${path}`, {
      method,
      headers: { Authorization: `Bearer ${token}`, 'X-Daash-Layout': 'native', ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}) },
      body: body !== undefined ? JSON.stringify(body) : undefined,
      signal: controller.signal,
    }).catch(() => { throw controller.signal.aborted ? new TimedOut() : new Offline(); });
    const answer = await res.json().catch(() => ({})) as { error?: string; code?: string; provider?: string };
    if (res.status === 401) throw new NotSignedIn();
    // TODO(server): an expired Garmin login answers {code: "reconnect_needed", provider}.
    if (answer.code === 'reconnect_needed') throw new ReconnectNeeded(answer.provider ?? 'Garmin');
    if (res.status === 429) throw new ApiError('Daash is busy. Trying again shortly.', 429, true);
    if (res.status >= 500) throw new ApiError(SERVER_TROUBLE, res.status, true);
    if (!res.ok || answer.error) throw new ApiError(answer.error ?? SERVER_TROUBLE, res.status, false);
    return answer as T;
  } finally {
    clearTimeout(timer);
  }
}

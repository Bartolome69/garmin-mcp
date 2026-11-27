// The signed-in API client (build prompt, section 0: the existing Daash backend).
// Sign-in itself lands with onboarding (milestone 9); until then a development
// build reads fixtures. TODO(M9): refresh tokens, sign-in, sign-out.
import * as SecureStore from 'expo-secure-store';

export const API_BASE = process.env.EXPO_PUBLIC_DAASH_API ?? 'https://mcp.garmin.daash.run';
const ACCESS_KEY = 'daash.access';

export class NotSignedIn extends Error {
  constructor() { super('Not signed in'); }
}

async function accessToken(): Promise<string | null> {
  try { return await SecureStore.getItemAsync(ACCESS_KEY); } catch { return null; }
}

/** The server didn't answer in time (the request was abandoned). */
export class TimedOut extends Error {
  constructor() { super('Timed out'); }
}

export async function getJson<T>(path: string, init?: { timeoutMs?: number }): Promise<T> {
  return request<T>(path, { timeoutMs: init?.timeoutMs });
}

export async function postJson<T>(path: string, body: unknown, init?: { timeoutMs?: number }): Promise<T> {
  return request<T>(path, { method: 'POST', body, timeoutMs: init?.timeoutMs });
}

async function request<T>(path: string, { method = 'GET', body, timeoutMs = 15000 }: { method?: string; body?: unknown; timeoutMs?: number }): Promise<T> {
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
    }).catch((err) => { throw controller.signal.aborted ? new TimedOut() : err; });
    const answer = await res.json().catch(() => ({}));
    if (res.status === 401) throw new NotSignedIn();
    if (!res.ok || answer.error) throw new Error(answer.error ?? 'Daash didn’t answer. Try again in a moment.');
    return answer as T;
  } finally {
    clearTimeout(timer);
  }
}

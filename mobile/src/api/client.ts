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

export async function getJson<T>(path: string, init?: { timeoutMs?: number }): Promise<T> {
  const token = await accessToken();
  if (!token) throw new NotSignedIn();
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), init?.timeoutMs ?? 15000);
  try {
    const res = await fetch(`${API_BASE}${path}`, {
      headers: { Authorization: `Bearer ${token}`, 'X-Daash-Layout': 'native' }, signal: controller.signal });
    const body = await res.json().catch(() => ({}));
    if (res.status === 401) throw new NotSignedIn();
    if (!res.ok || body.error) throw new Error(body.error ?? 'Daash didn’t answer. Try again in a moment.');
    return body as T;
  } finally {
    clearTimeout(timer);
  }
}

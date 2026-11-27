import { Linking } from 'react-native';

import { API_BASE } from '../../api/client';
import type { Provider } from './model';

/** Reconnect opens the provider's own sign-in on the server (Garmin's is /connect), which the server names per provider. */
export function reconnectUrl(p: Provider): string {
  const path = p.reconnectUrl ?? (p.key === 'garmin' ? '/connect' : `/app/connect/${encodeURIComponent(p.key)}`);
  return /^https?:/.test(path) ? path : `${API_BASE}${path}`;
}

export function reconnect(p: Provider): void {
  Linking.openURL(reconnectUrl(p)).catch(() => {});
}

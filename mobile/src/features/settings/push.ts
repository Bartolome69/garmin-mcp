// The push token, for when the server sends pushes (TODO(server): POST /app/api/v1/push-token,
// with the four kinds' switches from You). Expo Go has no remote push on Android since SDK 53
// (asking throws there), so in Expo Go this never asks: only the permission and local
// notifications are used. Nothing calls it yet; it's the one place allowed to (see expoGo.test.ts).
import { inExpoGo } from '../../data/sample';

export async function pushToken(expoGo: boolean = inExpoGo()): Promise<string | null> {
  if (expoGo) return null;
  try {
    const Notifications = require('expo-notifications') as typeof import('expo-notifications');
    const t = await Notifications.getDevicePushTokenAsync();
    return typeof t.data === 'string' ? t.data : null;
  } catch {
    return null;
  }
}

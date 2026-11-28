// The push token, for when the server sends pushes. Waiting on the server: nothing calls it
// until TODO(server) POST /app/api/v1/push-token exists (with the four kinds' switches from
// You); DECISIONS, milestone 12. Expo Go has no remote push on Android since SDK 53 (asking
// throws there), so in Expo Go this never asks. It's the one place allowed to ask
// (expoGo.test.ts), so the Expo Go rule can't be broken by a second asker.
import { notificationsModule } from '../../data/notifications';
import { inExpoGo } from '../../data/sample';

export async function pushToken(expoGo: boolean = inExpoGo()): Promise<string | null> {
  if (expoGo) return null;
  try {
    const Notifications = notificationsModule();
    const t = await Notifications.getDevicePushTokenAsync();
    return typeof t.data === 'string' ? t.data : null;
  } catch {
    return null;
  }
}

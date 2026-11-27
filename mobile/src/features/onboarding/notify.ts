// The notification permission, asked once and only after the first workout lands
// (build prompt, sections 5.13 and 8). Kept apart so tests can stand in for the OS.
import * as Notifications from 'expo-notifications';

export async function askNotifications(): Promise<'granted' | 'denied'> {
  try {
    const r = await Notifications.requestPermissionsAsync();
    return r.granted ? 'granted' : 'denied';
  } catch {
    return 'denied';
  }
}

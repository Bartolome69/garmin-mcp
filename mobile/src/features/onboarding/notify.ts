// The notification permission, asked once and only after the first workout lands
// (build prompt, sections 5.13 and 8). Kept apart so tests can stand in for the OS,
// and the native module is loaded only when asked, so nothing touches it before then.
export async function askNotifications(): Promise<'granted' | 'denied'> {
  try {
    const Notifications = require('expo-notifications') as typeof import('expo-notifications');
    const r = await Notifications.requestPermissionsAsync();
    return r.granted ? 'granted' : 'denied';
  } catch {
    return 'denied';
  }
}

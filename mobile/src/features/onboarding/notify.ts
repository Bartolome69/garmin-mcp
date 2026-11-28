// The notification permission, asked once and only after the first workout lands
// (build prompt, sections 5.13 and 8). Kept apart so tests can stand in for the OS,
// and the native module is loaded only when asked, so nothing touches it before then.
// On Android 13+ the system shows its prompt only once an app has a notification
// channel, so the default one is made first (needs a device build to see).
import { Platform } from 'react-native';

import { notificationsModule } from '../../data/notifications';

export async function askNotifications(os: string = Platform.OS): Promise<'granted' | 'denied'> {
  try {
    const Notifications = notificationsModule();
    if (os === 'android') {
      await Notifications.setNotificationChannelAsync('default', { name: 'Daash', importance: Notifications.AndroidImportance.DEFAULT });
    }
    const r = await Notifications.requestPermissionsAsync();
    return r.granted ? 'granted' : 'denied';
  } catch {
    return 'denied';
  }
}

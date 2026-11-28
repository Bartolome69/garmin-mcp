// expo-notifications, loaded the first time it's used (not at import), so a test or a build
// without the native module still opens, and nothing touches it before it's needed. The one
// loader for the permission ask (onboarding/notify.ts), the free week's reminder
// (plus/reminder.ts) and the push token (settings/push.ts); callers catch what it throws.
import type * as NotificationsModule from 'expo-notifications';

export function notificationsModule(): typeof NotificationsModule {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  return require('expo-notifications');
}

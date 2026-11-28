// The free week sheet's "Day 5 · We remind you the trial is ending" (build prompt,
// section 5.14): a local notification two days before a renewing free week ends,
// set when the free week starts and taken away once it's bought, cancelled or over.
// The stores don't reliably send one, so Daash does. It shows only where the
// runner has allowed notifications (asked after the first workout lands, never
// here); TODO(server): an email on day 5 so the promise holds without that.
import type * as NotificationsModule from 'expo-notifications';

import { dayWords, isActive, manageLink, REMINDER_DAY, TRIAL_DAYS, type Entitlement } from './entitlement';

const DAY = 24 * 60 * 60 * 1000;
export const REMINDER_ID = 'daash-free-week-ending';

export type Reminder = { at: number; title: string; body: string };

/**
 * When to remind, and what to say: a running free week that will turn into Plus,
 * on day 5 (two days before it ends). Null when there's nothing to remind about:
 * Free, Plus, a free week already cancelled, no end known, or day 5 already past.
 */
export function trialReminder(e: Entitlement, now: number, os: string): Reminder | null {
  if (e.tier !== 'trial' || !isActive(e, now) || !e.willRenew || e.expiresAt == null) return null;
  const at = e.expiresAt - (TRIAL_DAYS - REMINDER_DAY) * DAY;
  if (at <= now) return null;
  const where = manageLink(e.source, os);
  const left = TRIAL_DAYS - REMINDER_DAY;
  return {
    at,
    title: `Your free week ends in ${left} days`,
    body: `Plus starts ${dayWords(e.expiresAt)} unless you cancel${where ? ` ${where.prep} ${where.where}` : ''}.`,
  };
}

/** Sets or takes away the one reminder. Never asks for permission; never throws. */
export interface TrialReminder {
  set(r: Reminder): Promise<void>;
  clear(): Promise<void>;
}

// Loaded when first used, so a build or test without the native module still opens.
function notifications(): typeof NotificationsModule {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  return require('expo-notifications');
}

export const notificationReminder: TrialReminder = {
  async set(r) {
    try {
      const Notifications = notifications();
      await Notifications.cancelScheduledNotificationAsync(REMINDER_ID);
      await Notifications.scheduleNotificationAsync({
        identifier: REMINDER_ID,
        content: { title: r.title, body: r.body },
        trigger: { type: Notifications.SchedulableTriggerInputTypes.DATE, date: r.at },
      });
    } catch {
      // No notifications on this phone (or not allowed): nothing to set.
    }
  },
  async clear() {
    try { await notifications().cancelScheduledNotificationAsync(REMINDER_ID); } catch { /* nothing set */ }
  },
};

/** For tests: keeps the reminder it was given. */
export class FakeReminder implements TrialReminder {
  current: Reminder | null = null;
  calls: string[] = [];
  async set(r: Reminder) { this.current = r; this.calls.push('set'); }
  async clear() { this.current = null; this.calls.push('clear'); }
}

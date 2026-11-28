// Notifications (build prompt, section 8): four kinds, each switchable in You: session
// heads-up (the evening before), sent to your watch, run summary, plan updated. No streak
// shaming, so the Notifications boards' "Missed: …" is not one of them (the prompt wins).
// With the Coach on, the run summary's line is the Coach's debrief (the boards); the title
// and its numbers are the same either way.
//
// The four kinds' words are in notificationCopy.ts (the server's pushes, not sent by the app).

export type NotificationKind = 'headsUp' | 'sent' | 'runSummary' | 'planUpdated';

export const NOTIFICATION_KINDS: { kind: NotificationKind; title: string; sub: string }[] = [
  { kind: 'headsUp', title: 'Session heads-up', sub: 'The evening before' },
  { kind: 'sent', title: 'Sent to your watch', sub: 'When a change reaches Garmin' },
  { kind: 'runSummary', title: 'Run summary', sub: 'When a run comes back from Garmin' },
  { kind: 'planUpdated', title: 'Plan updated', sub: 'When a future session changes' },
];

export type NotificationPrefs = Record<NotificationKind, boolean>;
export const DEFAULT_PREFS: NotificationPrefs = { headsUp: true, sent: true, runSummary: true, planUpdated: true };
export const PREFS_KEY = 'notify:prefs';

export function readPrefs(raw: unknown): NotificationPrefs {
  const out = { ...DEFAULT_PREFS };
  if (raw && typeof raw === 'object') {
    for (const k of Object.keys(DEFAULT_PREFS) as NotificationKind[]) {
      const v = (raw as Record<string, unknown>)[k];
      if (typeof v === 'boolean') out[k] = v;
    }
  }
  return out;
}

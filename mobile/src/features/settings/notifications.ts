// Notifications (build prompt, section 8): four kinds, each switchable in You: session
// heads-up (the evening before), sent to your watch, run summary, plan updated. No streak
// shaming, so the Notifications boards' "Missed: …" is not one of them (the prompt wins).
// With the Coach on, the run summary's line is the Coach's debrief (the boards); the title
// and its numbers are the same either way.
//
// The copy is here so the server's pushes (TODO(server)) and any local notification say the
// same words. Pure: tested against the boards' examples.

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

export type Note = { title: string; body: string };

/** The evening before: what's on tomorrow, and whether it's on the watch. */
export function headsUp(s: { title: string; onWatch: boolean; device?: string | null }): Note {
  return { title: `Tomorrow · ${s.title}`, body: s.onWatch ? `It’s on ${s.device ?? 'your watch'}.` : 'Not on your watch yet. Open Daash to send it.' };
}

/** A confirmed change reached Garmin. */
export function sentToWatch(s: { title: string; day: string; device?: string | null }): Note {
  return { title: 'Sent to your watch', body: `${s.title} is on ${s.device ?? 'your watch'} for ${s.day}.` };
}

/** A run came back. With the Coach on, its debrief is the line; the title (the numbers) is the same. */
export function runSummary(r: { title: string; km: string; pace: string; onPlan: boolean | null; debrief?: string | null }): Note {
  const title = `Run done · ${r.km} km at ${r.pace} /km`;
  if (r.debrief) return { title, body: r.debrief };
  return { title, body: r.onPlan == null ? 'Tap for the summary.' : `${r.onPlan ? 'On plan' : 'Off plan'}. Tap for the summary.` };
}

/** A future session changed. Says when nothing went to the watch yet. */
export function planUpdated(p: { what: string; sentToWatch: boolean }): Note {
  return { title: 'Plan updated', body: `${p.what}${p.sentToWatch ? '' : ' Nothing sent to the watch yet.'}` };
}

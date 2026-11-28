// What the four notifications say (build prompt, section 8; the Notifications boards), for
// the server that sends them: TODO(server) the pushes themselves. Nothing in the app imports
// this (so it isn't in the bundle); it's kept beside the switches so the boards' copy has
// one written-down, tested source the server's copy is checked against
// (src/settings.test.tsx). If the app ever schedules one locally, it uses these words.
// With the Coach on, the run summary's line is the Coach's debrief; the title and its
// numbers are the same either way.

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

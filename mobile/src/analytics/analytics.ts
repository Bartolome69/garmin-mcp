// Analytics (build prompt, section 11): privacy-respecting, named events only, and
// never a health value in a payload. The app has no analytics backend wired yet;
// events go to a sink that a later milestone points at PostHog. Tests swap the sink.

export type EventName = 'paywall_viewed' | 'trial_started';

/** What an event may carry: where it came from and what was chosen. Never a health value. */
export type EventProps = Record<string, string | number | boolean>;

type Sink = (event: EventName, props: EventProps) => void;
let sink: Sink = () => {};

export function setAnalyticsSink(next: Sink | null): void {
  sink = next ?? (() => {});
}

// Keys a payload may never carry: the readiness number, paces, heart rate and the like.
const HEALTH = /hrv|heart|bpm|sleep|readiness|pace|weight|vo2|load|score/i;

export function track(event: EventName, props: EventProps = {}): void {
  const safe = Object.fromEntries(Object.entries(props).filter(([k]) => !HEALTH.test(k)));
  try { sink(event, safe); } catch { /* analytics never breaks the app */ }
}

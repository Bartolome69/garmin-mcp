// "The plan changed somewhere else": onboarding made one (Plan's "Create a plan").
// Screens that hold the plan (Plan, Today) refresh, instead of drawing the old one
// until they remount.
type Listener = () => void;
const listeners = new Set<Listener>();

export function onPlanChanged(fn: Listener): () => void {
  listeners.add(fn);
  return () => { listeners.delete(fn); };
}

export function planChanged(): void {
  listeners.forEach((fn) => fn());
}

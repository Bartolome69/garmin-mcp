// Accessibility helpers shared by the screens (build prompt, section 9):
//   - useReduceMotion: the system's Reduce Motion (iOS) / Remove animations (Android)
//     setting, so a screen can skip an animation it would otherwise run;
//   - announce / useAnnounce: says a change out loud when it happens (the Coach's reply when
//     it's finished; a send's status is said once by the watch queue, WatchProvider). iOS
//     has no live regions, so these announce on both platforms; a view that is announced
//     this way carries no live region too, or TalkBack would say it twice.
import { useEffect, useRef, useState } from 'react';
import { AccessibilityInfo } from 'react-native';

export function useReduceMotion(): boolean {
  const [reduce, setReduce] = useState(false);
  useEffect(() => {
    let live = true;
    AccessibilityInfo.isReduceMotionEnabled?.().then((on) => { if (live) setReduce(!!on); }).catch(() => {});
    const sub = AccessibilityInfo.addEventListener?.('reduceMotionChanged', (on: boolean) => setReduce(!!on));
    return () => { live = false; sub?.remove?.(); };
  }, []);
  return reduce;
}

/** Says `words` now, on both platforms. */
export function announce(words: string): void {
  AccessibilityInfo.announceForAccessibility?.(words);
}

/** Announces `words` each time they change after the first draw (null says nothing). */
export function useAnnounce(words: string | null): void {
  const last = useRef<string | null | undefined>(undefined);
  useEffect(() => {
    if (last.current === undefined) { last.current = words; return; }
    if (words && words !== last.current) announce(words);
    last.current = words;
  }, [words]);
}

/** VoiceOver or TalkBack is on: timed things (a toast that goes by itself) wait for the runner. */
export function useScreenReader(): boolean {
  const [on, setOn] = useState(false);
  useEffect(() => {
    let live = true;
    AccessibilityInfo.isScreenReaderEnabled?.().then((v) => { if (live) setOn(!!v); }).catch(() => {});
    const sub = AccessibilityInfo.addEventListener?.('screenReaderChanged', (v: boolean) => setOn(!!v));
    return () => { live = false; sub?.remove?.(); };
  }, []);
  return on;
}

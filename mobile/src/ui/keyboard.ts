// The software keyboard, as one shared state: whether it's up and where its top edge is
// on screen. The tab bar hides while it's up (app/(tabs)/_layout.tsx) and the Coach's
// composer sits on it (features/coach/CoachView.tsx). Works the same in Expo Go and a
// store build: iOS sends keyboardWill*, Android keyboardDid* (with softwareKeyboardLayoutMode
// "resize", or edge to edge where the keyboard overlays the window; both are handled by
// measuring how far the keyboard overlaps the view, see keyboardOverlap).
import { useSyncExternalStore } from 'react';
import { Keyboard, Platform, type KeyboardEvent } from 'react-native';

export type KeyboardState = { visible: boolean; height: number; screenY: number };

const HIDDEN: KeyboardState = { visible: false, height: 0, screenY: 0 };
let state: KeyboardState = HIDDEN;
const listeners = new Set<() => void>();
let subscribed = false;

function set(next: KeyboardState) {
  if (next.visible === state.visible && next.height === state.height && next.screenY === state.screenY) return;
  state = next;
  listeners.forEach((l) => l());
}

function subscribeNative() {
  if (subscribed) return;
  subscribed = true;
  const ios = Platform.OS === 'ios';
  const show = (e: KeyboardEvent) => set({ visible: true, height: e.endCoordinates.height, screenY: e.endCoordinates.screenY });
  const hide = () => set(HIDDEN);
  Keyboard.addListener(ios ? 'keyboardWillShow' : 'keyboardDidShow', show);
  Keyboard.addListener(ios ? 'keyboardWillHide' : 'keyboardDidHide', hide);
  if (ios) Keyboard.addListener('keyboardWillChangeFrame', (e) => { if (state.visible) show(e); });
}

function subscribe(l: () => void) {
  subscribeNative();
  listeners.add(l);
  return () => { listeners.delete(l); };
}

export function useKeyboard(): KeyboardState {
  return useSyncExternalStore(subscribe, () => state, () => HIDDEN);
}

/** Only whether the keyboard is up: a height or frame change doesn't redraw the caller (the tab layout). */
export function useKeyboardVisible(): boolean {
  return useSyncExternalStore(subscribe, () => state.visible, () => false);
}

/**
 * How much of a view the keyboard covers: the view's bottom edge (in window coordinates)
 * below the keyboard's top edge. Zero when the window already shrank for it (Android's
 * resize) or the view ends above it.
 */
export function keyboardOverlap(viewBottom: number, k: KeyboardState): number {
  if (!k.visible) return 0;
  return Math.max(0, Math.round(viewBottom - k.screenY));
}

/** Tests only: the keyboard as the native side would report it. */
export function setKeyboardForTests(next: Partial<KeyboardState> | null): void {
  set(next ? { ...HIDDEN, ...next, visible: next.visible ?? true } : HIDDEN);
}

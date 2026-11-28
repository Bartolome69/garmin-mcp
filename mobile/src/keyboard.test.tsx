// The Coach's composer and the keyboard (milestone 12, "how Claude works"): the screen never
// moves; the header stays, the thread shrinks and stays on the newest message, the composer
// sits directly on the keyboard, and the tab bar hides while the keyboard is up. Driven by the
// same Keyboard events the phone sends (keyboardWill* on iOS, keyboardDid* on Android).
import { afterEach, describe, expect, it, jest } from '@jest/globals';
import { act, screen } from 'expo-router/testing-library';
import { DeviceEventEmitter, FlatList, Platform, StyleSheet } from 'react-native';

import { openScreen, SCREENS } from './testing/screens';
import { renderCounts } from './ui/hooks';
import { keyboardOverlap, setKeyboardForTests } from './ui/keyboard';

const coach = SCREENS.find((c) => c.name === 'Coach')!;
const ios = Platform.OS === 'ios';
const WINDOW_H = 844; // testing/setup.ts

async function keyboard(show: boolean, height = 336) {
  const e = { endCoordinates: { height, screenY: WINDOW_H - height, screenX: 0, width: 390 }, duration: 250, easing: 'keyboard' };
  await act(async () => { DeviceEventEmitter.emit(show ? (ios ? 'keyboardWillShow' : 'keyboardDidShow') : (ios ? 'keyboardWillHide' : 'keyboardDidHide'), e); });
}
const dockPadding = () => StyleSheet.flatten(screen.getByTestId('coach-dock').props.style).paddingBottom;
type Node = { type: string; props: Record<string, any>; children: (Node | string)[] | null };
function tabsHost(n: unknown): Node | null {
  if (!n || typeof n !== 'object') return null;
  if (Array.isArray(n)) { for (const c of n) { const f = tabsHost(c); if (f) return f; } return null; }
  const node = n as Node;
  if (/^RNSTabsHost/.test(node.type)) return node;
  return tabsHost(node.children ?? []);
}
const tabBarHidden = () => tabsHost(screen.toJSON())?.props.tabBarHidden;

afterEach(async () => { await act(async () => { setKeyboardForTests(null); }); jest.restoreAllMocks(); });

describe(`the Coach with the keyboard on ${Platform.OS}`, () => {
  it('the composer sits on the keyboard and the tab bar hides; both come back when it goes', async () => {
    await openScreen(coach);
    expect(dockPadding()).toBe(10);
    expect(tabBarHidden()).toBe(false);
    await keyboard(true);
    // The Coach screen reaches the window's bottom once the bar hides: the dock is lifted by the whole keyboard.
    expect(dockPadding()).toBe(336 + 8);
    expect(tabBarHidden()).toBe(true);
    await keyboard(false);
    expect(dockPadding()).toBe(10);
    expect(tabBarHidden()).toBe(false);
  });

  it('the header and the thread stay where they are: only the dock moves, the list shrinks', async () => {
    await openScreen(coach);
    const before = StyleSheet.flatten(screen.getByTestId('coach').props.style);
    await keyboard(true);
    const after = StyleSheet.flatten(screen.getByTestId('coach').props.style);
    expect(after).toEqual(before); // no padding or offset on the whole screen: nothing pushes the header up
    expect(StyleSheet.flatten(screen.getByTestId('coach-thread').props.style).flex).toBe(1);
  });

  it('stays on the newest message when the list shrinks', async () => {
    const toEnd = jest.spyOn(FlatList.prototype, 'scrollToEnd');
    await openScreen(coach);
    toEnd.mockClear();
    await keyboard(true);
    await act(async () => {
      screen.getByTestId('coach-thread').props.onLayout?.({ nativeEvent: { layout: { x: 0, y: 0, width: 390, height: 300 } } });
    });
    expect(toEnd).toHaveBeenCalled();
  });

  it('a keyboard that changes height while up (QuickType, emoji) moves the dock, not the whole tab tree', async () => {
    await openScreen(coach);
    await keyboard(true);
    const tabs = renderCounts.get('TabsLayout') ?? 0;
    expect(tabs).toBeGreaterThan(0);
    const e = { endCoordinates: { height: 380, screenY: WINDOW_H - 380, screenX: 0, width: 390 }, duration: 250, easing: 'keyboard' };
    await act(async () => { DeviceEventEmitter.emit(ios ? 'keyboardWillChangeFrame' : 'keyboardDidShow', e); });
    expect(dockPadding()).toBe(380 + 8);
    expect(renderCounts.get('TabsLayout') ?? 0).toBe(tabs);
  });

  it('a phone that already shrank its window for the keyboard (Android resize) adds nothing', () => {
    // The screen's bottom edge is the keyboard's top edge: no overlap.
    expect(keyboardOverlap(508, { visible: true, height: 336, screenY: 508 })).toBe(0);
    // Edge to edge, the keyboard covers the bottom 336 pt.
    expect(keyboardOverlap(844, { visible: true, height: 336, screenY: 508 })).toBe(336);
    expect(keyboardOverlap(844, { visible: false, height: 0, screenY: 0 })).toBe(0);
  });
});

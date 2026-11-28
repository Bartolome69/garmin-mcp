// Scripted screen-reader walkthroughs, one per screen (milestone 12): what VoiceOver and
// TalkBack would focus, in order (src/testing/a11y.ts reads it from the rendered tree).
// On every screen:
//   - the screen says what it is before any action (a title, header or line of text first);
//   - nothing focusable is unnamed, and no two controls share a name;
//   - the primary action (the one brand fill) can be reached;
//   - a sheet is a native form sheet over its screen (the system, not the app, keeps focus inside
//     it and closes it with Escape or back: checked on a device, WALKTHROUGHS.md), and the screen
//     drawn behind it keeps no brand fill.
// The device run is docs/native-app/WALKTHROUGHS.md.
import { describe, expect, it } from '@jest/globals';
import fs from 'fs';
import path from 'path';
import { screen } from 'expo-router/testing-library';
import { Platform, StyleSheet } from 'react-native';

import { a11yElements, all, headerTitle, locate, words, type Node } from './testing/a11y';
import { openScreen, SCREENS } from './testing/screens';
import { colors } from './theme/tokens';

const CONTROL_ROLES = new Set(['button', 'link', 'radio', 'tab', 'checkbox', 'switch', 'adjustable', 'menuitem', 'textfield', 'tablist']);
// Navigation chrome a screen reader meets before the title, as in the system's own modals.
const CHROME = new Set(['Close', 'Back', 'Cancel']);

/** How a route is presented, from its stack's layout file (the test renderer doesn't keep it). */
function presentation(route: string): string | null {
  const parts = route.split('/');
  for (let i = parts.length - 1; i >= 0; i--) {
    const file = path.join(__dirname, 'app', ...parts.slice(0, i), '_layout.tsx');
    if (!fs.existsSync(file)) continue;
    const name = parts.slice(i).join('/');
    const src = fs.readFileSync(file, 'utf8');
    const at = src.indexOf(`<Stack.Screen name="${name}"`);
    if (at < 0) continue;
    const options = src.slice(at, src.indexOf('/>', at));
    if (/presentation: 'formSheet'|options=\{sheet\}|\.\.\.sheet/.test(options)) return 'formSheet';
    return /presentation: '(\w+)'/.exec(options)?.[1] ?? 'push';
  }
  return null;
}

// Names two controls may share on purpose: a row of choices repeated per item (each sits under its own heading).
const SHARED_NAMES = new Set(['Reconnect', 'Helpful', 'Not helpful', 'Edit', 'Remove', 'Request it', 'These are different runs']);

describe(`focus order on ${Platform.OS}`, () => {
  it.each(SCREENS.map((c) => [c.name, c] as const))('%s', async (_name, c) => {
    await openScreen(c, 'dark', true);
    const { front, behind } = locate(screen.toJSON(), c.id);
    const els = a11yElements(front);
    const title = headerTitle(front);
    expect(els.length).toBeGreaterThan(0);

    // Says what it is first.
    const firstControl = els.findIndex((e) => CONTROL_ROLES.has(e.role) && !CHROME.has(e.label));
    const firstWords = els.findIndex((e) => e.role === 'header' || e.role === 'text' || e.role === 'group' || e.role === 'image');
    expect({ title, before: firstWords >= 0 && (firstControl < 0 || firstWords < firstControl) || !!title }).toEqual({ title, before: true });

    // Named, and not twice.
    expect(els.filter((e) => e.role !== 'text' && !e.label).map((e) => `${e.role} ${e.id ?? ''}`)).toEqual([]);
    const names = els.filter((e) => CONTROL_ROLES.has(e.role) && e.role !== 'textfield').map((e) => e.label);
    expect(names.filter((n, i) => names.indexOf(n) !== i && !SHARED_NAMES.has(n))).toEqual([]);

    // The primary action is focusable.
    const brand = all(front).filter((n: Node) => StyleSheet.flatten(n.props?.style)?.backgroundColor === colors.dark.brand);
    for (const b of brand) {
      const label = b.props.accessibilityLabel ?? words(b).join(' ').trim();
      expect(els.some((e) => e.label === label)).toBe(true);
    }

    if (c.sheet) {
      // A native sheet: the system makes it modal for VoiceOver and TalkBack, and Escape / back closes it.
      expect(presentation(c.route)).toBe('formSheet');
      // Something is drawn behind it (else the next check proves nothing), and it keeps no brand fill (it lost focus).
      expect(behind.length).toBeGreaterThan(0);
      expect(behind.flatMap((b) => all(b).filter((n: Node) => StyleSheet.flatten(n.props?.style)?.backgroundColor === colors.dark.brand))).toEqual([]);
    }
  });
});

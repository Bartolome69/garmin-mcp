// Large windows (milestone 12): on a tablet, a foldable or Android in landscape, content
// keeps one readable column (MAX_CONTENT_WIDTH, centred) instead of stretching; on a phone
// nothing changes. Today at 200% text (P3-Today-xl) is in the snapshot matrix.
import { afterAll, describe, expect, it } from '@jest/globals';
import { screen } from 'expo-router/testing-library';
import { Platform, StyleSheet } from 'react-native';

import { all, locate } from './testing/a11y';
import { openScreen, SCREENS, setWindow } from './testing/screens';
import { MAX_CONTENT_WIDTH } from './ui/Screen';

const WIDE = ['Today', 'Session detail', 'Post-run (Plan)', 'You', 'Connections', 'Coach', 'Plus (paywall)', 'Onboarding · goal', 'Privacy'];
// The screen's main vertical scroll (or list): its content column.
const column = (id: string) => {
  const { front } = locate(screen.toJSON(), id);
  const scroll = all(front).find((n) => n.props?.contentContainerStyle && !n.props.horizontal);
  return StyleSheet.flatten(scroll?.props.contentContainerStyle) ?? {};
};

afterAll(() => setWindow());

describe(`wide windows on ${Platform.OS}`, () => {
  it.each(WIDE.map((n) => [n, SCREENS.find((c) => c.name === n)!] as const))('%s keeps a readable column', async (_n, c) => {
    for (const [width, height] of [[1024, 768], [844, 390], [390, 844]]) {
      await openScreen(c, 'dark', true, { width, height });
      const style = column(c.id);
      expect([width, style.maxWidth, style.alignSelf]).toEqual([width, MAX_CONTENT_WIDTH, 'center']);
    }
  });
});

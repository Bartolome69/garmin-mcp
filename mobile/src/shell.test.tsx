// The empty shell (milestone 1): the real tab layout and screens, on iOS and
// Android (this suite runs once per platform), in dark and light, with the
// Coach flag off and on.
import { describe, expect, it } from '@jest/globals';
import { Slot } from 'expo-router';
import { renderRouter, screen } from 'expo-router/testing-library';
import { Platform, StyleSheet } from 'react-native';
import type { ReactNode } from 'react';

import { DEFAULT_FLAGS } from './flags/flags';
import { FlagsProvider } from './flags/FlagsProvider';
import { ThemeProvider, type Appearance } from './theme/ThemeProvider';
import { colors } from './theme/tokens';
import TabsLayout from './app/(tabs)/_layout';
import CoachIndex from './app/(tabs)/coach/index';
import CrewIndex from './app/(tabs)/crew/index';
import PlanIndex from './app/(tabs)/plan/index';
import TodayLayout from './app/(tabs)/today/_layout';
import TodayIndex from './app/(tabs)/today/index';
import YouIndex from './app/(tabs)/you/index';

function routes(appearance: Appearance, coach: boolean) {
  const Wrap = ({ children }: { children: ReactNode }) => (
    <ThemeProvider initial={appearance}>
      <FlagsProvider initial={{ ...DEFAULT_FLAGS, coach_enabled: coach }}>{children}</FlagsProvider>
    </ThemeProvider>
  );
  return {
    _layout: () => <Wrap><Slot /></Wrap>,
    '(tabs)/_layout': TabsLayout,
    '(tabs)/today/_layout': TodayLayout,
    '(tabs)/today/index': TodayIndex,
    '(tabs)/plan/index': PlanIndex,
    '(tabs)/coach/index': CoachIndex,
    '(tabs)/crew/index': CrewIndex,
    '(tabs)/you/index': YouIndex,
  };
}

describe.each(['dark', 'light'] as const)(`shell on ${Platform.OS}, %s`, (appearance) => {
  it('opens on Today with the token background', async () => {
    await renderRouter(routes(appearance, false), { initialUrl: '/today' });
    const today = await screen.findByTestId('today');
    expect(StyleSheet.flatten(today.props.style).backgroundColor).toBe(colors[appearance].bg);
  });

  it('keeps the Coach out of reach with coach_enabled off', async () => {
    await renderRouter(routes(appearance, false), { initialUrl: '/coach' });
    expect(screen.queryByTestId('coach')).toBeNull();
  });

  it('opens the Coach with coach_enabled on', async () => {
    await renderRouter(routes(appearance, true), { initialUrl: '/coach' });
    expect(await screen.findByTestId('coach')).toBeTruthy();
  });
});

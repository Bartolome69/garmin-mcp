// The app's root: theme, flags, Plus, fonts and the local database, then the tabs.
import { GeistMono_500Medium } from '@expo-google-fonts/geist-mono';
import { SourceSerif4_400Regular, SourceSerif4_400Regular_Italic } from '@expo-google-fonts/source-serif-4';
import { useFonts } from 'expo-font';
import { Stack } from 'expo-router';
import * as SplashScreen from 'expo-splash-screen';
import { StatusBar } from 'expo-status-bar';
import { useEffect, useMemo, useState } from 'react';
import { Platform } from 'react-native';
import { GestureHandlerRootView } from 'react-native-gesture-handler';

import { getJson } from '../api/client';
import { openDb } from '../db/expo';
import { DbContext } from '../db/DbContext';
import { kv } from '../db/kv';
import type { Db } from '../db/types';
import { FlagsProvider, type CoachChoiceStore, type FlagsFetcher, type FlagsStore } from '../flags/FlagsProvider';
import { EntitlementsProvider } from '../features/plus/EntitlementsProvider';
import { WatchToast } from '../features/watch/WatchToast';
import { WatchProvider } from '../features/watch/WatchProvider';
import { ThemeProvider, useTheme, type Appearance } from '../theme/ThemeProvider';

SplashScreen.preventAutoHideAsync().catch(() => {});

const APPEARANCE_KEY = 'appearance';
const FLAGS_KEY = 'flags';
const COACH_OFF_KEY = 'coach:off';

// The server's flags for this person: GET /app/api/features (the chat pilot's, on main:
// {"chat": true} for the Coach's people, {} for everyone else). Null when signed out or
// offline, so the cached answer stands. A development build can force the Coach on.
// TODO(server): the v1 answer adds plus, coach_daily_limit and the rest of section 10.
const flagsFetcher: FlagsFetcher = __DEV__ && process.env.EXPO_PUBLIC_DAASH_COACH === '1'
  ? async () => ({ coach_enabled: true })
  : () => getJson<unknown>('/app/api/features').catch(() => null);

export default function RootLayout() {
  const [fontsLoaded, fontError] = useFonts({ GeistMono_500Medium, SourceSerif4_400Regular, SourceSerif4_400Regular_Italic });
  const [db, setDb] = useState<Db | null>(null);
  const [appearance, setAppearance] = useState<Appearance | null>(null);

  useEffect(() => {
    openDb()
      .then(async (opened) => {
        setAppearance((await kv(opened).get<Appearance>(APPEARANCE_KEY)) ?? 'system');
        setDb(opened);
      })
      .catch(() => setAppearance('system'));
  }, []);

  const ready = (fontsLoaded || !!fontError) && appearance !== null;
  useEffect(() => { if (ready) SplashScreen.hideAsync().catch(() => {}); }, [ready]);

  const flagsStore = useMemo<FlagsStore | undefined>(() => db
    ? { load: () => kv(db).get(FLAGS_KEY), save: (raw) => kv(db).set(FLAGS_KEY, raw) }
    : undefined, [db]);
  const coachChoice = useMemo<CoachChoiceStore | undefined>(() => db
    ? { load: () => kv(db).get<boolean>(COACH_OFF_KEY), save: (off) => kv(db).set(COACH_OFF_KEY, off) }
    : undefined, [db]);

  if (!ready) return null;
  return (
    <GestureHandlerRootView style={{ flex: 1 }}>
      <DbContext.Provider value={db}>
        <ThemeProvider initial={appearance} onChange={(a) => { if (db) kv(db).set(APPEARANCE_KEY, a); }}>
          <FlagsProvider store={flagsStore} fetcher={flagsFetcher} choice={coachChoice}>
            <EntitlementsProvider>
              <WatchProvider>
                <Root />
              </WatchProvider>
            </EntitlementsProvider>
          </FlagsProvider>
        </ThemeProvider>
      </DbContext.Provider>
    </GestureHandlerRootView>
  );
}

function Root() {
  const { scheme, colors } = useTheme();
  return (
    <>
      <StatusBar style={scheme === 'light' ? 'dark' : 'light'} />
      <Stack screenOptions={{ headerShown: false, contentStyle: { backgroundColor: colors.bg } }}>
        <Stack.Screen name="(tabs)" />
        <Stack.Screen name="onboarding" options={{ gestureEnabled: false }} />
        {/* The watch confirm, over whichever tab asked: a sheet with a grabber. */}
        <Stack.Screen name="watch/confirm" options={{
          presentation: 'formSheet', sheetGrabberVisible: true, sheetAllowedDetents: 'fitToContents',
          sheetCornerRadius: Platform.OS === 'android' ? 28 : undefined, contentStyle: { backgroundColor: colors.surface },
        }} />
        {/* Daash Plus, over whichever tab asked, and its trial sheet over it. */}
        <Stack.Screen name="plus/index" options={{ presentation: Platform.OS === 'ios' ? 'modal' : 'card' }} />
        <Stack.Screen name="plus/trial" options={{
          presentation: 'formSheet', sheetGrabberVisible: true, sheetAllowedDetents: 'fitToContents',
          sheetCornerRadius: Platform.OS === 'android' ? 28 : undefined, contentStyle: { backgroundColor: colors.surface },
        }} />
      </Stack>
      <WatchToast />
    </>
  );
}

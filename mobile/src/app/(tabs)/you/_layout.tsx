// You's stack (build prompt, section 4): You, then Connections and Source priority,
// with the trust sheets, the widgets setup, Coach memory or Training preferences with its sheets,
// then Activities, settings (units, notifications, appearance), privacy, help and Sign out. Large titles on iOS, the Material 3 app bar on Android;
// navigation in the text colour, never brand.
import { Stack } from 'expo-router';
import { Platform } from 'react-native';

import { ConnectionsProvider } from '../../../features/connections/ConnectionsProvider';
import { useTheme } from '../../../theme/ThemeProvider';

// A sheet opened straight from a link sits over the tab's first screen, not on its own.
export const unstable_settings = { anchor: 'index' };

export default function YouLayout() {
  const { colors } = useTheme();
  const sheet = {
    presentation: 'formSheet' as const, sheetGrabberVisible: true, sheetAllowedDetents: [0.6, 1],
    sheetCornerRadius: Platform.OS === 'android' ? 28 : undefined, headerShown: false, contentStyle: { backgroundColor: colors.surface },
  };
  return (
    <ConnectionsProvider>
      <Stack screenOptions={{
        headerShadowVisible: false, headerStyle: { backgroundColor: colors.bg }, headerTintColor: colors.text,
        headerTitleStyle: { color: colors.text }, contentStyle: { backgroundColor: colors.bg },
      }}>
        <Stack.Screen name="index" options={{ title: 'You', headerLargeTitle: Platform.OS === 'ios', headerLargeTitleShadowVisible: false }} />
        <Stack.Screen name="connections" options={{ title: 'Connections', headerBackTitle: 'You' }} />
        <Stack.Screen name="priority" options={{ title: 'Source priority', headerBackTitle: 'Connections' }} />
        <Stack.Screen name="widgets" options={{ title: 'Widgets', headerBackTitle: 'You' }} />
        <Stack.Screen name="coming" options={{ title: 'Advanced merge rules', headerBackTitle: 'Source priority' }} />
        <Stack.Screen name="see" options={sheet} />
        <Stack.Screen name="trouble" options={sheet} />
        <Stack.Screen name="request" options={{ ...sheet, sheetAllowedDetents: 'fitToContents' }} />
        <Stack.Screen name="memory" options={{ title: 'Coach memory', headerBackTitle: 'You' }} />
        <Stack.Screen name="memory-edit" options={{ ...sheet, sheetAllowedDetents: [0.7, 1] }} />
        <Stack.Screen name="memory-clear" options={{ ...sheet, sheetAllowedDetents: 'fitToContents' }} />
        <Stack.Screen name="activities" options={{ title: 'Activities', headerBackTitle: 'You' }} />
        <Stack.Screen name="run/[id]" options={{ title: 'Run', headerBackTitle: 'Activities' }} />
        <Stack.Screen name="training" options={{ title: 'Units and zones', headerBackTitle: 'You' }} />
        <Stack.Screen name="notifications" options={{ title: 'Notifications', headerBackTitle: 'You' }} />
        <Stack.Screen name="appearance" options={{ title: 'Appearance', headerBackTitle: 'You' }} />
        <Stack.Screen name="privacy" options={{ title: 'Privacy', headerBackTitle: 'You' }} />
        <Stack.Screen name="help" options={{ title: 'Help', headerBackTitle: 'You' }} />
        <Stack.Screen name="sign-out" options={{ ...sheet, sheetAllowedDetents: 'fitToContents' }} />
      </Stack>
    </ConnectionsProvider>
  );
}

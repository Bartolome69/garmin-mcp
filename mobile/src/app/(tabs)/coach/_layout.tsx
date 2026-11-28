// The Coach's stack: the one conversation (a large title on iOS, the Material 3 app
// bar on Android), its sheets (menu, filter, clear, today's limit) and the shared diff
// a proposal opens. Navigation uses the text colour, never brand.
import { Stack } from 'expo-router';
import { Platform } from 'react-native';

import { useTheme } from '../../../theme/ThemeProvider';

// A sheet opened straight from a link sits over the conversation, not on its own.
export const unstable_settings = { anchor: 'index' };

export default function CoachLayout() {
  const { colors } = useTheme();
  const sheet = {
    presentation: 'formSheet' as const, sheetGrabberVisible: true, sheetAllowedDetents: 'fitToContents' as const,
    sheetCornerRadius: Platform.OS === 'android' ? 28 : undefined, headerShown: false, contentStyle: { backgroundColor: colors.surface },
  };
  return (
    <Stack screenOptions={{
      headerShadowVisible: false, headerStyle: { backgroundColor: colors.bg }, headerTintColor: colors.text,
      headerTitleStyle: { color: colors.text }, contentStyle: { backgroundColor: colors.bg },
    }}>
      <Stack.Screen name="index" options={{ title: 'Coach', headerLargeTitle: Platform.OS === 'ios', headerLargeTitleShadowVisible: false }} />
      <Stack.Screen name="menu" options={sheet} />
      <Stack.Screen name="filter" options={sheet} />
      <Stack.Screen name="clear" options={sheet} />
      <Stack.Screen name="limit" options={sheet} />
      <Stack.Screen name="new-week" options={{ title: 'New week', headerBackTitle: 'Coach' }} />
    </Stack>
  );
}

// Today's stack: the screen, its two sheets (platform form sheets with a grabber)
// and the steps that open from it. Navigation uses the text colour, never brand.
import { Stack } from 'expo-router';
import { Platform } from 'react-native';

import { TodayProvider } from '../../../features/today/TodayProvider';
import { WidgetSync } from '../../../features/widgets/WidgetSync';
import { useTheme } from '../../../theme/ThemeProvider';

// A sheet opened straight from a link sits over the tab's first screen, not on its own.
export const unstable_settings = { anchor: 'index' };

export default function TodayLayout() {
  const { colors } = useTheme();
  const sheet = {
    presentation: 'formSheet' as const,
    sheetGrabberVisible: true,
    sheetAllowedDetents: 'fitToContents' as const,
    sheetCornerRadius: Platform.OS === 'android' ? 28 : undefined,
    headerShown: false,
    contentStyle: { backgroundColor: colors.surface },
  };
  return (
    <TodayProvider>
      {/* The widgets read what Today draws. */}
      <WidgetSync />
      <Stack screenOptions={{
        headerShadowVisible: false,
        headerStyle: { backgroundColor: colors.bg },
        headerTintColor: colors.text,
        headerTitleStyle: { color: colors.text },
        contentStyle: { backgroundColor: colors.bg },
      }}>
        <Stack.Screen name="index" options={{ title: 'Today', headerShown: Platform.OS === 'android' }} />
        <Stack.Screen name="readiness" options={sheet} />
        <Stack.Screen name="options" options={sheet} />
        <Stack.Screen name="new-week" options={{ title: 'New week', headerShown: true, headerBackTitle: 'Today' }} />
        <Stack.Screen name="run/[id]" options={{ title: '', headerShown: true, headerBackTitle: 'Today' }} />
        <Stack.Screen name="next" options={{ title: '' }} />
      </Stack>
    </TodayProvider>
  );
}

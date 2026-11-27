// Today's stack: the screen, its two sheets (platform form sheets with a grabber)
// and the steps that open from it. Navigation uses the text colour, never brand.
import { Stack } from 'expo-router';
import { Platform } from 'react-native';

import { TodayProvider } from '../../../features/today/TodayProvider';
import { useTheme } from '../../../theme/ThemeProvider';

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
        <Stack.Screen name="next" options={{ title: '' }} />
      </Stack>
    </TodayProvider>
  );
}

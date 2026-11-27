// Each tab is a stack: large titles on iOS, a Material 3 top app bar on Android.
// Navigation uses the text colour, never brand (build prompt, section 2).
import { Stack } from 'expo-router';
import { Platform } from 'react-native';

import { useTheme } from '../theme/ThemeProvider';

export function TabStack({ title }: { title: string }) {
  const { colors } = useTheme();
  return (
    <Stack
      screenOptions={{
        headerLargeTitle: Platform.OS === 'ios',
        headerLargeTitleShadowVisible: false,
        headerShadowVisible: false,
        headerStyle: { backgroundColor: colors.bg },
        headerTintColor: colors.text,
        headerTitleStyle: { color: colors.text },
        contentStyle: { backgroundColor: colors.bg },
      }}
    >
      <Stack.Screen name="index" options={{ title }} />
    </Stack>
  );
}

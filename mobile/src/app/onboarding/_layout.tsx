// Onboarding's stack: welcome, the try builder, then the four steps to the first
// workout on the watch. Back on each step; none before welcome.
import { Stack } from 'expo-router';

import { OnboardingProvider } from '../../features/onboarding/OnboardingProvider';
import { useTheme } from '../../theme/ThemeProvider';

export default function OnboardingLayout() {
  const { colors } = useTheme();
  return (
    <OnboardingProvider>
      <Stack screenOptions={{
        headerShadowVisible: false, headerStyle: { backgroundColor: colors.bg }, headerTintColor: colors.text,
        headerTitleStyle: { color: colors.text }, contentStyle: { backgroundColor: colors.bg }, title: '', headerBackTitle: 'Back',
      }}>
        <Stack.Screen name="index" options={{ headerShown: false }} />
        <Stack.Screen name="try" options={{ title: 'Try it' }} />
        <Stack.Screen name="landed" options={{ headerShown: false, gestureEnabled: false }} />
      </Stack>
    </OnboardingProvider>
  );
}

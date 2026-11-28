// Onboarding's stack: welcome, the try builder, then the four steps to the first
// workout on the watch. Back on each step; none before welcome, and none once
// week 1 has gone to the watch.
import { Stack } from 'expo-router';

import { OnboardingProvider } from '../../features/onboarding/OnboardingProvider';
import { useTheme } from '../../theme/ThemeProvider';

/** The stack alone, so tests can start it part way through with their own provider. */
export function OnboardingStack() {
  const { colors } = useTheme();
  return (
    <Stack screenOptions={{
      headerShadowVisible: false, headerStyle: { backgroundColor: colors.bg }, headerTintColor: colors.text,
      headerTitleStyle: { color: colors.text }, contentStyle: { backgroundColor: colors.bg }, title: '', headerBackTitle: 'Back',
    }}>
      <Stack.Screen name="index" options={{ headerShown: false }} />
      <Stack.Screen name="try" options={{ title: 'Try it' }} />
      <Stack.Screen name="landed" options={{ headerShown: false, gestureEnabled: false }} />
    </Stack>
  );
}

export default function OnboardingLayout() {
  return (
    <OnboardingProvider>
      <OnboardingStack />
    </OnboardingProvider>
  );
}

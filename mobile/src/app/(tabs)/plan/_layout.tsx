// Plan's stack: the tab (a large title on iOS, the Material 3 app bar on Android),
// a session's detail, the Move sheet, and the steps that open from them.
// Navigation uses the text colour, never brand.
import { Stack } from 'expo-router';
import { Platform } from 'react-native';

import { PlanProvider } from '../../../features/plan/PlanProvider';
import { useTheme } from '../../../theme/ThemeProvider';

export default function PlanLayout() {
  const { colors } = useTheme();
  return (
    <PlanProvider>
      <Stack screenOptions={{
        headerShadowVisible: false,
        headerStyle: { backgroundColor: colors.bg },
        headerTintColor: colors.text,
        headerTitleStyle: { color: colors.text },
        contentStyle: { backgroundColor: colors.bg },
      }}>
        <Stack.Screen name="index" options={{ title: 'Plan', headerLargeTitle: Platform.OS === 'ios', headerLargeTitleShadowVisible: false }} />
        <Stack.Screen name="session/[id]" options={{ title: '', headerBackTitle: 'Plan' }} />
        <Stack.Screen name="move" options={{
          presentation: 'formSheet', sheetGrabberVisible: true, sheetAllowedDetents: 'fitToContents',
          sheetCornerRadius: Platform.OS === 'android' ? 28 : undefined, headerShown: false,
          contentStyle: { backgroundColor: colors.surface },
        }} />
        <Stack.Screen name="next" options={{ title: '' }} />
      </Stack>
    </PlanProvider>
  );
}

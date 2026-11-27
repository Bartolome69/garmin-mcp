import type { ReactNode } from 'react';
import { ScrollView } from 'react-native';

import { useTheme } from '../theme/ThemeProvider';

/** A tab's scrolling body under its large title. Empty until the screens are built. */
export function Screen({ children, testID }: { children?: ReactNode; testID?: string }) {
  const { colors } = useTheme();
  return (
    <ScrollView
      testID={testID}
      contentInsetAdjustmentBehavior="automatic"
      style={{ flex: 1, backgroundColor: colors.bg }}
      contentContainerStyle={{ padding: 16, gap: 16 }}
    >
      {children}
    </ScrollView>
  );
}

import type { ReactNode } from 'react';
import { View, type ViewStyle } from 'react-native';

import { useTheme } from '../theme/ThemeProvider';

/** A surface card, or the warning card (Back off): warning-tint, no border. */
export function Card({ children, tone = 'surface', style, testID }: {
  children: ReactNode; tone?: 'surface' | 'warning'; style?: ViewStyle; testID?: string;
}) {
  const { colors } = useTheme();
  return (
    <View testID={testID} style={[{
      backgroundColor: tone === 'warning' ? colors.warningTint : colors.surface,
      borderColor: tone === 'warning' ? colors.warningTint : colors.hairline,
      borderWidth: 1, borderRadius: 12, padding: 14, gap: 8,
    }, style]}>
      {children}
    </View>
  );
}

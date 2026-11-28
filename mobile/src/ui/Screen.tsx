import type { ReactNode } from 'react';
import { ScrollView } from 'react-native';

import { useTheme } from '../theme/ThemeProvider';

/**
 * Content keeps a readable width on a wide window (a tablet, a foldable, Android in landscape):
 * one centred column up to 680 pt, the phone layout unchanged below that (milestone 12).
 */
export const MAX_CONTENT_WIDTH = 680;
export const READABLE = { width: '100%', maxWidth: MAX_CONTENT_WIDTH, alignSelf: 'center' } as const;

/** A tab's scrolling body under its large title. */
export function Screen({ children, testID }: { children?: ReactNode; testID?: string }) {
  const { colors } = useTheme();
  return (
    <ScrollView
      testID={testID}
      contentInsetAdjustmentBehavior="automatic"
      style={{ flex: 1, backgroundColor: colors.bg }}
      contentContainerStyle={{ ...READABLE, padding: 16, gap: 16 }}
    >
      {children}
    </ScrollView>
  );
}

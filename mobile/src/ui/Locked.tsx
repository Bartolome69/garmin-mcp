// A Plus feature for a Free runner (build prompt, section 1): visible but locked,
// never hidden. The preview is faded and hidden from screen readers (React Native
// has no blur without another native module; DECISIONS, milestone 4), with a lock,
// the words, and a control that opens the paywall. Opening it is always the
// runner's own tap: nothing opens the paywall by itself, or mid-flow.
import type { ReactNode } from 'react';
import { Pressable, View } from 'react-native';

import { useTheme } from '../theme/ThemeProvider';
import { MIN_TOUCH } from './Button';
import { Icon } from './Icon';
import { Text } from './Text';

export function LockedPreview({ children, caption, onPress, testID, buttonTestID = 'see-plus', label = 'See Plus' }: {
  children: ReactNode;
  /** What's locked, in words: "Trend and race-day projection are in Plus". */
  caption: string;
  onPress: () => void;
  testID: string;
  buttonTestID?: string;
  label?: string;
}) {
  const { colors } = useTheme();
  return (
    <View testID={testID} style={{ borderRadius: 12, overflow: 'hidden' }}>
      <View importantForAccessibility="no-hide-descendants" accessibilityElementsHidden style={{ opacity: 0.3 }} pointerEvents="none">
        {children}
      </View>
      <View style={{ position: 'absolute', top: 0, left: 0, right: 0, bottom: 0, alignItems: 'center', justifyContent: 'center', gap: 8, padding: 16 }}>
        <Pressable testID={buttonTestID} accessibilityRole="button" accessibilityLabel={`${label}. ${caption}`} onPress={onPress}
          style={{ flexDirection: 'row', alignItems: 'center', gap: 6, minHeight: MIN_TOUCH, paddingHorizontal: 16, borderRadius: 999, backgroundColor: colors.button }}>
          <Icon name="lock" />
          <Text variant="subhead" style={{ fontWeight: '600' }}>{label}</Text>
        </Pressable>
        <Text variant="footnote" tone="text2" style={{ textAlign: 'center' }}>{caption}</Text>
      </View>
    </View>
  );
}

/** A row for a Plus feature: locked for Free (lock, words, "Plus"), plain once unlocked. */
export function PlusRow({ title, sub, plus, onPress, testID }: { title: string; sub: string; plus: boolean; onPress: () => void; testID: string }) {
  const { colors } = useTheme();
  return (
    <Pressable testID={testID} accessibilityRole="button" onPress={onPress}
      accessibilityLabel={plus ? `${title}. ${sub}` : `${title}, Plus. ${sub}`}
      accessibilityHint={plus ? undefined : 'Opens Daash Plus'}
      style={({ pressed }) => ({ flexDirection: 'row', alignItems: 'center', gap: 10, padding: 14, minHeight: MIN_TOUCH, borderRadius: 12,
        borderWidth: 1, borderColor: colors.hairline, backgroundColor: pressed ? colors.sunken : 'transparent' })}>
      {plus ? null : <Icon name="lock" tone="muted" />}
      <View style={{ flex: 1 }}>
        <Text variant="body">{title}</Text>
        <Text variant="footnote" tone="muted">{sub}</Text>
      </View>
      {plus ? <Icon name="chevronRight" tone="muted" /> : <Text testID={`${testID}-plus`} variant="footnote" tone="muted" style={{ fontWeight: '600' }}>Plus</Text>}
    </Pressable>
  );
}

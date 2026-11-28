// Grouped rows, as in You and Connections: a surface with hairlines between rows, a
// kicker above it marked as a header. A row that opens another screen has a chevron
// (build prompt, section 3: chevrons only on rows that open another screen).
import type { ReactNode } from 'react';
import { Pressable, Switch, View } from 'react-native';

import { useTheme } from '../theme/ThemeProvider';
import { Icon } from './Icon';
import { Text } from './Text';

export function SectionTitle({ children, testID }: { children: string; testID?: string }) {
  return <Text testID={testID} variant="kicker" tone="muted" accessibilityRole="header" style={{ marginTop: 6 }}>{children}</Text>;
}

export function ListGroup({ children, testID }: { children: ReactNode; testID?: string }) {
  const { colors } = useTheme();
  const items = (Array.isArray(children) ? children.flat() : [children]).filter(Boolean);
  return (
    <View testID={testID} style={{ borderRadius: 12, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.hairline, overflow: 'hidden' }}>
      {items.map((c, i) => <View key={i} style={{ borderTopWidth: i ? 1 : 0, borderTopColor: colors.hairline }}>{c}</View>)}
    </View>
  );
}

/**
 * A switch's colours: the off track muted (at least 3:1 on the surface it sits on; the
 * button grey was 1.7:1, and in dark mode the off thumb matched the row), the on track
 * success, the thumb the surface (3:1 on both tracks). tokens.source.test.ts checks the pairs.
 */
export const switchColors = (colors: { muted: string; success: string; surface: string }) => ({
  trackColor: { false: colors.muted, true: colors.success },
  ios_backgroundColor: colors.muted,
  thumbColor: colors.surface,
});

/**
 * A row with a switch. The whole row is the control (role switch, its name and on/off
 * state, at least 56 tall): the native switch alone is 31 pt tall on iOS. The switch inside
 * still toggles when touched, and is hidden from screen readers (the row says it).
 */
export function SwitchRow({ title, sub, label, value, onChange, testID, disabled = false, minHeight = 56 }: {
  title: string; sub?: string;
  /** What a screen reader says, when not the title and the line under it. */
  label?: string;
  value: boolean; onChange: (on: boolean) => void; testID: string; disabled?: boolean; minHeight?: number;
}) {
  const { colors } = useTheme();
  return (
    <Pressable testID={`${testID}-row`} onPress={() => onChange(!value)} disabled={disabled}
      accessibilityRole="switch" accessibilityLabel={label ?? (sub ? `${title}. ${sub}` : title)}
      accessibilityState={{ checked: value, disabled }}
      style={{ flexDirection: 'row', alignItems: 'center', gap: 10, minHeight, paddingVertical: 10, paddingHorizontal: 14 }}>
      <View style={{ flex: 1, minWidth: 0 }}>
        <Text variant="body">{title}</Text>
        {sub ? <Text variant="footnote" tone="muted">{sub}</Text> : null}
      </View>
      <View importantForAccessibility="no-hide-descendants" accessibilityElementsHidden>
        <Switch testID={testID} value={value} onValueChange={onChange} disabled={disabled} {...switchColors(colors)} />
      </View>
    </Pressable>
  );
}

/** One row: a title, an optional line under it, something on the right; pressable when it does something. */
export function ListRow({ title, sub, value, right, onPress, chevron = false, testID }: {
  title: string; sub?: string; value?: string; right?: ReactNode; onPress?: () => void; chevron?: boolean; testID?: string;
}) {
  const { colors } = useTheme();
  const body = (
    <View style={{ flexDirection: 'row', alignItems: 'center', gap: 10, minHeight: 56, paddingVertical: 10, paddingHorizontal: 14 }}>
      <View style={{ flex: 1, minWidth: 0 }}>
        <Text variant="body">{title}</Text>
        {sub ? <Text variant="footnote" tone="muted">{sub}</Text> : null}
      </View>
      {value ? <Text variant="body" tone="text2" numeric style={{ flexShrink: 1, textAlign: 'right' }}>{value}</Text> : null}
      {right}
      {chevron ? <Icon name="chevronRight" tone="muted" /> : null}
    </View>
  );
  const label = [title, value, sub].filter(Boolean).join(', ');
  return onPress
    ? <Pressable testID={testID} accessibilityRole="button" accessibilityLabel={label} onPress={onPress}
        style={({ pressed }) => ({ backgroundColor: pressed ? colors.sunken : 'transparent' })}>{body}</Pressable>
    : <View testID={testID} accessible={!right} accessibilityLabel={right ? undefined : label}>{body}</View>;
}

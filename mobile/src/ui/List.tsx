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

/** A row with a switch: the switch carries the row's name and its on/off state. */
export function SwitchRow({ title, sub, value, onChange, testID, disabled = false }: {
  title: string; sub?: string; value: boolean; onChange: (on: boolean) => void; testID: string; disabled?: boolean;
}) {
  const { colors } = useTheme();
  return (
    <View style={{ flexDirection: 'row', alignItems: 'center', gap: 10, minHeight: 56, paddingVertical: 10, paddingHorizontal: 14 }}>
      <View style={{ flex: 1, minWidth: 0 }} importantForAccessibility="no-hide-descendants" accessibilityElementsHidden>
        <Text variant="body">{title}</Text>
        {sub ? <Text variant="footnote" tone="muted">{sub}</Text> : null}
      </View>
      <Switch testID={testID} value={value} onValueChange={onChange} disabled={disabled}
        accessibilityLabel={sub ? `${title}. ${sub}` : title} accessibilityRole="switch"
        trackColor={{ false: colors.button, true: colors.success }} thumbColor={colors.surface} />
    </View>
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

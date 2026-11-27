// Week / Block / Trends (build prompt, section 3): the standard segmented control
// on iOS, Material 3 primary tabs on Android (full width under the app bar, an
// underline in the text colour on the selected one).
import NativeSegmented, { type SegmentedControlProps } from '@react-native-segmented-control/segmented-control';
import type { ComponentType } from 'react';
import { Platform, Pressable, View } from 'react-native';

import { useTheme } from '../../theme/ThemeProvider';
import { Text } from '../../ui/Text';

// The package's typings predate React 19's JSX types; the component itself is fine.
const SegmentedControl = NativeSegmented as unknown as ComponentType<SegmentedControlProps>;

export const PLAN_TABS = ['Week', 'Block', 'Trends'] as const;
export type PlanTab = (typeof PLAN_TABS)[number];

export function PlanTabs({ value, onChange, os = Platform.OS }: { value: PlanTab; onChange: (t: PlanTab) => void; os?: string }) {
  const { colors, scheme } = useTheme();
  const index = PLAN_TABS.indexOf(value);
  if (os === 'ios') {
    return (
      <View style={{ paddingHorizontal: 16, paddingTop: 8 }}>
        <SegmentedControl
          testID="plan-tabs"
          values={[...PLAN_TABS]}
          selectedIndex={index}
          appearance={scheme === 'light' ? 'light' : 'dark'}
          onValueChange={(v) => onChange(v as PlanTab)}
          accessibilityLabel="Plan view"
        />
      </View>
    );
  }
  return (
    <View testID="plan-tabs" accessibilityRole="tablist"
      style={{ flexDirection: 'row', borderBottomWidth: 1, borderBottomColor: colors.hairline }}>
      {PLAN_TABS.map((t) => {
        const on = t === value;
        return (
          <Pressable key={t} testID={`plan-tab-${t}`} accessibilityRole="tab" accessibilityState={{ selected: on }}
            onPress={() => onChange(t)} style={{ flex: 1, minHeight: 48, alignItems: 'center', justifyContent: 'center' }}>
            <Text variant="subhead" tone={on ? 'text' : 'muted'} style={{ fontWeight: '500' }}>{t}</Text>
            {on ? <View style={{ position: 'absolute', bottom: 0, left: '28%', right: '28%', height: 3,
              borderTopLeftRadius: 3, borderTopRightRadius: 3, backgroundColor: colors.text }} /> : null}
          </Pressable>
        );
      })}
    </View>
  );
}

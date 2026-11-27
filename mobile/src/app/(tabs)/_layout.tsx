// Today · Plan · (Coach) · Crew · You (build prompt, section 4). Native tab bars:
// UITabBarController on iOS (only the selected tab bright, with its filled
// symbol), a Material 3 navigation bar on Android. With coach_enabled off the
// Coach tab is hidden, which also makes it unreachable, and the bar reflows to 4.
import { NativeTabs } from 'expo-router/native-tabs';

import { tabsFor, type TabName } from '../../flags/flags';
import { useFlags } from '../../flags/FlagsProvider';
import { ICONS } from '../../ui/Icon';
import { useTheme } from '../../theme/ThemeProvider';

const TAB_LABELS: Record<TabName, string> = {
  today: 'Today', plan: 'Plan', coach: 'Coach', crew: 'Crew', you: 'You',
};
const ALL_TABS: TabName[] = ['today', 'plan', 'coach', 'crew', 'you'];

export default function TabsLayout() {
  const { colors } = useTheme();
  const { flags } = useFlags();
  const shown = new Set(tabsFor(flags));
  return (
    <NativeTabs
      backgroundColor={colors.bg}
      tintColor={colors.text}
      iconColor={{ default: colors.muted, selected: colors.text }}
      labelStyle={{ default: { color: colors.muted }, selected: { color: colors.text } }}
      indicatorColor={colors.sunken}
      rippleColor={colors.sunken}
    >
      {ALL_TABS.map((name) => {
        const icon = ICONS[name];
        return (
          <NativeTabs.Trigger key={name} name={name} hidden={!shown.has(name)}>
            <NativeTabs.Trigger.Icon sf={{ default: icon.ios, selected: icon.iosSelected }} md={icon.android} />
            <NativeTabs.Trigger.Label>{TAB_LABELS[name]}</NativeTabs.Trigger.Label>
          </NativeTabs.Trigger>
        );
      })}
    </NativeTabs>
  );
}

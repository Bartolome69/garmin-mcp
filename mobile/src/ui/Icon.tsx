// SF Symbols on iOS, Material Symbols on Android, by one semantic name, at the
// standard sizes (build prompt, section 2): 17 pt in rows, 22 pt toolbar, 25 pt
// tab bar on iOS; 24 dp everywhere on Android. Every icon that means something
// carries an accessibility label.
import { SymbolView, type AndroidSymbol } from 'expo-symbols';
import { Platform } from 'react-native';
import type { SFSymbol } from 'sf-symbols-typescript';

import { useTheme } from '../theme/ThemeProvider';
import type { colors } from '../theme/tokens';

export const ICONS = {
  today: { ios: 'sun.max', iosSelected: 'sun.max.fill', android: 'today' },
  plan: { ios: 'list.bullet.rectangle.portrait', iosSelected: 'list.bullet.rectangle.portrait.fill', android: 'calendar_month' },
  coach: { ios: 'bubble.left', iosSelected: 'bubble.left.fill', android: 'forum' },
  crew: { ios: 'person.2', iosSelected: 'person.2.fill', android: 'group' },
  you: { ios: 'person.crop.circle', iosSelected: 'person.crop.circle.fill', android: 'person' },
  done: { ios: 'checkmark.circle.fill', android: 'check_circle' },
  onWatch: { ios: 'watch.analog', android: 'watch' },
  chevronRight: { ios: 'chevron.right', android: 'chevron_right' },
  chevronLeft: { ios: 'chevron.left', android: 'chevron_left' },
  add: { ios: 'plus', android: 'add' },
  more: { ios: 'ellipsis', android: 'more_horiz' },
  send: { ios: 'arrow.up', android: 'send' },
  lock: { ios: 'lock', android: 'lock' },
  error: { ios: 'exclamationmark.circle', android: 'error' },
  sync: { ios: 'arrow.triangle.2.circlepath', android: 'sync' },
  race: { ios: 'flag', android: 'flag' },
  edit: { ios: 'pencil', android: 'edit' },
  close: { ios: 'xmark', android: 'close' },
} as const satisfies Record<string, { ios: SFSymbol; iosSelected?: SFSymbol; android: AndroidSymbol }>;

export type IconName = keyof typeof ICONS;
export type IconContext = 'row' | 'toolbar' | 'tab';

export function iconSize(context: IconContext, os: string = Platform.OS): number {
  if (os === 'android') return 24;
  return context === 'tab' ? 25 : context === 'toolbar' ? 22 : 17;
}

type Tone = keyof (typeof colors)['dark'];

export function Icon({ name, context = 'row', tone = 'text', label, selected = false }: {
  name: IconName;
  context?: IconContext;
  tone?: Exclude<Tone, 'band'>;
  /** Spoken by VoiceOver and TalkBack. Leave out only for decoration beside words that say the same. */
  label?: string;
  selected?: boolean;
}) {
  const { colors } = useTheme();
  const icon: { ios: SFSymbol; iosSelected?: SFSymbol; android: AndroidSymbol } = ICONS[name];
  return (
    <SymbolView
      name={{ ios: selected && icon.iosSelected ? icon.iosSelected : icon.ios, android: icon.android }}
      size={iconSize(context)}
      tintColor={colors[tone]}
      accessible={!!label}
      accessibilityLabel={label}
      importantForAccessibility={label ? 'yes' : 'no-hide-descendants'}
    />
  );
}

// Buttons (build prompt, sections 2 and 3): primary is the one brand fill a screen
// may have; secondary is a 1px muted outline (at least 3:1). Both are at least
// 44 pt / 48 dp tall. A screen behind an open sheet passes brand={false}.
import { Platform, Pressable, type PressableProps, type ViewStyle } from 'react-native';

import { useTheme } from '../theme/ThemeProvider';
import { Icon, type IconName } from './Icon';
import { Text } from './Text';

export const MIN_TOUCH = Platform.OS === 'android' ? 48 : 44;

export function Button({ label, kind = 'secondary', brand = true, full = false, small = false, icon, style, ...rest }: Omit<PressableProps, 'style'> & {
  label: string;
  kind?: 'primary' | 'secondary';
  /** false while a sheet is open over this screen: the primary loses its brand fill. */
  brand?: boolean;
  full?: boolean;
  /** Shorter sideways padding and a smaller label, for a row of choices; still a full-height target. */
  small?: boolean;
  /** A symbol before the words, as decoration: the words say what it does. */
  icon?: IconName;
  style?: ViewStyle;
}) {
  const { colors } = useTheme();
  // A primary that can't be pressed doesn't look like one: no brand fill, dimmed.
  const off = !!rest.disabled;
  const filled = kind === 'primary' && brand && !off;
  return (
    <Pressable
      accessibilityRole="button"
      testID={`button-${label}`}
      style={({ pressed }) => [{
        minHeight: MIN_TOUCH, paddingHorizontal: small ? 14 : 18, borderRadius: 999, alignItems: 'center', justifyContent: 'center',
        flexDirection: 'row', gap: 6,
        borderWidth: 1, borderColor: filled ? colors.brand : colors.muted,
        backgroundColor: filled ? colors.brand : 'transparent',
        opacity: off ? 0.4 : pressed ? 0.75 : 1, flexGrow: full ? 1 : 0, flexBasis: full ? 0 : 'auto',
      }, style]}
      {...rest}
    >
      {icon ? <Icon name={icon} tone={filled ? 'onBrand' : 'text'} /> : null}
      <Text variant={small ? 'subhead' : 'headline'} tone={filled ? 'onBrand' : 'text'}
        style={{ textAlign: 'center', fontWeight: '600' }}>{label}</Text>
    </Pressable>
  );
}

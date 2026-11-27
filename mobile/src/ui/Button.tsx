// Two buttons (build prompt, sections 2 and 3): primary is the one brand fill a
// screen may have; secondary is a 1px muted outline (at least 3:1). Both are at
// least 44 pt / 48 dp tall. A screen behind an open sheet passes brand={false}.
import { Platform, Pressable, type PressableProps, type ViewStyle } from 'react-native';

import { useTheme } from '../theme/ThemeProvider';
import { Text } from './Text';

export const MIN_TOUCH = Platform.OS === 'android' ? 48 : 44;

export function Button({ label, kind = 'secondary', brand = true, full = false, style, ...rest }: Omit<PressableProps, 'style'> & {
  label: string;
  kind?: 'primary' | 'secondary';
  /** false while a sheet is open over this screen: the primary loses its brand fill. */
  brand?: boolean;
  full?: boolean;
  style?: ViewStyle;
}) {
  const { colors } = useTheme();
  const filled = kind === 'primary' && brand;
  return (
    <Pressable
      accessibilityRole="button"
      testID={`button-${label}`}
      style={({ pressed }) => [{
        minHeight: MIN_TOUCH, paddingHorizontal: 18, borderRadius: 999, alignItems: 'center', justifyContent: 'center',
        borderWidth: 1, borderColor: filled ? colors.brand : colors.muted,
        backgroundColor: filled ? colors.brand : 'transparent',
        opacity: pressed ? 0.75 : 1, flexGrow: full ? 1 : 0, flexBasis: full ? 0 : 'auto',
      }, style]}
      {...rest}
    >
      <Text variant="headline" tone={filled ? 'onBrand' : 'text'} style={{ textAlign: 'center' }}>{label}</Text>
    </Pressable>
  );
}

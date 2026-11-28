// Android's extended floating action button (Material 3): 56 dp, 16 dp corners,
// an icon and its words. It's the screen's one brand fill, so it loses the fill
// when a sheet is open over the screen (brand={false}).
import { Pressable } from 'react-native';

import { useTheme } from '../theme/ThemeProvider';
import { Icon, type IconName } from './Icon';
import { Text } from './Text';

export function Fab({ label, icon, onPress, brand = true, testID }: {
  label: string; icon: IconName; onPress: () => void; brand?: boolean; testID?: string;
}) {
  const { colors } = useTheme();
  return (
    <Pressable
      testID={testID ?? `fab-${label}`}
      accessibilityRole="button"
      accessibilityLabel={label}
      onPress={onPress}
      style={({ pressed }) => ({
        position: 'absolute', right: 16, bottom: 16, minHeight: 56, borderRadius: 16, paddingLeft: 16, paddingRight: 20,
        flexDirection: 'row', alignItems: 'center', gap: 10, elevation: 6, opacity: pressed ? 0.85 : 1,
        backgroundColor: brand ? colors.brand : colors.sunken,
      })}
    >
      <Icon name={icon} tone={brand ? 'onBrand' : 'text'} />
      <Text variant="subhead" tone={brand ? 'onBrand' : 'text'} style={{ fontWeight: '600' }}>{label}</Text>
    </Pressable>
  );
}

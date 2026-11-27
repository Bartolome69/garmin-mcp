import { View } from 'react-native';

import { useTheme } from '../../theme/ThemeProvider';
import { Text } from '../Text';
import type { SessionType } from '../../features/today/model';
import { TYPE_TOKEN } from './types';

/** Type colour plus its word: never the colour alone. */
export function TypeChip({ type, label }: { type: SessionType; label: string }) {
  const { colors } = useTheme();
  const rest = type === 'rest';
  return (
    <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6, alignSelf: 'flex-start', minHeight: 24,
      paddingLeft: 7, paddingRight: 9, borderRadius: 7, backgroundColor: colors.sunken }}>
      <View style={{ width: 9, height: 9, borderRadius: 3, backgroundColor: rest ? 'transparent' : colors[TYPE_TOKEN[type]],
        borderWidth: rest ? 1.5 : 0, borderColor: colors.cRest }} />
      <Text variant="caption" style={{ fontWeight: '600' }}>{label}</Text>
    </View>
  );
}

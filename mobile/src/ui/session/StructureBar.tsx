import { View } from 'react-native';

import { useTheme } from '../../theme/ThemeProvider';
import type { SessionType, StructurePart } from '../../features/today/model';
import { Text } from '../Text';
import { TYPE_TOKEN } from './types';

/** Warm-up, reps, recoveries and cool-down as a bar, with words under it for screen readers and everyone else. */
export function StructureBar({ parts, type, legend, label }: {
  parts: StructurePart[]; type: SessionType; legend: string[];
  /** What a screen reader says when there are no words under the bar. */
  label?: string;
}) {
  const { colors } = useTheme();
  return (
    <View style={{ gap: 6 }}>
      <View accessible accessibilityRole="image" accessibilityLabel={label ?? (legend.length ? `Session structure: ${legend.join(', ')}` : 'Session structure')}
        style={{ flexDirection: 'row', gap: 3, height: 10, borderRadius: 5, overflow: 'hidden' }}>
        {parts.map((p, i) => (
          <View key={i} style={{ flex: p.weight, borderRadius: 2, opacity: p.part === 'rec' ? 0.8 : 1,
            backgroundColor: p.part === 'work' ? colors[TYPE_TOKEN[type]] : p.part === 'easy' ? colors.cEasy : colors.button }} />
        ))}
      </View>
      <View style={{ flexDirection: 'row', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
        {legend.map((l) => <Text key={l} variant="caption" tone="muted" numeric>{l}</Text>)}
      </View>
    </View>
  );
}

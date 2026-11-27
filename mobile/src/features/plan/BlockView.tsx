// Plan · Block: the whole plan to race day. The phase bar is neutral (build prompt,
// section 5.13): Base, Build, Peak and Taper as steps of the text colour's
// opacity with their names, never session colours; this week stands taller.
import { View } from 'react-native';

import { useTheme } from '../../theme/ThemeProvider';
import { Button } from '../../ui/Button';
import { Card } from '../../ui/Card';
import { Text } from '../../ui/Text';
import type { BlockVM } from './model';

/** Text-colour opacity per phase, lightest first. */
export const PHASE_OPACITY = [0.3, 0.5, 0.75, 1];

export function phaseBarLabel(b: BlockVM): string {
  const phases = b.phases.map((p) => `${p.name}, weeks ${p.from} to ${p.to}`).join('; ');
  return `${b.totalWeeks} weeks: ${phases}. You're in week ${b.currentWeek}.`;
}

export function BlockView({ block, onChange }: { block: BlockVM | null; onChange: () => void }) {
  const { colors } = useTheme();
  if (!block) {
    return (
      <View testID="block" style={{ padding: 16 }}>
        <Card><Text variant="body" tone="muted">No plan yet. A plan shows its weeks to race day here.</Text></Card>
      </View>
    );
  }
  const phaseOf = (w: number) => Math.max(0, block.phases.findIndex((p) => w >= p.from && w <= p.to));
  return (
    <View testID="block" style={{ padding: 16, gap: 12 }}>
      <Card>
        <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'baseline', gap: 8, flexWrap: 'wrap' }}>
          <Text variant="title" numeric>{block.title}</Text>
          <Text variant="footnote" tone="muted">{block.raceDate}</Text>
        </View>
        <View testID="phase-bar" accessible accessibilityRole="image" accessibilityLabel={phaseBarLabel(block)}
          style={{ flexDirection: 'row', gap: 2, alignItems: 'flex-end', height: 16, marginTop: 4 }}>
          {Array.from({ length: block.totalWeeks }, (_, i) => {
            const w = i + 1;
            const now = w === block.currentWeek;
            return <View key={w} style={{ flex: 1, height: now ? 16 : 10, borderRadius: 2, backgroundColor: colors.text,
              opacity: now ? 1 : PHASE_OPACITY[phaseOf(w) % PHASE_OPACITY.length] }} />;
          })}
        </View>
        <View style={{ flexDirection: 'row', gap: 2 }} importantForAccessibility="no-hide-descendants" accessibilityElementsHidden>
          {block.phases.map((p) => (
            <View key={p.name} style={{ flex: p.to - p.from + 1, minWidth: 0 }}>
              {/* A short phase has room for its name only; the Phases card has every count. */}
              <Text variant="kicker" tone="muted" numberOfLines={1} maxFontSizeMultiplier={1.5}>
                {p.to - p.from + 1 >= 6 ? `${p.name} · ${p.to - p.from + 1}` : p.name}
              </Text>
            </View>
          ))}
        </View>
        <View style={{ flexDirection: 'row', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
          <Text variant="footnote" tone="muted" numeric>{block.now}</Text>
          <Text variant="footnote" tone="muted" numeric>{block.peak}</Text>
        </View>
      </Card>
      <Card>
        <Text variant="headline" accessibilityRole="header">Phases</Text>
        {block.phases.map((p, i) => {
          const now = block.currentWeek >= p.from && block.currentWeek <= p.to;
          return (
            <View key={p.name} testID={`phase-${p.name}`} accessible
              accessibilityLabel={`${p.name}, weeks ${p.from} to ${p.to}${now ? ', now' : `, from ${p.starts}`}. ${p.focus}`}
              style={{ flexDirection: 'row', gap: 10, paddingVertical: 8, borderTopWidth: i ? 1 : 0, borderTopColor: colors.hairline }}>
              <View style={{ width: 6, borderRadius: 3, backgroundColor: colors.text, opacity: PHASE_OPACITY[i % PHASE_OPACITY.length] }} />
              <View style={{ flex: 1, gap: 2 }}>
                <Text variant="subhead" style={{ fontWeight: '600' }} numeric>{`${p.name} · weeks ${p.from} to ${p.to}`}</Text>
                <Text variant="footnote" tone="muted">{p.focus}</Text>
              </View>
              <Text variant="footnote" tone={now ? 'text' : 'muted'} style={{ fontWeight: now ? '600' : '400' }}>{now ? 'now' : p.starts}</Text>
            </View>
          );
        })}
      </Card>
      <Button label="Change the plan" onPress={onChange} />
    </View>
  );
}

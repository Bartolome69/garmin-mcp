// This week (build prompt, section 5.1): seven days, each a type-coloured square
// (filled with a tick when done, outlined when planned, dashed for rest) and a
// word. Today's date is outlined in brand-text on Today, which already has its
// one brand fill. Rest days count: no "Missed!".
import { Pressable, View } from 'react-native';

import { useTheme } from '../../theme/ThemeProvider';
import { MIN_TOUCH } from '../../ui/Button';
import { Card } from '../../ui/Card';
import { Icon } from '../../ui/Icon';
import { TYPE_TOKEN } from '../../ui/session/types';
import { Text } from '../../ui/Text';
import type { WeekDay } from './model';

function Day({ d, onRun }: { d: WeekDay; onRun?: (id: string) => void }) {
  const { colors } = useTheme();
  const colour = colors[TYPE_TOKEN[d.type]];
  const rest = d.status === 'rest' || d.status === 'rest_done';
  const done = d.status === 'done' || d.status === 'rest_done';
  const state = rest ? 'rest day' : done ? 'done' : 'planned';
  // A done day opens its run (post-run); the others are information only.
  const run = done && !rest && d.runId && onRun ? d.runId : null;
  const Box = run ? Pressable : View;
  return (
    <Box accessible accessibilityLabel={`${d.label}, ${d.day}, ${state}${d.today ? ', today' : ''}`} testID={`day-${d.date}`}
      {...(run ? { accessibilityRole: 'button' as const, accessibilityHint: 'Opens the run', onPress: () => onRun!(run) } : {})}
      style={{ flex: 1, minHeight: MIN_TOUCH, alignItems: 'center', gap: 5, paddingVertical: 6, borderRadius: 10,
        backgroundColor: d.today ? colors.sunken : 'transparent' }}>
      <Text variant="caption" tone="muted" maxFontSizeMultiplier={1.5}>{d.letter}</Text>
      <View style={{ minWidth: 26, paddingHorizontal: 2, borderRadius: 13, borderWidth: 1.5, borderColor: d.today ? colors.brandText : 'transparent' }}>
        <Text variant="subhead" tone={d.today ? 'brandText' : 'text'} numeric maxFontSizeMultiplier={1.5}
          style={{ fontWeight: '600', textAlign: 'center' }}>{String(d.day)}</Text>
      </View>
      <View style={{ width: 24, height: 24, borderRadius: 7, alignItems: 'center', justifyContent: 'center',
        backgroundColor: done && !rest ? colour : 'transparent',
        borderWidth: done && !rest ? 0 : rest ? 1.5 : 2, borderStyle: rest ? 'dashed' : 'solid', borderColor: colour }}>
        {done ? <Icon name="check" tone={rest ? 'cRest' : 'bg'} /> : null}
      </View>
      <Text variant="caption" tone="muted" maxFontSizeMultiplier={1.5} numberOfLines={1}>{d.label}</Text>
    </Box>
  );
}

export function WeekStrip({ week, onRun }: { week: { doneKm: number; plannedKm: number; activeWeeks: number; days: WeekDay[] }; onRun?: (id: string) => void }) {
  return (
    <Card testID="week" style={{ paddingVertical: 12 }}>
      <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'baseline', gap: 8, flexWrap: 'wrap' }}>
        <Text variant="headline">This week</Text>
        <Text variant="footnote" tone="muted" numeric>
          <Text variant="footnote" numeric style={{ fontWeight: '700' }}>{String(week.doneKm)}</Text>
          {` of ${week.plannedKm} km · ${week.activeWeeks} active weeks`}
        </Text>
      </View>
      <View style={{ flexDirection: 'row', gap: 4 }}>
        {week.days.map((d) => <Day key={d.date} d={d} onRun={onRun} />)}
      </View>
    </Card>
  );
}

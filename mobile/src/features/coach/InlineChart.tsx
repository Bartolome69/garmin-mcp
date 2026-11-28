// A chart inside a Coach reply (build prompt, section 5.10). The app draws it from the
// numbers the server sent with the reply, never from the reply's words: a line (a
// signal by night, with the runner's own range shaded) or bars (one per rep). Marked
// points are said in the summary as well, so colour is never the only cue; a screen
// reader hears the summary.
import { View } from 'react-native';
import Svg, { Circle, Polyline, Rect, Text as SvgText } from 'react-native-svg';

import { useTheme } from '../../theme/ThemeProvider';
import { Text } from '../../ui/Text';
import type { InlineChart } from './model';

const W = 260;
const H = 72;
const TOP = 8;
const BASE = 56;

export function InlineChartView({ chart, testID }: { chart: InlineChart; testID?: string }) {
  const { colors } = useTheme();
  const vals = chart.values.filter((v): v is number => v != null);
  const lo = Math.min(...vals, ...(chart.band ?? []));
  const hi = Math.max(...vals, ...(chart.band ?? []));
  const pad = (hi - lo) * 0.15 || 1;
  const min = chart.kind === 'bars' ? Math.max(0, lo - pad * 2) : lo - pad;
  const max = hi + pad;
  const y = (v: number) => BASE - ((v - min) / (max - min)) * (BASE - TOP);
  const n = chart.values.length;
  const x = (i: number) => (n === 1 ? W / 2 : 8 + (i * (W - 16)) / (n - 1));
  const slot = W / Math.max(1, n);
  const marked = new Set(chart.highlight);
  return (
    <View testID={testID} accessible accessibilityRole="image" accessibilityLabel={chart.summary}
      style={{ marginTop: 8, padding: 10, paddingBottom: 6, borderRadius: 12, backgroundColor: colors.sunken, gap: 4 }}>
      <View style={{ flexDirection: 'row', justifyContent: 'space-between', gap: 8 }}>
        <Text variant="caption" tone="muted" style={{ flexShrink: 1 }}>{chart.title}</Text>
        {chart.caption ? <Text variant="caption" tone="muted" numeric>{chart.caption}</Text> : null}
      </View>
      <Svg width="100%" height={H} viewBox={`0 0 ${W} ${H}`}>
        {chart.band ? <Rect x={0} y={y(chart.band[1])} width={W} height={y(chart.band[0]) - y(chart.band[1])} fill={colors.band} /> : null}
        {chart.kind === 'line' ? (
          <>
            <Polyline points={chart.values.map((v, i) => (v == null ? null : `${x(i)},${y(v)}`)).filter(Boolean).join(' ')}
              fill="none" stroke={colors.text} strokeWidth={2} />
            {chart.values.map((v, i) => (v != null && marked.has(i)
              ? <Circle key={i} cx={x(i)} cy={y(v)} r={3.5} fill={colors.warning} stroke={colors.text} strokeWidth={1} /> : null))}
          </>
        ) : chart.values.map((v, i) => (v == null ? null : (
          <Rect key={i} x={i * slot + slot * 0.2} y={y(v)} width={slot * 0.6} height={BASE - y(v)} rx={2}
            fill={marked.has(i) ? colors.text : colors.button} />
        )))}
        {chart.labels.map((l, i) => (
          <SvgText key={i} x={chart.kind === 'bars' ? i * slot + slot / 2 : x(i)} y={H - 2} textAnchor="middle" fill={colors.muted} fontSize={9}>
            {chart.kind === 'bars' ? `${chart.values[i] ?? ''}` : l}
          </SvgText>
        ))}
      </Svg>
    </View>
  );
}

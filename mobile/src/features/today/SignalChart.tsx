// A signal's last 14 nights against the person's own range (shaded band) or their
// usual (a line). Charts carry a text summary for screen readers.
import { View } from 'react-native';
import Svg, { Circle, Line, Polyline, Rect } from 'react-native-svg';

import { useTheme } from '../../theme/ThemeProvider';
import { Text } from '../../ui/Text';
import type { Signal } from './model';

const W = 320;
const H = 90;
const PAD = 8;

export function SignalChart({ signal }: { signal: Signal }) {
  const { colors } = useTheme();
  const { series, band, line, rangeLabel, lastLabel } = signal.sheet;
  const known = series.filter((v): v is number => v != null);
  const all = [...known, ...(band ?? []), ...(line != null ? [line] : [])];
  if (!known.length) {
    return <Text variant="footnote" tone="muted">No nights recorded yet.</Text>;
  }
  const lo = Math.min(...all), hi = Math.max(...all);
  const span = hi - lo || 1;
  const y = (v: number) => PAD + (H - 2 * PAD) * (1 - (v - lo) / span);
  const x = (i: number) => (series.length === 1 ? W / 2 : (W - PAD) * (i / (series.length - 1)));
  const points = series.map((v, i) => (v == null ? null : `${x(i).toFixed(1)},${y(v).toFixed(1)}`)).filter(Boolean).join(' ');
  const lastIndex = series.length - 1 - [...series].reverse().findIndex((v) => v != null);
  const last = series[lastIndex] as number;
  const lastTone = signal.status === 'low' ? colors.warning : colors.text;
  return (
    <View accessible accessibilityRole="image" accessibilityLabel={`${signal.label} over the last ${series.length} nights. ${rangeLabel}. ${lastLabel}.`}
      style={{ gap: 4 }}>
      <Text variant="caption" tone="muted" numeric>{rangeLabel}</Text>
      <Svg width="100%" height={H} viewBox={`0 0 ${W} ${H}`}>
        {band ? <Rect x={0} y={y(band[1])} width={W} height={Math.max(1, y(band[0]) - y(band[1]))} fill={colors.band} /> : null}
        {line != null ? <Line x1={0} x2={W} y1={y(line)} y2={y(line)} stroke={colors.muted} strokeDasharray="4 4" strokeWidth={1} /> : null}
        <Polyline points={points} fill="none" stroke={colors.text} strokeWidth={2} />
        <Circle cx={x(lastIndex)} cy={y(last)} r={4} fill={lastTone} />
      </Svg>
      <Text variant="caption" tone={signal.status === 'low' ? 'warning' : 'muted'} numeric style={{ alignSelf: 'flex-end' }}>{lastLabel}</Text>
    </View>
  );
}

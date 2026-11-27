// Plan · Trends (build prompt, section 5.5): training load with done weeks solid,
// the next planned weeks dashed and the target band shaded, any week over the band
// in warning with "Trim week N"; the predicted race time (free); and the prediction
// trend with the race-day projection, which is Plus: Free sees a locked preview
// drawn from no one's data, never the person's own numbers behind a blur.
import { Pressable, View } from 'react-native';
import Svg, { Line, Polyline, Rect, Text as SvgText } from 'react-native-svg';

import { mmss } from '../../domain/format';
import { useTheme } from '../../theme/ThemeProvider';
import { MIN_TOUCH } from '../../ui/Button';
import { Card } from '../../ui/Card';
import { Icon } from '../../ui/Icon';
import { Text } from '../../ui/Text';
import type { LoadWeek, TrendsVM } from './model';

const W = 320;
const H = 140;
const TOP = 18;
const BASE = 120;

export function loadSummary(weeks: LoadWeek[]): string {
  const done = weeks.filter((w) => w.state === 'done').length;
  const planned = weeks.filter((w) => w.state === 'planned').length;
  const over = weeks.filter((w) => w.over && w.number != null).map((w) => `Week ${w.number}`);
  return `Weekly training load against your target band: ${done} weeks done, this week, and ${planned} planned.`
    + (over.length ? ` ${over.join(' and ')} ${over.length > 1 ? 'are' : 'is'} above the band.` : ' Every week is within the band or below it.');
}

function LoadChart({ weeks }: { weeks: LoadWeek[] }) {
  const { colors } = useTheme();
  const max = Math.max(...weeks.map((w) => Math.max(w.load, w.high ?? 0)), 1) * 1.05;
  const y = (v: number) => BASE - (v / max) * (BASE - TOP);
  const slot = W / weeks.length;
  const bar = Math.min(22, slot - 8);
  const split = weeks.findIndex((w) => w.state !== 'done');
  return (
    <Svg testID="load-chart" width="100%" height={H} viewBox={`0 0 ${W} ${H}`} accessible accessibilityRole="image" accessibilityLabel={loadSummary(weeks)}>
      {weeks.map((w, i) => (w.low != null && w.high != null
        ? <Rect key={`b${i}`} x={i * slot} y={y(w.high)} width={slot} height={y(w.low) - y(w.high)} fill={colors.band} />
        : null))}
      {weeks.map((w, i) => {
        const x = i * slot + (slot - bar) / 2;
        const top = y(w.load);
        const solid = w.state !== 'planned' || w.over;
        return (
          <Rect key={`w${i}`} testID={`load-${w.starts}`} x={x} y={top} width={bar} height={BASE - top} rx={3}
            fill={w.over ? colors.warning : solid ? colors.text : 'none'}
            stroke={solid ? 'none' : colors.text} strokeWidth={1.5} strokeDasharray={solid ? undefined : '3 3'} />
        );
      })}
      {split > 0 ? (
        <>
          <Line x1={split * slot - 1} y1={4} x2={split * slot - 1} y2={BASE + 2} stroke={colors.muted} strokeDasharray="2 3" />
          <SvgText x={split * slot - 5} y={12} textAnchor="end" fill={colors.muted} fontSize={10}>Done</SvgText>
          <SvgText x={split * slot + 3} y={12} fill={colors.muted} fontSize={10}>Planned</SvgText>
        </>
      ) : null}
      {weeks.map((w, i) => (w.over && w.number != null
        ? <SvgText key={`l${i}`} x={i * slot + slot / 2} y={BASE + 14} textAnchor="middle" fill={colors.warning} fontSize={10}>{`Wk ${w.number}`}</SvgText>
        : null))}
    </Svg>
  );
}

function TrendChart({ points, target, start, projection, label }: {
  points: number[]; target: number | null; start: string; projection: string; label: string;
}) {
  const { colors } = useTheme();
  const all = [...points, ...(target != null ? [target] : [])];
  const lo = Math.min(...all) - 120, hi = Math.max(...all) + 60;
  const y = (s: number) => 8 + ((s - lo) / (hi - lo)) * 64;   // slower is lower on the chart
  const step = 240 / Math.max(points.length - 1, 1);
  const line = points.map((p, i) => `${i * step},${y(p)}`).join(' ');
  const last = points[points.length - 1];
  const ahead = target != null ? `${240},${y(last)} ${280},${(y(last) + y(target)) / 2} ${320},${y(target) + 6}` : '';
  return (
    <Svg width="100%" height={90} viewBox="0 0 320 90" accessible accessibilityRole="image" accessibilityLabel={label}>
      {target != null ? <Line x1={0} y1={y(target)} x2={320} y2={y(target)} stroke={colors.muted} strokeDasharray="3 3" /> : null}
      <Polyline points={line} fill="none" stroke={colors.text} strokeWidth={2.4} />
      {ahead ? <Polyline points={ahead} fill="none" stroke={colors.text} strokeWidth={2} strokeDasharray="4 4" /> : null}
      {target != null ? <SvgText x={4} y={y(target) - 6} fill={colors.muted} fontSize={10}>{`Target ${mmss(target).replace(/:00$/, '')}`}</SvgText> : null}
      <SvgText x={0} y={88} fill={colors.muted} fontSize={10}>{start}</SvgText>
      <SvgText x={316} y={88} textAnchor="end" fill={colors.muted} fontSize={10}>{projection}</SvgText>
    </Svg>
  );
}

// The locked preview's shape: a generic line, not the person's predictions.
const PREVIEW = [12000, 11940, 11880, 11800, 11760, 11700, 11640];

export function TrendsView({ vm, plus, onTrim, onPlus }: { vm: TrendsVM; plus: boolean; onTrim: () => void; onPlus: () => void }) {
  const { colors } = useTheme();
  const over = vm.load.weeks.find((w) => w.trim);
  return (
    <View testID="trends" style={{ padding: 16, gap: 12 }}>
      <Card>
        <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'baseline', gap: 8, flexWrap: 'wrap' }}>
          <Text variant="headline" accessibilityRole="header">Training load</Text>
          <Text variant="footnote" tone="muted">{vm.load.caption}</Text>
        </View>
        <LoadChart weeks={vm.load.weeks} />
        {vm.load.warning ? (
          <View testID="load-warning" style={{ flexDirection: 'row', gap: 10, padding: 12, borderRadius: 12, backgroundColor: colors.warningTint }}>
            <Icon name="warning" tone="warning" />
            <View style={{ flex: 1, gap: 4 }}>
              <Text variant="subhead">{vm.load.warning}</Text>
              {over?.trim ? (
                <Pressable testID="trim" accessibilityRole="link" onPress={onTrim} hitSlop={12} style={{ alignSelf: 'flex-start', minHeight: 24 }}>
                  <Text variant="subhead" tone="brandText" style={{ fontWeight: '600', textDecorationLine: 'underline' }}>{over.trim}</Text>
                </Pressable>
              ) : null}
            </View>
          </View>
        ) : null}
      </Card>

      {vm.prediction ? (
        <Card testID="prediction">
          <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'baseline', gap: 8, flexWrap: 'wrap' }}>
            <Text variant="headline" accessibilityRole="header">{`Predicted ${vm.prediction.race} time`}</Text>
            <Text variant="footnote" tone="muted">{vm.prediction.basis}</Text>
          </View>
          <View style={{ flexDirection: 'row', gap: 40, flexWrap: 'wrap' }}>
            <View accessible accessibilityLabel={`Today ${vm.prediction.today}`}>
              <Text variant="largeTitle" numeric>{vm.prediction.today}</Text>
              <Text variant="footnote" tone="muted">Today</Text>
            </View>
            {vm.prediction.target ? (
              <View accessible accessibilityLabel={`Your target ${vm.prediction.target}`}>
                <Text variant="largeTitle" numeric>{vm.prediction.target}</Text>
                <Text variant="footnote" tone="muted">Your target</Text>
              </View>
            ) : null}
          </View>
        </Card>
      ) : null}

      {plus && vm.trend ? (
        <Card testID="trend">
          <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'baseline', gap: 8 }}>
            <Text variant="headline" accessibilityRole="header">Prediction trend</Text>
            <Text variant="footnote" tone="muted">12 weeks</Text>
          </View>
          <TrendChart points={vm.trend.points.map((p) => p.seconds)} target={vm.trend.target} start={vm.trend.start} projection={vm.trend.projection}
            label={`Predicted time over 12 weeks, from ${vm.trend.start.replace(' · ', ' ')} to ${mmss(vm.trend.points[vm.trend.points.length - 1].seconds)} now. ${vm.trend.projection}.`} />
        </Card>
      ) : !plus ? (
        <View testID="trend-locked" style={{ borderRadius: 12, overflow: 'hidden' }}>
          <View importantForAccessibility="no-hide-descendants" accessibilityElementsHidden style={{ opacity: 0.3 }}>
            <Card>
              <Text variant="headline">Prediction trend</Text>
              <TrendChart points={PREVIEW} target={null} start="" projection="" label="" />
            </Card>
          </View>
          <View style={{ position: 'absolute', top: 0, left: 0, right: 0, bottom: 0, alignItems: 'center', justifyContent: 'center', gap: 8, padding: 16 }}>
            <Pressable testID="see-plus" accessibilityRole="button" accessibilityLabel="See Plus. Trend and race-day projection are in Plus" onPress={onPlus}
              style={{ flexDirection: 'row', alignItems: 'center', gap: 6, minHeight: MIN_TOUCH, paddingHorizontal: 16, borderRadius: 999, backgroundColor: colors.button }}>
              <Icon name="lock" />
              <Text variant="subhead" style={{ fontWeight: '600' }}>See Plus</Text>
            </Pressable>
            <Text variant="footnote" tone="text2" style={{ textAlign: 'center' }}>Trend and race-day projection are in Plus</Text>
          </View>
        </View>
      ) : null}
    </View>
  );
}

// Post-run (build prompt, section 5.9), in the prompt's order: route map, (Coach on:
// a collapsible debrief with thumbs up / down and "Ask about this run"), title and
// type chip, planned vs actual, stacked Pace and HR charts with axes, units and one
// scrub line shared by every metric (Cadence and Elevation as further tabs), one
// rule-based takeaway under each chart, and the HR zones card (a text-opacity ramp,
// labelled). Every number is the server's (model.ts); this file only draws it.
import { useState } from 'react';
import { Pressable, ScrollView, View, type GestureResponderEvent, type LayoutChangeEvent } from 'react-native';
import Svg, { Circle, G, Line, Path, Polyline, Rect, Text as SvgText } from 'react-native-svg';

import { mmss } from '../../domain/format';
import { useTheme } from '../../theme/ThemeProvider';
import { MIN_TOUCH } from '../../ui/Button';
import { FONT_FILES } from '../../theme/typography';
import { Card } from '../../ui/Card';
import { Icon } from '../../ui/Icon';
import { TypeChip } from '../../ui/session/TypeChip';
import { Text } from '../../ui/Text';
import { lapAt, pointAt, type Chart, type Metric, type RunVM, type Takeaway } from './model';

// -- route --------------------------------------------------------------------------------

const MAP_W = 350, MAP_H = 110, MAP_PAD = 14;

export function RouteMap({ route, km }: { route: NonNullable<RunVM['route']>; km: number }) {
  const { colors } = useTheme();
  const ex = route.aspect >= 1 ? 1 : route.aspect;
  const ey = route.aspect >= 1 ? 1 / route.aspect : 1;
  const s = Math.min((MAP_W - 2 * MAP_PAD) / ex, (MAP_H - 2 * MAP_PAD) / ey);
  const ox = (MAP_W - ex * s) / 2, oy = (MAP_H - ey * s) / 2;
  const pts = route.points.map(([x, y]) => [ox + x * s, oy + y * s] as const);
  const d = pts.map(([x, y], i) => `${i ? 'L' : 'M'}${x.toFixed(1)} ${y.toFixed(1)}`).join(' ');
  const [sx, sy] = pts[0], [ex2, ey2] = pts[pts.length - 1];
  return (
    <View testID="route-map" accessible accessibilityRole="image" accessibilityLabel={`Route map, ${km} km. Hollow dot: start. Solid dot: finish.`}
      style={{ height: MAP_H, borderRadius: 12, backgroundColor: colors.sunken, overflow: 'hidden' }}>
      <Svg width="100%" height="100%" viewBox={`0 0 ${MAP_W} ${MAP_H}`}>
        <Path d={d} fill="none" stroke={colors.text} strokeWidth={3.5} strokeLinecap="round" strokeLinejoin="round" />
        <Circle cx={sx} cy={sy} r={5} fill={colors.sunken} stroke={colors.text} strokeWidth={2} />
        <Circle cx={ex2} cy={ey2} r={5} fill={colors.text} />
      </Svg>
    </View>
  );
}

// -- Coach debrief ------------------------------------------------------------------------

export function Debrief({ text, onAsk }: { text: string; onAsk: () => void }) {
  const { colors } = useTheme();
  const [open, setOpen] = useState(true);
  const [rated, setRated] = useState<'up' | 'down' | null>(null);
  const thumb = (which: 'up' | 'down') => {
    const on = rated === which;
    return (
      <Pressable testID={`thumb-${which}`} accessibilityRole="button" accessibilityLabel={which === 'up' ? 'Helpful' : 'Not helpful'}
        accessibilityState={{ selected: on }} onPress={() => setRated(on ? null : which)} hitSlop={(MIN_TOUCH - 34) / 2}
        style={{ width: 34, height: 34, borderRadius: 17, borderWidth: 1, borderColor: on ? colors.text : colors.hairline,
          backgroundColor: on ? colors.sunken : 'transparent', alignItems: 'center', justifyContent: 'center' }}>
        <Icon name={which === 'up' ? 'thumbUp' : 'thumbDown'} tone={on ? 'text' : 'muted'} />
      </Pressable>
    );
  };
  return (
    <View testID="debrief" style={{ gap: 8, padding: 14, paddingHorizontal: 16, borderRadius: 16, backgroundColor: colors.surface,
      borderWidth: 1, borderColor: colors.hairline }}>
      <Pressable testID="debrief-toggle" accessibilityRole="button" accessibilityLabel="Coach debrief" accessibilityState={{ expanded: open }}
        onPress={() => setOpen(!open)} style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', minHeight: MIN_TOUCH }}>
        <Text variant="kicker" tone="muted">Coach debrief</Text>
        <Icon name={open ? 'chevronUp' : 'chevronDown'} tone="muted" />
      </Pressable>
      {open ? (
        <>
          <Text testID="debrief-text" variant="coach">{text}</Text>
          <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
            <View style={{ flexDirection: 'row', gap: 6 }}>{thumb('up')}{thumb('down')}</View>
            <Pressable testID="ask-run" accessibilityRole="button" onPress={onAsk} style={{ minHeight: MIN_TOUCH, justifyContent: 'center' }}>
              <View style={{ minHeight: 32, paddingHorizontal: 12, borderRadius: 999, justifyContent: 'center', borderWidth: 1, borderColor: colors.muted }}>
                <Text variant="subhead" style={{ fontWeight: '600' }}>Ask about this run</Text>
              </View>
            </Pressable>
          </View>
        </>
      ) : null}
    </View>
  );
}

// -- planned vs actual --------------------------------------------------------------------

export function PlannedCard({ rows }: { rows: NonNullable<RunVM['planned']> }) {
  const { colors } = useTheme();
  const cell = { flex: 1, paddingVertical: 8 } as const;
  return (
    <Card testID="planned" style={{ paddingVertical: 8, paddingHorizontal: 14, gap: 0 }}>
      <View style={{ flexDirection: 'row' }} importantForAccessibility="no-hide-descendants" accessibilityElementsHidden>
        <Text variant="kicker" tone="muted" style={{ flex: 1.6, paddingVertical: 6 }}>Planned vs actual</Text>
        <Text variant="kicker" tone="muted" style={{ ...cell, paddingVertical: 6 }}>Plan</Text>
        <Text variant="kicker" tone="muted" style={{ ...cell, paddingVertical: 6 }}>Done</Text>
      </View>
      {rows.map((r) => (
        <View key={r.label} testID={`planned-${r.label}`} accessible
          accessibilityLabel={`${r.label}, plan ${r.plan.replace('–', ' to ')}, done ${r.done}, ${r.ok ? 'on plan' : 'off plan'}`}
          style={{ flexDirection: 'row', alignItems: 'center', borderTopWidth: 1, borderTopColor: colors.hairline }}>
          <Text variant="subhead" style={{ flex: 1.6, paddingVertical: 8 }}>{r.label}</Text>
          <Text variant="subhead" tone="text2" style={cell} numeric>{r.plan}</Text>
          <View style={{ ...cell, flexDirection: 'row', alignItems: 'center', gap: 4 }}>
            <Icon name={r.ok ? 'check' : 'warning'} tone={r.ok ? 'success' : 'warning'} />
            <Text variant="subhead" tone={r.ok ? 'success' : 'warning'} numeric style={{ flexShrink: 1 }}>{r.done}</Text>
          </View>
        </View>
      ))}
    </Card>
  );
}

// -- charts -------------------------------------------------------------------------------

const W = 320, H = 112, LEFT = 38, RIGHT = 316, TOP = 8, BOTTOM = 92;
export const TABS = [{ key: 'paceHr', label: 'Pace + HR' }, { key: 'cadence', label: 'Cadence' }, { key: 'elevation', label: 'Elev' }] as const;
export type ChartTab = (typeof TABS)[number]['key'];
const METRICS: Record<ChartTab, Metric[]> = { paceHr: ['pace', 'hr'], cadence: ['cadence'], elevation: ['elevation'] };

export function valueWords(metric: Metric, v: number | null): string {
  if (v == null) return 'no reading';
  if (metric === 'pace') return `${mmss(v)} /km`;
  if (metric === 'hr') return `${Math.round(v)} bpm`;
  if (metric === 'cadence') return `${Math.round(v)} spm`;
  return `${Math.round(v)} m`;
}

const kmWords = (km: number) => `${+km.toFixed(1)}`;

/** What the scrub line reads at a distance: "Rep 4 · 4:00 /km". */
export function scrubWords(vm: RunVM, km: number, metric: Metric): string {
  const i = pointAt(vm, km);
  const lap = lapAt(vm, vm.series.km[i]);
  const where = lap && lap.kind !== 'run' ? lap.label : `${kmWords(vm.series.km[i])} km`;
  return `${where} · ${valueWords(metric, vm.series[metric][i])}`;
}

function TakeawayRow({ t, metric }: { t: Takeaway; metric: Metric }) {
  return (
    <View testID={`takeaway-${metric}`} accessible accessibilityLabel={t.text} style={{ flexDirection: 'row', gap: 8, alignItems: 'center' }}>
      <Icon name={t.tone === 'good' ? 'check' : 'info'} tone={t.tone === 'good' ? 'success' : 'muted'} />
      <Text variant="footnote" style={{ flex: 1 }} numeric>
        <Text variant="footnote" style={{ fontWeight: '600' }}>{t.bold}</Text>{t.rest ? ` ${t.rest}` : ''}
      </Text>
    </View>
  );
}

function ChartSvg({ vm, metric, chart, km }: { vm: RunVM; metric: Metric; chart: Chart; km: number }) {
  const { colors } = useTheme();
  const dom = chart.domain!;
  const total = vm.totalKm || 1;
  const x = (k: number) => LEFT + (k / total) * (RIGHT - LEFT);
  const y = (v: number) => {
    const f = (v - dom.min) / (dom.max - dom.min || 1);
    return chart.inverted ? TOP + f * (BOTTOM - TOP) : BOTTOM - f * (BOTTOM - TOP);
  };
  // A gap in the readings breaks the line rather than joining across it.
  const runs: string[][] = [[]];
  vm.series[metric].forEach((v, i) => {
    if (v == null) { if (runs[runs.length - 1].length) runs.push([]); return; }
    runs[runs.length - 1].push(`${x(vm.series.km[i]).toFixed(1)},${y(v).toFixed(1)}`);
  });
  const i = pointAt(vm, km);
  const v = vm.series[metric][i];
  const sx = x(vm.series.km[i]);
  const label = scrubWords(vm, km, metric);
  const lw = label.length * 6.2 + 12;   // the mono face: about 6.2 units a character at 10.5
  const lx = sx + 6 + lw > W ? sx - 6 - lw : sx + 6;
  return (
    <Svg testID={`chart-${metric}`} width="100%" height={H} viewBox={`0 0 ${W} ${H}`} accessible={false}>
      <Line x1={LEFT} y1={TOP} x2={LEFT} y2={BOTTOM} stroke={colors.hairline} />
      <Line x1={LEFT} y1={BOTTOM} x2={RIGHT} y2={BOTTOM} stroke={colors.hairline} />
      {dom.ticks.map((t) => (
        <G key={t.label}>
          <Line x1={LEFT} y1={y(t.value)} x2={RIGHT} y2={y(t.value)} stroke={colors.hairline} strokeDasharray="2 3" />
          <SvgText x={LEFT - 4} y={y(t.value) + 3} textAnchor="end" fill={colors.muted} fontSize={10} fontFamily={FONT_FILES.mono}>{t.label}</SvgText>
        </G>
      ))}
      {runs.filter((r) => r.length > 1).map((r, k) => (
        <Polyline key={k} points={r.join(' ')} fill="none" stroke={colors.text} strokeWidth={2} strokeLinejoin="round" />
      ))}
      <Line testID={`scrub-${metric}`} x1={sx} y1={TOP} x2={sx} y2={BOTTOM} stroke={colors.text} strokeWidth={1.2} />
      {v != null ? <Circle cx={sx} cy={y(v)} r={4} fill={colors.text} /> : null}
      <Rect x={lx} y={TOP - 4} width={lw} height={18} rx={5} fill={colors.sunken} />
      <SvgText testID={`scrub-label-${metric}`} x={lx + 6} y={TOP + 9} fill={colors.text} fontSize={10.5} fontFamily={FONT_FILES.mono}>{label}</SvgText>
      <SvgText x={LEFT + 2} y={H - 6} fill={colors.muted} fontSize={10} fontFamily={FONT_FILES.mono}>0</SvgText>
      <SvgText x={(LEFT + RIGHT) / 2} y={H - 6} textAnchor="middle" fill={colors.muted} fontSize={10} fontFamily={FONT_FILES.mono}>{kmWords(total / 2)}</SvgText>
      <SvgText x={RIGHT} y={H - 6} textAnchor="end" fill={colors.muted} fontSize={10} fontFamily={FONT_FILES.mono}>{`${kmWords(total)} km`}</SvgText>
    </Svg>
  );
}

/** Next or previous lap's middle (or a kilometre, with no laps), for the adjustable charts. */
export function stepScrub(vm: RunVM, km: number, dir: 1 | -1): number {
  if (vm.laps.length) {
    const mids = vm.laps.map((l) => (l.fromKm + l.toKm) / 2);
    const next = dir > 0 ? mids.find((m) => m > km + 1e-6) : [...mids].reverse().find((m) => m < km - 1e-6);
    return next ?? km;
  }
  return Math.min(vm.totalKm, Math.max(0, km + dir));
}

export function Charts({ vm, km, onScrub }: { vm: RunVM; km: number; onScrub: (km: number) => void }) {
  const { colors } = useTheme();
  const [tab, setTab] = useState<ChartTab>('paceHr');
  const [width, setWidth] = useState(0);
  const metrics = METRICS[tab];
  const toKm = (e: GestureResponderEvent) => {
    if (!width) return;
    const vx = (e.nativeEvent.locationX / width) * W;
    onScrub(Math.min(vm.totalKm, Math.max(0, ((vx - LEFT) / (RIGHT - LEFT)) * vm.totalKm)));
  };
  return (
    <Card testID="charts" style={{ gap: 6, paddingVertical: 12, paddingHorizontal: 14 }}>
      <View accessibilityRole="tablist" style={{ flexDirection: 'row', gap: 4, padding: 3, borderRadius: 999, backgroundColor: colors.sunken }}>
        {TABS.map((t) => {
          const on = t.key === tab;
          return (
            <Pressable key={t.key} testID={`metric-${t.key}`} accessibilityRole="tab" accessibilityState={{ selected: on }}
              onPress={() => setTab(t.key)} style={{ flex: 1, minHeight: MIN_TOUCH, justifyContent: 'center' }}>
              <View style={{ minHeight: 32, paddingVertical: 4, borderRadius: 999, alignItems: 'center', justifyContent: 'center', backgroundColor: on ? colors.text : 'transparent' }}>
                <Text variant="footnote" tone={on ? 'bg' : 'muted'} style={{ fontWeight: '600' }}>{t.label}</Text>
              </View>
            </Pressable>
          );
        })}
      </View>
      <View testID="scrub-area" onLayout={(e: LayoutChangeEvent) => setWidth(e.nativeEvent.layout.width)}
        onStartShouldSetResponder={() => true} onMoveShouldSetResponder={() => true}
        onResponderGrant={toKm} onResponderMove={toKm} onResponderTerminationRequest={() => true}
        accessible accessibilityRole="adjustable"
        accessibilityLabel={metrics.map((m) => vm.charts[m].summary).join('. ')}
        accessibilityValue={{ text: metrics.map((m) => scrubWords(vm, km, m)).join(', ') }}
        accessibilityActions={[{ name: 'increment' }, { name: 'decrement' }]}
        onAccessibilityAction={(e) => onScrub(stepScrub(vm, km, e.nativeEvent.actionName === 'increment' ? 1 : -1))}
        style={{ gap: 6 }}>
        {metrics.map((m) => {
          const c = vm.charts[m];
          return (
            <View key={m} style={{ gap: 4 }}>
              <View style={{ flexDirection: 'row', justifyContent: 'space-between' }}>
                <Text variant="caption" tone="muted">{`${c.label} · ${c.unit}`}</Text>
                {c.inverted ? <Text variant="caption" tone="muted">faster ↑</Text> : null}
              </View>
              {c.domain ? <ChartSvg vm={vm} metric={m} chart={c} km={km} /> : <Text variant="footnote" tone="muted">{c.summary}</Text>}
              {c.takeaway ? <TakeawayRow t={c.takeaway} metric={m} /> : null}
            </View>
          );
        })}
      </View>
    </Card>
  );
}

// -- zones --------------------------------------------------------------------------------

const RAMP = [0.2, 0.35, 0.55, 0.75, 1];

export function ZonesCard({ zones }: { zones: NonNullable<RunVM['zones']> }) {
  const { colors } = useTheme();
  return (
    <Card testID="zones" style={{ gap: 8, paddingVertical: 12, paddingHorizontal: 14 }}>
      <Text variant="headline">Heart rate zones</Text>
      <View style={{ gap: 6 }}>
        {zones.map((z, i) => (
          <View key={z.label} accessible accessibilityLabel={`Zone ${i + 1}, ${z.minutes} minutes`}
            style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
            <Text variant="caption" tone="muted" style={{ width: 28 }}>{z.label}</Text>
            <View style={{ flex: 1, height: 10, borderRadius: 5, backgroundColor: colors.sunken, overflow: 'hidden' }}>
              <View style={{ width: `${Math.round(z.share * 100)}%`, height: '100%', borderRadius: 5, backgroundColor: colors.text, opacity: RAMP[i] ?? 1 }} />
            </View>
            <Text variant="caption" style={{ width: 54, textAlign: 'right' }} numeric>{`${z.minutes} min`}</Text>
          </View>
        ))}
      </View>
    </Card>
  );
}

// -- the screen ---------------------------------------------------------------------------

export function RunView({ vm, coach, onAsk }: { vm: RunVM; coach: boolean; onAsk: () => void }) {
  const { colors } = useTheme();
  const [km, setKm] = useState(vm.scrubKm);
  return (
    <ScrollView testID="run" contentInsetAdjustmentBehavior="automatic" style={{ flex: 1, backgroundColor: colors.bg }}
      contentContainerStyle={{ padding: 16, paddingTop: 4, gap: 10 }}>
      {vm.route ? <RouteMap route={vm.route} km={vm.totalKm} /> : null}
      {coach && vm.coach?.debrief ? <Debrief text={vm.coach.debrief} onAsk={onAsk} /> : null}
      <View style={{ flexDirection: 'row', alignItems: 'flex-end', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
        <View style={{ gap: 4, flexShrink: 1 }}>
          <TypeChip type={vm.type} label={vm.typeLabel} />
          <Text variant="title" accessibilityRole="header" numeric>{vm.title}</Text>
        </View>
        <Text variant="footnote" tone="muted" numeric>{vm.when}</Text>
      </View>
      {vm.planned ? <PlannedCard rows={vm.planned} /> : null}
      <Charts vm={vm} km={km} onScrub={setKm} />
      {vm.zones ? <ZonesCard zones={vm.zones} /> : null}
    </ScrollView>
  );
}

/** What Share sends: the run in words, every number the screen's. */
export function shareText(vm: RunVM): string {
  const takes = (['pace', 'hr'] as Metric[]).map((m) => vm.charts[m].takeaway?.text).filter(Boolean);
  return [`${vm.title} · ${vm.when}`, `${vm.totalKm} km in ${vm.duration}`, ...takes].join('\n');
}

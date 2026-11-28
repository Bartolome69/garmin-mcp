// The shared diff (build prompt, section 5.4): one screen for every proposed
// change. Day by day, before struck through and after with its type rail; the
// summary; kilometres for this week and the next two, before as a muted outline
// and after as a solid bar, value labels only on weeks that change, unchanged
// weeks identical; the rule used, and with the Coach on one Coach observation
// added (the prompt: "Coach ON adds an observation card"), so no number goes. "Review watch changes" opens the watch confirm; nothing is sent
// from here.
import { Pressable, ScrollView, View } from 'react-native';
import Svg, { G, Rect, Text as SvgText } from 'react-native-svg';

import { useTheme } from '../../theme/ThemeProvider';
import { Button, MIN_TOUCH } from '../../ui/Button';
import { Card } from '../../ui/Card';
import { TYPE_TOKEN } from '../../ui/session/types';
import { Text } from '../../ui/Text';
import type { DiffDay, DiffSession, LoadPair, Proposal } from './model';

function Rail({ s }: { s: DiffSession | null }) {
  const { colors } = useTheme();
  const rest = !s || s.type === 'rest';
  return <View style={{ width: 4, height: 16, borderRadius: 2, backgroundColor: rest ? 'transparent' : colors[TYPE_TOKEN[s!.type]],
    borderWidth: rest ? 1.5 : 0, borderStyle: 'dashed', borderColor: colors.cRest }} />;
}

const words = (s: DiffSession | null) => (s ? s.title : 'Rest');

function Row({ d, first }: { d: DiffDay; first: boolean }) {
  const { colors } = useTheme();
  const label = d.changed
    ? `${d.label}: ${words(d.before)}, becomes ${words(d.after)}${d.after ? `, ${d.after.sub}` : ''}`
    : `${d.label}: ${words(d.after)}, unchanged`;
  return (
    <View testID={`diff-${d.date}`} accessible accessibilityLabel={label}
      style={{ flexDirection: 'row', alignItems: 'center', gap: 10, minHeight: 40, paddingVertical: 6, opacity: d.changed ? 1 : 0.55,
        borderTopWidth: first ? 0 : 1, borderTopColor: colors.hairline }}>
      <Text variant="kicker" tone="muted" style={{ width: 52 }} maxFontSizeMultiplier={1.5}>{d.label}</Text>
      <Text variant="subhead" tone="muted" numberOfLines={2}
        style={{ flex: 1, textDecorationLine: d.changed ? 'line-through' : 'none' }}>{words(d.before)}</Text>
      <View style={{ flex: 1, flexDirection: 'row', alignItems: 'center', gap: 8 }}>
        <Rail s={d.after} />
        <Text variant="subhead" style={{ flex: 1, fontWeight: '600' }} numberOfLines={2}>{words(d.after)}</Text>
      </View>
    </View>
  );
}

export function loadWords(load: LoadPair[]): string {
  return 'Kilometres a week. ' + load.map((l) => (l.changed
    ? `${l.label}: ${l.before} before, ${l.after} after.`
    : `${l.label}: unchanged at ${l.after}.`)).join(' ');
}

function LoadChart({ load }: { load: LoadPair[] }) {
  const { colors } = useTheme();
  const W = 320, H = 130, BASE = 100, TOP = 18;
  const max = Math.max(...load.flatMap((l) => [l.before, l.after, l.high ?? 0]), 1) * 1.05;
  const y = (v: number) => BASE - (v / max) * (BASE - TOP);
  const slot = W / load.length;
  const bar = 22;
  const band = load.find((l) => l.low != null && l.high != null) ?? null;
  return (
    <Svg testID="diff-load" width="100%" height={H} viewBox={`0 0 ${W} ${H}`} accessible accessibilityRole="image" accessibilityLabel={loadWords(load)}>
      {band ? <Rect x={0} y={y(band.high!)} width={W} height={y(band.low!) - y(band.high!)} fill={colors.band} /> : null}
      {load.map((l, i) => {
        const cx = i * slot + slot / 2;
        return (
          <G key={l.label}>
            <Rect testID={`diff-before-${i}`} x={cx - bar - 3} y={y(l.before)} width={bar} height={BASE - y(l.before)} rx={3}
              fill="none" stroke={colors.muted} strokeWidth={1.5} />
            <Rect testID={`diff-after-${i}`} x={cx + 3} y={y(l.after)} width={bar} height={BASE - y(l.after)} rx={3} fill={colors.text} />
            {l.changed ? (
              <>
                <SvgText x={cx - bar / 2 - 3} y={y(l.before) - 4} textAnchor="middle" fill={colors.muted} fontSize={10.5} fontWeight="600">{`${l.before}`}</SvgText>
                <SvgText x={cx + bar / 2 + 3} y={y(l.after) - 4} textAnchor="middle" fill={colors.text} fontSize={10.5} fontWeight="600">{`${l.after} km`}</SvgText>
              </>
            ) : null}
            <SvgText x={cx} y={BASE + 16} textAnchor="middle" fill={colors.muted} fontSize={10}>
              {l.changed ? l.label : `${i === 1 && l.label === 'Next week' ? 'Next' : l.label} · unchanged`}
            </SvgText>
          </G>
        );
      })}
    </Svg>
  );
}

function Legend() {
  const { colors } = useTheme();
  const dot = (filled: boolean) => <View style={{ width: 10, height: 10, borderRadius: 2, borderWidth: 1.5,
    borderColor: filled ? colors.text : colors.muted, backgroundColor: filled ? colors.text : 'transparent' }} />;
  return (
    <View style={{ flexDirection: 'row', gap: 14, flexWrap: 'wrap', alignItems: 'center' }} importantForAccessibility="no-hide-descendants" accessibilityElementsHidden>
      <View style={{ flexDirection: 'row', gap: 6, alignItems: 'center' }}>{dot(false)}<Text variant="caption" tone="muted">Before</Text></View>
      <View style={{ flexDirection: 'row', gap: 6, alignItems: 'center' }}>{dot(true)}<Text variant="caption" tone="muted">After</Text></View>
      <Text variant="caption" tone="muted">Shaded: your target band</Text>
    </View>
  );
}

export function DiffView({ p, coach, brand = true, onReview, onBack, onAsk }: {
  p: Proposal;
  coach: boolean;
  /** false while the watch confirm is open over it: the button loses its brand fill. */
  brand?: boolean;
  onReview: () => void;
  onBack: () => void;
  onAsk: () => void;
}) {
  const { colors } = useTheme();
  const nothing = !p.ops.length;
  return (
    <ScrollView testID="diff" contentInsetAdjustmentBehavior="automatic" style={{ flex: 1, backgroundColor: colors.bg }}
      contentContainerStyle={{ padding: 16, gap: 14 }}>
      <View style={{ gap: 4 }}>
        {p.kicker ? <Text variant="kicker" tone="muted" testID="diff-kicker">{p.kicker}</Text> : null}
        <Text variant="title" accessibilityRole="header" testID="diff-title">{p.title}</Text>
        <Text variant="subhead" tone="muted" numeric testID="diff-subtitle">{p.subtitle}</Text>
      </View>

      {p.days.length ? (
        <Card testID="diff-days" style={{ gap: 0, paddingVertical: 8 }}>
          <View style={{ flexDirection: 'row', gap: 10, paddingBottom: 4 }} importantForAccessibility="no-hide-descendants" accessibilityElementsHidden>
            <Text variant="kicker" tone="muted" style={{ width: 52 }}>Day</Text>
            <Text variant="kicker" tone="muted" style={{ flex: 1 }}>Before</Text>
            <Text variant="kicker" tone="muted" style={{ flex: 1, paddingLeft: 12 }}>After</Text>
          </View>
          {p.days.map((d, i) => <Row key={d.date} d={d} first={i === 0} />)}
        </Card>
      ) : null}

      {p.load ? (
        <Card style={{ gap: 6 }}>
          <Text variant="kicker" accessibilityRole="header" tone="muted">Kilometres</Text>
          <LoadChart load={p.load} />
          <Legend />
        </Card>
      ) : null}

      {p.rule ? (
        <View testID="diff-rule" style={{ gap: 2 }}>
          <Text variant="kicker" accessibilityRole="header" tone="muted">Rule used</Text>
          <Text variant="footnote" tone="muted">{`${p.rule}.`}</Text>
        </View>
      ) : null}

      {coach && p.coachNote ? (
        <View testID="diff-coach" style={{ gap: 8, padding: 14, borderRadius: 12, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.hairline }}>
          <Text variant="kicker" accessibilityRole="header" tone="muted">Coach</Text>
          <Text variant="coach">{p.coachNote}</Text>
          <Pressable accessibilityRole="button" onPress={onAsk} style={{ alignSelf: 'flex-start', minHeight: MIN_TOUCH, justifyContent: 'center' }}>
            <View style={{ paddingHorizontal: 12, minHeight: 30, borderRadius: 999, borderWidth: 1, borderColor: colors.muted, justifyContent: 'center' }}>
              <Text variant="footnote">Ask about this</Text>
            </View>
          </Pressable>
        </View>
      ) : null}

      {p.disclaimer ? <Text variant="footnote" tone="muted" testID="diff-disclaimer">{p.disclaimer}</Text> : null}

      {nothing
        ? <Button label="Back to the plan" onPress={onBack} />
        : <Button label="Review watch changes" kind="primary" brand={brand} onPress={onReview} />}
    </ScrollView>
  );
}

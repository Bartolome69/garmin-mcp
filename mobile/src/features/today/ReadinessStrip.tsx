// The compact readiness strip (build prompt, section 5.1): ring with Garmin's
// number, the decision word in its colour, three tappable signals, one primary
// button for the recommended action and a secondary "Other options". With the
// Coach on, one serif observation line and "Ask why"; nothing else changes.
import { Pressable, View } from 'react-native';

import { useTheme } from '../../theme/ThemeProvider';
import { typeStyle } from '../../theme/typography';
import { Button, MIN_TOUCH } from '../../ui/Button';
import { Card } from '../../ui/Card';
import { Text } from '../../ui/Text';
import type { Decision, Readiness, Signal, SignalKey } from './model';

const DECISION_TONE = { push: 'success', hold: 'text2', back_off: 'warning' } as const;

const FOOTNOTE_LINE = typeStyle('footnote').lineHeight ?? 18;

export function ReadinessRing({ decision, score, large }: { decision: Decision | null; score: number | null; large?: boolean }) {
  const { colors } = useTheme();
  const tone = decision ? DECISION_TONE[decision] : 'muted';
  const size = large ? 60 : 40;
  return (
    <View accessible accessibilityLabel={score != null ? `Garmin readiness ${score}` : 'Garmin readiness not available'}
      style={{ width: size, height: size, borderRadius: size / 2, borderWidth: large ? 3.5 : 2.5, borderColor: colors[tone],
        alignItems: 'center', justifyContent: 'center' }}>
      <Text variant="caption" tone={tone} numeric style={{ fontWeight: '700' }} maxFontSizeMultiplier={1.4}>
        {score != null ? String(score) : '–'}
      </Text>
    </View>
  );
}

function SignalLink({ s, onPress, row }: { s: Signal; onPress: () => void; row: boolean }) {
  const noteTone = s.status === 'low' ? 'warning' : s.status === 'usual' ? 'success' : 'muted';
  const label = `${s.label} ${s.value ?? 'missing'}, ${s.note}. Opens details`;
  return (
    <Pressable accessibilityRole="button" accessibilityLabel={label} onPress={onPress} testID={`signal-${s.key}`}
      style={{ minHeight: MIN_TOUCH, justifyContent: 'center', flex: row ? undefined : 1, paddingHorizontal: 2 }}>
      <Text variant="caption" tone="muted">{s.label}</Text>
      <Text variant="headline" numeric>{s.value ?? '–'}</Text>
      <Text variant="caption" tone={noteTone} style={{ fontWeight: '600', textDecorationLine: 'underline', textDecorationStyle: 'dotted' }}>
        {s.note}
      </Text>
    </Pressable>
  );
}

export function ReadinessStrip({ readiness, coach, brand, xl, onSignal, onRecommended, onOptions, onHow, onAskWhy }: {
  readiness: Readiness;
  coach: boolean;
  brand: boolean;
  xl: boolean;
  onSignal: (key: SignalKey) => void;
  onRecommended: () => void;
  onOptions: () => void;
  onHow: () => void;
  onAskWhy: () => void;
}) {
  const { colors } = useTheme();
  const r = readiness;
  const tone = r.decision ? DECISION_TONE[r.decision] : 'muted';
  const sub = [r.garminScore != null ? `Garmin readiness ${r.garminScore}` : null, r.summary].filter(Boolean) as string[];
  return (
    <Card tone={r.decision === 'back_off' ? 'warning' : 'surface'} testID="readiness" style={{ gap: 6 }}>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 12 }}>
        <ReadinessRing decision={r.decision} score={r.garminScore} large={xl} />
        <View style={{ flex: 1 }}>
          <Text variant="title" tone={tone} testID="readiness-word">{r.word ?? 'Not enough data yet'}</Text>
          <Text variant="footnote" tone="muted" numeric>{xl ? sub.map((p) => p.replace(/ /g, ' ')).join(' · ') : sub.join(' · ')}</Text>
        </View>
        {!xl && (
          <Pressable accessibilityRole="link" onPress={onHow} style={{ minHeight: MIN_TOUCH, alignSelf: 'flex-start' }}>
            <Text variant="footnote" tone="text2">How it works</Text>
          </Pressable>
        )}
      </View>
      <View testID="signals" style={{ flexDirection: xl ? 'column' : 'row', gap: xl ? 8 : 4 }}>
        {r.signals.map((s) => (
          xl ? (
            <View key={s.key} style={{ backgroundColor: colors.surface, borderRadius: 12, paddingHorizontal: 14, paddingVertical: 6 }}>
              <SignalLink s={s} row onPress={() => onSignal(s.key)} />
            </View>
          ) : <SignalLink key={s.key} s={s} row={false} onPress={() => onSignal(s.key)} />
        ))}
      </View>
      {coach && r.coachLine ? (
        <View style={{ gap: 2 }}>
          <Text variant="coach" tone="text2" numberOfLines={xl ? 2 : undefined} testID="coach-line">{r.coachLine}</Text>
          {/* Its own control rather than a link inside the line, so it has a full-size target. */}
          <Pressable testID="ask-why" accessibilityRole="link" onPress={onAskWhy} hitSlop={{ top: (MIN_TOUCH - FOOTNOTE_LINE) / 2, bottom: (MIN_TOUCH - FOOTNOTE_LINE) / 2 }}
            style={{ alignSelf: 'flex-start', minWidth: MIN_TOUCH }}>
            <Text variant="footnote" style={{ textDecorationLine: 'underline' }}>Ask why</Text>
          </Pressable>
        </View>
      ) : null}
      <View style={{ flexDirection: xl ? 'column' : 'row', gap: 8, marginTop: 2 }}>
        <Button label={r.recommended.label} kind="primary" brand={brand} full={!xl} onPress={onRecommended} />
        <Button label="Other options" full={!xl} onPress={onOptions} />
      </View>
      {xl && <Button label="How it works" onPress={onHow} />}
    </Card>
  );
}

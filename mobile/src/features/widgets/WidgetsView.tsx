// Widgets setup (build prompt, section 5.15; boards P1-Widgets-ios/android): the
// widgets as they'll look, drawn from the same snapshot they read, and how to add
// them. Widgets are Plus: for Free the previews are faded behind a lock.
import { Platform, ScrollView, View } from 'react-native';
import Svg, { Circle } from 'react-native-svg';

import { useTheme } from '../../theme/ThemeProvider';
import { Card } from '../../ui/Card';
import { LockedPreview } from '../../ui/Locked';
import { TYPE_TOKEN } from '../../ui/session/types';
import { Text } from '../../ui/Text';
import type { Decision } from '../today/model';
import type { WidgetSnapshot } from './snapshot';

const DECISION_TONE: Record<Decision, 'success' | 'text2' | 'warning'> = { push: 'success', hold: 'text2', back_off: 'warning' };

function Ring({ score, decision, size = 56 }: { score: number | null; decision: Decision; size?: number }) {
  const { colors } = useTheme();
  const r = size / 2 - 4;
  const c = 2 * Math.PI * r;
  const part = Math.max(0, Math.min(100, score ?? 0)) / 100;
  return (
    <View style={{ width: size, height: size, alignItems: 'center', justifyContent: 'center' }}>
      <Svg width={size} height={size} style={{ position: 'absolute' }}>
        <Circle cx={size / 2} cy={size / 2} r={r} stroke={colors.hairline} strokeWidth={5} fill="none" />
        <Circle cx={size / 2} cy={size / 2} r={r} stroke={colors[DECISION_TONE[decision]]} strokeWidth={5} fill="none"
          strokeDasharray={`${c * part} ${c}`} strokeLinecap="round" transform={`rotate(-90 ${size / 2} ${size / 2})`} />
      </Svg>
      <Text variant="headline" numeric maxFontSizeMultiplier={1.2}>{score != null ? String(score) : '–'}</Text>
    </View>
  );
}

function Tile({ children, wide = false, testID, label }: { children: React.ReactNode; wide?: boolean; testID: string; label: string }) {
  const { colors } = useTheme();
  return (
    <View testID={testID} accessible accessibilityRole="image" accessibilityLabel={label}
      style={{ width: wide ? '100%' : '47%', minHeight: 150, padding: 12, gap: 6, borderRadius: Platform.OS === 'android' ? 24 : 20,
        backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.hairline }}>
      {children}
    </View>
  );
}

const Updated = ({ s }: { s: WidgetSnapshot }) => (s.updated ? <Text variant="caption" tone="muted" maxFontSizeMultiplier={1.2}>{s.updated}</Text> : null);

/** What a widget says, in one line, for screen readers and the tests. */
export function smallWords(s: WidgetSnapshot): string {
  if (s.message) return s.message;
  return [s.readiness && `Readiness ${s.readiness.score ?? ''} ${s.readiness.word}`.replace('  ', ' '),
    s.session && `${s.session.line}, ${s.session.title}, ${s.session.watch}`, s.updated].filter(Boolean).join('. ');
}

export function WidgetPreviews({ s }: { s: WidgetSnapshot }) {
  const { colors } = useTheme();
  if (s.message) {
    return (
      <Tile testID="widget-message" label={s.message}>
        <Text variant="kicker" tone="muted" maxFontSizeMultiplier={1.2}>Daash</Text>
        <Text variant="subhead" maxFontSizeMultiplier={1.2}>{s.message}</Text>
      </Tile>
    );
  }
  return (
    <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 12, justifyContent: 'space-between' }}>
      <Tile testID="widget-small" label={smallWords(s)}>
        {s.readiness ? (
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
            <Ring score={s.readiness.score} decision={s.readiness.decision} />
            <View style={{ flex: 1 }}>
              <Text variant="caption" tone="muted" maxFontSizeMultiplier={1.2}>Readiness</Text>
              <Text variant="subhead" tone={DECISION_TONE[s.readiness.decision]} style={{ fontWeight: '700' }} maxFontSizeMultiplier={1.2}>{s.readiness.word}</Text>
            </View>
          </View>
        ) : null}
        {s.session ? (
          <View style={{ gap: 2, borderLeftWidth: 3, borderLeftColor: colors[TYPE_TOKEN[s.session.type]], paddingLeft: 6 }}>
            <Text variant="caption" tone="muted" maxFontSizeMultiplier={1.2}>{s.session.line}</Text>
            <Text variant="subhead" style={{ fontWeight: '600' }} maxFontSizeMultiplier={1.2}>{s.session.title}</Text>
            <Text variant="caption" tone="text2" maxFontSizeMultiplier={1.2}>{s.session.watch}</Text>
          </View>
        ) : <Text variant="subhead" maxFontSizeMultiplier={1.2}>Rest day</Text>}
        <Updated s={s} />
      </Tile>
      {s.countdown ? (
        <Tile testID="widget-countdown" label={`${s.countdown.race}, ${s.countdown.days} days, ${s.countdown.date}`}>
          <Text variant="caption" tone="muted" maxFontSizeMultiplier={1.2}>{s.countdown.race}</Text>
          <Text variant="largeTitle" numeric maxFontSizeMultiplier={1.2}>{String(s.countdown.days)}</Text>
          <Text variant="caption" tone="muted" maxFontSizeMultiplier={1.2}>{`days · ${s.countdown.date}`}</Text>
          <Updated s={s} />
        </Tile>
      ) : null}
      {s.week ? (
        <Tile wide testID="widget-medium" label={`${s.week.title}. ${s.week.summary}. ${s.week.days.map((d) => `${d.letter} ${d.day} ${d.label}${d.done ? ' done' : ''}`).join(', ')}`}>
          <Text variant="caption" tone="muted" maxFontSizeMultiplier={1.2}>{s.week.title}</Text>
          <Text variant="subhead" style={{ fontWeight: '600' }} maxFontSizeMultiplier={1.2}>{s.week.summary}</Text>
          <View style={{ flexDirection: 'row', gap: 4 }}>
            {s.week.days.map((d, i) => (
              <View key={i} style={{ flex: 1, alignItems: 'center', gap: 2, paddingVertical: 4, borderRadius: 8,
                backgroundColor: d.today ? colors.sunken : 'transparent', borderTopWidth: 3, borderTopColor: colors[TYPE_TOKEN[d.type]] }}>
                <Text variant="caption" tone="muted" maxFontSizeMultiplier={1.2}>{d.letter}</Text>
                <Text variant="caption" numeric maxFontSizeMultiplier={1.2} style={{ fontWeight: d.today ? '700' : '400' }}>{String(d.day)}</Text>
                <Text variant="caption" tone={d.done ? 'muted' : 'text'} maxFontSizeMultiplier={1.2}>{d.label}</Text>
              </View>
            ))}
          </View>
          <Updated s={s} />
        </Tile>
      ) : null}
      {s.lock.circular || s.lock.rectangular ? (
        <Tile wide testID="widget-lock" label={['Lock screen', s.lock.circular && `${s.lock.circular.score ?? ''} ${s.lock.circular.word}`,
          s.lock.rectangular && `${s.lock.rectangular.line1}, ${s.lock.rectangular.line2}`].filter(Boolean).join('. ')}>
          <Text variant="caption" tone="muted" maxFontSizeMultiplier={1.2}>Lock screen</Text>
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 12 }}>
            {s.lock.circular && s.readiness ? <Ring score={s.lock.circular.score} decision={s.readiness.decision} size={48} /> : null}
            {s.lock.rectangular ? (
              <View style={{ flex: 1 }}>
                <Text variant="subhead" style={{ fontWeight: '600' }} maxFontSizeMultiplier={1.2}>{s.lock.rectangular.line1}</Text>
                <Text variant="caption" tone="muted" maxFontSizeMultiplier={1.2}>{s.lock.rectangular.line2}</Text>
              </View>
            ) : null}
          </View>
        </Tile>
      ) : null}
    </View>
  );
}

export function WidgetsView({ snapshot, plus, os = Platform.OS, onPlus }: { snapshot: WidgetSnapshot; plus: boolean; os?: string; onPlus: () => void }) {
  const { colors } = useTheme();
  const how = os === 'android'
    ? 'Touch and hold your home screen, tap Widgets, then find Daash. For At a Glance, touch and hold it and choose Customise.'
    : 'Touch and hold your home screen, tap Edit, then Add Widget, and choose Daash. For the lock screen, touch and hold it and tap Customise.';
  return (
    <ScrollView testID="widgets" contentInsetAdjustmentBehavior="automatic" style={{ flex: 1, backgroundColor: colors.bg }}
      contentContainerStyle={{ padding: 16, gap: 14 }}>
      <Text variant="body" tone="muted">Readiness, today’s session, your week and the race countdown, on your home and lock screen.</Text>
      {plus ? <WidgetPreviews s={snapshot} /> : (
        <LockedPreview testID="widgets-locked" buttonTestID="widgets-see-plus" caption="Home and lock screen widgets are in Plus" onPress={onPlus}>
          <WidgetPreviews s={snapshot} />
        </LockedPreview>
      )}
      <Card testID="widgets-how">
        <Text variant="headline" accessibilityRole="header">{plus ? 'Add a widget' : 'Once you have Plus'}</Text>
        <Text variant="subhead">{how}</Text>
      </Card>
      <Text variant="footnote" tone="muted">Widgets read cached data. When it’s old they show the time, never a blank.</Text>
    </ScrollView>
  );
}

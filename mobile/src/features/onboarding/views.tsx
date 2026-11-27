// Onboarding (build prompt, section 5.13): welcome with try-first, the goal, paces
// (import from the watch, recommended, or four questions), the result to confirm,
// the plan reveal, and the first workout landing: a filled success check, and only
// then the ask for notifications.
import { useState, type ReactNode } from 'react';
import { Pressable, ScrollView, TextInput, View } from 'react-native';
import Svg, { Circle, Path } from 'react-native-svg';

import { useTheme } from '../../theme/ThemeProvider';
import { Button } from '../../ui/Button';
import { Card } from '../../ui/Card';
import { StructureBar } from '../../ui/session/StructureBar';
import { TypeChip } from '../../ui/session/TypeChip';
import { TYPE_TOKEN } from '../../ui/session/types';
import { Text } from '../../ui/Text';
import { DISTANCES, EXPERIENCE, WEEKLY, goalValid, raceSeconds, type Experience, type Goal, type PaceResult, type Reveal, type Weekly } from './model';

function Step({ kicker, title, sub, children, footer, testID }: { kicker?: string; title: string; sub?: string; children: ReactNode; footer?: ReactNode; testID: string }) {
  const { colors } = useTheme();
  return (
    <View testID={testID} style={{ flex: 1, backgroundColor: colors.bg }}>
      <ScrollView contentInsetAdjustmentBehavior="automatic" keyboardShouldPersistTaps="handled" contentContainerStyle={{ padding: 20, paddingTop: 24, gap: 12 }}>
        {kicker ? <Text variant="kicker" tone="muted">{kicker}</Text> : null}
        <Text variant="largeTitle" accessibilityRole="header">{title}</Text>
        {sub ? <Text variant="body" tone="muted">{sub}</Text> : null}
        {children}
      </ScrollView>
      {footer ? <View style={{ padding: 20, paddingBottom: 36, gap: 8 }}>{footer}</View> : null}
    </View>
  );
}

function Chip({ label, on, onPress, testID }: { label: string; on: boolean; onPress: () => void; testID: string }) {
  const { colors } = useTheme();
  return (
    <Pressable testID={testID} accessibilityRole="radio" accessibilityState={{ selected: on }} onPress={onPress} style={{ minHeight: 44, justifyContent: 'center' }}>
      <View style={{ minHeight: 34, paddingHorizontal: 14, borderRadius: 999, justifyContent: 'center', backgroundColor: on ? colors.text : 'transparent',
        borderWidth: 1, borderColor: on ? colors.text : colors.muted }}>
        <Text variant="subhead" tone={on ? 'bg' : 'text'} style={{ fontWeight: '600' }}>{label}</Text>
      </View>
    </Pressable>
  );
}

function Field({ label, value, onChange, placeholder, testID, hint }: { label: string; value: string; onChange: (t: string) => void; placeholder: string; testID: string; hint?: string }) {
  const { colors } = useTheme();
  return (
    <View style={{ gap: 4 }}>
      <Text variant="footnote" tone="muted">{label}</Text>
      <TextInput testID={testID} accessibilityLabel={label} accessibilityHint={hint} value={value} onChangeText={onChange} placeholder={placeholder}
        placeholderTextColor={colors.muted} autoCorrect={false}
        style={{ minHeight: 48, paddingHorizontal: 14, borderRadius: 12, backgroundColor: colors.sunken, color: colors.text, fontSize: 17 }} />
    </View>
  );
}

// -- welcome -------------------------------------------------------------------------------

export function Welcome({ brand, onTry, onSetUp, onSignIn }: { brand: boolean; onTry: () => void; onSetUp: () => void; onSignIn: () => void }) {
  const { colors } = useTheme();
  return (
    <View testID="welcome" style={{ flex: 1, backgroundColor: colors.bg, padding: 24, paddingTop: 72, paddingBottom: 40, justifyContent: 'space-between' }}>
      <View style={{ gap: 18 }}>
        <View style={{ width: 56, height: 56, borderRadius: 16, backgroundColor: colors.sunken, alignItems: 'center', justifyContent: 'center' }}>
          <Text variant="largeTitle" tone="brandText" style={{ fontWeight: '800' }}>D</Text>
        </View>
        <Text variant="largeTitle" accessibilityRole="header">{'Plan the run.\nSend it to your watch.'}</Text>
        <Text variant="body" tone="muted">Build a session in plain words and see it as steps. No account needed to try.</Text>
        <Card style={{ gap: 8 }} testID="welcome-example">
          <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' }}>
            <TypeChip type="tempo" label="Tempo" />
            <Text variant="footnote" tone="muted">example</Text>
          </View>
          <Text variant="headline">5 × 1 km at threshold, 2 min easy</Text>
          <StructureBar type="tempo" parts={[{ part: 'wu', weight: 10 }, ...Array.from({ length: 5 }, (_, i) => [{ part: 'work' as const, weight: 4.4 }, ...(i < 4 ? [{ part: 'rec' as const, weight: 2 }] : [])]).flat(), { part: 'cd', weight: 10 }]} legend={[]} />
        </Card>
      </View>
      <View style={{ gap: 10 }}>
        <Button label="Try building a session" kind="primary" brand={brand} onPress={onTry} />
        <Button label="Set up my plan" onPress={onSetUp} />
        <Pressable testID="sign-in" accessibilityRole="button" onPress={onSignIn} style={{ minHeight: 44, alignItems: 'center', justifyContent: 'center' }}>
          <Text variant="headline">Sign in</Text>
        </Pressable>
      </View>
    </View>
  );
}

/** The try builder's last card: what sending needs, and that it's free. */
export function TryCard({ brand, onCreate }: { brand: boolean; onCreate: () => void }) {
  return (
    <Card testID="try-card" style={{ gap: 8 }}>
      <Text variant="headline">Like it? Send it to your watch.</Text>
      <Text variant="footnote" tone="muted">A free account and a watch connection. Sending is always free.</Text>
      <Button label="Create free account" kind="primary" brand={brand} onPress={onCreate} />
    </Card>
  );
}

// -- step 1: the goal ----------------------------------------------------------------------

export function GoalStep({ initial, brand, onContinue }: { initial: Goal | null; brand: boolean; onContinue: (g: Goal) => void }) {
  const { colors } = useTheme();
  const race0 = initial?.kind === 'race' ? initial : null;
  const [kind, setKind] = useState<'race' | 'fit'>(initial?.kind ?? 'race');
  const [name, setName] = useState(race0?.name ?? '');
  const [km, setKm] = useState(race0?.km ?? 42.195);
  const [date, setDate] = useState(race0?.date ?? '');
  const [target, setTarget] = useState(race0?.target ?? '');
  const goal: Goal = kind === 'fit' ? { kind: 'fit' } : { kind: 'race', name, date, km, target: target.trim() || null };
  const targetOk = !target.trim() || raceSeconds(target, km) != null;
  const choice = (k: 'race' | 'fit', title: string, sub: string) => (
    <Pressable testID={`goal-${k}`} accessibilityRole="radio" accessibilityState={{ selected: kind === k }} onPress={() => setKind(k)}
      style={{ flexDirection: 'row', alignItems: 'center', gap: 12, padding: 16, borderRadius: 16, backgroundColor: colors.surface,
        borderWidth: 1, borderColor: kind === k ? colors.text : colors.hairline }}>
      <View style={{ width: 22, height: 22, borderRadius: 11, borderWidth: 2, borderColor: kind === k ? colors.text : colors.muted, backgroundColor: kind === k ? colors.text : 'transparent' }} />
      <View style={{ flex: 1 }}>
        <Text variant="headline">{title}</Text>
        <Text variant="footnote" tone="muted">{sub}</Text>
      </View>
    </Pressable>
  );
  return (
    <Step testID="ob-goal" kicker="Step 1 of 4" title="What are you training for?"
      footer={<Button label="Continue" kind="primary" brand={brand} disabled={!goalValid(goal) || !targetOk} onPress={() => onContinue(goal)} />}>
      <View accessibilityRole="radiogroup" style={{ gap: 10 }}>
        {choice('race', 'A race', 'Name, date and a target if you have one')}
        {choice('fit', 'Just get fitter', 'Steady weeks, no race date')}
      </View>
      {kind === 'race' ? (
        <Card style={{ gap: 12 }}>
          <Field testID="race-name" label="Race" value={name} onChange={setName} placeholder="London Marathon" />
          <View style={{ gap: 4 }}>
            <Text variant="footnote" tone="muted">Distance</Text>
            <View accessibilityRole="radiogroup" style={{ flexDirection: 'row', flexWrap: 'wrap', columnGap: 8 }}>
              {DISTANCES.map((d) => <Chip key={d.label} testID={`distance-${d.label}`} label={d.label} on={km === d.km} onPress={() => setKm(d.km)} />)}
            </View>
          </View>
          <Field testID="race-date" label="Date" value={date} onChange={setDate} placeholder="2027-04-24" hint="Year, month and day" />
          <Field testID="race-target" label="Target, optional" value={target} onChange={setTarget} placeholder={km >= 21 ? '3:15' : '45:00'} />
          {!targetOk ? <Text testID="target-problem" variant="footnote" tone="warning">{`Write the target as ${km >= 21 ? 'h:mm, like 3:15' : 'mm:ss, like 45:00'}.`}</Text> : null}
        </Card>
      ) : null}
    </Step>
  );
}

// -- step 2: paces -------------------------------------------------------------------------

export function PacesStep({ brand, busy, error, onImport, onQuestions }: { brand: boolean; busy: boolean; error: string | null; onImport: () => void; onQuestions: () => void }) {
  const { colors } = useTheme();
  return (
    <Step testID="ob-paces" kicker="Step 2 of 4" title="Set your paces" sub="Fastest is to read your recent runs.">
      <Card style={{ gap: 8, borderWidth: 1, borderColor: colors.text }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
          <View style={{ height: 22, paddingHorizontal: 8, borderRadius: 6, justifyContent: 'center', overflow: 'hidden' }}>
            <View style={{ position: 'absolute', left: 0, right: 0, top: 0, bottom: 0, backgroundColor: colors.success, opacity: 0.18 }} />
            <Text variant="caption" tone="success" style={{ fontWeight: '700' }}>Recommended</Text>
          </View>
          <Text variant="footnote" tone="muted">about 20 seconds</Text>
        </View>
        <Text variant="title">Import my recent runs from my watch</Text>
        <Text variant="footnote" tone="muted">Reads the last 12 weeks to set easy, threshold and race paces.</Text>
        <Button label={busy ? 'Reading your runs…' : 'Connect Garmin'} kind="primary" brand={brand} disabled={busy} onPress={onImport} />
        {error ? <Text testID="import-error" variant="footnote" tone="warning">{error}</Text> : null}
      </Card>
      <Pressable testID="questions" accessibilityRole="button" onPress={onQuestions}
        style={{ gap: 6, padding: 16, borderRadius: 16, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.hairline }}>
        <Text variant="headline">Answer 4 questions instead</Text>
        <Text variant="footnote" tone="muted">Experience, days a week, recent distance, a recent race</Text>
      </Pressable>
      <Text variant="footnote" tone="muted">Daash reads activities only for this. You can disconnect any time in You › Connections.</Text>
    </Step>
  );
}

export function QuestionsStep({ brand, onContinue }: { brand: boolean; onContinue: (a: { experience: Experience; days: number; weekly: Weekly; race: { km: number; secs: number } | null }) => void }) {
  const [experience, setExperience] = useState<Experience | null>(null);
  const [days, setDays] = useState<number | null>(null);
  const [weekly, setWeekly] = useState<Weekly | null>(null);
  const [raceKm, setRaceKm] = useState<number | null>(null);
  const [time, setTime] = useState('');
  const secs = raceKm != null && time.trim() ? raceSeconds(time, raceKm) : null;
  const raceOk = raceKm == null || !time.trim() || secs != null;
  const ready = experience && days && weekly && raceOk;
  const group = (title: string, items: ReactNode) => (
    <Card style={{ gap: 6 }}>
      <Text variant="headline">{title}</Text>
      <View accessibilityRole="radiogroup" accessibilityLabel={title} style={{ flexDirection: 'row', flexWrap: 'wrap', columnGap: 8 }}>{items}</View>
    </Card>
  );
  return (
    <Step testID="ob-questions" kicker="Step 2 of 4" title="How do you train?"
      footer={<Button label="Continue" kind="primary" brand={brand} disabled={!ready}
        onPress={() => onContinue({ experience: experience!, days: days!, weekly: weekly!, race: raceKm != null && secs != null ? { km: raceKm, secs } : null })} />}>
      {group('Experience', EXPERIENCE.map((e) => <Chip key={e.key} testID={`exp-${e.key}`} label={e.label} on={experience === e.key} onPress={() => setExperience(e.key)} />))}
      {group('Days a week', [3, 4, 5, 6, 7].map((d) => <Chip key={d} testID={`days-${d}`} label={String(d)} on={days === d} onPress={() => setDays(d)} />))}
      {group('A usual week lately', WEEKLY.map((w) => <Chip key={w.key} testID={`weekly-${w.key}`} label={w.label} on={weekly === w.key} onPress={() => setWeekly(w.key)} />))}
      <Card style={{ gap: 8 }}>
        <Text variant="headline">A recent race, optional</Text>
        <View accessibilityRole="radiogroup" style={{ flexDirection: 'row', flexWrap: 'wrap', columnGap: 8 }}>
          {DISTANCES.map((d) => <Chip key={d.label} testID={`qrace-${d.label}`} label={d.label} on={raceKm === d.km} onPress={() => setRaceKm(raceKm === d.km ? null : d.km)} />)}
        </View>
        {raceKm != null ? <Field testID="race-time" label="Time" value={time} onChange={setTime} placeholder={raceKm >= 21 ? '1:45:00' : '45:00'} /> : null}
        {!raceOk ? <Text testID="time-problem" variant="footnote" tone="warning">Write the time as mm:ss or h:mm:ss.</Text> : null}
      </Card>
    </Step>
  );
}

export function ResultStep({ result, device, brand, onAdjust, onRight }: { result: PaceResult; device: string | null; brand: boolean; onAdjust: () => void; onRight: () => void }) {
  const { colors } = useTheme();
  return (
    <Step testID="ob-result" kicker="Step 2 of 4" title={result.title}
      footer={(
        <View style={{ flexDirection: 'row', gap: 8 }}>
          <Button label="Adjust" full onPress={onAdjust} />
          <Button label="Looks right" kind="primary" brand={brand} full onPress={onRight} />
        </View>
      )}>
      <View style={{ borderRadius: 12, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.hairline }}>
        {result.rows.map((r, i) => (
          <View key={r.key} testID={`result-${r.key}`} accessible accessibilityLabel={`${r.label}, ${r.value.replace('–', ' to ')}, ${r.note}`}
            style={{ flexDirection: 'row', alignItems: 'center', gap: 8, padding: 14, borderTopWidth: i ? 1 : 0, borderTopColor: colors.hairline }}>
            <View style={{ flex: 1 }}>
              <Text variant="footnote" tone="muted">{r.label}</Text>
              <Text variant="headline" numeric>{r.value}</Text>
            </View>
            <Text variant="footnote" tone="muted">{r.note}</Text>
          </View>
        ))}
      </View>
      {result.source === 'import' && device ? <Text variant="footnote" tone="muted">{`Connected to Garmin · ${device}`}</Text> : null}
    </Step>
  );
}

// -- the plan reveal --------------------------------------------------------------------------

const RAMP = [0.25, 0.45, 0.7, 1];

export function RevealView({ r, coach, brand, busy, onSend }: { r: Reveal; coach: boolean; brand: boolean; busy: boolean; onSend: () => void }) {
  const { colors } = useTheme();
  const total = r.phases.reduce((n, p) => n + p.weeks, 0);
  const big = (value: string, label: string, testID: string) => (
    <View testID={testID} accessible accessibilityLabel={`${label}, ${value}`} style={{ gap: 2 }}>
      <Text variant="largeTitle" numeric style={{ fontWeight: '700' }}>{value}</Text>
      <Text variant="footnote" tone="muted">{label}</Text>
    </View>
  );
  return (
    <Step testID="ob-reveal" kicker={r.kicker} title={r.title}
      footer={<Button label={busy ? 'Getting week 1 ready…' : 'Send week 1 to my watch'} kind="primary" brand={brand} disabled={busy} onPress={onSend} />}>
      <Card style={{ gap: 12 }}>
        <View testID="phases" accessible accessibilityRole="image" accessibilityLabel={`Phases: ${r.phases.map((p) => `${p.label} ${p.weeks} weeks`).join(', ')}`}
          style={{ flexDirection: 'row', gap: 3 }}>
          {r.phases.map((p, i) => (
            <View key={p.label} style={{ flex: p.weeks / total, gap: 4, minWidth: 0 }}>
              <View style={{ height: 10, borderRadius: 3, backgroundColor: colors.text, opacity: RAMP[i] ?? 1 }} />
              <Text variant="footnote" style={{ fontWeight: '600' }} numberOfLines={1}>{p.label}</Text>
              <Text variant="caption" tone="muted" numberOfLines={1}>{`${p.weeks} wks`}</Text>
            </View>
          ))}
        </View>
        <View style={{ flexDirection: 'row', gap: 24, flexWrap: 'wrap' }}>
          {r.predicted ? big(r.predicted, 'Predicted now', 'predicted') : null}
          {r.target ? big(r.target, 'Target', 'target') : null}
          {big(`${r.startKm}`, `km in week 1 · peak ${r.peakKm}`, 'volume')}
        </View>
      </Card>
      {coach && r.coachNote ? (
        <View testID="coach-note" style={{ gap: 4, padding: 14, borderRadius: 12, backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.hairline }}>
          <Text variant="kicker" tone="muted">Coach · why this shape</Text>
          <Text variant="coach">{r.coachNote}</Text>
        </View>
      ) : <Text testID="because" variant="footnote" tone="muted">{r.because}</Text>}
      <Card testID="week1" style={{ gap: 0, paddingVertical: 6 }}>
        <Text variant="kicker" tone="muted" style={{ paddingVertical: 6 }}>Week 1</Text>
        {r.week1.map((s, i) => (
          <View key={s.id} accessible accessibilityLabel={`${s.dow} ${s.day}, ${s.title}, ${s.sub.replace('–', ' to ')}`}
            style={{ flexDirection: 'row', alignItems: 'center', gap: 12, minHeight: 52, borderTopWidth: i ? 1 : 0, borderTopColor: colors.hairline }}>
            <View style={{ width: 34, alignItems: 'center' }}>
              <Text variant="caption" tone="muted">{s.dow}</Text>
              <Text variant="headline" numeric>{String(s.day)}</Text>
            </View>
            <View style={{ width: 4, alignSelf: 'stretch', marginVertical: 10, borderRadius: 2, backgroundColor: colors[TYPE_TOKEN[s.type]] }} />
            <View style={{ flex: 1, minWidth: 0 }}>
              <Text variant="subhead" style={{ fontWeight: '600' }} numeric>{s.title}</Text>
              <Text variant="footnote" tone="muted" numeric>{s.sub}</Text>
            </View>
          </View>
        ))}
      </Card>
    </Step>
  );
}

// -- landed ----------------------------------------------------------------------------------

export function SuccessCheck() {
  const { colors } = useTheme();
  return (
    <Svg testID="success-check" width={56} height={56} viewBox="0 0 24 24" accessible accessibilityRole="image" accessibilityLabel="Done">
      <Circle cx={12} cy={12} r={11} fill={colors.success} />
      <Path d="M7 12.4l3.3 3.3 6.7-7.2" fill="none" stroke={colors.bg} strokeWidth={2.2} strokeLinecap="round" strokeLinejoin="round" />
    </Svg>
  );
}

const LONG_DAYS: Record<string, string> = { MON: 'Monday', TUE: 'Tuesday', WED: 'Wednesday', THU: 'Thursday', FRI: 'Friday', SAT: 'Saturday', SUN: 'Sunday' };

export type Landing =
  | { state: 'sending' }
  | { state: 'landed'; at: string; device: string | null; rest: number }
  | { state: 'failed'; error: string };

export function LandedView({ first, landing, brand, onResend, onAllow, onNotNow }: {
  first: Reveal['week1'][number]; landing: Landing; brand: boolean; onResend: () => void; onAllow: () => void; onNotNow: () => void;
}) {
  const { colors } = useTheme();
  const day = LONG_DAYS[first.dow] ?? first.dow;
  return (
    <View testID="ob-landed" style={{ flex: 1, backgroundColor: colors.bg, padding: 24, paddingTop: 80, paddingBottom: 40, gap: 18 }}>
      {landing.state === 'landed' ? (
        <>
          <SuccessCheck />
          <Text variant="largeTitle" accessibilityRole="header">{`${day}’s run is on your watch.`}</Text>
          <Text testID="landed-detail" variant="body" tone="muted" numeric>
            {`${first.title} landed on ${landing.device ?? 'your watch'} at ${landing.at}.${landing.rest ? ` The rest of week 1 ${landing.rest === 1 ? 'follows' : 'follows'} as the watch syncs.` : ''}`}
          </Text>
          <View style={{ flex: 1 }} />
          <Card testID="notify-ask" style={{ gap: 10 }}>
            <Text variant="headline">Want a heads-up when sessions land or change?</Text>
            <Text variant="footnote" tone="muted">Asked now, not at sign-up, because now you know what they’re for.</Text>
            <View style={{ flexDirection: 'row', gap: 8 }}>
              <Button label="Not now" full onPress={onNotNow} />
              <Button label="Allow notifications" kind="primary" brand={brand} full onPress={onAllow} />
            </View>
          </Card>
        </>
      ) : landing.state === 'failed' ? (
        <>
          <Text variant="largeTitle" accessibilityRole="header">Week 1 isn’t on your watch yet.</Text>
          <Text testID="landed-error" variant="body" tone="warning">{landing.error}</Text>
          <Button label="Resend" kind="primary" brand={brand} onPress={onResend} />
        </>
      ) : (
        <>
          <Text variant="largeTitle" accessibilityRole="header" accessibilityLiveRegion="polite">{`Sending ${day}’s run to your watch…`}</Text>
          <Text variant="body" tone="muted">It shows on the watch next time it syncs with your phone. No need to wait here.</Text>
          <Button label="Not now" onPress={onNotNow} />
        </>
      )}
    </View>
  );
}

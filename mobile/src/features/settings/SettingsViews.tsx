// You's settings, below Connections, the Coach and Plus (the You boards; build prompt,
// sections 3, 4 and 8): Activities, units and zones, notifications, appearance, privacy,
// help and Sign out. Pure views: the routes in app/(tabs)/you/ hold the data and actions.
// Several are sketches until the server has their endpoints (each route says what's missing).
import Constants from 'expo-constants';
import { Pressable, ScrollView, View } from 'react-native';

import type { Appearance } from '../../theme/ThemeProvider';
import { useTheme } from '../../theme/ThemeProvider';
import { Button } from '../../ui/Button';
import { Icon } from '../../ui/Icon';
import { ListGroup, ListRow, SectionTitle, SwitchRow } from '../../ui/List';
import { TypeChip } from '../../ui/session/TypeChip';
import { Text } from '../../ui/Text';
import type { RunListItem } from '../run/source';
import { NOTIFICATION_KINDS, type NotificationKind, type NotificationPrefs } from './notifications';
import { READABLE } from '../../ui/Screen';

export const INDEPENDENT = 'Daash is independent and not endorsed by any watch maker.';
export const APP_VERSION = `Daash ${Constants.expoConfig?.version ?? ''}`.trim();

function Page({ testID, children }: { testID: string; children: React.ReactNode }) {
  const { colors } = useTheme();
  return (
    <ScrollView testID={testID} contentInsetAdjustmentBehavior="automatic" style={{ flex: 1, backgroundColor: colors.bg }}
      contentContainerStyle={{ ...READABLE, padding: 16, gap: 10 }}>
      {children}
    </ScrollView>
  );
}

export type SettingsActions = {
  activities: () => void; training: () => void; notifications: () => void; appearance: () => void;
  privacy: () => void; help: () => void; signOut: () => void; tryOnboarding?: () => void;
};

const APPEARANCE_WORDS: Record<Appearance, string> = { system: 'Same as the phone', light: 'Light', dark: 'Dark' };

/** You's last sections. */
export function SettingsSection({ appearance, notificationsOn, actions }: {
  appearance: Appearance; notificationsOn: number; actions: SettingsActions;
}) {
  return (
    <View style={{ gap: 10 }} testID="you-settings">
      <SectionTitle>Runs</SectionTitle>
      <ListGroup>
        <ListRow testID="row-activities" title="Activities" sub="Your recent runs" onPress={actions.activities} chevron />
      </ListGroup>
      <SectionTitle>Settings</SectionTitle>
      <ListGroup>
        <ListRow testID="row-training" title="Units and zones" sub="Units, week start, heart rate zones" onPress={actions.training} chevron />
        <ListRow testID="row-notifications" title="Notifications" sub={notificationsOn ? `${notificationsOn} of 4 on` : 'Off'} onPress={actions.notifications} chevron />
        <ListRow testID="row-appearance" title="Appearance" sub={APPEARANCE_WORDS[appearance]} onPress={actions.appearance} chevron />
      </ListGroup>
      <SectionTitle>Privacy</SectionTitle>
      <ListGroup>
        <ListRow testID="row-privacy" title="What Daash uses" sub="Runs, sleep and readiness from Garmin" onPress={actions.privacy} chevron />
        <ListRow testID="row-help" title="Help" onPress={actions.help} chevron />
      </ListGroup>
      {actions.tryOnboarding ? <Button label="Try onboarding" onPress={actions.tryOnboarding} /> : null}
      <Button label="Sign out" onPress={actions.signOut} />
      <Text variant="footnote" tone="muted" style={{ textAlign: 'center' }}>{APP_VERSION}</Text>
      <Text variant="footnote" tone="muted" style={{ textAlign: 'center' }}>{INDEPENDENT}</Text>
    </View>
  );
}

/** Sign out asks first: what goes and what stays. */
export function SignOutSheet({ onSignOut, onKeep, busy = false }: { onSignOut: () => void; onKeep: () => void; busy?: boolean }) {
  return (
    <View testID="sign-out-sheet" style={{ padding: 20, gap: 12 }}>
      <Text variant="title" accessibilityRole="header">Sign out?</Text>
      <Text variant="body" tone="muted">Daash takes your plan, runs and the watch queue off this phone. They’re still on your account; sign in again to get them back.</Text>
      <Text variant="footnote" tone="muted">Nothing changes on your watch.</Text>
      <Button label="Sign out" kind="primary" disabled={busy} onPress={onSignOut} />
      <Button label="Stay signed in" onPress={onKeep} />
    </View>
  );
}

/** The app's look: the phone's setting, or always light or dark (build prompt, section 3). */
export function AppearanceView({ value, onChange }: { value: Appearance; onChange: (a: Appearance) => void }) {
  const { colors } = useTheme();
  return (
    <Page testID="appearance">
      <Text variant="footnote" tone="muted">Daash follows your phone unless you pick one here. It’s kept on this phone.</Text>
      <ListGroup>
        {(['system', 'light', 'dark'] as Appearance[]).map((a) => (
          <Pressable key={a} testID={`appearance-${a}`} accessibilityRole="radio" accessibilityState={{ selected: value === a, checked: value === a }}
            accessibilityLabel={APPEARANCE_WORDS[a]} onPress={() => onChange(a)}
            style={({ pressed }) => ({ flexDirection: 'row', alignItems: 'center', minHeight: 56, paddingHorizontal: 14, gap: 10, backgroundColor: pressed ? colors.sunken : 'transparent' })}>
            <Text variant="body" style={{ flex: 1 }}>{APPEARANCE_WORDS[a]}</Text>
            {value === a ? <Icon name="check" tone="text" /> : null}
          </Pressable>
        ))}
      </ListGroup>
    </Page>
  );
}

/** The four kinds, each switchable. */
export function NotificationsView({ prefs, allowed, onChange }: {
  prefs: NotificationPrefs; allowed: boolean | null; onChange: (kind: NotificationKind, on: boolean) => void;
}) {
  return (
    <Page testID="notifications">
      {allowed === false ? (
        <Text testID="notifications-blocked" variant="footnote" tone="muted">Notifications are off for Daash in your phone’s settings. These choices apply once they’re on.</Text>
      ) : null}
      <ListGroup>
        {NOTIFICATION_KINDS.map((k) => (
          <SwitchRow key={k.kind} testID={`notify-${k.kind}`} title={k.title} sub={k.sub} value={prefs[k.kind]} onChange={(on) => onChange(k.kind, on)} />
        ))}
      </ListGroup>
      <Text variant="footnote" tone="muted">No reminders about streaks or sessions you didn’t do. Rest days count.</Text>
    </Page>
  );
}

/** What Daash uses, export and deleting the account (the You board's Privacy). */
export function PrivacyView() {
  return (
    <Page testID="privacy">
      <SectionTitle>What Daash uses</SectionTitle>
      <ListGroup>
        <ListRow title="Runs, sleep and readiness" sub="From Garmin. Nothing is sold or shared." />
        <ListRow title="Kept on this phone" sub="A copy of your plan and recent runs, so Daash opens offline. Sign out removes it." />
      </ListGroup>
      <SectionTitle>Export</SectionTitle>
      <ListGroup>
        <ListRow title="Your runs" sub="They’re in Garmin Connect, which exports them." />
      </ListGroup>
      <SectionTitle>Delete account</SectionTitle>
      <ListGroup>
        <ListRow testID="delete-account" title="Delete account" sub="Disconnects Garmin and deletes everything Daash keeps for you, crew included. Not in the app yet: sign out removes everything on this phone." />
      </ListGroup>
      <Text variant="footnote" tone="muted">{INDEPENDENT}</Text>
    </Page>
  );
}

/** Help: the trust sheets and the version. */
export function HelpView({ actions }: { actions: { trouble: () => void; see: () => void } }) {
  return (
    <Page testID="help">
      <ListGroup>
        <ListRow testID="help-trouble" title="Trouble connecting?" sub="Steps for Garmin sign-in and syncing" onPress={actions.trouble} />
        <ListRow testID="help-see" title="What Daash can and can’t see" onPress={actions.see} />
      </ListGroup>
      <Text variant="footnote" tone="muted">Daash doesn’t give medical advice. If you’re unwell or hurt, check with a doctor or physio.</Text>
      <Text variant="footnote" tone="muted">{APP_VERSION}</Text>
      <Text variant="footnote" tone="muted">{INDEPENDENT}</Text>
    </Page>
  );
}

/** Units, the week and heart rate zones: read from Garmin, not set here. */
export function TrainingView() {
  return (
    <Page testID="training">
      <ListGroup>
        <ListRow title="Units" sub="From Garmin" />
        <ListRow title="Week starts" value="Monday" />
        <ListRow title="Heart rate zones" sub="From Garmin" />
        <ListRow title="Goal race" sub="Set when you make a plan" />
      </ListGroup>
      <Text variant="footnote" tone="muted">Change units and zones in Garmin Connect; Daash picks them up at the next sync.</Text>
    </Page>
  );
}

/** Recent runs, newest first; each opens its post-run screen. */
export function ActivitiesView({ runs, onOpen }: { runs: RunListItem[]; onOpen: (id: string) => void }) {
  if (!runs.length) {
    return (
      <Page testID="activities">
        <Text testID="activities-empty" variant="headline" accessibilityRole="header">No runs yet</Text>
        <Text variant="body" tone="muted">Runs come back from Garmin after each sync and show here.</Text>
      </Page>
    );
  }
  return (
    <Page testID="activities">
      <ListGroup>
        {runs.map((r) => (
          <Pressable key={r.id} testID={`activity-${r.id}`} accessibilityRole="button" onPress={() => onOpen(r.id)}
            accessibilityLabel={`${r.typeLabel}, ${r.title}, ${r.when}, ${r.totalKm} km, ${r.duration}`}
            style={{ flexDirection: 'row', alignItems: 'center', gap: 10, minHeight: 64, paddingVertical: 10, paddingHorizontal: 14 }}>
            <View style={{ flex: 1, minWidth: 0, gap: 4 }}>
              <TypeChip type={r.type} label={r.typeLabel} />
              <Text variant="body" style={{ fontWeight: '600' }}>{r.title}</Text>
              <Text variant="footnote" tone="muted" numeric>{`${r.when} · ${r.totalKm} km · ${r.duration}`}</Text>
            </View>
            <Icon name="chevronRight" tone="muted" />
          </Pressable>
        ))}
      </ListGroup>
    </Page>
  );
}

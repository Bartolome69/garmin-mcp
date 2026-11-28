import { useIsFocused, useRouter } from 'expo-router';
import { useEffect, useState } from 'react';
import { Linking, Platform } from 'react-native';

import { sampleData } from '../../../data/sample';
import { useDb } from '../../../db/DbContext';
import { kv } from '../../../db/kv';
import { CoachSection } from '../../../features/coach/MemoryView';
import { useConnections } from '../../../features/connections/ConnectionsProvider';
import { YouView } from '../../../features/connections/ConnectionsView';
import { reconnect } from '../../../features/connections/reconnect';
import { plusStatus } from '../../../features/plus/entitlement';
import { restoreWords, useEntitlement } from '../../../features/plus/EntitlementsProvider';
import { openPaywall } from '../../../features/plus/open';
import { PlusSection } from '../../../features/plus/PaywallView';
import { PREFS_KEY, readPrefs } from '../../../features/settings/notifications';
import { SettingsSection } from '../../../features/settings/SettingsViews';
import { useFlags } from '../../../flags/FlagsProvider';
import { useTheme } from '../../../theme/ThemeProvider';
import { SampleDataLabel } from '../../../ui/StateViews';
import { Text } from '../../../ui/Text';

// You: Connections, then the Coach (its switch, when the server offers it) and Coach
// memory, or Training preferences with the Coach off, then Plus, then Activities, settings,
// privacy, help and Sign out (the You boards). Sample data says so at the top.
export default function YouScreen() {
  const c = useConnections();
  const e = useEntitlement();
  const { flags, coachAvailable, setCoachOff } = useFlags();
  const coach = flags.coach_enabled;
  const router = useRouter();
  const focused = useIsFocused();
  const [restored, setRestored] = useState<string | null>(null);
  const status = plusStatus(e.entitlement, Date.now(), Platform.OS, coach);
  const { appearance } = useTheme();
  const db = useDb();
  const [notificationsOn, setNotificationsOn] = useState(4);
  useEffect(() => {
    if (!db || !focused) return;
    kv(db).get(PREFS_KEY).then((raw) => setNotificationsOn(Object.values(readPrefs(raw)).filter(Boolean).length)).catch(() => {});
  }, [db, focused]);
  return (
    <YouView vm={c.vm} brand={focused} top={sampleData() ? <SampleDataLabel style={{ marginHorizontal: 0, marginTop: 0 }} /> : null}
      settings={(
        <SettingsSection appearance={appearance} notificationsOn={notificationsOn} actions={{
          activities: () => router.push('/you/activities'),
          training: () => router.push('/you/training'),
          notifications: () => router.push('/you/notifications'),
          appearance: () => router.push('/you/appearance'),
          privacy: () => router.push('/you/privacy'),
          help: () => router.push('/you/help'),
          signOut: () => router.push('/you/sign-out'),
          tryOnboarding: sampleData() ? () => router.push('/onboarding') : undefined,
        }} />
      )} actions={{ connections: () => router.push('/you/connections'), reconnect }}
      plus={(
        <>
          <PlusSection status={status} plus={e.plus} canManage={!!e.manageUrl} actions={{
            open: () => openPaywall(router, 'you'),
            widgets: () => router.push('/you/widgets'),
            restore: async () => {
              setRestored(null);
              const r = await e.restore();
              setRestored(restoreWords(r));
            },
            manage: () => { if (e.manageUrl) Linking.openURL(e.manageUrl).catch(() => {}); },
          }} />
          {restored ? <Text testID="restore-message" variant="footnote" tone="text2">{restored}</Text> : null}
        </>
      )}>
      <CoachSection available={coachAvailable} coach={coach}
        onToggle={(on) => setCoachOff(!on)} onOpen={() => router.push('/you/memory')} />
    </YouView>
  );
}

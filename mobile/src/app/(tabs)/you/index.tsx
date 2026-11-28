import { useIsFocused, useRouter } from 'expo-router';
import { useState } from 'react';
import { Linking, Platform } from 'react-native';

import { CoachSection } from '../../../features/coach/MemoryView';
import { useConnections } from '../../../features/connections/ConnectionsProvider';
import { YouView } from '../../../features/connections/ConnectionsView';
import { reconnect } from '../../../features/connections/reconnect';
import { plusStatus } from '../../../features/plus/entitlement';
import { restoreWords, useEntitlement } from '../../../features/plus/EntitlementsProvider';
import { openPaywall } from '../../../features/plus/open';
import { PlusSection } from '../../../features/plus/PaywallView';
import { useFlags } from '../../../flags/FlagsProvider';
import { Text } from '../../../ui/Text';

// You: Connections, then the Coach (its switch, when the server offers it) and Coach
// memory, or Training preferences with the Coach off, then Plus. Notifications and
// Privacy arrive with their milestones (build prompt, section 15).
export default function YouScreen() {
  const c = useConnections();
  const e = useEntitlement();
  const { flags, coachAvailable, setCoachOff } = useFlags();
  const coach = flags.coach_enabled;
  const router = useRouter();
  const focused = useIsFocused();
  const [restored, setRestored] = useState<string | null>(null);
  const status = plusStatus(e.entitlement, Date.now(), Platform.OS, coach);
  return (
    <YouView vm={c.vm} brand={focused} actions={{ connections: () => router.push('/you/connections'), reconnect }}
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

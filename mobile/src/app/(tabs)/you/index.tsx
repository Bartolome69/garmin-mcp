import { useIsFocused, useRouter } from 'expo-router';
import { useState } from 'react';
import { Linking, Platform } from 'react-native';

import { useConnections } from '../../../features/connections/ConnectionsProvider';
import { YouView } from '../../../features/connections/ConnectionsView';
import { reconnect } from '../../../features/connections/reconnect';
import { plusStatus } from '../../../features/plus/entitlement';
import { restoreWords, useEntitlement } from '../../../features/plus/EntitlementsProvider';
import { openPaywall } from '../../../features/plus/open';
import { PlusSection } from '../../../features/plus/PaywallView';
import { useFlags } from '../../../flags/FlagsProvider';
import { Text } from '../../../ui/Text';

// You: Plus and Connections for now; Coach memory or Training preferences,
// Notifications and Privacy arrive with their milestones (build prompt, section 15).
export default function YouScreen() {
  const c = useConnections();
  const e = useEntitlement();
  const { flags } = useFlags();
  const router = useRouter();
  const focused = useIsFocused();
  const [restored, setRestored] = useState<string | null>(null);
  const status = plusStatus(e.entitlement, Date.now(), Platform.OS, flags.coach_enabled);
  return (
    <YouView vm={c.vm} brand={focused} actions={{ connections: () => router.push('/you/connections'), reconnect }}
      plus={(
        <>
          <PlusSection status={status} plus={e.plus} actions={{
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
      )} />
  );
}

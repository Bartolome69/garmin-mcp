import { useIsFocused, useLocalSearchParams, useRouter } from 'expo-router';
import { useEffect, useState } from 'react';
import { Linking } from 'react-native';

import { track } from '../../analytics/analytics';
import { restoreWords, useEntitlement } from '../../features/plus/EntitlementsProvider';
import { PaywallView } from '../../features/plus/PaywallView';
import { useFlags } from '../../flags/FlagsProvider';

// Daash Plus, over whichever tab asked (a lock, or You's Plus row).
export default function PaywallScreen() {
  const { from } = useLocalSearchParams<{ from?: string }>();
  const e = useEntitlement();
  const { flags } = useFlags();
  const router = useRouter();
  // Behind the trial sheet it loses its brand fill.
  const focused = useIsFocused();
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    track('paywall_viewed', { from: from ?? 'you', coach: flags.coach_enabled });
    e.loadProducts();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const trialOffered = e.products?.length ? e.products.some((p) => p.trialDays != null) : null;
  return (
    <PaywallView
      coach={flags.coach_enabled}
      coachLimit={flags.coach_daily_limit}
      plus={e.plus}
      trialOffered={trialOffered}
      brand={focused}
      message={message}
      actions={{
        start: () => router.push({ pathname: '/plus/trial', params: { from: from ?? 'you' } }),
        close: () => router.back(),
        restore: async () => {
          setMessage(null);
          const r = await e.restore();
          setMessage(restoreWords(r));
        },
        manage: () => { if (e.manageUrl) Linking.openURL(e.manageUrl).catch(() => {}); },
      }}
    />
  );
}

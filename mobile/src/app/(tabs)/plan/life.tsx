// Life happened (build prompt, section 5.4): a reason and since when, then the new week.
import { useRouter } from 'expo-router';
import { useState } from 'react';
import { Alert } from 'react-native';

import { usePlan } from '../../../features/plan/PlanProvider';
import { LifeSheet } from '../../../features/watch/Sheets';
import { useWatch } from '../../../features/watch/WatchProvider';

export default function LifeHappened() {
  const plan = usePlan();
  const watch = useWatch();
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const today = plan.shown?.today ?? new Date().toISOString().slice(0, 10);
  return (
    <LifeSheet today={today} busy={busy} onSee={async (reason, since) => {
      setBusy(true);
      try {
        const p = await watch.propose({ kind: 'life', reason, since });
        router.back();
        router.push({ pathname: '/plan/new-week', params: { id: p.id } });
      } catch (err) {
        Alert.alert('Couldn’t work out a new week', err instanceof Error ? err.message : 'Try again in a moment.');
      } finally {
        setBusy(false);
      }
    }} />
  );
}

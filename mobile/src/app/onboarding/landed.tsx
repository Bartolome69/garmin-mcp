// The first workout lands: a filled success check, and only now the ask for
// notifications (build prompt, sections 5.13 and 8), once per device. Then Today (or
// Plan, when onboarding started from Plan's "Create a plan"). Week 1 has gone to the
// watch: no way back to the steps (no header, no swipe, and Android's back does nothing).
import { Redirect, useFocusEffect, useIsFocused, useRouter } from 'expo-router';
import { useCallback } from 'react';
import { BackHandler } from 'react-native';

import { planChanged } from '../../data/planChanged';
import { askNotifications } from '../../features/onboarding/notify';
import { useOnboarding } from '../../features/onboarding/OnboardingProvider';
import { LandedView } from '../../features/onboarding/views';
import { landingFor } from '../../features/onboarding/week1';
import { useWatch } from '../../features/watch/WatchProvider';

export default function LandedScreen() {
  const ob = useOnboarding();
  const watch = useWatch();
  const router = useRouter();
  const focused = useIsFocused();
  useFocusEffect(useCallback(() => {
    const sub = BackHandler.addEventListener('hardwareBackPress', () => true);
    return () => sub.remove();
  }, []));
  const week = ob.reveal?.week1 ?? [];
  const first = week[0];
  if (!first) return <Redirect href="/onboarding" />;
  const landing = landingFor(week, watch.opFor, watch.connection.device) ?? { state: 'sending' as const };
  const home = ob.origin === 'plan' ? 'Plan' : 'Today';
  const done = async () => {
    await ob.finish();
    planChanged();   // Plan and Today, if open, fetch the plan just made
    router.dismissTo(home === 'Plan' ? '/plan' : '/today');
  };
  const batch = watch.opFor(first.id)?.batch;
  return (
    <LandedView first={first} landing={landing} brand={focused} device={watch.connection.device} home={home}
      ask={ob.notificationsAnswered == null ? null : !ob.notificationsAnswered}
      onResend={() => watch.ops.filter((o) => o.batch === batch && o.state === 'failed').forEach((o) => watch.resend(o.id))}
      onTrouble={() => router.push('/you/trouble')}
      onAllow={async () => { await ob.rememberNotifications(await askNotifications()); await done(); }}
      onNotNow={async () => { await ob.rememberNotifications('not_now'); await done(); }}
      onLeave={done} />
  );
}

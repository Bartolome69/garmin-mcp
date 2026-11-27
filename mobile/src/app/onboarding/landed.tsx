// The first workout lands: a filled success check, and only now the ask for
// notifications (build prompt, sections 5.13 and 8). Then Today.
import { useIsFocused, useRouter } from 'expo-router';

import { askNotifications } from '../../features/onboarding/notify';
import { useOnboarding } from '../../features/onboarding/OnboardingProvider';
import { LandedView, type Landing } from '../../features/onboarding/views';
import { useWatch } from '../../features/watch/WatchProvider';

const hhmm = (t: number) => { const d = new Date(t); return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`; };

export default function LandedScreen() {
  const ob = useOnboarding();
  const watch = useWatch();
  const router = useRouter();
  const focused = useIsFocused();
  const week = ob.reveal?.week1 ?? [];
  const first = week[0];
  if (!first) return null;
  const op = watch.opFor(first.id);
  const others = week.slice(1).filter((s) => watch.opFor(s.id)?.state !== 'on_watch').length;
  const landing: Landing = op?.state === 'on_watch'
    ? { state: 'landed', at: hhmm(op.onWatchAt ?? Date.now()), device: watch.connection.device, rest: others }
    : op?.state === 'failed' ? { state: 'failed', error: op.error ?? 'Not sent.' } : { state: 'sending' };
  const done = async () => { await ob.finish(); router.replace('/today'); };
  return (
    <LandedView first={first} landing={landing} brand={focused}
      onResend={() => watch.ops.filter((o) => o.state === 'failed').forEach((o) => watch.resend(o.id))}
      onAllow={async () => { await askNotifications(); await done(); }}
      onNotNow={done} />
  );
}

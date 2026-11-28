import { useRouter } from 'expo-router';

import { useConnections } from '../../../features/connections/ConnectionsProvider';
import { PriorityView } from '../../../features/connections/ConnectionsView';
import { usePlus } from '../../../features/plus/EntitlementsProvider';
import { openPaywall } from '../../../features/plus/open';
import { Screen } from '../../../ui/Screen';

export default function PriorityScreen() {
  const c = useConnections();
  const plus = usePlus();
  const router = useRouter();
  if (!c.vm) return <Screen testID="priority-missing" />;
  return (
    <PriorityView vm={c.vm} plus={plus} actions={{
      reorder: c.setPriority, keepApart: c.keepApart, mergeAgain: c.mergeAgain,
      plus: () => (plus ? router.push('/you/coming') : openPaywall(router, 'merge_rules')),
    }} />
  );
}

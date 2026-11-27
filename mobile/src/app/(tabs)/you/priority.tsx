import { useRouter } from 'expo-router';

import { useConnections } from '../../../features/connections/ConnectionsProvider';
import { PriorityView } from '../../../features/connections/ConnectionsView';
import { useFlags } from '../../../flags/FlagsProvider';
import { Screen } from '../../../ui/Screen';

export default function PriorityScreen() {
  const c = useConnections();
  const { flags } = useFlags();
  const router = useRouter();
  if (!c.vm) return <Screen testID="priority-missing" />;
  return (
    <PriorityView vm={c.vm} plus={flags.plus} actions={{
      reorder: c.setPriority, keepApart: c.keepApart, mergeAgain: c.mergeAgain,
      plus: () => router.push({ pathname: '/plan/next', params: { kind: 'plus' } }),
    }} />
  );
}

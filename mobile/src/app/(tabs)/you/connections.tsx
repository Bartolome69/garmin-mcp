import { useRouter } from 'expo-router';

import { useConnections } from '../../../features/connections/ConnectionsProvider';
import { ConnectionsView } from '../../../features/connections/ConnectionsView';
import { reconnect } from '../../../features/connections/reconnect';
import { Screen } from '../../../ui/Screen';
import { Text } from '../../../ui/Text';

export default function ConnectionsScreen() {
  const c = useConnections();
  const router = useRouter();
  if (!c.vm) return <Screen testID="connections-missing"><Text variant="body" tone="muted">{c.error ? 'Couldn’t reach Daash. Your connections show here once it’s back.' : 'Getting your connections…'}</Text></Screen>;
  return (
    <ConnectionsView vm={c.vm} asked={c.asked} actions={{
      reconnect,
      priority: () => router.push('/you/priority'),
      see: () => router.push('/you/see'),
      trouble: () => router.push('/you/trouble'),
      request: () => router.push('/you/request'),
      tell: c.ask,
    }} />
  );
}

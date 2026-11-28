import { useRouter } from 'expo-router';

import { freshness } from '../../../data/freshness';

import { useConnections } from '../../../features/connections/ConnectionsProvider';
import { ConnectionsView } from '../../../features/connections/ConnectionsView';
import { reconnect } from '../../../features/connections/reconnect';
import { LoadState } from '../../../ui/StateViews';

export default function ConnectionsScreen() {
  const c = useConnections();
  const router = useRouter();
  if (!c.vm) return <LoadState what="your connections" failure={c.failure} error={c.error} onRetry={c.refresh} testID="connections-missing" />;
  return (
    <ConnectionsView vm={c.vm} asked={c.asked} updated={(() => { const f = freshness(c.fetchedAt, Date.now()); return f?.stale ? f.label : null; })()} actions={{
      reconnect,
      priority: () => router.push('/you/priority'),
      see: () => router.push('/you/see'),
      trouble: () => router.push('/you/trouble'),
      request: () => router.push('/you/request'),
      tell: c.ask,
    }} />
  );
}

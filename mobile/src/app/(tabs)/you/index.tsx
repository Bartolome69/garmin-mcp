import { useIsFocused, useRouter } from 'expo-router';

import { useConnections } from '../../../features/connections/ConnectionsProvider';
import { YouView } from '../../../features/connections/ConnectionsView';
import { reconnect } from '../../../features/connections/reconnect';

// You: Connections for now; Coach memory or Training preferences, Plus, Notifications
// and Privacy arrive with their milestones (build prompt, section 15).
export default function YouScreen() {
  const c = useConnections();
  const router = useRouter();
  const focused = useIsFocused();
  return <YouView vm={c.vm} brand={focused} actions={{ connections: () => router.push('/you/connections'), reconnect }} />;
}

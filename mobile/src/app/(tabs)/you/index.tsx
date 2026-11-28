import { useIsFocused, useRouter } from 'expo-router';

import { CoachSection } from '../../../features/coach/MemoryView';
import { useConnections } from '../../../features/connections/ConnectionsProvider';
import { YouView } from '../../../features/connections/ConnectionsView';
import { reconnect } from '../../../features/connections/reconnect';
import { useFlags } from '../../../flags/FlagsProvider';

// You: Connections, then the Coach (its switch, when the server offers it) and Coach
// memory, or Training preferences with the Coach off. Plus, Notifications and Privacy
// arrive with their milestones (build prompt, section 15).
export default function YouScreen() {
  const c = useConnections();
  const router = useRouter();
  const focused = useIsFocused();
  const { flags, coachAvailable, setCoachOff } = useFlags();
  const coach = flags.coach_enabled;
  return (
    <YouView vm={c.vm} brand={focused} actions={{ connections: () => router.push('/you/connections'), reconnect }}>
      <CoachSection available={coachAvailable} coach={coach}
        onToggle={(on) => setCoachOff(!on)} onOpen={() => router.push('/you/memory')} />
    </YouView>
  );
}

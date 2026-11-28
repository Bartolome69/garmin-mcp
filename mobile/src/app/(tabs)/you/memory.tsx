import { Stack, useRouter } from 'expo-router';

import { useMemory } from '../../../features/coach/MemoryProvider';
import { MemoryView } from '../../../features/coach/MemoryView';
import { useFlags } from '../../../flags/FlagsProvider';

// Coach memory (Coach on) or Training preferences (Coach off): the same items (build prompt, section 5.11).
export default function YouMemory() {
  const memory = useMemory();
  const { flags } = useFlags();
  const router = useRouter();
  const coach = flags.coach_enabled;
  return (
    <>
      <Stack.Screen options={{ title: coach ? 'Coach memory' : 'Training preferences' }} />
      <MemoryView items={memory.items} coach={coach} actions={{
        edit: (id) => router.push({ pathname: '/you/memory-edit', params: { id } }),
        add: () => router.push('/you/memory-edit'),
        clear: () => router.push('/you/memory-clear'),
      }} />
    </>
  );
}

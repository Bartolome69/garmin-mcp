import { Redirect, useRouter } from 'expo-router';

import { useMemory } from '../../../features/coach/MemoryProvider';
import { MemoryClearSheet } from '../../../features/coach/MemoryView';
import { useFlags } from '../../../flags/FlagsProvider';

// Clear memory asks first: the plan, runs and the conversation stay. It's the Coach's
// only (Training preferences have no Clear), so with the Coach off it isn't there.
export default function YouMemoryClear() {
  const memory = useMemory();
  const router = useRouter();
  const { flags } = useFlags();
  if (!flags.coach_enabled) return <Redirect href="/you/memory" />;
  return <MemoryClearSheet onClear={() => { memory.clear().then(() => router.back()); }} onKeep={() => router.back()} />;
}

import { useRouter } from 'expo-router';

import { useMemory } from '../../../features/coach/MemoryProvider';
import { MemoryClearSheet } from '../../../features/coach/MemoryView';

// Clear memory asks first: the plan, runs and the conversation stay.
export default function YouMemoryClear() {
  const memory = useMemory();
  const router = useRouter();
  return <MemoryClearSheet onClear={() => { memory.clear().then(() => router.back()); }} onKeep={() => router.back()} />;
}

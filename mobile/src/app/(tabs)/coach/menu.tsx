import { useRouter } from 'expo-router';

import { useCoach } from '../../../features/coach/CoachProvider';
import { useMemory } from '../../../features/coach/MemoryProvider';
import { visibleItems } from '../../../features/coach/memory';
import { MenuSheet } from '../../../features/coach/Sheets';

// The ⋯ menu: search, filter by run or session, memory, clear (build prompt, section 5.10).
export default function CoachMenu() {
  const coach = useCoach();
  const memory = useMemory();
  const router = useRouter();
  return (
    <MenuSheet memoryCount={visibleItems(memory.items, true).length} actions={{
      search: () => { coach.setFilter(null); coach.setSearching(true); router.back(); },
      filter: () => router.replace('/coach/filter'),
      memory: () => { router.back(); router.push('/you/memory'); },
      clear: () => router.replace('/coach/clear'),
      close: () => router.back(),
    }} />
  );
}

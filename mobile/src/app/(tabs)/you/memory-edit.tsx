import { useLocalSearchParams, useRouter } from 'expo-router';

import { useMemory } from '../../../features/coach/MemoryProvider';
import { MemoryEditSheet } from '../../../features/coach/MemoryView';
import type { MemoryKind } from '../../../features/coach/memory';
import { useFlags } from '../../../flags/FlagsProvider';

// Edit one memory item or preference, delete it, or add one.
export default function YouMemoryEdit() {
  const { id, kind } = useLocalSearchParams<{ id?: string; kind?: MemoryKind }>();
  const memory = useMemory();
  const { flags } = useFlags();
  const router = useRouter();
  const item = id ? memory.items.find((i) => i.id === id) ?? null : null;
  return (
    <MemoryEditSheet key={item?.id ?? 'new'} item={item} coach={flags.coach_enabled} defaultKind={kind}
      onSave={(k, text) => { (item ? memory.update(item.id, { text, kind: k }) : memory.add(k, text)).then(() => router.back()); }}
      onDelete={item ? () => { memory.remove(item.id).then(() => router.back()); } : null}
      onCancel={() => router.back()} />
  );
}

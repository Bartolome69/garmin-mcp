// Coach memory, or Training preferences with the Coach off (build prompt, section
// 5.11): the same items, editable in You either way. Goal and race, training days,
// injuries mentioned (on) or niggles to plan around (off), preferences, travel. With
// the Coach on, also the tone of its answers, "Clear memory", and items the Coach
// saved from the conversation. Nothing here is a number the app computes with: the
// items change no number on any screen.
//
// Kept on the phone in SQLite (memory_items, milestone 1's table).
// TODO(server): GET/PUT/DELETE /app/api/v1/memory, so the Coach reads them (with the
// last 14 days of the thread) and plans use the preferences; until then they stay here.
import type { Db } from '../../db/types';

export type MemoryKind = 'goal' | 'days' | 'injury' | 'preferences' | 'travel' | 'tone' | 'note';
export type MemoryItem = { id: string; kind: MemoryKind; text: string; source: 'you' | 'chat'; createdAt: number; updatedAt: number };

export const MEMORY_KINDS: MemoryKind[] = ['goal', 'days', 'injury', 'preferences', 'travel', 'tone', 'note'];

/** What each kind is called, with the Coach on and off. Tone is the Coach's alone: hidden (not lost) with it off. */
export function kindLabel(kind: MemoryKind, coach: boolean): string | null {
  switch (kind) {
    case 'goal': return 'Goal';
    case 'days': return 'Training days';
    case 'injury': return coach ? 'Injuries you mentioned' : 'Niggles to plan around';
    case 'preferences': return 'Preferences';
    case 'travel': return 'Travel';
    case 'tone': return coach ? 'Tone' : null;
    case 'note': return coach ? 'Added by you' : 'Note';
  }
}

export const kindsFor = (coach: boolean) => MEMORY_KINDS.filter((k) => kindLabel(k, coach) != null);

/** The items to show, in kind order then oldest first. */
export function visibleItems(items: MemoryItem[], coach: boolean): MemoryItem[] {
  return items.filter((i) => kindLabel(i.kind, coach) != null)
    .sort((a, b) => MEMORY_KINDS.indexOf(a.kind) - MEMORY_KINDS.indexOf(b.kind) || a.createdAt - b.createdAt);
}

/** "Left achilles · 12 Nov · you said in chat": where an item came from, said only with the Coach on. */
export function itemWords(i: MemoryItem, coach: boolean): string {
  return coach && i.source === 'chat' ? `${i.text} · you said in chat` : i.text;
}

export interface MemoryStore {
  list(): Promise<MemoryItem[]>;
  put(item: MemoryItem): Promise<void>;
  remove(id: string): Promise<void>;
  clear(): Promise<void>;
}

export function memoryItemsStore(seed: MemoryItem[] = []): MemoryStore {
  let items = [...seed];
  return {
    list: async () => [...items],
    put: async (item) => { items = [...items.filter((i) => i.id !== item.id), item]; },
    remove: async (id) => { items = items.filter((i) => i.id !== id); },
    clear: async () => { items = []; },
  };
}

type Row = { id: string; kind: string; text: string; source: string; created_at: number; updated_at: number };

export function sqlMemoryStore(db: Db): MemoryStore {
  return {
    list: async () => (await db.all<Row>('SELECT * FROM memory_items ORDER BY created_at')).filter((r) => MEMORY_KINDS.includes(r.kind as MemoryKind))
      .map((r) => ({ id: r.id, kind: r.kind as MemoryKind, text: r.text, source: r.source === 'chat' ? 'chat' : 'you', createdAt: r.created_at, updatedAt: r.updated_at })),
    put: async (i) => db.run('INSERT INTO memory_items (id, kind, text, source, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?) '
      + 'ON CONFLICT(id) DO UPDATE SET kind = excluded.kind, text = excluded.text, updated_at = excluded.updated_at',
    [i.id, i.kind, i.text, i.source, i.createdAt, i.updatedAt]),
    remove: async (id) => db.run('DELETE FROM memory_items WHERE id = ?', [id]),
    clear: async () => db.run('DELETE FROM memory_items'),
  };
}

/** The design's persona (build prompt, section 16), for development builds only. */
export function personaMemory(now: number): MemoryItem[] {
  const item = (id: string, kind: MemoryKind, text: string, source: 'you' | 'chat' = 'you'): MemoryItem =>
    ({ id, kind, text, source, createdAt: now, updatedAt: now });
  return [
    item('persona-goal', 'goal', 'London Marathon · Sat 24 Apr · 3:15'),
    item('persona-days', 'days', '6 a week · long run Sunday with the crew'),
    item('persona-injury', 'injury', 'Left achilles · 12 Nov', 'chat'),
    item('persona-prefs', 'preferences', 'Mornings · km · no treadmill intervals'),
    item('persona-tone', 'tone', 'Short answers, numbers first'),
  ];
}

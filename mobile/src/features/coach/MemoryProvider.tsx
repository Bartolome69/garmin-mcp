// The memory items for You and the Coach: one copy, on the phone (memory.ts).
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';

import { useDb } from '../../db/DbContext';
import { kv } from '../../db/kv';
import { memoryItemsStore, personaMemory, sqlMemoryStore, type MemoryItem, type MemoryKind, type MemoryStore } from './memory';

export type MemoryContext = {
  items: MemoryItem[];
  loaded: boolean;
  add: (kind: MemoryKind, text: string, source?: 'you' | 'chat', id?: string) => Promise<MemoryItem | null>;
  update: (id: string, patch: { text?: string; kind?: MemoryKind }) => Promise<void>;
  remove: (id: string) => Promise<void>;
  clear: () => Promise<void>;
};

const Ctx = createContext<MemoryContext | null>(null);
const USE_FIXTURE = __DEV__ || process.env.EXPO_PUBLIC_DAASH_FIXTURE === '1';
const SEEDED = 'memory:seeded';

export function MemoryProvider({ children, store: given, clock = Date.now, seed }: {
  children: ReactNode; store?: MemoryStore; clock?: () => number;
  /** Development builds start with the persona's items once; tests pass their own or []. */
  seed?: MemoryItem[];
}) {
  const db = useDb();
  const store = useMemo(() => given ?? (db ? sqlMemoryStore(db) : memoryItemsStore()), [given, db]);
  const [items, setItems] = useState<MemoryItem[]>([]);
  const [loaded, setLoaded] = useState(false);
  const n = useRef(0);

  useEffect(() => {
    let live = true;
    (async () => {
      let list = await store.list().catch(() => [] as MemoryItem[]);
      if (!list.length) {
        // Seeded once: a cleared memory stays clear.
        const seeded = seed ? false : db ? !!(await kv(db).get<boolean>(SEEDED).catch(() => true)) : !!given;
        const start = seed ?? (USE_FIXTURE && !seeded ? personaMemory(clock()) : []);
        for (const i of start) await store.put(i).catch(() => {});
        list = start;
      }
      if (db) await kv(db).set(SEEDED, true).catch(() => {});
      if (live) { setItems(list); setLoaded(true); }
    })();
    return () => { live = false; };
  }, [store]); // eslint-disable-line react-hooks/exhaustive-deps

  const save = useCallback(async (next: MemoryItem[], changed: MemoryItem | null, removed: string | null) => {
    setItems(next);
    if (changed) await store.put(changed).catch(() => {});
    if (removed) await store.remove(removed).catch(() => {});
  }, [store]);

  const value = useMemo<MemoryContext>(() => ({
    items, loaded,
    add: async (kind, text, source = 'you', id) => {
      const words = text.trim();
      if (!words) return null;
      const now = clock();
      const item: MemoryItem = { id: id ?? `m${now}-${n.current++}`, kind, text: words, source, createdAt: now, updatedAt: now };
      await save([...items.filter((i) => i.id !== item.id), item], item, null);
      return item;
    },
    update: async (id, patch) => {
      const old = items.find((i) => i.id === id);
      if (!old) return;
      const item = { ...old, ...patch, text: (patch.text ?? old.text).trim() || old.text, updatedAt: clock() };
      await save(items.map((i) => (i.id === id ? item : i)), item, null);
    },
    remove: async (id) => { await save(items.filter((i) => i.id !== id), null, id); },
    clear: async () => { setItems([]); await store.clear().catch(() => {}); },
  }), [items, loaded, save, store, clock]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useMemory(): MemoryContext {
  const c = useContext(Ctx);
  if (!c) throw new Error('useMemory outside MemoryProvider');
  return c;
}

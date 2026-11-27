import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react';
import { AppState } from 'react-native';

import { DEFAULT_FLAGS, parseFlags, type Flags } from './flags';

export type FlagsStore = { load(): Promise<unknown | null>; save(raw: unknown): Promise<void> };
/** Asks the server for this person's flags; resolves null when it can't (signed out, offline). */
export type FlagsFetcher = () => Promise<unknown | null>;

type FlagsState = { flags: Flags; refresh: () => Promise<void> };
const FlagsContext = createContext<FlagsState>({ flags: DEFAULT_FLAGS, refresh: async () => {} });

/**
 * Opens on the cached answer, then asks the server, on mount and whenever the
 * app comes back to the foreground. A failed ask keeps what's there.
 */
export function FlagsProvider({ children, store, fetcher, initial }: {
  children: ReactNode;
  store?: FlagsStore;
  fetcher?: FlagsFetcher;
  initial?: Flags;
}) {
  const [flags, setFlags] = useState<Flags>(initial ?? DEFAULT_FLAGS);
  const busy = useRef(false);

  const refresh = useCallback(async () => {
    if (!fetcher || busy.current) return;
    busy.current = true;
    try {
      const raw = await fetcher();
      if (raw != null) {
        setFlags(parseFlags(raw));
        await store?.save(raw);
      }
    } catch {
      // Offline or signed out: the cached flags stand.
    } finally {
      busy.current = false;
    }
  }, [fetcher, store]);

  useEffect(() => {
    let live = true;
    store?.load().then((raw) => { if (live && raw != null) setFlags(parseFlags(raw)); }).catch(() => {});
    refresh();
    const sub = AppState.addEventListener('change', (s) => { if (s === 'active') refresh(); });
    return () => { live = false; sub.remove(); };
  }, [store, refresh]);

  return <FlagsContext.Provider value={{ flags, refresh }}>{children}</FlagsContext.Provider>;
}

export function useFlags(): FlagsState {
  return useContext(FlagsContext);
}

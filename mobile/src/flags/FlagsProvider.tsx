import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { AppState } from 'react-native';

import { DEFAULT_FLAGS, parseFlags, type Flags } from './flags';

export type FlagsStore = { load(): Promise<unknown | null>; save(raw: unknown): Promise<void> };
/** Asks the server for this person's flags; resolves null when it can't (signed out, offline). */
export type FlagsFetcher = () => Promise<unknown | null>;

/** The runner's own Coach switch in You, kept on the phone. TODO(server): stored per person too. */
export type CoachChoiceStore = { load(): Promise<boolean | null>; save(off: boolean): Promise<void> };

type FlagsState = {
  /** The flags as the app uses them: coach_enabled is the server's flag, unless the runner turned the Coach off. */
  flags: Flags;
  refresh: () => Promise<void>;
  /** The server offers this person the Coach (whatever their own switch says). */
  coachAvailable: boolean;
  coachOff: boolean;
  setCoachOff: (off: boolean) => void;
};
const FlagsContext = createContext<FlagsState>({ flags: DEFAULT_FLAGS, refresh: async () => {}, coachAvailable: false, coachOff: false, setCoachOff: () => {} });

/**
 * Opens on the cached answer, then asks the server, on mount and whenever the
 * app comes back to the foreground. A failed ask keeps what's there.
 */
export function FlagsProvider({ children, store, fetcher, initial, choice, initialCoachOff = false }: {
  children: ReactNode;
  store?: FlagsStore;
  fetcher?: FlagsFetcher;
  initial?: Flags;
  choice?: CoachChoiceStore;
  initialCoachOff?: boolean;
}) {
  const [flags, setFlags] = useState<Flags>(initial ?? DEFAULT_FLAGS);
  const [coachOff, setOff] = useState(initialCoachOff);
  useEffect(() => { choice?.load().then((off) => { if (off != null) setOff(off); }).catch(() => {}); }, [choice]);
  const setCoachOff = useCallback((off: boolean) => { setOff(off); choice?.save(off).catch(() => {}); }, [choice]);
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

  // Turning the Coach off hides the tab and every Coach block; no number on any screen changes.
  const value = useMemo<FlagsState>(() => ({
    flags: flags.coach_enabled && coachOff ? { ...flags, coach_enabled: false } : flags,
    refresh, coachAvailable: flags.coach_enabled, coachOff, setCoachOff,
  }), [flags, refresh, coachOff, setCoachOff]);
  return <FlagsContext.Provider value={value}>{children}</FlagsContext.Provider>;
}

export function useFlags(): FlagsState {
  return useContext(FlagsContext);
}

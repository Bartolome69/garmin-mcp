// Free or Plus for the whole app (build prompt, sections 1, 5.14 and 10). Opens on
// the cached answer, then asks the server and the store; a purchase on this phone
// unlocks at once and is handed to the server to check. Only the paywall's trial
// sheet buys, and only when the runner presses "Start free week".
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { AppState } from 'react-native';

import { track } from '../../analytics/analytics';
import { useDb } from '../../db/DbContext';
import { kv } from '../../db/kv';
import { useFlags } from '../../flags/FlagsProvider';
import { fromPurchase, parseEntitlement, resolve, type Entitlement } from './entitlement';
import { fetchEntitlement, verifyPurchase } from './source';
import { defaultStore, PRODUCT_IDS, StoreError, type Product, type Purchase, type Store } from './store';

export type Resolved = ReturnType<typeof resolve>;
export type TrialOutcome = { kind: 'started' | 'pending' | 'cancelled' } | { kind: 'failed'; message: string };
export type RestoreOutcome = { kind: 'restored' | 'nothing' } | { kind: 'failed'; message: string };

/** Restore's outcome, in words. */
export function restoreWords(r: RestoreOutcome): string {
  if (r.kind === 'failed') return r.message;
  return r.kind === 'restored' ? 'Purchases restored.' : 'No purchases found for this account.';
}

export type EntitlementsState = {
  entitlement: Resolved;
  /** Plus or a running trial: every Plus feature unlocks. */
  plus: boolean;
  /**
   * False until the answer kept on the device has been read (found or not). Until
   * then `plus` is false only because nothing is known yet: Plan doesn't fetch and
   * the widgets aren't written, so a Plus runner never gets the Free versions.
   */
  ready: boolean;
  /** The store's products with its own prices; null until asked. */
  products: Product[] | null;
  productsError: string | null;
  loadProducts: () => Promise<void>;
  startTrial: (productId?: string) => Promise<TrialOutcome>;
  restore: () => Promise<RestoreOutcome>;
  manageUrl: string;
  refresh: () => Promise<void>;
};

const KEY = 'entitlement';
const Ctx = createContext<EntitlementsState | null>(null);

export function EntitlementsProvider({ children, store: given, fetcher = fetchEntitlement, verify = verifyPurchase, clock = Date.now }: {
  children: ReactNode;
  store?: Store;
  fetcher?: () => Promise<Entitlement>;
  verify?: (p: Purchase) => Promise<Entitlement | null>;
  clock?: () => number;
}) {
  const db = useDb();
  const { flags } = useFlags();
  const store = useMemo(() => given ?? defaultStore(), [given]);
  const [server, setServer] = useState<Entitlement | null>(null);
  // Nothing to read without a database: known at once.
  const [cacheRead, setCacheRead] = useState(!db);
  const [purchases, setPurchases] = useState<Purchase[]>([]);
  const [products, setProducts] = useState<Product[] | null>(null);
  const [productsError, setProductsError] = useState<string | null>(null);
  const busy = useRef(false);
  const latest = useRef({ fetcher, verify, db });
  latest.current = { fetcher, verify, db };

  const keepServer = useCallback((e: Entitlement) => {
    setServer(e);
    if (latest.current.db) kv(latest.current.db).set(KEY, e).catch(() => {});
  }, []);

  // Each transaction goes to the server once (the store's listener and the purchase both report it).
  const sent = useRef(new Set<string>());
  const verifyAll = useCallback(async (list: Purchase[]) => {
    for (const p of list.filter((x) => !sent.current.has(x.transactionId))) {
      sent.current.add(p.transactionId);
      const e = await latest.current.verify(p);
      if (e && e.tier !== 'free') keepServer(e);
      // Not checked (offline): try again with the next restore or purchase.
      else sent.current.delete(p.transactionId);
    }
  }, [keepServer]);

  const refresh = useCallback(async () => {
    if (busy.current) return;
    busy.current = true;
    try {
      const [answer, held] = await Promise.all([
        latest.current.fetcher().catch(() => null),
        store.current().catch(() => null),
      ]);
      if (answer) keepServer(answer);
      if (held) setPurchases(held);
    } finally {
      busy.current = false;
    }
  }, [store, keepServer]);

  useEffect(() => {
    let live = true;
    if (db) {
      kv(db).get<Entitlement>(KEY)
        .then((e) => { if (live && e) setServer((now) => now ?? e); })
        .catch(() => {})
        .finally(() => { if (live) setCacheRead(true); });
    } else {
      setCacheRead(true);
    }
    refresh();
    const unlisten = store.listen((list) => { setPurchases(list); verifyAll(list); });
    const sub = AppState.addEventListener('change', (s) => { if (s === 'active') refresh(); });
    return () => { live = false; unlisten(); sub.remove(); };
  }, [db, store, refresh, verifyAll]);

  const loadProducts = useCallback(async () => {
    try {
      setProducts(await store.products());
      setProductsError(null);
    } catch (err) {
      setProductsError(err instanceof StoreError ? err.message : 'The store didn’t answer. Try again in a moment.');
    }
  }, [store]);

  const startTrial = useCallback(async (productId?: string): Promise<TrialOutcome> => {
    const id = productId ?? products?.find((p) => p.id === PRODUCT_IDS.monthly)?.id ?? products?.[0]?.id ?? PRODUCT_IDS.monthly;
    try {
      const r = await store.purchase(id);
      if (r.kind !== 'purchased') return { kind: r.kind };
      setPurchases((list) => [...list.filter((p) => p.productId !== r.purchase.productId), r.purchase]);
      track('trial_started', { product: r.purchase.productId, trial: r.purchase.trial });
      verifyAll([r.purchase]);
      return { kind: 'started' };
    } catch (err) {
      return { kind: 'failed', message: err instanceof StoreError ? err.message : 'The purchase didn’t go through. You haven’t been charged.' };
    }
  }, [store, products, verifyAll]);

  const restore = useCallback(async (): Promise<RestoreOutcome> => {
    try {
      const list = await store.restore();
      setPurchases(list);
      await verifyAll(list);
      await refresh();
      return { kind: list.length ? 'restored' : 'nothing' };
    } catch (err) {
      return { kind: 'failed', message: err instanceof StoreError ? err.message : 'The store didn’t answer. Try again in a moment.' };
    }
  }, [store, verifyAll, refresh]);

  const entitlement = resolve([server, flags.plus ? parseEntitlement(true) : null, ...purchases.map(fromPurchase)], clock());
  const plus = entitlement.tier !== 'free';
  // The cached answer read, or anything that unlocks already known (a purchase, the flags).
  const ready = cacheRead || plus;
  const same = JSON.stringify(entitlement);
  const value = useMemo<EntitlementsState>(() => ({
    entitlement, plus, ready, products, productsError, loadProducts, startTrial, restore, manageUrl: store.manageUrl(), refresh,
  }), [same, ready, products, productsError, loadProducts, startTrial, restore, store, refresh]); // eslint-disable-line react-hooks/exhaustive-deps

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

/**
 * Free or Plus, for any screen. Outside the provider (a screen's own test) it
 * falls back to the features flags' `plus`, and buying isn't offered.
 */
export function useEntitlement(): EntitlementsState {
  const c = useContext(Ctx);
  const { flags } = useFlags();
  if (c) return c;
  const entitlement = resolve([flags.plus ? parseEntitlement(true) : null], Date.now());
  const failed = { kind: 'failed' as const, message: 'Purchases aren’t available here.' };
  return {
    entitlement, plus: entitlement.tier !== 'free', ready: true, products: null, productsError: null,
    loadProducts: async () => {}, startTrial: async () => failed, restore: async () => failed,
    manageUrl: '', refresh: async () => {},
  };
}

/** Whether Plus features unlock: Plus, or a running free week. */
export function usePlus(): boolean {
  return useEntitlement().plus;
}

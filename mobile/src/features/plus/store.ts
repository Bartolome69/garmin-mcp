// The app store, behind one interface (build prompt, section 5.14: StoreKit 2 and
// Google Play Billing). Prices always come from the store, in the runner's own
// currency and words ("£4.99", "4,99 €"); nothing here holds a price.
//
// No in-app purchase library is installed yet: it needs a device build and the
// products set up in App Store Connect and the Play Console (DECISIONS, milestone
// 10). Until then a development build and the tests use FakeStore, and a release
// build gets UnavailableStore, which offers nothing and says so.
import { Platform } from 'react-native';

import { TRIAL_DAYS } from './entitlement';

export type Product = {
  id: string;
  /** The store's own localized price string. Never built or formatted in the app. */
  price: string;
  period: 'month' | 'year';
  /** Days of free trial the store will give this person, or null when they've had it. */
  trialDays: number | null;
  title: string;
};

export type Purchase = {
  productId: string;
  transactionId: string;
  purchasedAt: number;
  expiresAt: number | null;
  trial: boolean;
  willRenew: boolean;
  platform: 'app_store' | 'play';
  /** What the server checks with Apple or Google (a JWS on iOS, a purchase token on Android). */
  receipt: string;
};

export type PurchaseResult =
  | { kind: 'purchased'; purchase: Purchase }
  | { kind: 'cancelled' }
  /** Ask to Buy, or a payment the store is still checking: unlocks when it clears. */
  | { kind: 'pending' };

/** The store couldn't do it: words to show, never a code. */
export class StoreError extends Error {}

export interface Store {
  products(): Promise<Product[]>;
  /** Buys the product, starting its free trial when the store offers one. */
  purchase(productId: string): Promise<PurchaseResult>;
  /** Asks the store for this account's purchases again ("Restore purchases"). */
  restore(): Promise<Purchase[]>;
  /** The purchases the store holds now, without asking the runner to sign in. */
  current(): Promise<Purchase[]>;
  /** Purchases that arrive later (a renewal, Ask to Buy approved, bought on another device). */
  listen(cb: (purchases: Purchase[]) => void): () => void;
}

export const PRODUCT_IDS = { monthly: 'run.daash.plus.monthly', yearly: 'run.daash.plus.yearly' } as const;

const DAY = 24 * 60 * 60 * 1000;

/**
 * A stand-in store for development and tests. Its price strings are sample data
 * standing in for what the store would send; the app shows whatever comes back.
 */
export class FakeStore implements Store {
  private owned: Purchase[] = [];
  private listeners = new Set<(p: Purchase[]) => void>();
  private hadTrial = false;
  calls: string[] = [];

  constructor(private opts: {
    products?: Product[];
    /** What the next purchase does. */
    outcome?: 'purchased' | 'cancelled' | 'pending' | 'fail' | 'offline';
    /** Purchases this account already holds (for restore). */
    owned?: Purchase[];
    clock?: () => number;
    platform?: 'app_store' | 'play';
  } = {}) {
    this.owned = [...(opts.owned ?? [])];
    this.hadTrial = this.owned.length > 0;
  }

  private get now() { return (this.opts.clock ?? Date.now)(); }
  private get platform() { return this.opts.platform ?? (Platform.OS === 'android' ? 'play' : 'app_store'); }

  set outcome(o: NonNullable<FakeStore['opts']['outcome']>) { this.opts.outcome = o; }

  async products(): Promise<Product[]> {
    this.calls.push('products');
    if (this.opts.outcome === 'offline') throw new StoreError('The store didn’t answer. Try again in a moment.');
    const list = this.opts.products ?? [
      { id: PRODUCT_IDS.monthly, price: '£4.99', period: 'month', trialDays: TRIAL_DAYS, title: 'Daash Plus' },
      { id: PRODUCT_IDS.yearly, price: '£39.99', period: 'year', trialDays: TRIAL_DAYS, title: 'Daash Plus, yearly' },
    ];
    return list.map((p) => ({ ...p, trialDays: this.hadTrial ? null : p.trialDays }));
  }

  async purchase(productId: string): Promise<PurchaseResult> {
    this.calls.push(`purchase:${productId}`);
    const outcome = this.opts.outcome ?? 'purchased';
    if (outcome === 'fail') throw new StoreError('The purchase didn’t go through. You haven’t been charged.');
    if (outcome === 'offline') throw new StoreError('The store didn’t answer. Try again in a moment.');
    if (outcome === 'cancelled') return { kind: 'cancelled' };
    if (outcome === 'pending') return { kind: 'pending' };
    const product = (await this.products()).find((p) => p.id === productId);
    if (!product) throw new StoreError('That plan isn’t available right now.');
    const trial = product.trialDays != null;
    const now = this.now;
    const purchase: Purchase = {
      productId, transactionId: `fake-${now}`, purchasedAt: now,
      expiresAt: now + (trial ? product.trialDays! : product.period === 'year' ? 365 : 30) * DAY,
      trial, willRenew: true, platform: this.platform, receipt: `fake-receipt-${now}`,
    };
    this.hadTrial = true;
    this.owned = [...this.owned.filter((p) => p.productId !== productId), purchase];
    this.listeners.forEach((l) => l(this.owned));
    return { kind: 'purchased', purchase };
  }

  async restore(): Promise<Purchase[]> {
    this.calls.push('restore');
    if (this.opts.outcome === 'offline') throw new StoreError('The store didn’t answer. Try again in a moment.');
    return this.owned;
  }

  async current(): Promise<Purchase[]> { return this.owned; }

  listen(cb: (p: Purchase[]) => void): () => void {
    this.listeners.add(cb);
    return () => { this.listeners.delete(cb); };
  }

  /** As if bought on another device, or Ask to Buy approved later. */
  deliver(p: Purchase) {
    this.owned = [...this.owned, p];
    this.listeners.forEach((l) => l(this.owned));
  }

}

/** A build without a store library: no products, and buying says why instead of failing silently. */
export class UnavailableStore implements Store {
  async products(): Promise<Product[]> { return []; }
  async purchase(): Promise<PurchaseResult> { throw new StoreError('Purchases aren’t available in this version of Daash yet.'); }
  async restore(): Promise<Purchase[]> { return []; }
  async current(): Promise<Purchase[]> { return []; }
  listen(): () => void { return () => {}; }
}

const USE_FIXTURE = __DEV__ || process.env.EXPO_PUBLIC_DAASH_FIXTURE === '1';

/** The store this build talks to. TODO(device build): a StoreKit 2 / Play Billing store goes here. */
export function defaultStore(): Store {
  return USE_FIXTURE ? new FakeStore() : new UnavailableStore();
}

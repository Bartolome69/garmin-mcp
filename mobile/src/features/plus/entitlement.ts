// Free or Plus (build prompt, sections 1 and 5.14): who has what, and until when.
// Pure: no React, no network, no clock of its own (every check takes `now`).
//
// Where an answer can come from, most trusted first:
//   server     the server's answer (GET /app/api/v1/entitlements, or the features
//              flags' bare `plus: true` as a grant), which checks receipts with the
//              stores and knows web subscriptions
//   app_store  a transaction StoreKit holds on this phone (signed by Apple)
//   play       a purchase Google Play holds on this phone
// A purchase on this phone unlocks at once; the server's answer replaces it once
// the server has checked the receipt. An answer that has run out counts as Free,
// offline included: a cached trial can't outlive its end.

export type Tier = 'free' | 'trial' | 'plus';
export type EntitlementSource = 'server' | 'app_store' | 'play' | 'web' | 'none';

export type Entitlement = {
  tier: Tier;
  /** When it ends (ms since epoch); null when it doesn't (Free, or a lifetime grant). */
  expiresAt: number | null;
  source: EntitlementSource;
  /** When Plus (or the trial) started. */
  since: number | null;
  /** Whether it renews at expiresAt; false once cancelled. Unknown counts as true. */
  willRenew: boolean;
  productId: string | null;
};

export const FREE: Entitlement = { tier: 'free', expiresAt: null, source: 'none', since: null, willRenew: false, productId: null };

const DAY = 24 * 60 * 60 * 1000;
export const TRIAL_DAYS = 7;
/** The day of the free week Daash reminds the runner it's ending (the trial sheet's "Day 5"; features/plus/reminder.ts). */
export const REMINDER_DAY = 5;

/** Whether this answer unlocks Plus now. */
export function isActive(e: Entitlement | null | undefined, now: number): boolean {
  if (!e || e.tier === 'free') return false;
  return e.expiresAt == null || e.expiresAt > now;
}

export const isPlus = isActive;

const RANK: Record<Tier, number> = { free: 0, trial: 1, plus: 2 };
const TRUST: Record<EntitlementSource, number> = { server: 3, web: 2, app_store: 1, play: 1, none: 0 };

/**
 * One answer from several: the best active one (Plus over a trial, then the later
 * end, then the more trusted source). None active: Free, keeping the last one that
 * ran out as `lapsed` so You can say "Your free week ended".
 */
export function resolve(candidates: (Entitlement | null | undefined)[], now: number): Entitlement & { lapsed: Entitlement | null } {
  const all = candidates.filter((c): c is Entitlement => !!c && c.tier !== 'free');
  const active = all.filter((c) => isActive(c, now));
  if (!active.length) {
    const lapsed = all.sort((a, b) => (b.expiresAt ?? 0) - (a.expiresAt ?? 0))[0] ?? null;
    return { ...FREE, lapsed };
  }
  const end = (e: Entitlement) => e.expiresAt ?? Number.POSITIVE_INFINITY;
  const best = active.sort((a, b) => RANK[b.tier] - RANK[a.tier] || end(b) - end(a) || TRUST[b.source] - TRUST[a.source])[0];
  return { ...best, lapsed: null };
}

const TIERS: Tier[] = ['free', 'trial', 'plus'];
const SOURCES: EntitlementSource[] = ['server', 'app_store', 'play', 'web', 'none'];

function time(v: unknown): number | null {
  if (typeof v === 'number' && Number.isFinite(v)) return v < 1e12 ? v * 1000 : v; // seconds or ms
  if (typeof v === 'string' && v) { const t = Date.parse(v); return Number.isNaN(t) ? null : t; }
  return null;
}

/**
 * The server's answer, read leniently: {tier, expires_at, source, since, will_renew,
 * product_id}, times as ISO strings or epoch seconds. Unknown or malformed: Free.
 * The features flags' bare `plus: true` means Plus with no end the app knows of.
 */
export function parseEntitlement(raw: unknown): Entitlement {
  if (raw === true) return { ...FREE, tier: 'plus', source: 'server', willRenew: true };
  if (!raw || typeof raw !== 'object') return FREE;
  const r = raw as Record<string, unknown>;
  const tier = TIERS.includes(r.tier as Tier) ? r.tier as Tier : r.plus === true ? 'plus' : 'free';
  if (tier === 'free') return FREE;
  // Where the server says the money comes from (the store or the web); the server vouches for it either way.
  const source = SOURCES.includes(r.source as EntitlementSource) ? r.source as EntitlementSource : 'server';
  return {
    tier,
    expiresAt: time(r.expires_at ?? r.expiresAt),
    source,
    since: time(r.since),
    willRenew: (r.will_renew ?? r.willRenew) !== false,
    productId: typeof (r.product_id ?? r.productId) === 'string' ? (r.product_id ?? r.productId) as string : null,
  };
}

/** Whole days left before it ends, rounded up (a trial ending later today has 1 day). */
export function daysLeft(e: Entitlement, now: number): number | null {
  if (e.expiresAt == null) return null;
  return Math.max(0, Math.ceil((e.expiresAt - now) / DAY));
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
export const dayWords = (t: number) => { const d = new Date(t); return `${DAYS[d.getDay()]} ${d.getDate()} ${MONTHS[d.getMonth()]}`; };

/**
 * The daash.run page where a web subscription is managed or cancelled.
 * TODO(server): the page itself, before any subscription is sold on the web.
 */
export const WEB_ACCOUNT_URL = 'https://daash.run/account';
const APP_STORE_SUBSCRIPTIONS = 'https://apps.apple.com/account/subscriptions';
const PLAY_SUBSCRIPTIONS = 'https://play.google.com/store/account/subscriptions?package=run.daash.app';

export type ManageLink = { where: string; prep: 'in' | 'on'; url: string };

/**
 * Where the runner manages or cancels it: the place the money goes through. A
 * web subscription on daash.run, a store's on that store's subscriptions page
 * (whichever phone it's opened on). A grant from Daash with no store or web
 * subscription behind it (the features flags' `plus`, or a server answer that
 * names no source) has nothing to manage: null. No source yet (Free): this
 * phone's store, where a purchase would go.
 */
export function manageLink(source: EntitlementSource, os: string, productId: string | null = null): ManageLink | null {
  if (source === 'web') return { where: 'daash.run', prep: 'on', url: WEB_ACCOUNT_URL };
  if (source === 'server') return null;
  if (source === 'play' || (source === 'none' && os === 'android')) {
    return { where: 'Google Play', prep: 'in', url: productId ? `${PLAY_SUBSCRIPTIONS}&sku=${encodeURIComponent(productId)}` : PLAY_SUBSCRIPTIONS };
  }
  return { where: 'the App Store', prep: 'in', url: APP_STORE_SUBSCRIPTIONS };
}

/** Where a purchase on this phone would be cancelled, in words (the paywall's footnote). */
export function storeName(os: string): string {
  return os === 'android' ? 'Google Play' : 'the App Store';
}

export type PlusStatus = { title: string; sub: string; badge: string | null };

/** You's Plus row (the YouPlus board): what the runner has, when it changes, and where to manage it. */
export function plusStatus(e: Entitlement & { lapsed?: Entitlement | null }, now: number, os: string, coach: boolean): PlusStatus {
  if (e.tier === 'trial' && isActive(e, now)) {
    const ends = e.expiresAt != null ? dayWords(e.expiresAt) : null;
    return {
      title: 'Daash Plus',
      sub: ends ? (e.willRenew ? `Free week · Plus starts ${ends}` : `Free week · ends ${ends}`) : 'Free week',
      badge: 'Trial',
    };
  }
  if (e.tier === 'plus' && isActive(e, now)) {
    const since = e.since != null ? `Since ${MONTHS[new Date(e.since).getMonth()]}` : null;
    const ending = !e.willRenew && e.expiresAt != null ? `Ends ${dayWords(e.expiresAt)}` : null;
    const link = manageLink(e.source, os, e.productId);
    const sub = [ending ?? since, link ? `manage ${link.prep} ${link.where}` : null].filter(Boolean).join(' · ');
    return { title: 'Daash Plus', sub: sub || 'On your Daash account', badge: 'Active' };
  }
  const offer = coach ? 'Prediction trend, plan styles, widgets, Coach without a daily limit' : 'Prediction trend, plan styles, widgets';
  if (e.lapsed) return { title: 'Daash Plus', sub: `${e.lapsed.tier === 'trial' ? 'Your free week ended' : 'Plus ended'} · ${offer}`, badge: null };
  return { title: 'Daash Plus', sub: offer, badge: null };
}

/** The trial sheet's timeline: Today unlocks, a reminder on day 5, the price from day 7. Price comes from the store. */
export function trialTimeline(price: string | null, period: 'month' | 'year' = 'month'): { when: string; what: string }[] {
  return [
    { when: 'Today', what: 'Everything in Plus unlocks' },
    { when: `Day ${REMINDER_DAY}`, what: 'We remind you the trial is ending' },
    { when: `Day ${TRIAL_DAYS}`, what: price ? `${price} a ${period} starts, unless you cancel` : 'Plus starts at the store’s price, unless you cancel' },
  ];
}

/** A purchase this phone's store holds, as an answer. Pure, so it can be tested without a store. */
export function fromPurchase(p: { productId: string; purchasedAt: number; expiresAt: number | null; trial: boolean; willRenew?: boolean; platform: 'app_store' | 'play' }): Entitlement {
  return { tier: p.trial ? 'trial' : 'plus', expiresAt: p.expiresAt, source: p.platform, since: p.purchasedAt, willRenew: p.willRenew !== false, productId: p.productId };
}

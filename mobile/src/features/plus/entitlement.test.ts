import { describe, expect, it } from '@jest/globals';

import { daysLeft, FREE, fromPurchase, isActive, manageLink, parseEntitlement, plusStatus, resolve, trialTimeline, WEB_ACCOUNT_URL, type Entitlement } from './entitlement';
import { FakeStore, PRODUCT_IDS, StoreError, UnavailableStore } from './store';

const DAY = 86400000;
const NOW = Date.UTC(2026, 10, 28, 9, 0); // Sat 28 Nov 2026
const trial: Entitlement = { tier: 'trial', expiresAt: NOW + 7 * DAY, source: 'app_store', since: NOW, willRenew: true, productId: PRODUCT_IDS.monthly };
const plus: Entitlement = { tier: 'plus', expiresAt: NOW + 30 * DAY, source: 'server', since: Date.UTC(2026, 10, 1), willRenew: true, productId: PRODUCT_IDS.monthly };

describe('entitlement', () => {
  it('Free never unlocks; trial and Plus unlock until they end', () => {
    expect(isActive(FREE, NOW)).toBe(false);
    expect(isActive(trial, NOW)).toBe(true);
    expect(isActive(trial, NOW + 7 * DAY)).toBe(false);
    expect(isActive({ ...plus, expiresAt: null }, NOW + 9999 * DAY)).toBe(true);
    expect(isActive(null, NOW)).toBe(false);
  });

  it('resolve picks Plus over a trial, then the later end, then the server', () => {
    expect(resolve([trial, plus], NOW).tier).toBe('plus');
    const later = { ...plus, expiresAt: NOW + 60 * DAY, source: 'play' as const };
    expect(resolve([plus, later], NOW).expiresAt).toBe(NOW + 60 * DAY);
    const sameEnd = { ...plus, source: 'app_store' as const };
    expect(resolve([sameEnd, plus], NOW).source).toBe('server');
  });

  it('an answer that ran out is Free, offline included, and kept as lapsed', () => {
    const r = resolve([trial], NOW + 8 * DAY);
    expect(r.tier).toBe('free');
    expect(r.lapsed?.tier).toBe('trial');
    expect(resolve([], NOW)).toEqual({ ...FREE, lapsed: null });
  });

  it('reads the server leniently: ISO or epoch seconds, unknown means Free', () => {
    expect(parseEntitlement({ tier: 'plus', expires_at: '2026-12-28T09:00:00Z', source: 'web', will_renew: false })).toMatchObject(
      { tier: 'plus', expiresAt: Date.UTC(2026, 11, 28, 9), source: 'web', willRenew: false });
    expect(parseEntitlement({ tier: 'trial', expires_at: NOW / 1000 }).expiresAt).toBe(NOW);
    expect(parseEntitlement({ tier: 'gold' })).toEqual(FREE);
    expect(parseEntitlement(null)).toEqual(FREE);
    expect(parseEntitlement('plus')).toEqual(FREE);
    expect(parseEntitlement(true)).toMatchObject({ tier: 'plus', source: 'server', expiresAt: null });
    expect(parseEntitlement({ plus: true }).tier).toBe('plus');
    expect(parseEntitlement({ tier: 'plus', source: 'somewhere' }).source).toBe('server');
  });

  it('days left round up', () => {
    expect(daysLeft(trial, NOW)).toBe(7);
    expect(daysLeft(trial, NOW + 6.5 * DAY)).toBe(1);
    expect(daysLeft(trial, NOW + 8 * DAY)).toBe(0);
    expect(daysLeft(FREE, NOW)).toBeNull();
  });

  it('a store purchase becomes a trial or Plus with its own source', () => {
    expect(fromPurchase({ productId: 'p', purchasedAt: NOW, expiresAt: NOW + DAY, trial: true, platform: 'play' })).toMatchObject(
      { tier: 'trial', source: 'play', since: NOW, willRenew: true });
    expect(fromPurchase({ productId: 'p', purchasedAt: NOW, expiresAt: null, trial: false, willRenew: false, platform: 'app_store' })).toMatchObject(
      { tier: 'plus', source: 'app_store', willRenew: false });
  });

  it('Manage goes where the money goes: daash.run for the web, the store it was bought in, nowhere for a grant', () => {
    expect(manageLink('web', 'ios')).toEqual({ where: 'daash.run', prep: 'on', url: WEB_ACCOUNT_URL });
    expect(manageLink('web', 'android')?.url).toBe(WEB_ACCOUNT_URL);
    expect(manageLink('app_store', 'android')?.url).toBe('https://apps.apple.com/account/subscriptions');
    expect(manageLink('play', 'ios', PRODUCT_IDS.monthly)?.url).toBe(`https://play.google.com/store/account/subscriptions?package=run.daash.app&sku=${PRODUCT_IDS.monthly}`);
    expect(manageLink('server', 'ios')).toBeNull();
    expect(manageLink('none', 'android')?.where).toBe('Google Play');
    expect(manageLink('none', 'ios')?.where).toBe('the App Store');
  });

  it('You says what the runner has and where to manage it (the YouPlus board)', () => {
    expect(plusStatus({ ...FREE, lapsed: null }, NOW, 'ios', false)).toEqual({ title: 'Daash Plus', sub: 'Prediction trend, plan styles, widgets', badge: null });
    expect(plusStatus({ ...FREE, lapsed: null }, NOW, 'ios', true).sub).toContain('Coach without a daily limit');
    expect(plusStatus({ ...FREE, lapsed: trial }, NOW + 8 * DAY, 'ios', false).sub).toMatch(/^Your free week ended/);
    expect(plusStatus(trial, NOW, 'ios', false)).toEqual({ title: 'Daash Plus', sub: 'Free week · Plus starts Sat 5 Dec', badge: 'Trial' });
    expect(plusStatus({ ...trial, willRenew: false }, NOW, 'ios', false).sub).toBe('Free week · ends Sat 5 Dec');
    expect(plusStatus({ ...plus, source: 'web' }, NOW, 'ios', false)).toEqual({ title: 'Daash Plus', sub: 'Since Nov · manage on daash.run', badge: 'Active' });
    expect(plusStatus({ ...plus, source: 'play' }, NOW, 'android', false).sub).toBe('Since Nov · manage in Google Play');
    // A grant from Daash: nothing at a store to manage.
    expect(plusStatus({ ...plus, source: 'server' }, NOW, 'ios', false).sub).toBe('Since Nov');
    expect(plusStatus({ ...plus, source: 'server', since: null }, NOW, 'ios', false).sub).toBe('On your Daash account');
    expect(plusStatus({ ...plus, source: 'app_store' }, NOW, 'android', false).sub).toBe('Since Nov · manage in the App Store');
    expect(plusStatus({ ...plus, source: 'app_store', willRenew: false }, NOW, 'ios', false).sub).toBe('Ends Mon 28 Dec · manage in the App Store');
  });

  it('the trial timeline takes its price from the store, never its own', () => {
    expect(trialTimeline('4,99 €')).toEqual([
      { when: 'Today', what: 'Everything in Plus unlocks' },
      { when: 'Day 5', what: 'We remind you the trial is ending' },
      { when: 'Day 7', what: '4,99 € a month starts, unless you cancel' },
    ]);
    expect(trialTimeline(null)[2].what).toBe('Plus starts at the store’s price, unless you cancel');
    expect(trialTimeline('$39.99', 'year')[2].what).toBe('$39.99 a year starts, unless you cancel');
  });
});

describe('FakeStore', () => {
  it('offers a free week once; the purchase unlocks a 7-day trial and tells listeners', async () => {
    const store = new FakeStore({ clock: () => NOW, platform: 'app_store' });
    const seen: number[] = [];
    store.listen((p) => seen.push(p.length));
    expect((await store.products())[0]).toMatchObject({ id: PRODUCT_IDS.monthly, trialDays: 7 });
    const r = await store.purchase(PRODUCT_IDS.monthly);
    expect(r.kind).toBe('purchased');
    if (r.kind !== 'purchased') return;
    expect(r.purchase).toMatchObject({ trial: true, expiresAt: NOW + 7 * DAY, platform: 'app_store' });
    expect(seen).toEqual([1]);
    expect((await store.products())[0].trialDays).toBeNull();
    expect(await store.restore()).toHaveLength(1);
  });

  it('cancelled, pending and failed purchases say so', async () => {
    const store = new FakeStore({ outcome: 'cancelled' });
    expect(await store.purchase(PRODUCT_IDS.monthly)).toEqual({ kind: 'cancelled' });
    store.outcome = 'pending';
    expect(await store.purchase(PRODUCT_IDS.monthly)).toEqual({ kind: 'pending' });
    store.outcome = 'fail';
    await expect(store.purchase(PRODUCT_IDS.monthly)).rejects.toBeInstanceOf(StoreError);
    store.outcome = 'offline';
    await expect(store.products()).rejects.toBeInstanceOf(StoreError);
  });

  it('a build without a store library offers nothing and explains a purchase', async () => {
    const store = new UnavailableStore();
    expect(await store.products()).toEqual([]);
    await expect(store.purchase()).rejects.toThrow('Purchases aren’t available in this version of Daash yet.');
    expect(await store.restore()).toEqual([]);
  });
});

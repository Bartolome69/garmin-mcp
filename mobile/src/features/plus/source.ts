import { getJson, postJson } from '../../api/client';
import { FREE, parseEntitlement, type Entitlement } from './entitlement';
import type { Purchase } from './store';

const USE_FIXTURE = __DEV__ || process.env.EXPO_PUBLIC_DAASH_FIXTURE === '1';

/**
 * The server's answer. TODO(server): GET /app/api/v1/entitlements answers
 * {tier, expires_at, source, since, will_renew, product_id}, from the receipts it
 * has checked with Apple and Google and from web subscriptions. Until it exists a
 * development build is Free (EXPO_PUBLIC_DAASH_PLUS=1 makes it Plus), so the
 * locks show; a release build keeps the cached answer and the features flags'
 * `plus`.
 */
export async function fetchEntitlement(): Promise<Entitlement> {
  try {
    return parseEntitlement(await getJson<unknown>('/app/api/v1/entitlements'));
  } catch (err) {
    if (USE_FIXTURE) {
      return process.env.EXPO_PUBLIC_DAASH_PLUS === '1'
        ? { tier: 'plus', expiresAt: null, source: 'server', since: Date.UTC(2026, 10, 1), willRenew: true, productId: 'run.daash.plus.monthly' }
        : FREE;
    }
    throw err;
  }
}

/**
 * Hands a store purchase to the server to check with Apple or Google.
 * TODO(server): POST /app/api/v1/entitlements/verify {platform, product_id,
 * transaction_id, receipt} answers the entitlement it now records. Best effort:
 * the purchase already unlocks on this phone; the next refresh retries.
 */
export async function verifyPurchase(p: Purchase): Promise<Entitlement | null> {
  try {
    return parseEntitlement(await postJson<unknown>('/app/api/v1/entitlements/verify', {
      platform: p.platform, product_id: p.productId, transaction_id: p.transactionId, receipt: p.receipt,
    }));
  } catch {
    return null;
  }
}

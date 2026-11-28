import { useIsFocused, useRouter } from 'expo-router';
import { useState } from 'react';

import { useEntitlement } from '../../features/plus/EntitlementsProvider';
import { TrialSheet } from '../../features/plus/PaywallView';
import { PRODUCT_IDS } from '../../features/plus/store';

// "How your free week works": the only place that buys, and only on "Start free week".
export default function TrialScreen() {
  const e = useEntitlement();
  const router = useRouter();
  const focused = useIsFocused();
  const [busy, setBusy] = useState(false);
  const [outcome, setOutcome] = useState<string | null>(null);
  const product = e.products?.find((p) => p.id === PRODUCT_IDS.monthly) ?? e.products?.[0] ?? null;
  const unavailable = e.products !== null && !e.products.length ? 'Daash Plus isn’t on sale in this version yet.' : null;

  return (
    <TrialSheet
      product={product}
      loading={e.products === null && !e.productsError}
      error={e.productsError ?? unavailable}
      busy={busy}
      outcome={outcome}
      brand={focused}
      actions={{
        start: async () => {
          if (!product || busy) return;
          setBusy(true);
          setOutcome(null);
          const r = await e.startTrial(product.id);
          setBusy(false);
          // Started: back to whatever was locked, now open.
          if (r.kind === 'started') router.dismiss(2);
          else if (r.kind === 'pending') setOutcome('Waiting for approval. Plus unlocks as soon as it’s approved.');
          else if (r.kind === 'failed') setOutcome(r.message);
        },
        notNow: () => router.back(),
        retry: () => { e.loadProducts(); },
      }}
    />
  );
}

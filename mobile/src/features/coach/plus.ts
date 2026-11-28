// Whether the runner has Plus, for the Coach's daily limit (build prompt, sections 1
// and 5.10: free has 10 Coach messages a day, Plus none). One function, so the
// entitlements milestone can replace it in one place.
// TODO(M10 integration): swap isPlus for M10's entitlements, and openPlus for its paywall route.
import type { Href } from 'expo-router';

import type { Flags } from '../../flags/flags';

export function isPlus(flags: Pick<Flags, 'plus'>): boolean {
  return flags.plus;
}

/** Where "See Plus" goes: the Plus screen (a placeholder until milestone 10). */
export const PLUS_HREF: Href = { pathname: '/plan/next', params: { kind: 'plus' } };

import type { useRouter } from 'expo-router';

/**
 * Opens Daash Plus over whichever tab asked. Only ever called from the runner's
 * own tap on a lock or on You's Plus row, never by a flow on its own
 * (build prompt, section 1); src/plus.flows.test.tsx checks where it's called.
 */
export function openPaywall(router: Pick<ReturnType<typeof useRouter>, 'push'>, from: string): void {
  router.push({ pathname: '/plus', params: { from } });
}

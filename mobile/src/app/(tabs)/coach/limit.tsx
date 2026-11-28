import { useRouter } from 'expo-router';

import { useCoach } from '../../../features/coach/CoachProvider';
import { LimitSheet } from '../../../features/coach/Sheets';
import { openPaywall } from '../../../features/plus/open';

// Today's free messages are used: what Plus changes. Shown when a message can't go, never in
// the middle of a change. "See Plus" (the runner's tap) opens Daash Plus with its Coach row first.
export default function CoachLimit() {
  const coach = useCoach();
  const router = useRouter();
  return <LimitSheet limit={coach.limit.limit ?? 10} onPlus={() => { router.back(); openPaywall(router, 'coach'); }} onLater={() => router.back()} />;
}

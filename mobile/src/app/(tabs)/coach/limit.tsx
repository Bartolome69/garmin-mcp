import { useRouter } from 'expo-router';

import { useCoach } from '../../../features/coach/CoachProvider';
import { PLUS_HREF } from '../../../features/coach/plus';
import { LimitSheet } from '../../../features/coach/Sheets';

// Today's free messages are used: what Plus changes. Shown when a message can't go, never in the middle of a change.
export default function CoachLimit() {
  const coach = useCoach();
  const router = useRouter();
  return <LimitSheet limit={coach.limit.limit ?? 10} onPlus={() => { router.back(); router.push(PLUS_HREF); }} onLater={() => router.back()} />;
}

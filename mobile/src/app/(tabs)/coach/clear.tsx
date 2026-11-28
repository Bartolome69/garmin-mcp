import { useRouter } from 'expo-router';

import { useCoach } from '../../../features/coach/CoachProvider';
import { ClearSheet } from '../../../features/coach/Sheets';

// Clear conversation: destructive, so it asks first. Memory and the plan stay.
export default function CoachClear() {
  const coach = useCoach();
  const router = useRouter();
  return <ClearSheet onClear={() => { coach.clear(); router.back(); }} onKeep={() => router.back()} />;
}

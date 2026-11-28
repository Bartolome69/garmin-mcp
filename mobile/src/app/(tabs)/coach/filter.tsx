import { useRouter } from 'expo-router';

import { useCoach } from '../../../features/coach/CoachProvider';
import { tagsOf } from '../../../features/coach/model';
import { FilterSheet } from '../../../features/coach/Sheets';

export default function CoachFilter() {
  const coach = useCoach();
  const router = useRouter();
  return (
    <FilterSheet tags={tagsOf(coach.messages)} onClose={() => router.back()}
      onPick={(t) => { coach.setSearching(false); coach.setQuery(''); coach.setFilter(t); router.back(); }} />
  );
}

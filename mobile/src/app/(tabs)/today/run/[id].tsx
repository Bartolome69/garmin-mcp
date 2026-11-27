// A recorded run, from a done day in Today's week strip.
import { useLocalSearchParams } from 'expo-router';

import { RunScreen } from '../../../../features/run/RunScreen';

export default function TodayRun() {
  const { id } = useLocalSearchParams<{ id: string }>();
  return <RunScreen id={id} />;
}

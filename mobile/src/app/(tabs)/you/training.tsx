// SKETCH: You-ios (Training) — units, week start, heart rate zones and the goal race are the
// runner's own, read from Garmin by the server (TODO(server): GET /app/api/v1/profile). Until then
// the rows say where each comes from, never the board's sample values.
import { TrainingView } from '../../../features/settings/SettingsViews';

export default function YouTraining() {
  return <TrainingView />;
}

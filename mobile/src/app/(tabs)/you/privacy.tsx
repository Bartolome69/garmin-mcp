// SKETCH: You-ios (Privacy) — deleting the account needs a server endpoint (TODO(server):
// DELETE /app/api/v1/account, which disconnects Garmin and deletes the crew data too) and a
// confirm; export is Garmin Connect's. The words are the board's.
import { PrivacyView } from '../../../features/settings/SettingsViews';

export default function YouPrivacy() {
  return <PrivacyView />;
}

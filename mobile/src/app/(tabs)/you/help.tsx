// SKETCH: You-ios (footer "Privacy · Help") — no Help board yet: this holds the trust sheets and
// the version. Needs design, and a support contact the owner chooses.
import { useRouter } from 'expo-router';

import { HelpView } from '../../../features/settings/SettingsViews';

export default function YouHelp() {
  const router = useRouter();
  return <HelpView actions={{ trouble: () => router.push('/you/trouble'), see: () => router.push('/you/see') }} />;
}

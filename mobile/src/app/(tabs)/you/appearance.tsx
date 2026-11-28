// Appearance: the phone's setting, or always light or dark (build prompt, section 3). Kept on
// the device (the root layout saves it), so signing out keeps it.
import { AppearanceView } from '../../../features/settings/SettingsViews';
import { useTheme } from '../../../theme/ThemeProvider';

export default function YouAppearance() {
  const { appearance, setAppearance } = useTheme();
  return <AppearanceView value={appearance} onChange={setAppearance} />;
}

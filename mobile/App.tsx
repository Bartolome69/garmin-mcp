// Daash native app: an empty shell until the designs land (docs/native-app/PLAN.md).
// It only proves the theme tokens generated from garmin_mcp/app/app.css are wired in.
import { StatusBar } from 'expo-status-bar';
import { useColorScheme, View } from 'react-native';

import { colors } from './src/theme/tokens';

export default function App() {
  const scheme = useColorScheme() === 'light' ? 'light' : 'dark';
  return (
    <View style={{ flex: 1, backgroundColor: colors[scheme].ground }}>
      <StatusBar style={scheme === 'light' ? 'dark' : 'light'} />
    </View>
  );
}

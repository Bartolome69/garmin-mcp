// Dark mode is first class and follows the system, with an override in You
// (build prompt, section 3). The choice is kept on the device.
import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from 'react';
import { useColorScheme } from 'react-native';

import { derived, type Derived } from './derived';
import { colors, type ColorScheme } from './tokens';

export type Appearance = 'system' | 'light' | 'dark';

export type Theme = {
  scheme: ColorScheme;
  appearance: Appearance;
  colors: (typeof colors)[ColorScheme];
  derived: Derived;
  setAppearance: (a: Appearance) => void;
};

const ThemeContext = createContext<Theme | null>(null);

export function resolveScheme(appearance: Appearance, system: string | null | undefined): ColorScheme {
  if (appearance !== 'system') return appearance;
  return system === 'light' ? 'light' : 'dark';
}

export function ThemeProvider({ children, initial = 'system', onChange }: {
  children: ReactNode;
  initial?: Appearance;
  onChange?: (a: Appearance) => void;
}) {
  const system = useColorScheme();
  const [appearance, setState] = useState<Appearance>(initial);
  const setAppearance = useCallback((a: Appearance) => { setState(a); onChange?.(a); }, [onChange]);
  const value = useMemo<Theme>(() => {
    const scheme = resolveScheme(appearance, system);
    return { scheme, appearance, colors: colors[scheme], derived: derived(scheme), setAppearance };
  }, [appearance, system, setAppearance]);
  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme(): Theme {
  const theme = useContext(ThemeContext);
  if (!theme) throw new Error('useTheme outside ThemeProvider');
  return theme;
}

import { describe, expect, it } from '@jest/globals';
import { Platform } from 'react-native';

import { derived, withAlpha, ZONE_OPACITY } from './derived';
import { resolveScheme } from './ThemeProvider';
import { colors } from './tokens';
import { MAX_FONT_SCALE, TABULAR, typeStyle } from './typography';

const APPROVED = ['bg', 'surface', 'sunken', 'hairline', 'button', 'text', 'text2', 'muted', 'brand', 'brandText',
  'onBrand', 'success', 'warning', 'warningTint', 'cEasy', 'cTempo', 'cInt', 'cLong', 'cStr', 'cRest', 'race',
  'raceTint', 'band'];

describe('tokens', () => {
  it('are exactly the approved set in both modes', () => {
    expect(Object.keys(colors.dark)).toEqual(APPROVED);
    expect(Object.keys(colors.light)).toEqual(APPROVED);
  });
  it('race is brand-text', () => {
    expect(colors.dark.race).toBe(colors.dark.brandText);
    expect(colors.light.race).toBe(colors.light.brandText);
  });
});

describe('derived values', () => {
  it('fade a token without a new literal', () => {
    expect(withAlpha('#161614', 0.7)).toBe('rgba(22, 22, 20, 0.7)');
    expect(() => withAlpha('rgba(1, 2, 3, 0.5)', 0.5)).toThrow();
  });
  it('scrim is bg at 70% in dark and text at 35% in light', () => {
    expect(derived('dark').scrim).toBe(withAlpha(colors.dark.bg, 0.7));
    expect(derived('light').scrim).toBe(withAlpha(colors.light.text, 0.35));
  });
  it('HR zones are text at 20/35/55/75/100%', () => {
    expect(ZONE_OPACITY).toEqual([0.2, 0.35, 0.55, 0.75, 1]);
    expect(derived('dark').zones).toEqual(ZONE_OPACITY.map((a) => withAlpha(colors.dark.text, a)));
  });
  it('badge tints are a token at 18%', () => {
    expect(derived('light').tint('cInt')).toBe(withAlpha(colors.light.cInt, 0.18));
  });
});

describe('appearance', () => {
  it('follows the system unless overridden', () => {
    expect(resolveScheme('system', 'light')).toBe('light');
    expect(resolveScheme('system', 'dark')).toBe('dark');
    expect(resolveScheme('system', null)).toBe('dark');
    expect(resolveScheme('light', 'dark')).toBe('light');
    expect(resolveScheme('dark', 'light')).toBe('dark');
  });
});

describe('type', () => {
  it('uses the system font for UI, mono for kickers and serif for the Coach', () => {
    expect(typeStyle('body').fontFamily).toBeUndefined();
    expect(typeStyle('kicker').fontFamily).toBe('GeistMono_500Medium');
    expect(typeStyle('coach').fontFamily).toBe('SourceSerif4_400Regular');
  });
  it(`follows the platform's scale (${Platform.OS})`, () => {
    expect(typeStyle('largeTitle').fontSize).toBe(Platform.OS === 'ios' ? 34 : 28);
  });
  it('scales to 200% and has tabular numbers', () => {
    expect(MAX_FONT_SCALE).toBe(2);
    expect(TABULAR.fontVariant).toEqual(['tabular-nums']);
  });
});

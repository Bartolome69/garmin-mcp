// Values the build prompt derives from the approved tokens (section 2), computed
// here so no colour literal exists outside tokens.ts.
import { colors, type ColorScheme } from './tokens';

type Palette = (typeof colors)[ColorScheme];

/** A token at an opacity, as rgba(). Only hex tokens can be faded. */
export function withAlpha(hex: string, alpha: number): string {
  const m = /^#([0-9a-f]{6})$/i.exec(hex);
  if (!m) throw new Error(`withAlpha needs a #rrggbb token, got ${hex}`);
  const n = parseInt(m[1], 16);
  const a = Math.round(Math.min(1, Math.max(0, alpha)) * 1000) / 1000;
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${a})`;
}

/** HR zones Z1 to Z5: text at 20 / 35 / 55 / 75 / 100% opacity. Always labelled; never session colours. */
export const ZONE_OPACITY = [0.2, 0.35, 0.55, 0.75, 1] as const;

export function derived(scheme: ColorScheme) {
  const c: Palette = colors[scheme];
  return {
    zones: ZONE_OPACITY.map((a) => withAlpha(c.text, a)),
    // Scrim behind sheets: bg at 70% in dark, text at 35% in light.
    scrim: scheme === 'dark' ? withAlpha(c.bg, 0.7) : withAlpha(c.text, 0.35),
    // Badge tints: a token at 18%.
    tint: (token: Exclude<keyof Palette, 'band'>) => withAlpha(c[token], 0.18),
  };
}

export type Derived = ReturnType<typeof derived>;

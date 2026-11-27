// The design tokens against their source, read independently of the generator:
// every colour the app uses is exactly app.css's, in both modes. And every pair of
// colours the screens put text or controls on meets WCAG contrast: 4.5:1 for text,
// 3:1 for large text and UI (outlines, bars, icons).
import { describe, expect, it } from '@jest/globals';

import { colors } from './tokens';

// eslint-disable-next-line @typescript-eslint/no-require-imports
const fs = require('fs') as typeof import('fs');
// eslint-disable-next-line @typescript-eslint/no-require-imports
const path = require('path') as typeof import('path');

const CSS = fs.readFileSync(path.join(__dirname, '..', '..', '..', 'garmin_mcp', 'app', 'app.css'), 'utf8');

// The prompt's names for app.css's variables (scripts/build_tokens.py APPROVED).
const SOURCE: Record<keyof typeof colors.dark, string> = {
  bg: 'ground', surface: 'surface', sunken: 'sunken', hairline: 'hairline', button: 'button', text: 'ink', text2: 'ink-2',
  muted: 'muted', brand: 'red', brandText: 'red-text', onBrand: 'on-brand', success: 'easy', warning: 'amber',
  warningTint: 'amber-soft', cEasy: 'easy', cTempo: 'hard', cInt: 'intervals', cLong: 'long', cStr: 'strength', cRest: 'dash',
  race: 'red-text', raceTint: 'tint-race', band: 'band-easy',
};

function block(selector: RegExp): Record<string, string> {
  const m = selector.exec(CSS);
  if (!m) throw new Error(`no block for ${selector}`);
  const out: Record<string, string> = {};
  for (const d of m[1].replace(/\/\*[\s\S]*?\*\//g, '').matchAll(/--([a-z0-9-]+)\s*:\s*([^;]+);/g)) out[d[1]] = d[2].trim().replace(/\s+/g, ' ');
  return out;
}

const dark = block(/(?:^|\n)\s*:root\s*\{([^{}]*)\}/);
const light = block(/(?:^|\n)\s*:root\[data-theme="light"\]\s*\{([^{}]*)\}/);
const lightMedia = block(/@media \(prefers-color-scheme: light\)\s*\{\s*:root:not\(\[data-theme="dark"\]\)\s*\{([^{}]*)\}/);

describe('tokens are app.css, exactly', () => {
  it.each(Object.entries(SOURCE))('%s is --%s in dark and light', (token, variable) => {
    expect(colors.dark[token as keyof typeof colors.dark].toLowerCase()).toBe(dark[variable].toLowerCase());
    expect(colors.light[token as keyof typeof colors.light].toLowerCase()).toBe(light[variable].toLowerCase());
  });
  it('the system light theme and the chosen light theme are the same colours', () => {
    expect(lightMedia).toEqual(light);
  });
  it('the app has no token app.css doesn\'t define', () => {
    expect(Object.keys(colors.dark).sort()).toEqual(Object.keys(SOURCE).sort());
    expect(Object.keys(colors.light).sort()).toEqual(Object.keys(SOURCE).sort());
  });
});

// -- contrast -------------------------------------------------------------------------------

type Mode = 'dark' | 'light';
function rgb(c: string, over?: [number, number, number]): [number, number, number] {
  const hex = /^#([0-9a-f]{6})$/i.exec(c);
  if (hex) return [0, 2, 4].map((i) => parseInt(hex[1].slice(i, i + 2), 16)) as [number, number, number];
  const m = /^rgba?\(([^)]+)\)$/.exec(c);
  if (!m) throw new Error(`not a colour: ${c}`);
  const [r, g, b, a = 1] = m[1].split(',').map((x) => parseFloat(x));
  const base = over ?? [0, 0, 0];
  return [r, g, b].map((v, i) => v * a + base[i] * (1 - a)) as [number, number, number];
}
const lum = ([r, g, b]: [number, number, number]) => {
  const f = (v: number) => { const s = v / 255; return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4; };
  return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
};
export function contrast(mode: Mode, fg: keyof typeof colors.dark, bg: keyof typeof colors.dark): number {
  const c = colors[mode];
  const base = rgb(c.bg);
  const back = rgb(c[bg], base);
  const front = rgb(c[fg], back);
  const [a, b] = [lum(front), lum(back)].sort((x, y) => y - x);
  return (a + 0.05) / (b + 0.05);
}

// Text the screens draw, on the surfaces they draw it on.
const TEXT: [keyof typeof colors.dark, keyof typeof colors.dark][] = [
  ['text', 'bg'], ['text', 'surface'], ['text', 'sunken'], ['text2', 'bg'], ['text2', 'surface'], ['text2', 'sunken'],
  ['muted', 'bg'], ['muted', 'surface'], ['muted', 'sunken'], ['onBrand', 'brand'], ['brandText', 'bg'], ['brandText', 'sunken'],
  ['success', 'bg'], ['success', 'surface'], ['warning', 'bg'], ['warning', 'surface'], ['warning', 'warningTint'],
  ['bg', 'text'], ['race', 'raceTint'],
];
// Controls and marks: outlines, type bars, icons, chart lines.
const UI: [keyof typeof colors.dark, keyof typeof colors.dark][] = [
  ['muted', 'bg'], ['muted', 'surface'], ['text', 'surface'], ['brand', 'bg'],
  ['cEasy', 'surface'], ['cTempo', 'surface'], ['cInt', 'surface'], ['cLong', 'surface'], ['cStr', 'surface'],
  ['warning', 'surface'], ['success', 'surface'],
];

describe.each(['dark', 'light'] as const)('contrast, %s', (mode) => {
  it.each(TEXT)('%s text on %s is at least 4.5:1', (fg, bg) => {
    expect(contrast(mode, fg, bg)).toBeGreaterThanOrEqual(4.5);
  });
  it.each(UI)('%s marks on %s are at least 3:1', (fg, bg) => {
    expect(contrast(mode, fg, bg)).toBeGreaterThanOrEqual(3);
  });
});

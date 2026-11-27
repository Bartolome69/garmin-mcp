// Type (build prompt, section 2): the system font for UI (SF Pro on iOS, Roboto on
// Android), a mono face for kickers and step targets, a serif for Coach prose only.
// Tabular numerals for every pace, distance, time and HR. Scales to 200%.
import { Platform, type TextStyle } from 'react-native';

export const FONT_FILES = {
  mono: 'GeistMono_500Medium',
  serif: 'SourceSerif4_400Regular',
  serifItalic: 'SourceSerif4_400Regular_Italic',
} as const;

/** Largest font scale every screen must survive without clipping. */
export const MAX_FONT_SCALE = 2;

type Variant =
  | 'largeTitle' | 'title' | 'headline' | 'body' | 'callout' | 'subhead' | 'footnote' | 'caption'
  | 'kicker' | 'stepTarget' | 'coach';

// iOS text styles (points) and the Material 3 type scale (sp), matched by role.
const IOS: Record<Variant, TextStyle> = {
  largeTitle: { fontSize: 34, lineHeight: 41, fontWeight: '700' },
  title: { fontSize: 22, lineHeight: 28, fontWeight: '700' },
  headline: { fontSize: 17, lineHeight: 22, fontWeight: '600' },
  body: { fontSize: 17, lineHeight: 22 },
  callout: { fontSize: 16, lineHeight: 21 },
  subhead: { fontSize: 15, lineHeight: 20 },
  footnote: { fontSize: 13, lineHeight: 18 },
  caption: { fontSize: 12, lineHeight: 16 },
  kicker: { fontSize: 12, lineHeight: 16, letterSpacing: 0.6, fontFamily: FONT_FILES.mono, textTransform: 'uppercase' },
  stepTarget: { fontSize: 15, lineHeight: 20, fontFamily: FONT_FILES.mono },
  coach: { fontSize: 17, lineHeight: 25, fontFamily: FONT_FILES.serif },
};

const ANDROID: Record<Variant, TextStyle> = {
  largeTitle: { fontSize: 28, lineHeight: 36 },              // headline large
  title: { fontSize: 22, lineHeight: 28 },                   // title large
  headline: { fontSize: 16, lineHeight: 24, fontWeight: '500' }, // title medium
  body: { fontSize: 16, lineHeight: 24 },                    // body large
  callout: { fontSize: 15, lineHeight: 22 },
  subhead: { fontSize: 14, lineHeight: 20 },                 // body medium
  footnote: { fontSize: 12, lineHeight: 16 },                // body small
  caption: { fontSize: 11, lineHeight: 16, fontWeight: '500' }, // label small
  kicker: { fontSize: 12, lineHeight: 16, letterSpacing: 0.6, fontFamily: FONT_FILES.mono, textTransform: 'uppercase' },
  stepTarget: { fontSize: 14, lineHeight: 20, fontFamily: FONT_FILES.mono },
  coach: { fontSize: 16, lineHeight: 24, fontFamily: FONT_FILES.serif },
};

export function typeStyle(variant: Variant, os: string = Platform.OS): TextStyle {
  return (os === 'android' ? ANDROID : IOS)[variant];
}

/** Every number a runner reads (pace, distance, time, HR) uses tabular figures. */
export const TABULAR: TextStyle = { fontVariant: ['tabular-nums'] };

export type TypeVariant = Variant;

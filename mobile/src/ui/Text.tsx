import { Text as RNText, type TextProps } from 'react-native';

import { useTheme } from '../theme/ThemeProvider';
import { MAX_FONT_SCALE, TABULAR, typeStyle, type TypeVariant } from '../theme/typography';
import type { colors } from '../theme/tokens';

type Tone = keyof (typeof colors)['dark'];

/** The one text component: a type role, a token colour, tabular numbers on request. */
export function Text({ variant = 'body', tone = 'text', numeric = false, style, ...rest }: TextProps & {
  variant?: TypeVariant;
  tone?: Tone;
  numeric?: boolean;
}) {
  const { colors } = useTheme();
  return (
    <RNText
      maxFontSizeMultiplier={MAX_FONT_SCALE}
      style={[typeStyle(variant), { color: colors[tone] }, numeric && TABULAR, style]}
      {...rest}
    />
  );
}

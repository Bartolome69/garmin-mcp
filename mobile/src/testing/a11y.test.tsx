// The screen-reader model's own checks (src/testing/a11y.ts), on small trees: what the
// matrix, smoke and focus tests would miss if these went quiet.
import { describe, expect, it } from '@jest/globals';
import { render } from '@testing-library/react-native';
import { Pressable, Switch, Text, View } from 'react-native';

import { ThemeProvider } from '../theme/ThemeProvider';
import { MIN_TOUCH } from '../ui/Button';
import { SwitchRow } from '../ui/List';
import { a11yElements, sharedNames, smallTargets, unnamed } from './a11y';

const tree = async (ui: React.ReactElement) => (await render(<ThemeProvider initial="dark">{ui}</ThemeProvider>)).toJSON();

describe('touch targets and names', () => {
  it('a role-less icon Pressable with no label is a small, unnamed control', async () => {
    const t = await tree(<View><Pressable testID="icon" onPress={() => {}} style={{ width: 20, height: 20 }} /></View>);
    expect(smallTargets(t)).toEqual(['icon: 20 × 20']);
    expect(unnamed(a11yElements(t))).toEqual(['group icon']);
  });

  it('width counts where it is set: a 10 × 44 button is too narrow', async () => {
    const t = await tree(<Pressable testID="thin" accessibilityRole="button" accessibilityLabel="Thin" onPress={() => {}} style={{ width: 10, height: MIN_TOUCH }} />);
    expect(smallTargets(t)).toEqual([`thin: 10 × ${MIN_TOUCH}`]);
  });

  it('a bare native switch is checked (31 pt tall); a switch row is the target instead', async () => {
    const bare = await tree(<Switch testID="bare" value={false} onValueChange={() => {}} />);
    expect(smallTargets(bare)).toEqual(['bare: 51 × 31']);
    const row = await tree(<SwitchRow testID="s" title="Session heads-up" value onChange={() => {}} />);
    expect(smallTargets(row)).toEqual([]);
    expect(a11yElements(row)).toEqual([{ role: 'switch', label: 'Session heads-up', value: 'on', id: 's-row' }]);
  });

  it('two controls with one name are flagged, bar the names shared on purpose', async () => {
    const t = await tree(<View>
      {['Send', 'Send', 'Edit', 'Edit'].map((l, i) => <Pressable key={i} accessibilityRole="button" accessibilityLabel={l} onPress={() => {}} style={{ minHeight: MIN_TOUCH }}><Text>{l}</Text></Pressable>)}
    </View>);
    expect(sharedNames(a11yElements(t))).toEqual(['Send']);
  });
});

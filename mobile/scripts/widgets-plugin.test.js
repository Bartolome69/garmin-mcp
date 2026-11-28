// The widgets' config plugin (native/widgets/plugin): the parts that can be checked
// without a device build. The native widgets themselves are unverified.
const fs = require('fs');
const path = require('path');

const plugin = require('../native/widgets/plugin');
const tokens = require('../src/theme/design-tokens.json');

describe('widgets config plugin', () => {
  it('adds the App Group once, keeping any others', () => {
    const once = plugin.addAppGroup({ 'aps-environment': 'production' });
    expect(once['com.apple.security.application-groups']).toEqual(['group.run.daash.app']);
    expect(once['aps-environment']).toBe('production');
    expect(plugin.addAppGroup(once)['com.apple.security.application-groups']).toEqual(['group.run.daash.app']);
    expect(plugin.addAppGroup({ 'com.apple.security.application-groups': ['group.other'] })['com.apple.security.application-groups'])
      .toEqual(['group.other', 'group.run.daash.app']);
  });

  it('the widgets’ colours are the design tokens, light and dark, on both platforms', () => {
    const swift = plugin.swiftTokens(tokens);
    const kotlin = plugin.kotlinTokens(tokens);
    for (const [name, dark] of Object.entries(tokens.colors.dark)) {
      if (!/^#[0-9a-fA-F]{6}$/.test(dark)) continue;
      const light = tokens.colors.light[name].slice(1).toUpperCase();
      const d = dark.slice(1).toUpperCase();
      expect(swift).toContain(`static let daash${name[0].toUpperCase()}${name.slice(1)} = dyn(0x${light}, 0x${d})`);
      expect(kotlin).toContain(`val ${name} = ColorProvider(day = Color(0xFF${light}), night = Color(0xFF${d}))`);
    }
    expect(swift).not.toMatch(/rgba/);
  });

  it('every colour the native widgets name exists in the tokens', () => {
    const swift = plugin.swiftTokens(tokens);
    const kotlin = plugin.kotlinTokens(tokens);
    const ios = fs.readFileSync(path.join(__dirname, '..', 'native', 'widgets', 'ios-extension', 'DaashWidgets.swift'), 'utf8');
    const android = fs.readFileSync(path.join(__dirname, '..', 'native', 'widgets', 'android', 'src', 'main', 'java', 'run', 'daash', 'widgets', 'DaashWidgets.kt'), 'utf8');
    for (const used of new Set(ios.match(/\.daash[A-Z]\w*/g))) expect(swift).toContain(`static let ${used.slice(1)} =`);
    for (const used of new Set(android.match(/DaashTokens\.(?!kt\b)\w+/g))) expect(kotlin).toContain(`val ${used.split('.')[1]} =`);
    // No colour typed by hand in the widgets.
    expect(ios).not.toMatch(/#[0-9a-fA-F]{6}|0x[0-9a-fA-F]{6}/);
    expect(android).not.toMatch(/#[0-9a-fA-F]{6}|0xFF[0-9a-fA-F]{6}/);
  });

  it('the native side reads the same key and staleness rule as the app', () => {
    const bridge = fs.readFileSync(path.join(__dirname, '..', 'src', 'features', 'widgets', 'bridge.ts'), 'utf8');
    const key = /SNAPSHOT_KEY = '([^']+)'/.exec(bridge)[1];
    const ios = fs.readFileSync(path.join(__dirname, '..', 'native', 'widgets', 'ios-extension', 'DaashWidgets.swift'), 'utf8');
    const android = fs.readFileSync(path.join(__dirname, '..', 'native', 'widgets', 'android', 'src', 'main', 'java', 'run', 'daash', 'widgets', 'DaashWidgets.kt'), 'utf8');
    expect(ios).toContain(`let snapshotKey = "${key}"`);
    expect(android).toContain(`const val SNAPSHOT_KEY = "${key}"`);
    expect(ios).toContain('let staleAfter: TimeInterval = 30 * 60');
    expect(android).toContain('const val STALE_AFTER_MS = 30 * 60 * 1000L');
    expect(ios).toContain(`let appGroup = "${plugin.APP_GROUP}"`);
  });
});

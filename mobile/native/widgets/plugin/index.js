// The widgets' config plugin (docs/native-app/DECISIONS.md, milestone 10). NOT yet
// in app.json: the native code it serves is unverified until a device build.
//
// What it does at prebuild:
//   - gives the app the App Group the widget extension shares (iOS), so the app can
//     write the snapshot where the widgets read it;
//   - writes the widgets' colours from the design tokens (src/theme/design-tokens.json)
//     as DaashTokens.swift and DaashTokens.kt, so the widgets use exactly the app's
//     tokens and no colour is typed by hand.
// The iOS extension target itself comes from @bacons/apple-targets and
// ios-extension/expo-target.config.js; the Android widgets merge in with the module.
const fs = require('fs');
const path = require('path');

const APP_GROUP = 'group.run.daash.app';
const KEY = 'com.apple.security.application-groups';

/** The entitlements with the App Group added once. Pure. */
function addAppGroup(entitlements, group = APP_GROUP) {
  const groups = Array.isArray(entitlements[KEY]) ? entitlements[KEY] : [];
  return { ...entitlements, [KEY]: groups.includes(group) ? groups : [...groups, group] };
}

const HEX = /^#([0-9a-fA-F]{6})$/;
const cap = (k) => k[0].toUpperCase() + k.slice(1);

/** Token names with a plain colour in both modes (the band's rgba stays app-only). */
function hexTokens(tokens) {
  const { dark, light } = tokens.colors;
  return Object.keys(dark).filter((k) => HEX.test(dark[k]) && HEX.test(light[k] || ''))
    .map((k) => ({ name: k, light: light[k].slice(1).toUpperCase(), dark: dark[k].slice(1).toUpperCase() }));
}

/** DaashTokens.swift: Color.daashText and friends, light and dark. Pure. */
function swiftTokens(tokens) {
  const lines = hexTokens(tokens).map((t) => `  static let daash${cap(t.name)} = dyn(0x${t.light}, 0x${t.dark})`);
  return [
    '// Generated at prebuild by native/widgets/plugin from src/theme/design-tokens.json; do not edit.',
    'import SwiftUI',
    'import UIKit',
    '',
    'private func rgb(_ v: UInt32) -> UIColor {',
    '  UIColor(red: CGFloat((v >> 16) & 0xFF) / 255, green: CGFloat((v >> 8) & 0xFF) / 255, blue: CGFloat(v & 0xFF) / 255, alpha: 1)',
    '}',
    'private func dyn(_ light: UInt32, _ dark: UInt32) -> Color {',
    '  Color(UIColor { $0.userInterfaceStyle == .dark ? rgb(dark) : rgb(light) })',
    '}',
    '',
    'extension Color {',
    ...lines,
    '}',
    '',
  ].join('\n');
}

/** DaashTokens.kt: DaashTokens.text and friends as Glance colour providers, day and night. Pure. */
function kotlinTokens(tokens) {
  const lines = hexTokens(tokens).map((t) => `  val ${t.name} = ColorProvider(day = Color(0xFF${t.light}), night = Color(0xFF${t.dark}))`);
  return [
    '// Generated at prebuild by native/widgets/plugin from src/theme/design-tokens.json; do not edit.',
    'package run.daash.widgets',
    '',
    'import androidx.compose.ui.graphics.Color',
    'import androidx.glance.color.ColorProvider',
    '',
    'object DaashTokens {',
    ...lines,
    '}',
    '',
  ].join('\n');
}

const readTokens = (root) => JSON.parse(fs.readFileSync(path.join(root, 'src', 'theme', 'design-tokens.json'), 'utf8'));

function withDaashWidgets(config) {
  // Required lazily so the pure helpers above can be tested without Expo's config tooling.
  const { withDangerousMod, withEntitlementsPlist } = require('expo/config-plugins');
  config = withEntitlementsPlist(config, (c) => { c.modResults = addAppGroup(c.modResults); return c; });
  config = withDangerousMod(config, ['ios', async (c) => {
    const root = c.modRequest.projectRoot;
    const dir = path.join(root, 'targets', 'widgets');
    if (fs.existsSync(dir)) fs.writeFileSync(path.join(dir, 'DaashTokens.swift'), swiftTokens(readTokens(root)));
    return c;
  }]);
  config = withDangerousMod(config, ['android', async (c) => {
    const root = c.modRequest.projectRoot;
    const dir = path.join(root, 'native', 'widgets', 'android', 'src', 'main', 'java', 'run', 'daash', 'widgets');
    fs.writeFileSync(path.join(dir, 'DaashTokens.kt'), kotlinTokens(readTokens(root)));
    return c;
  }]);
  return config;
}

module.exports = withDaashWidgets;
module.exports.addAppGroup = addAppGroup;
module.exports.swiftTokens = swiftTokens;
module.exports.kotlinTokens = kotlinTokens;
module.exports.APP_GROUP = APP_GROUP;

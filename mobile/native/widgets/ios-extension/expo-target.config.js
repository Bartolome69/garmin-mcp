// UNVERIFIED (docs/native-app/DECISIONS.md, milestone 10). The widget extension
// target, in the format of @bacons/apple-targets (not installed: adding it is the
// owner's call, with the device build). Copy this folder to mobile/targets/widgets/
// and add "@bacons/apple-targets" to app.json's plugins.
/** @type {import('@bacons/apple-targets/app.plugin').ConfigFunction} */
module.exports = () => ({
  type: 'widget',
  name: 'DaashWidgets',
  deploymentTarget: '17.0',
  entitlements: { 'com.apple.security.application-groups': ['group.run.daash.app'] },
  frameworks: ['SwiftUI', 'WidgetKit'],
});

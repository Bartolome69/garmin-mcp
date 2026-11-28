// The app must start and work in the store Expo Go app (SDK 58): every native module it
// loads is one Expo Go ships, and anything else is loaded optionally and falls back to its
// fake (DECISIONS, milestone 12; docs/native-app/EXPO_GO.md). This scans package.json and
// every import under src/ against the list below. A new dependency that isn't on it fails
// here: add it only if Expo Go ships it, or load it through requireOptionalNativeModule.
import { describe, expect, it } from '@jest/globals';
import fs from 'fs';
import path from 'path';

const ROOT = path.join(__dirname, '..');
const read = (p: string) => fs.readFileSync(path.join(ROOT, p), 'utf8');

// What Expo Go (SDK 58) ships natively, or packages that are JavaScript only. Each expo-*
// package by name, not by prefix: community expo-* modules (expo-iap, say) need a
// development build, so adding one has to be a decision made here. The Google Fonts
// packages are only font files.
const EXPO_GO: (string | RegExp)[] = [
  'expo', 'expo-asset', 'expo-constants', 'expo-font', 'expo-linking', 'expo-notifications', 'expo-router',
  'expo-secure-store', 'expo-splash-screen', 'expo-sqlite', 'expo-status-bar', 'expo-symbols', 'expo-system-ui',
  'expo-updates', '@expo/metro-runtime', /^@expo-google-fonts\//,
  'react', 'react-dom', 'react-native', 'react-native-web',
  'react-native-svg', 'react-native-reanimated', 'react-native-worklets', 'react-native-gesture-handler',
  'react-native-screens', 'react-native-safe-area-context', '@react-native-segmented-control/segmented-control',
];
// Native modules the app's own code may ask for, always through requireOptionalNativeModule
// with a fallback: the widget bridge (mobile/native/widgets, needs the App Store / Play build).
const OPTIONAL_NATIVE: Record<string, string> = { DaashWidgets: 'src/features/widgets/bridge.ts' };

const allowed = (name: string) => EXPO_GO.some((a) => (typeof a === 'string' ? a === name : a.test(name)));
const pkgName = (spec: string) => (spec.startsWith('@') ? spec.split('/').slice(0, 2).join('/') : spec.split('/')[0]);

function sources(dir = 'src'): string[] {
  return fs.readdirSync(path.join(ROOT, dir), { withFileTypes: true }).flatMap((e) => {
    const p = `${dir}/${e.name}`;
    if (e.isDirectory()) return e.name === 'testing' ? [] : sources(p);
    return /\.(ts|tsx)$/.test(e.name) && !/\.test\.(ts|tsx)$/.test(e.name) ? [p] : [];
  });
}
const SPEC = /(?:from\s+|import\s*\(\s*|require\(\s*|^import\s+)['"]([^'"]+)['"]/gm;
// The code, without comments and without type-only imports (erased at build time).
const code = (text: string) => text
  .replace(/\/\*[\s\S]*?\*\//g, '')
  .replace(/^\s*\/\/.*$/gm, '')
  .replace(/^import type [^;]+;/gm, '');

describe('Expo Go', () => {
  it('a community expo-* module or another @expo/ package is not let through by its name', () => {
    expect(['expo-iap', 'expo-dev-client', '@expo/vector-icons-pro'].filter(allowed)).toEqual([]);
  });

  it('every dependency in package.json is one Expo Go ships (or JavaScript only)', () => {
    const deps = Object.keys(JSON.parse(read('package.json')).dependencies);
    expect(deps.filter((d) => !allowed(d))).toEqual([]);
    expect(deps).toContain('expo-updates');
  });

  it('every SDK package is at the version Expo Go (SDK 58) expects, as `npx expo start` checks', () => {
    // semver comes with Expo's own tooling; bundledNativeModules.json is the list `expo install` follows.
    const semver = require('semver') as { minVersion: (r: string) => { version: string } | null; satisfies: (v: string, r: string) => boolean };
    const bundled = JSON.parse(fs.readFileSync(require.resolve('expo/bundledNativeModules.json'), 'utf8')) as Record<string, string>;
    const pkg = JSON.parse(read('package.json'));
    const off = Object.entries<string>({ ...pkg.dependencies, ...pkg.devDependencies })
      .filter(([name, spec]) => bundled[name] && !semver.satisfies(semver.minVersion(spec)!.version, bundled[name]))
      .map(([name, spec]) => `${name} ${spec} (Expo Go expects ${bundled[name]})`);
    expect(off).toEqual([]);
  });

  it('every package imported under src/ is on the list', () => {
    const bad: string[] = [];
    for (const file of sources()) {
      for (const m of code(read(file)).matchAll(SPEC)) {
        const spec = m[1];
        if (spec.startsWith('.')) continue;
        if (!allowed(pkgName(spec))) bad.push(`${file}: ${spec}`);
      }
    }
    expect(bad).toEqual([]);
  });

  it('native modules outside Expo Go are only ever loaded optionally, each with its fallback', () => {
    const strict: string[] = [];
    const optional: string[] = [];
    for (const file of sources()) {
      const text = read(file);
      if (/\brequireNativeModule\s*[<(]|TurboModuleRegistry\.getEnforcing|NativeModules\.\w+/.test(text)) strict.push(file);
      for (const m of text.matchAll(/requireOptionalNativeModule(?:<[^>]*>)?\(\s*['"](\w+)['"]/g)) optional.push(`${m[1]} ${file}`);
    }
    expect(strict).toEqual([]);
    expect(optional).toEqual(Object.entries(OPTIONAL_NATIVE).map(([n, f]) => `${n} ${f}`));
    // The bridge falls back to writing nowhere, and the store to its fake or the unavailable one: no purchase library.
    expect(read('src/features/widgets/bridge.ts')).toMatch(/native \? new NativeWidgetBridge\(native\) : nothing/);
    expect(read('src/features/plus/store.ts')).toMatch(/new FakeStore\(\) : new UnavailableStore\(\)/);
  });

  it('push tokens are asked for in one place, never in Expo Go (no remote push on Android there)', () => {
    const askers = sources().filter((f) => /get(Expo|Device)PushTokenAsync|addPushTokenListener/.test(read(f)));
    expect(askers).toEqual(['src/features/settings/push.ts']);
    expect(read('src/features/settings/push.ts')).toMatch(/if \(expoGo\) return null;/);
  });

  it('app.json loads EAS updates in Expo Go (runtimeVersion by appVersion, the project’s update URL)', () => {
    const { expo } = JSON.parse(read('app.json'));
    expect(expo.slug).toBe('daash');
    expect(expo.owner).toBe('bartolome69s-team');
    expect(expo.runtimeVersion).toEqual({ policy: 'appVersion' });
    expect(expo.updates.url).toBe('https://u.expo.dev/5ff0354e-22b6-417a-b6f3-9fc7e7278439');
    expect(expo.extra.eas.projectId).toBe('5ff0354e-22b6-417a-b6f3-9fc7e7278439');
    // Config plugins don't run in Expo Go; every one listed must be a package on the list (no native code of our own).
    const plugins = (expo.plugins as (string | [string, unknown])[]).map((p) => (Array.isArray(p) ? p[0] : p));
    expect(plugins.filter((p) => !allowed(pkgName(p)))).toEqual([]);
  });

  it('eas.json and the workflow publish the preview branch on each push', () => {
    const eas = JSON.parse(read('eas.json'));
    expect(eas.cli).toEqual({ version: '>= 16.0.0', appVersionSource: 'local' });
    expect(eas.build.preview).toEqual({ distribution: 'internal', channel: 'preview', android: { buildType: 'apk' } });
    expect(eas.build.development.developmentClient).toBe(true);
    const wf = read('.eas/workflows/expo-go-preview.yml');
    expect(wf).toMatch(/branches: \['claude\/native-app-m12'\]/);
    expect(wf).toMatch(/type: update/);
    expect(wf).toMatch(/branch: preview/);
    expect(wf).toMatch(/platform: all/);
  });

  it('documents the sample data switch', () => {
    expect(read('.env.example')).toMatch(/^EXPO_PUBLIC_DAASH_FIXTURE=1$/m);
  });
});

# Running Daash in Expo Go

The native app runs in the store **Expo Go** app (SDK 58) on a phone, end to end, on
sample data. Purchases, widgets and remote push need a development build (below).

## From your phone (no computer)

Once it's set up, each push to `claude/native-app-m12` publishes an update for Expo Go,
through the Expo workflow in `mobile/.eas/workflows/expo-go-preview.yml`.

- **One-time owner step:** on expo.dev, open the project's GitHub settings, link this
  repository and set the base directory to `mobile/`. Until that's done, pushes publish
  nothing.
- **Not yet seen to run:** the workflow was written in a container that can't reach
  expo.dev, so no run of it has been checked. Look at the first run in the **Workflows**
  tab after linking; if it fails, the computer route below still works.

1. On expo.dev, open the project, then **Updates**.
2. Open the latest update on branch **preview**, and open it in Expo Go (or scan its QR
   code from another screen: the Camera app on iOS, Expo Go itself on Android).
3. The **Workflows** tab shows each run; a new push publishes a new update in a few
   minutes. Close and reopen the app in Expo Go to load the newest one.

The workflow publishes with `EXPO_PUBLIC_DAASH_FIXTURE=1`, so every update on **preview**
runs on sample data, in Expo Go and in the preview APK alike.

## Android preview APK (installs without Expo Go)

The `preview` profile in `mobile/eas.json` builds an APK on the `preview` channel, with
`EXPO_PUBLIC_DAASH_FIXTURE=1` (the APK has no working sign-in yet). It loads the same updates
as Expo Go, so later pushes reach it without a new build.

```sh
cd mobile
eas login
eas build --platform android --profile preview --non-interactive --no-wait
```

EAS makes an Android keystore on the first build. When the build finishes, open its page on
expo.dev on the phone and install the APK (Android asks to allow installs from the browser).
`eas build:list --limit 1` shows the latest build's status.

## From a computer

```sh
cd mobile
npm install
npx expo start            # add --tunnel if the phone isn't on the same Wi-Fi
```

Scan the QR code with Expo Go (Android) or the Camera app (iOS). The dev server bundle is
a development build of the JavaScript (`__DEV__`), so it also falls back to the fixtures.

## Sample data

The native endpoints are mostly `TODO(server)` and native sign-in can't hand the app a
token yet, so in Expo Go the app runs on the fixtures (the London persona):

- **On** when the bundle was made with `EXPO_PUBLIC_DAASH_FIXTURE=1`, or when running in
  Expo Go with no signed-in token on the phone at launch (the default for the Expo Go
  preview).
- **Off** when a token is on the phone at launch (the real sign-in and data paths, as in
  a store build), or outside Expo Go without the switch.
- To switch it on for a local run: copy `mobile/.env.example` to `mobile/.env` (it's
  git-ignored), keep `EXPO_PUBLIC_DAASH_FIXTURE=1`, and restart `npx expo start -c`.
  Remove the line (or set it to anything else) to switch it off.

With sample data on:

- Today (in the kicker row, where the time it was fetched would go) and You show a small
  neutral **Sample data** label, so test numbers are never mistaken for real ones;
- the first screen goes straight to Today (no sign-in wall); You has **Try onboarding**
  to walk through onboarding on the persona's answers;
- the Coach is on and talks only to its stand-in (the fake client), never the server's
  model, even when a token is on the phone (`features/coach/source.ts`);
- sending to the watch goes to the stand-in Garmin (`FakeGarmin`): nothing reaches a real
  watch, but every send state (sending, on the watch, failed and Resend) shows;
- Plus uses the fake store: the free week starts without paying anything.

## What works in Expo Go, and what needs a development build

| Works in Expo Go | Needs a development build |
|---|---|
| Every screen and flow on sample data: Today, Plan, post-run, the builder, the diff and watch confirm, Connections, onboarding, the paywall, the Coach | **Purchases**: no in-app purchase library is installed yet (`features/plus/store.ts`); Expo Go uses `FakeStore` |
| Light and dark, large text, VoiceOver and TalkBack | **Widgets**: the `DaashWidgets` module and the WidgetKit / Glance targets (`mobile/native/widgets/`); the widget bridge loads it optionally and writes nowhere without it; the setup screen says so |
| The notification permission and local notifications (the trial reminder) | **Remote push**: Expo Go has none on Android since SDK 53; the app never asks for a push token in Expo Go (`features/settings/push.ts`) |
| The keyboard with the Coach's composer | `softwareKeyboardLayoutMode` and other `app.json` native settings (Expo Go uses its own) |

The next step for purchases and widgets is a development build: add `expo-dev-client`
(not installed now; `eas.json`'s `development` profile expects it), a purchase library
behind the `Store` interface, and the widgets module, then
`npx eas-cli@latest build --profile development`.

## Checks

- `src/expoGo.test.ts` fails if a dependency or an import under `src/` isn't one Expo Go
  ships (or loaded through `requireOptionalNativeModule` with a fallback).
- `npx expo export --platform ios --platform android --output-dir /tmp/expo-export-check`
  bundles both platforms with no unresolved imports (checked 28 Nov; delete the output).
- `npx expo config --type public` shows `runtimeVersion` (`appVersion` policy),
  `updates.url` and `extra.eas.projectId`, which Expo Go needs to load EAS updates.
- The Expo project's slug is assumed to be `daash`; if the project on expo.dev has another
  slug, change `slug` in `mobile/app.json`.

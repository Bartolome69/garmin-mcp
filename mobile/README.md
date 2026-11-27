# Daash mobile app

The native iOS and Android app: Expo SDK 58, TypeScript, expo-router. Built
milestone by milestone from the build prompt; what's settled is in
[docs/native-app/DECISIONS.md](../docs/native-app/DECISIONS.md), the API it
uses in [API_INVENTORY.md](../docs/native-app/API_INVENTORY.md).

```bash
npm ci
npx expo start            # a development build (native modules: not Expo Go)
npm run check             # tokens in step, colour lint, typecheck, tests on iOS and Android
EXPO_PUBLIC_DAASH_COACH=1 npx expo start   # development only: show the Coach tab
```

- **Colours** come from `garmin_mcp/app/app.css` through
  `scripts/build_tokens.py`, as the approved tokens only. Never edit
  `src/theme/tokens.*`; never write a colour literal (`npm run lint:colours`
  fails on any). Faded values come from `theme/derived.ts`.
- **Text** goes through `ui/Text` (type role, token colour, `numeric` for
  tabular figures); **icons** through `ui/Icon` (one name, SF Symbols on iOS,
  Material Symbols on Android).
- **Routes** live in `src/app/` and every file there is a route: keep tests and
  helpers outside it.
- **Local data** is SQLite (`src/db/`); add tables by adding a migration.
- `ios/` and `android/` are generated (Continuous Native Generation) and not
  committed. Install packages with `npx expo install <pkg>` where it can reach
  expo.dev, so versions match the SDK.
- The icons in `assets/` are Expo's placeholders.

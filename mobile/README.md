# Daash mobile app

The native iOS and Android app, in Expo (React Native) SDK 58. An empty shell
for now: no screens until the designs land. The plan, the API it uses and the
open questions are in [docs/native-app/](../docs/native-app/PLAN.md).

```bash
npm ci
npx expo start        # Expo Go or a development build
npm run typecheck
npm run tokens        # regenerate src/theme/tokens.* after changing garmin_mcp/app/app.css
```

- Colours come from the PWA's `garmin_mcp/app/app.css` through
  `scripts/build_tokens.py`. Never edit `src/theme/tokens.*` by hand; CI
  (`.github/workflows/mobile.yml`) fails if they drift.
- `ios/` and `android/` are generated (Continuous Native Generation) and not
  committed; configure native behaviour in `app.json` and config plugins.
- The icons in `assets/` are Expo's placeholders, to be replaced with the designs.
- Install packages with `npx expo install <pkg>` so versions match the SDK.

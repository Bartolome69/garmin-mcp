# End-to-end flows (Maestro)

The build prompt's E2E flows (section 13) for a device or simulator, with
[Maestro](https://maestro.mobile.dev). **Not run in this repo's CI**: it has no
simulator. The same four flows run on every push as Jest integration tests over
the real routes and watch queue (`src/flows.test.tsx`); keep the two in step.

Run against a development build, which shows the design's persona and sends to
a stand-in Garmin (`EXPO_PUBLIC_DAASH_WATCH=fail` makes the first send fail):

```bash
maestro test e2e/
```

| Flow | File |
|---|---|
| Today "Make it easier" → confirm → on your watch | `1-easier-on-watch.yaml` |
| Life happened → new week → confirm | `2-life-happened.yaml` |
| Send fails → Resend | `3-send-fail-resend.yaml` (needs `EXPO_PUBLIC_DAASH_WATCH=fail`) |
| Remove from watch | `4-remove-from-watch.yaml` |

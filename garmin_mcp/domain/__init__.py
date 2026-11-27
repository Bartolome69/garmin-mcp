"""Daash's deterministic engines (native app build prompt, section 6).

Pure functions: no network, no Garmin, no server state, no UI. The app, the PWA
and the Coach all read what these work out, so every number is identical with
the Coach on or off: nothing here takes a coach flag, and nothing here calls a
model. Thresholds live in config.py, not in the code paths.

Modules:
    readiness   Push / Hold / Back off from sleep, HRV and load ratio
    load        weekly load, the target band, the forward projection
    backtoback  two hard sessions on consecutive days
    adjust      Life happened: a reason becomes a list of day operations
    paces       target paces from the best recent race, Easier / Harder
    predict     race-time prediction and its 12-week trend
    dedupe      same run from two sources: keep one, hide the other
    takeaways   one observation under each post-run chart

The mobile app has TypeScript twins of backtoback and paces (and the text
parser, which lives only there); fixtures/ holds the cases both must agree on.
"""

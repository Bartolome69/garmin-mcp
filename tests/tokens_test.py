"""The native apps' tokens come from app.css and stay in step with it (scripts/build_tokens.py).

    python3 tests/tokens_test.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import build_tokens  # noqa: E402

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else f"  ({detail})"))
    if not ok:
        failures.append(name)


css = build_tokens.CSS.read_text()
tokens = build_tokens.read_tokens(css)
dark, light = tokens["colors"]["dark"], tokens["colors"]["light"]
check("every colour has a dark and a light value", set(dark) == set(light) and len(dark) > 40, str(len(dark)))
check("the PWA's own colours come across as they are",
      dark["ground"] == "#161614" and light["ground"] == "#F5F4F0" and dark["red"] == "#DD2C25"
      and dark["bandEasy"] == "rgba(140, 192, 143, 0.16)")
check("intervals is #E07AA8 dark and #B23A78 light", dark["intervals"] == "#E07AA8" and light["intervals"] == "#B23A78")
check("radius and fonts come across", tokens["radius"] == 12 and tokens["fonts"]["sans"] == "Geist")
check("the committed files are in step with app.css", build_tokens.main(["--check"]) == 0)

drifted = css.replace('  --intervals: #B23A78;\n', '  --intervals: #B23A79;\n', 1)
try:
    build_tokens.read_tokens(drifted)
    check("app.css's two light blocks drifting apart is caught", False, "no error")
except build_tokens.TokenError as exc:
    check("app.css's two light blocks drifting apart is caught", "differ" in str(exc), str(exc))

unpaired = css.replace("  --intervals: #E07AA8;\n", "  --intervals: #E07AA8;\n  --only-dark: #000000;\n", 1)
try:
    build_tokens.read_tokens(unpaired)
    check("a dark colour with no light value is caught", False, "no error")
except build_tokens.TokenError as exc:
    check("a dark colour with no light value is caught", "only-dark" in str(exc), str(exc))


def contrast(a: str, b: str) -> float:
    def lum(h: str) -> float:
        rgb = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
        return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]
    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


check("intervals reads at 4.5:1 on the ground and surface in both modes, like the other type colours",
      all(contrast(t["intervals"], t[bg]) >= 4.5 for t in (dark, light) for bg in ("ground", "surface")),
      str({m: [round(contrast(t["intervals"], t[bg]), 2) for bg in ("ground", "surface")]
           for m, t in (("dark", dark), ("light", light))}))

print()
if failures:
    print(f"{len(failures)} failed: {', '.join(failures)}")
    raise SystemExit(1)
print("all checks passed")

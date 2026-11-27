"""The native app's tokens come from app.css, match the approved table exactly, and stay in step.

The approved table is the build prompt's section 2 ("use exactly, add nothing"),
copied here by hand so neither app.css nor the generator can drift from it.

    python3 tests/tokens_test.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import build_tokens  # noqa: E402

# Section 2 of the build prompt: token -> (dark, light).
APPROVED = {
    "bg": ("#161614", "#F5F4F0"),
    "surface": ("#222220", "#FFFFFF"),
    "sunken": ("#2C2B28", "#ECEAE4"),
    "hairline": ("#3A3936", "#DEDCD5"),
    "button": ("#4A4844", "#C9C6BE"),
    "text": ("#F4F3EE", "#161614"),
    "text2": ("#E2E1DB", "#2E2D2A"),
    "muted": ("#A6A59F", "#5E5D57"),
    "brand": ("#DD2C25", "#C8251E"),
    "brandText": ("#FF6A61", "#C21F18"),
    "onBrand": ("#FFFFFF", "#FFFFFF"),
    "success": ("#8CC08F", "#2A7030"),
    "warning": ("#E8C35F", "#7F5A00"),
    "warningTint": ("#2A2520", "#FFF5DD"),
    "cEasy": ("#8CC08F", "#2A7030"),
    "cTempo": ("#E8A15F", "#9C4D0B"),
    "cInt": ("#E07AA8", "#B23A78"),
    "cLong": ("#74A3DB", "#2563A8"),
    "cStr": ("#B39DDB", "#6A47B0"),
    "cRest": ("#9A9993", "#8A8982"),
    "race": ("#FF6A61", "#C21F18"),  # = brandText
    "raceTint": ("#3A2422", "#FBE4E1"),
    "band": ("rgba(140, 192, 143, 0.16)", "rgba(42, 112, 48, 0.10)"),
}

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else f"  ({detail})"))
    if not ok:
        failures.append(name)


def norm(value: str) -> str:
    return value.replace(" ", "").upper()


css = build_tokens.CSS.read_text()
tokens = build_tokens.read_tokens(css)
dark, light = tokens["colors"]["dark"], tokens["colors"]["light"]
check("the app gets exactly the approved tokens, no more", list(dark) == list(APPROVED) and list(light) == list(APPROVED),
      str(sorted(set(dark) ^ set(APPROVED))))
wrong = {k: (dark.get(k), light.get(k)) for k, (d, lt) in APPROVED.items()
         if norm(dark.get(k, "")) != norm(d) or norm(light.get(k, "")) != norm(lt)}
check("every value matches the approved table in both modes", not wrong, str(wrong))
check("radius comes across", tokens["radius"] == 12)
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


text_tokens = ["text", "text2", "muted", "brandText", "success", "warning", "cEasy", "cTempo", "cInt", "cLong", "cStr"]
low = {f"{m}:{t}/{bg}": round(contrast(s[t], s[bg]), 2) for m, s in (("dark", dark), ("light", light))
       for t in text_tokens for bg in ("bg", "surface") if contrast(s[t], s[bg]) < 4.5}
check("every text colour reads at 4.5:1 on bg and surface in both modes", not low, str(low))
check("text on brand reads at 4.5:1 in both modes",
      all(contrast(s["onBrand"], s["brand"]) >= 4.5 for s in (dark, light)),
      str([round(contrast(s["onBrand"], s["brand"]), 2) for s in (dark, light)]))
check("warning reads on its tint in both modes",
      all(contrast(s["warning"], s["warningTint"]) >= 4.5 for s in (dark, light)),
      str([round(contrast(s["warning"], s["warningTint"]), 2) for s in (dark, light)]))
check("secondary button outline (muted) is at least 3:1 on bg and surface",
      all(contrast(s["muted"], s[bg]) >= 3 for s in (dark, light) for bg in ("bg", "surface")))

print()
if failures:
    print(f"{len(failures)} failed: {', '.join(failures)}")
    raise SystemExit(1)
print("all checks passed")

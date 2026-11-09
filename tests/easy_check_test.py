"""Was an easy run easy? Judged against the session's own target, never Garmin's zones.

Run: .venv/bin/python tests/easy_check_test.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from garmin_mcp import easy_check as ec  # noqa: E402

FAILED: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f" — {detail}" if detail and not ok else ""))
    if not ok:
        FAILED.append(name)


def laps(bpm: list[int], pace: int = 302, metres: float = 1000.0) -> list[dict]:
    return [{"distance": metres, "movingDuration": pace * metres / 1000, "averageHR": b} for b in bpm]


EASY = {"n": "16 km easy", "ty": "easy", "tg": "120–140 bpm"}
# Bart's Nov 8 run: 127 bpm average, half of it in Garmin's zone 3 (from 129 bpm), inside its target.
NOV_8 = [118, 122, 124, 124, 121, 126, 123, 125, 128, 127, 130, 132, 134, 135, 133, 137]


def main() -> int:
    print("against a heart-rate target")
    found = ec.check(EASY, laps(NOV_8), {"trainingEffectLabel": "TEMPO"})
    check("Nov 8's easy run, inside 120-140 bpm, is easy whatever its zones say",
          found and found["verdict"] == "easy" and found["over_share_pct"] == 0, str(found))
    check("and the target wins over Garmin's label", found["against"] == "its 120–140 bpm target")
    late = NOV_8[:9] + [146, 148, 149, 150, 151, 152, 153]
    found = ec.check(EASY, laps(late))
    check("seven of sixteen km over 142 bpm, to the end, is too hard, from km 10",
          found["verdict"] == "too_hard" and found["over_share_pct"] == 44 and found["from_km"] == 10
          and found["detail"] == "44% of it ran above 140 bpm, from km 10 on", str(found))
    found = ec.check(EASY, laps(NOV_8[:12] + [143, 150, 141, 139]))
    check("a few laps a beat or two over, or one bad one, is still easy",
          found["verdict"] == "easy", str(found))
    found = ec.check(EASY, laps([150, 150, 150, 150, 125, 125, 125, 125, 125, 125]))
    check("a fast start then settling is too hard, with no 'from km' as it came back",
          found["verdict"] == "too_hard" and found["from_km"] is None and found["over_share_pct"] == 40, str(found))
    strides = laps([125] * 8) + [{"distance": 100.0, "movingDuration": 20.0, "averageHR": 160}] * 4
    check("strides don't count: laps under 90 seconds are left out",
          ec.check({**EASY, "n": "8 km easy + strides"}, strides)["verdict"] == "easy")

    print("\nagainst a pace target")
    paced = {"n": "10 km easy", "ty": "easy", "tg": "5:20–5:50 /km"}
    found = ec.check(paced, laps([0] * 10, pace=290))
    check("4:50 /km all the way on a 5:20-5:50 easy run is too hard",
          found["verdict"] == "too_hard" and found["detail"] == "100% of it ran quicker than 5:20 /km"
          and found["against"] == "its 5:20–5:50 /km target", str(found))
    check("5:15 /km, inside the slack, is easy", ec.check(paced, laps([0] * 10, pace=315))["verdict"] == "easy")

    print("\nwithout a target")
    plain = {"n": "Easy 8k", "ty": "easy"}
    found = ec.check(plain, laps([150] * 8), {"trainingEffectLabel": "TEMPO"})
    check("Garmin rating it tempo makes it too hard, saying so",
          found["verdict"] == "too_hard" and "tempo" in found["detail"], str(found))
    check("Garmin rating it base is easy",
          ec.check(plain, laps([150] * 8), {"trainingEffectLabel": "AEROBIC_BASE"})["verdict"] == "easy")
    check("nothing to judge by, no verdict: zones are never used",
          ec.check(plain, laps([170] * 8), {"hrTimeInZone_5": 3000}) is None)

    print("\nonly for easy sessions")
    check("a threshold session, a long run or no plan gets nothing",
          ec.check({"ty": "threshold", "tg": "120–140 bpm"}, laps([170] * 8)) is None
          and ec.check({"ty": "long", "tg": "120–140 bpm"}, laps([170] * 8)) is None
          and ec.check(None, laps([170] * 8)) is None)
    check("a recovery run is checked like an easy one",
          ec.check({"ty": "recovery", "tg": "110–125 bpm"}, laps([140] * 6))["verdict"] == "too_hard")

    print("\nthe app's card")
    hard = ec.check(EASY, laps(late))
    card = ec.card(hard)
    check("a too-hard run gets a card with the figure",
          card and card["head"] == "Easy run, run harder than planned"
          and card["sub"].startswith("44% of it ran above 140 bpm, from km 10 on."), str(card))
    check("an easy one gets none", ec.card(ec.check(EASY, laps(NOV_8))) is None and ec.card(None) is None)

    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

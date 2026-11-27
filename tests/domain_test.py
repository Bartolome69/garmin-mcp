"""The deterministic engines (garmin_mcp/domain/, native app build prompt section 6).

Pure functions, so these run without Garmin, a server or a database. They cover
each engine's rule, the edge cases section 13 names (missing HRV, travel across
time zones, a DST week, a 5-day week), and that nothing depends on the Coach.

    python3 tests/domain_test.py
"""

from __future__ import annotations

import inspect
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from garmin_mcp.domain import adjust, backtoback, config, dedupe, load, paces, predict, readiness, takeaways  # noqa: E402
from garmin_mcp.domain.model import Activity, Session, mmss  # noqa: E402

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else f"  ({detail})"))
    if not ok:
        failures.append(name)


TODAY = date(2026, 11, 27)  # a Friday
LONDON = ZoneInfo("Europe/London")


def nights(value, n, today=TODAY, last=None, every=None):
    out = {today - timedelta(days=i): (every(i) if every else value) for i in range(1, n + 1)}
    if last is not None:
        out[today] = last
    return out


# -- readiness ---------------------------------------------------------------------------

def readiness_checks() -> None:
    usual_sleep = nights(450, 28, last=440)
    usual_hrv = nights(None, 60, last=58, every=lambda i: 52 + i % 12)
    steady = nights(60, 28)
    r = readiness.decide(today=TODAY, sleep=usual_sleep, hrv=usual_hrv, load=steady)
    check("nothing low: Push", r.decision == "push" and r.word == "Push" and r.summary == "0 of 3 below usual",
          f"{r.decision} {r.summary}")

    short = nights(450, 28, last=385)  # 65 min short
    r = readiness.decide(today=TODAY, sleep=short, hrv=usual_hrv, load=steady)
    check("an hour or more short of the usual sleep is one low signal: Hold",
          r.decision == "hold" and r.signals[0].status == "low" and r.summary == "1 of 3 below usual", r.summary)
    r = readiness.decide(today=TODAY, sleep=nights(450, 28, last=395), hrv=usual_hrv, load=steady)
    check("55 minutes short isn't", r.decision == "push", r.summary)

    low_hrv = {**usual_hrv, TODAY: 40}
    heavy = {**steady, **{TODAY - timedelta(days=i): 95 for i in range(1, 8)}}
    r = readiness.decide(today=TODAY, sleep=short, hrv=low_hrv, load=heavy, garmin_score=26)
    check("three low: Back off, with Garmin's number labelled as Garmin's",
          r.decision == "back_off" and r.word == "Back off" and r.summary == "3 of 3 below usual"
          and r.garmin_label == "Garmin readiness 26", f"{r.decision} {r.summary} {r.garmin_label}")
    r = readiness.decide(today=TODAY, sleep=short, hrv=low_hrv, load=steady)
    check("two low: Back off", r.decision == "back_off" and r.low == 2, r.summary)

    lo, hi = readiness.percentile(list(range(1, 101)), 10), readiness.percentile(list(range(1, 101)), 90)
    check("the HRV range is the 10th to 90th percentile", (round(lo, 1), round(hi, 1)) == (10.9, 90.1), str((lo, hi)))

    ratio = readiness.load_signal(heavy, TODAY, config.DEFAULT).value
    check("load ratio is the last 7 days against the last 28, ending yesterday",
          ratio == round((95 * 7 / 7) / ((95 * 7 + 60 * 21) / 28), 2), str(ratio))
    today_heavy = {**steady, TODAY: 999}
    check("today's own session doesn't count yet",
          readiness.load_signal(today_heavy, TODAY, config.DEFAULT).value == 1.0)

    # Edge: no HRV (Intervals.icu, a watch without it): decide on the rest, and say so.
    r = readiness.decide(today=TODAY, sleep=short, hrv={}, load=steady)
    check("missing HRV: decided on sleep and load, and the summary says HRV is missing",
          r.decision == "hold" and r.known == 2 and r.missing == ("HRV",)
          and r.summary == "1 of 2 below usual · HRV missing", r.summary)
    r = readiness.decide(today=TODAY, sleep={}, hrv={}, load={})
    check("nothing known: no decision, and it says so", r.decision is None and r.summary == "Not enough data yet")
    r = readiness.decide(today=TODAY, sleep=nights(450, 5, last=300), hrv=usual_hrv, load=steady)
    check("too few nights of sleep history counts as missing, not low",
          r.signals[0].status == "missing" and r.decision == "push", r.summary)

    # Edge: travelling across time zones. Nights are keyed by the local date you wake,
    # so a night in New York and the next in London are just two dates.
    travel = dict(usual_sleep)
    travel[TODAY - timedelta(days=1)] = 300   # the short night of the flight
    r = readiness.decide(today=TODAY, sleep=travel, hrv=usual_hrv, load=steady)
    check("travel: one short night in the history moves the median, not today's decision",
          r.decision == "push", r.summary)

    sig = r.signals[0]
    check("the sheet's chart is the last 14 nights, oldest first, ending today",
          len(sig.series) == 14 and sig.series[-1][0] == TODAY.isoformat()
          and sig.series[0][0] == (TODAY - timedelta(days=13)).isoformat())

    tighter = config.load_config(json.dumps({"readiness": {"sleep_short_hours": 0.5}}))
    r = readiness.decide(today=TODAY, sleep=nights(450, 28, last=410), hrv=usual_hrv, load=steady, cfg=tighter)
    check("thresholds come from config, not code", r.decision == "hold", r.summary)
    check("the readiness copy says it's the same with or without the Coach",
          readiness.SAME_EITHER_WAY == "Readiness is worked out the same way with or without the Coach. "
                                       "Not medical advice.")


# -- load and band ----------------------------------------------------------------------------

def week(start: date, spec: list[tuple[int, str, float | None, float | None]], prefix: str = "s") -> list[Session]:
    return [Session(start + timedelta(days=d), t, f"{t} {km}", km, mins, id=f"{prefix}{start.isoformat()}-{d}")
            for d, t, km, mins in spec]


def load_checks() -> None:
    check("Garmin's own load wins", load.session_load(Session(TODAY, "easy", minutes=50, load=80)) == 80)
    check("otherwise duration times the type's intensity",
          load.session_load(Session(TODAY, "tempo", minutes=40)) == 60.0
          and load.session_load(Session(TODAY, "easy", distance_km=10)) == 60.0)
    check("the band is 10% either side", load.band(400) == (360.0, 440.0))

    monday = date(2026, 11, 23)
    done = week(monday - timedelta(weeks=1), [(0, "easy", None, 60), (2, "tempo", None, 50), (6, "long", None, 120)])
    planned = (week(monday, [(5, "intervals", None, 60), (6, "long", None, 110)])
               + week(monday + timedelta(weeks=1), [(1, "easy", None, 60), (3, "tempo", None, 60), (6, "long", None, 150)]))
    intended = {monday - timedelta(weeks=1): 270, monday: 230, monday + timedelta(weeks=1): 250}
    numbers = {monday - timedelta(weeks=1): 3, monday: 4, monday + timedelta(weeks=1): 5}
    rows = {r.starts: r for r in load.chart(today=TODAY, done=done, planned=planned, intended=intended,
                                            numbers=numbers)}
    last, this, nxt = rows[monday - timedelta(weeks=1)], rows[monday], rows[monday + timedelta(weeks=1)]
    check("a done week shows what was done", last.state == "done" and last.load == 60 + 75 + 132, str(last))
    check("this week counts what's planned from today on", this.load == 102 + 121, str(this))
    check("a planned week over its band is flagged 'Trim week N'",
          nxt.over and nxt.trim == "Trim week 5" and nxt.load == 60 + 90 + 165, str(nxt))
    check("a done week over its band isn't asked to be trimmed", not last.trim)
    check("the chart runs four weeks back and five ahead", len(rows) == 10)

    # Edge: a DST week (the clocks went back on Sun 25 Oct 2026): still seven dates, one week.
    dst_monday = date(2026, 10, 19)
    dst = week(dst_monday, [(i, "easy", None, 40) for i in range(7)])
    check("DST week: seven sessions stay in one week", load.weekly_loads(dst) == {dst_monday: 280.0},
          str(load.weekly_loads(dst)))


# -- back-to-back --------------------------------------------------------------------------------

def backtoback_checks() -> None:
    monday = date(2026, 11, 23)
    plan = week(monday, [(1, "tempo", 10, 50), (3, "easy", 8, 45), (4, "intervals", 9, 50), (6, "long", 22, 120)])
    check("a plan with a day between hard sessions has no clash", backtoback.clashes(plan) == [])
    tempo = plan[0]
    found = backtoback.check_move(plan, tempo.id, monday + timedelta(days=3))
    check("moving the tempo next to the intervals warns, in words",
          len(found) == 1 and found[0].message == "Thu tempo and Fri intervals would be back to back",
          str([c.message for c in found]))
    check("a long run of 22 km isn't hard", not backtoback.is_hard(plan[3]))
    check("a long run over 25 km is",
          backtoback.is_hard(Session(TODAY, "long", distance_km=26)) and not backtoback.is_hard(Session(TODAY, "long", distance_km=25)))
    long26 = week(monday, [(5, "intervals", 9, 50), (6, "long", 28, 160)])
    check("intervals Saturday then 28 km Sunday clash", len(backtoback.clashes(long26)) == 1)
    check("a clash that's already there isn't blamed on a move elsewhere",
          backtoback.check_move(long26 + plan[2:3], plan[2].id, monday + timedelta(days=2)) == [])


# -- Life happened ---------------------------------------------------------------------------------

def plan_week(monday: date) -> list[Session]:
    return week(monday, [(0, "easy", 8, 45), (1, "intervals", 10, 55), (2, "easy", 8, 45), (3, "tempo", 10, 50),
                         (5, "easy", 6, 35), (6, "long", 18, 105)])


def adjust_checks() -> None:
    monday = date(2026, 11, 23)
    plan = plan_week(monday)
    since = monday + timedelta(days=1)  # Tue
    a = adjust.adjust(plan, "unwell", since)
    after = {o.date: o.after for o in a.ops if o.after}
    hard_in_window = [s for d, s in after.items() if since <= d < since + timedelta(days=3) and backtoback.is_hard(s)]
    check("unwell: no hard sessions for 3 days", not hard_in_window and after[since].type == "easy",
          str([(o.op, o.date, o.after and o.after.type) for o in a.ops]))
    check("unwell: this week at most 70% of planned", a.km_after <= round(a.km_before * 0.7, 1) + 0.5,
          f"{a.km_before} -> {a.km_after}")
    check("unwell: past days never change", all(o.date >= since for o in a.ops))
    check("unwell: the summary says how many change and the km", a.summary.startswith(f"{len(a.ops)} sessions change · ")
          and a.summary.endswith(f"{a.km_before:g} → {a.km_after:g} km this week"), a.summary)
    check("unwell and niggle carry the medical line; travelling doesn't",
          a.disclaimer == adjust.MEDICAL and adjust.adjust(plan, "niggle", since).disclaimer == adjust.MEDICAL
          and adjust.adjust(plan, "travelling", since).disclaimer is None)
    check("the medical line is the prompt's words",
          adjust.MEDICAL == "Daash doesn't give medical advice. If you're unwell or hurt, check with a doctor or physio.")
    check("with the Coach off the rule used is shown", a.rule.startswith("Not feeling 100%: no hard sessions for 3 days"))

    t = adjust.adjust(plan, "travelling", since)
    check("travelling: intervals become treadmill tempo, the long run stays",
          [(o.op, o.after.title) for o in t.ops] == [("replace", "Treadmill tempo")]
          and not any(o.before and o.before.type == "long" for o in t.ops), str(t.ops))

    b = adjust.adjust(plan, "busy", monday)
    kept = {o.before.id for o in b.ops}
    check("busy: the long run and one quality session stay",
          plan[5].id not in kept and (plan[1].id not in kept or plan[3].id not in kept), str(kept))
    check("busy: at most 75% of the week", b.km_after <= round(b.km_before * 0.75, 1) + 0.5, f"{b.km_before} -> {b.km_after}")

    race_plan = plan + [Session(monday + timedelta(days=13), "race", "Half", 21.1, 100, id="race")]
    r = adjust.adjust(race_plan, "race_moved", since, new_race=monday + timedelta(days=20))
    moved = [o for o in r.ops if o.after and o.after.type == "race"]
    check("race moved: the race goes on its new date", len(moved) == 1 and moved[0].date == monday + timedelta(days=20)
          and any(o.op == "remove" and o.before.id == "race" for o in r.ops), str(r.ops))

    # Every generated week keeps the back-to-back rule.
    clashy = week(monday, [(3, "tempo", 10, 50), (4, "intervals", 10, 50), (6, "long", 18, 100)], "c")
    t = adjust.adjust(clashy, "travelling", monday)
    final = {s.id: s for s in clashy}
    for o in t.ops:
        if o.after:
            final[o.after.id] = o.after
    check("a generated week never leaves two hard days in a row", backtoback.clashes(list(final.values())) == [],
          str([c.message for c in backtoback.clashes(list(final.values()))]))

    # Edge: a 5-day week (no runs Wed and Sat).
    five = week(monday, [(0, "easy", 8, 45), (1, "intervals", 10, 55), (3, "tempo", 10, 50), (4, "easy", 6, 35),
                         (6, "long", 18, 105)], "f")
    a5 = adjust.adjust(five, "unwell", monday)
    check("a 5-day week: no session is invented on a rest day", all(o.op == "replace" for o in a5.ops), str(a5.ops))
    check("an unknown reason is refused", _raises(lambda: adjust.adjust(plan, "bored", since)))
    nothing = adjust.adjust([s for s in plan if s.type in ("easy", "long")], "travelling", since)
    check("nothing to change says so", nothing.ops == () and nothing.summary == "Nothing needs to change")


def _raises(fn) -> bool:
    try:
        fn()
    except ValueError:
        return True
    return False


# -- paces, prediction ------------------------------------------------------------------------------

def pace_checks() -> None:
    p = paces.paces(10, 43 * 60 + 30, race_date=date(2026, 10, 12))
    check("the basis names the race, its time and its date", p.basis == "Based on your 10K of 43:30 (12 Oct)", p.basis)
    z = p.zones
    check("the 10K zone is the race's own pace, ±1%", z["10k"] == (258, 264), str(z["10k"]))
    check("zones run fast to slow: VO2, 5K, 10K, threshold, tempo, marathon, steady, easy",
          z["vo2"][0] < z["5k"][0] < z["10k"][0] < z["threshold"][0] < z["tempo"][0] < z["marathon"][0]
          < z["steady"][0] < z["easy"][0], str(z))
    harder, easier = paces.paces(10, 2610, step=1), paces.paces(10, 2610, step=-1)
    check("Harder is 2% faster, Easier 2% slower",
          harder.zones["10k"] == (round(z["10k"][0] * 0.98 / 1) if False else round(261 * 0.99 * 0.98), round(261 * 1.01 * 0.98))
          and easier.zones["10k"] == (round(261 * 0.99 * 1.02), round(261 * 1.01 * 1.02)),
          f"{harder.zones['10k']} {easier.zones['10k']}")
    d = paces.default_paces()
    check("before a watch is connected: a 10K of 45:00", d.basis == "Based on your 10K of 45:00", d.basis)
    check("a half marathon is named", paces.paces(21.0975, 5400).basis == "Based on your half marathon of 1:30:00")
    check("mm:ss and h:mm:ss", mmss(2610) == "43:30" and mmss(11700) == "3:15:00")

    races = [predict.Race(date(2026, 10, 12), 10, 2610), predict.Race(date(2026, 7, 1), 10, 2500)]
    pr = predict.predict(races, 42.195, TODAY)
    check("prediction: the best race of the last 12 weeks, carried to the goal distance",
          pr.basis == "race" and pr.from_race == races[0] and pr.seconds == round(2610 * 4.2195 ** 1.06), str(pr))
    th = predict.predict([], 42.195, TODAY, threshold_s_per_km=265)
    check("with no recent race, threshold stands in", th.basis == "threshold" and th.seconds and th.seconds > 10000, str(th))
    check("with neither, no number", predict.predict([], 42.195, TODAY) == predict.Prediction(None, "none", None))
    tr = predict.trend(races, 42.195, TODAY)
    check("the trend is 12 weeks, ending this week", len(tr) == 12 and tr[-1][0] == TODAY, str(tr[-1]))
    by_week = dict(tr)
    july = round(2500 * 4.2195 ** 1.06)
    check("each week uses the races of its own 12-week window: July's race early, none after it ages out, "
          "October's once run",
          tr[0][1] == july and by_week[date(2026, 9, 27)] is None and by_week[date(2026, 10, 11)] is None
          and by_week[date(2026, 10, 18)] == pr.seconds and tr[-1][1] == pr.seconds, str(tr))


# -- de-duplication ------------------------------------------------------------------------------------

def dedupe_checks() -> None:
    start = datetime(2026, 11, 26, 7, 5, tzinfo=LONDON)
    garmin = Activity("g1", "garmin", start, 10000, 2700)
    intervals = Activity("i1", "intervals", start + timedelta(seconds=40), 10150, 2690)
    other = Activity("i2", "intervals", start + timedelta(hours=9), 5000, 1500)
    shown, records = dedupe.merge([intervals, garmin, other], ["garmin", "intervals"])
    check("same start within a minute and distance within 2%: the higher priority source stays",
          [a.id for a in shown] == ["g1", "i2"] and records[0].kept == "g1" and records[0].hidden == "i1",
          str([a.id for a in shown]))
    check("the merge record says why", records[0].reason == "Started 40 s apart, distances 1.5% apart", records[0].reason)
    shown, _ = dedupe.merge([intervals, garmin], ["intervals", "garmin"])
    check("priority decides which source stays", [a.id for a in shown] == ["i1"])
    far = Activity("i3", "intervals", start + timedelta(seconds=61), 10000, 2700)
    check("61 seconds apart are different runs", len(dedupe.merge([garmin, far], ["garmin", "intervals"])[0]) == 2)
    longer = Activity("i4", "intervals", start, 10300, 2700)
    check("3% longer is a different run", len(dedupe.merge([garmin, longer], ["garmin", "intervals"])[0]) == 2)
    shown, records = dedupe.merge([garmin, intervals], ["garmin", "intervals"], not_same=[frozenset(("g1", "i1"))])
    check("'These are different runs' keeps both", len(shown) == 2 and not records)
    # Edge: across time zones and DST. The same instant written in two offsets still matches.
    utc_copy = Activity("i5", "intervals", start.astimezone(timezone.utc) + timedelta(seconds=10), 10000, 2700)
    check("the same run recorded in UTC and in London time still matches",
          len(dedupe.merge([garmin, utc_copy], ["garmin", "intervals"])[0]) == 1)
    before_change = datetime(2026, 10, 25, 1, 59, 50, tzinfo=timezone(timedelta(hours=1)))
    after_change = datetime(2026, 10, 25, 1, 0, 20, tzinfo=timezone.utc)  # 30 s later, after the clocks went back
    a = Activity("g2", "garmin", before_change, 8000, 2400)
    b = Activity("i6", "intervals", after_change, 8050, 2400)
    check("either side of the clocks going back, 30 s apart still matches",
          len(dedupe.merge([a, b], ["garmin", "intervals"])[0]) == 1)
    check("two runs from the same source are never merged",
          len(dedupe.merge([garmin, Activity("g3", "garmin", start, 10000, 2700)], ["garmin"])[0]) == 2)


# -- takeaways ---------------------------------------------------------------------------------------------

def takeaway_checks() -> None:
    check("even reps", takeaways.reps([240, 243, 238, 244, 241, 242]) == "Even reps. Fastest and slowest 6 s apart.")
    check("a fade, in numbers", takeaways.reps([238, 240, 244, 249, 252]) == "Last rep 14 s/km slower than the first.")
    check("negative split", takeaways.splits([300, 300, 298, 296, 290, 288, 286, 284])
          == "Negative split. Second half 12 s/km faster.")
    check("even pace", takeaways.splits([300, 301, 299, 300, 300, 301]).startswith("Even pace."))
    check("HR drift at the same pace", takeaways.hr_drift([300] * 6, [140, 141, 142, 144, 145, 146])
          == "HR drifted 4 bpm at the same pace.")
    check("no drift claim when the pace changed", takeaways.hr_drift([300, 300, 300, 270, 270, 270], [140] * 3 + [150] * 3) is None)
    check("cadence drop", takeaways.cadence([176, 176, 175, 174, 171, 171]) == "Cadence dropped 5 spm in the last third.")
    words = " ".join(filter(None, [takeaways.reps([240, 260]), takeaways.splits([300, 310, 320, 330]),
                                   takeaways.hr_drift([300] * 4, [140, 140, 150, 150]), takeaways.cadence([170, 170, 160])]))
    check("takeaways state observations, never causes",
          not any(w in words.lower() for w in ("because", "due to", "sleep", "tired", "fatigue", "in line with")), words)


# -- config and Coach parity ---------------------------------------------------------------------------------

def config_checks() -> None:
    check("bad config JSON keeps the defaults", config.load_config("{not json") == config.DEFAULT)
    c = config.load_config(json.dumps({"merge": {"start_within_seconds": 90, "nope": 1}, "readiness": {"hrv_low_percentile": "x"}}))
    check("overrides apply, unknown or mistyped keys are ignored",
          c.merge.start_within_seconds == 90 and c.readiness.hrv_low_percentile == 10.0)
    flags_defaults = {"sleepShortHours": 1, "loadRatioLow": 0.8, "loadRatioHigh": 1.3, "hrvLowPercentile": 10,
                      "startWithinSeconds": 60, "distanceWithinPct": 2}
    r, m = config.DEFAULT.readiness, config.DEFAULT.merge
    check("the server's defaults match the app's flag defaults",
          (r.sleep_short_hours, r.load_ratio_low, r.load_ratio_high, r.hrv_low_percentile, m.start_within_seconds,
           m.distance_within_pct) == tuple(flags_defaults.values()))
    modules = (readiness, load, backtoback, adjust, paces, predict, dedupe, takeaways)
    params = [p for mod in modules for _, fn in inspect.getmembers(mod, inspect.isfunction)
              if fn.__module__ == mod.__name__ for p in inspect.signature(fn).parameters]
    source = " ".join(inspect.getsource(mod) for mod in modules)
    check("no engine takes a coach flag or touches the network or a model",
          not any("coach" in p.lower() for p in params)
          and not any(w in source for w in ("urllib", "httpx", "requests", "server_mod", "anthropic", "openai")))


def main() -> int:
    readiness_checks()
    load_checks()
    backtoback_checks()
    adjust_checks()
    pace_checks()
    dedupe_checks()
    takeaway_checks()
    config_checks()
    print()
    if failures:
        print(f"{len(failures)} failed: {', '.join(failures)}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

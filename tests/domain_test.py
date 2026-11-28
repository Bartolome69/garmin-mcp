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

from garmin_mcp.domain import adjust, backtoback, coach_guard, config, connections, dedupe, load, onboarding, paces, postrun, predict, proposal, readiness, takeaways  # noqa: E402
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


def today_and_options_checks() -> None:
    monday = date(2026, 11, 23)
    plan = plan_week(monday)
    friday = monday + timedelta(days=4)
    # Started on Tuesday, told on Thursday: the window counts from Tuesday, but Tuesday and Wednesday are done.
    a = adjust.adjust(plan, "unwell", monday + timedelta(days=1), today=monday + timedelta(days=3))
    check("since days ago: done days never change", all(o.date >= monday + timedelta(days=3) for o in a.ops),
          str([(o.op, o.date) for o in a.ops]))
    check("since days ago: the rule's window still counts from when it started (Thu's tempo becomes easy)",
          any(o.date == monday + timedelta(days=3) and o.after and o.after.type == "easy" for o in a.ops))
    check("a hard session made easy says its size", a.ops[0].after.title == f"Easy {a.ops[0].after.distance_km:g} km",
          a.ops[0].after.title)
    check("a scaled run's title follows its distance; a title without one stays",
          adjust._scaled_title("Long 18 km easy", 18, 9) == "Long 9 km easy"
          and adjust._scaled_title("Hill reps", 10, 8) == "Hill reps")

    tempo_day = monday + timedelta(days=3)
    e = adjust.easier(plan, tempo_day)
    check("make it easier: only today's hard session changes, to an easy run",
          [(o.op, o.date, o.after.type) for o in e.ops] == [("replace", tempo_day, "easy")], str(e.ops))
    check("make it easier on an easy day changes nothing", adjust.easier(plan, monday).ops == ())
    r = adjust.rest(plan, tempo_day)
    check("rest and recover: today's session comes off, nothing else",
          [(o.op, o.date) for o in r.ops] == [("remove", tempo_day)] and "counts toward your active week" in r.rule)
    sw = adjust.swap(plan, tempo_day)
    moved = {(o.op, o.before.id if o.before else o.after.id, o.date) for o in sw.ops}
    check("swap a day: today's session and a later easy one trade days", len(sw.ops) == 4
          and ("add", plan[3].id, monday + timedelta(days=5)) in moved and sw.summary.startswith("2 sessions change"),
          str(moved))
    check("swap a day never leaves two hard days in a row",
          not backtoback.clashes(list(proposal._apply(plan, sw))))
    t = adjust.trim(plan, monday, 40, monday, number=4)
    check("trim: the easy runs come down to the limit; the long run and quality sessions stay",
          t.km_after <= 40.5 and not any(o.before.type in ("long", "intervals", "tempo") for o in t.ops),
          f"{t.km_before} -> {t.km_after}: {[(o.before.title, o.after.title) for o in t.ops]}")

    v = proposal.view(adjust.adjust(plan, "unwell", monday, today=monday), plan, monday, since=monday,
                      numbers={monday: 4, monday + timedelta(weeks=1): 5})
    check("the view: kicker, title in sessions, km subtitle", v["kicker"] == "Not feeling 100% · since today"
          and v["title"].endswith(("sessions change", "session changes"))
          and v["subtitle"].endswith("km this week. Next week unchanged."), f"{v['kicker']} | {v['title']} | {v['subtitle']}")
    check("the view: every op names its day and session; a replace says what with",
          all(o["dayLabel"] and o["what"] for o in v["ops"])
          and all(o["withWhat"] for o in v["ops"] if o["kind"] == "replace"))
    check("the view: unchanged weeks have identical before and after", all(l["before"] == l["after"] for l in v["load"]
                                                                          if not l["changed"]) and v["load"][0]["changed"])
    check("the view: no week drawn with nothing planned either side", all(l["before"] or l["after"] for l in v["load"]))
    check("the view: rows run in date order, changed ones marked",
          [r["date"] for r in v["days"]] == sorted(r["date"] for r in v["days"]) and any(r["changed"] for r in v["days"]))
    empty = proposal.view(adjust.easier(plan, monday), plan, monday)
    check("the view: nothing to change has no ops and no chart", empty["ops"] == [] and empty["load"] is None
          and empty["title"] == "Nothing needs to change")


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

def postrun_checks() -> None:
    import importlib.util
    spec = importlib.util.spec_from_file_location("domain_fixtures", ROOT / "scripts" / "domain_fixtures.py")
    fx = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fx)
    runs = fx.postrun_cases()
    v = runs["2026-11-25-intervals"]
    check("post-run: planned vs actual, each row marked",
          v["planned"] == [{"label": "Distance", "plan": "11.0 km", "done": "11.2 km", "ok": True},
                           {"label": "Rep pace", "plan": "3:58–4:08", "done": "4:01 avg", "ok": True},
                           {"label": "Reps", "plan": "6 × 800 m", "done": "6 of 6", "ok": True}], str(v["planned"]))
    check("post-run: the plan fixture's numbers", v["totalKm"] == 11.2 and v["when"] == "Wed 25 Nov · 06:30")
    check("post-run: one takeaway under each chart, an observation",
          [v["charts"][k]["takeaway"]["text"] for k in ("pace", "hr")]
          == ["Even reps. Fastest and slowest 6 s apart.", "HR drifted 4 bpm across the 6 reps."])
    check("post-run: takeaways split for drawing, with a tone",
          v["charts"]["pace"]["takeaway"] | {} == {"bold": "Even reps.", "rest": "Fastest and slowest 6 s apart.",
                                                   "text": "Even reps. Fastest and slowest 6 s apart.", "tone": "good"}
          and v["charts"]["hr"]["takeaway"]["tone"] == "note")
    lens = {k: len(x) for k, x in v["series"].items()}
    check("post-run: every metric on the same points, so one scrub line reads them all",
          set(lens.values()) == {postrun.POINTS}, str(lens))
    check("post-run: laps by distance, reps numbered",
          [lap["label"] for lap in v["laps"]][:4] == ["Warm-up", "Rep 1", "Recovery", "Rep 2"] and len(v["laps"]) == 13
          and v["laps"][-1]["toKm"] == 11.2)
    route = v["route"]["points"]
    check("post-run: the route fits a unit box", 2 < len(route) <= postrun.ROUTE_POINTS + 1
          and all(0 <= x <= 1 and 0 <= y <= 1 for x, y in route))
    check("post-run: zones in minutes, bars relative to the longest",
          [z["label"] for z in v["zones"]] == ["Z1", "Z2", "Z3", "Z4", "Z5"] and max(z["share"] for z in v["zones"]) == 1.0)
    check("post-run: pace axis is faster-up, on round values",
          v["charts"]["pace"]["inverted"] and [t["label"] for t in v["charts"]["pace"]["domain"]["ticks"]] == ["4:00", "6:00"])
    check("post-run: the scrub line starts on the rep nearest halfway", v["scrubKm"] == 6.0, str(v["scrubKm"]))
    easy = runs["2026-11-24-easy"]
    check("post-run: strides are their own laps, not reps, and the run's pace leaves them out",
          [lap["label"] for lap in easy["laps"]][:3] == ["Run", "Stride 1", "Recovery"]
          and easy["planned"][1]["label"] == "Pace" and easy["planned"][1]["done"] == "5:12 avg"
          and easy["charts"]["pace"]["takeaway"]["text"].startswith("Even pace."))
    club = runs["2026-11-26-easy"]
    check("post-run: an easy run faster than its range is marked off plan",
          club["planned"][1] == {"label": "Pace", "plan": "5:27–6:10", "done": "5:14 avg", "ok": False})
    check("post-run: a steady run's takeaways are splits and drift",
          club["charts"]["pace"]["takeaway"]["text"].startswith("Negative split.")
          and club["charts"]["hr"]["takeaway"]["text"].startswith("HR drifted"))
    bare = postrun.Run(id="x", start=datetime(2026, 11, 20, 7, 0), type="easy", title="Easy 5 km",
                       samples=[postrun.Sample(t=i * 3.0, d=i * 10.0) for i in range(1, 501)])
    b = postrun.view(bare)
    check("post-run: no HR, cadence, elevation or GPS: said, not drawn",
          b["charts"]["hr"]["takeaway"] is None and b["charts"]["hr"]["summary"] == "No heart rate recorded"
          and b["route"] is None and b["planned"] is None and b["zones"] is None)
    still = postrun.Run(id="y", start=datetime(2026, 11, 20, 7, 0), type="easy", title="x",
                        samples=[postrun.Sample(t=i * 3.0, d=min(i, 200) * 10.0 + max(0, i - 260) * 10.0) for i in range(1, 501)])
    check("post-run: standing still reads as the slowest pace, not infinity",
          max(postrun.view(still)["series"]["pace"]) == postrun.SLOWEST_PACE)
    check("rep HR drift", takeaways.rep_hr([160, 161, 162, 165, 166, 166]) == "HR drifted 5 bpm across the 6 reps.")
    check("rep HR steady", takeaways.rep_hr([160, 161, 161, 161]) == "HR steady across the 4 reps.")
    check("elevation", takeaways.elevation(48.2, 46.0) == "48 m up, 46 m down."
          and takeaways.elevation(4, 6) == "Flat. Under 15 m of climbing.")


def connections_checks() -> None:
    import importlib.util
    spec = importlib.util.spec_from_file_location("domain_fixtures", ROOT / "scripts" / "domain_fixtures.py")
    fx = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fx)
    v = fx.connections_cases()["ios"]
    words = {p["key"]: p["status"] for p in v["providers"]}
    check("connections: each state in words",
          words == {"garmin": "Connected to Garmin · Forerunner 965 · synced 07:02",
                    "intervals": "Reconnect needed · last synced Tue", "health": "Reads only · synced 06:58"}, str(words))
    check("connections: capability chips, each on or off",
          [(c["label"], c["on"]) for c in v["providers"][1]["caps"]] == [("Activities", True), ("Sleep", False), ("Send workouts", False)])
    check("connections: only a provider needing it has a reconnect link",
          [p["reconnectUrl"] for p in v["providers"]] == [None, "/app/connect/intervals", None])
    check("connections: two runs that came twice are merged, the higher source kept",
          [(m["kept"], m["hidden"], m["title"]) for m in v["merges"]]
          == [("g-1126", "h-1126", "Thu 26 · 9.2 km"), ("g-1122", "i-1122", "Sun 22 · 16.0 km")])
    check("connections: a UTC copy of a London run is the same instant", v["merges"][1]["gapSeconds"] == 20)
    again = connections.view(fx._persona_connections("ios"), ["garmin", "health", "intervals"], fx.PERSONA_RUNS, fx.NOW,
                             kept_apart=[("g-1126", "h-1126")])
    check("connections: \"These are different runs\" keeps a pair apart",
          [m["id"] for m in again["merges"]] == ["g-1122|i-1122"] and again["keptApart"] == [["g-1126", "h-1126"]])
    flipped = connections.view(fx._persona_connections("ios"), ["health", "garmin"], fx.PERSONA_RUNS, fx.NOW)
    check("connections: the order decides which is kept; unlisted sources go last",
          flipped["merges"][0]["kept"] == "h-1126" and flipped["priority"] == ["health", "garmin", "intervals"])
    old = [a for a in fx.PERSONA_RUNS if a.start < fx.NOW - timedelta(days=30)]
    check("connections: this month's runs only", all(a["start"] >= "2026-10-28" for a in v["activities"]) and not old)


def onboarding_checks() -> None:
    import importlib.util
    spec = importlib.util.spec_from_file_location("domain_fixtures", ROOT / "scripts" / "domain_fixtures.py")
    fx = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fx)
    c = fx.onboarding_cases()
    rows = {r["key"]: r for r in c["import"]["rows"]}
    check("onboarding: the import reads 12 weeks", c["import"]["title"].startswith("Found ") and c["import"]["title"].endswith("since September"))
    check("onboarding: the best recent race, and threshold from it",
          rows["race"]["value"] == "10K · 43:30" and rows["race"]["note"] == "12 Oct" and rows["threshold"]["note"] == "from your 10K")
    check("onboarding: the usual week", rows["week"]["value"] == "48 km · 5 runs", rows["week"]["value"])
    z = paces.paces(10, 2610).zones["threshold"]
    check("onboarding: threshold is the pace engine's", rows["threshold"]["value"] == f"{mmss(z[0])}–{mmss(z[1])} /km")
    q = {r["key"]: r for r in c["questions"]["rows"]}
    check("onboarding: four questions with no race use the experience's 10K and say so",
          q["race"]["value"] == "None given" and q["race"]["note"] == "using a 10K of 45:00")
    r = c["reveal"]
    check("onboarding: the reveal is the plan builder's plan",
          r["kicker"] == "Your plan · 20 weeks" and r["title"] == "London Marathon, Sat 24 Apr"
          and [p["label"] for p in r["phases"]] == ["Base", "Build", "Peak", "Taper"] and sum(p["weeks"] for p in r["phases"]) == 20)
    check("onboarding: predicted from the race, against the target", (r["predicted"], r["target"]) == ("3:20", "3:15"))
    check("onboarding: week 1 carries its workouts for the watch",
          all(s["workout"]["schema"] == "daash.workout.v1" and s["workout"]["steps"] for s in r["week1"]) and len(r["week1"]) >= 3)
    check("onboarding: no race, a fitness plan with no prediction",
          c["reveal_fit"]["title"] == "Your fitness plan" and c["reveal_fit"]["predicted"] is None)
    empty = c["import_empty"]
    er = {r["key"]: r for r in empty["rows"]}
    check("onboarding: no runs read says so and uses the default 10K",
          empty["title"] == "No runs in the last 12 weeks" and empty["basis"]["raceSeconds"] == 45 * 60 and empty["runs"] == 0)
    check("onboarding: with no runs, easy pace says where it comes from and there's no usual week",
          er["easy"]["note"] == "from the 10K above" and er["race"]["value"] == "None in 12 weeks" and "week" not in er, str(er))
    check("onboarding: the import counts the runs it read", c["import"]["runs"] == 56)
    two = [onboarding.RecentRun(TODAY - timedelta(days=d), 8, 8 * 330) for d in (3, 10)]
    thin = {r["key"]: r for r in onboarding.import_summary(two, TODAY)["rows"]}
    check("onboarding: too few easy runs to read a range uses the 10K, not a race that isn't there",
          thin["easy"]["note"] == "from the 10K above", thin["easy"]["note"])
    qr = {r["key"]: r for r in c["questions_race"]["rows"]}
    check("onboarding: a recent race in the four questions sets the paces",
          qr["race"]["value"] == "Half marathon · 1:45:00" and qr["threshold"]["note"] == "from your half marathon"
          and c["questions_race"]["basis"]["raceSeconds"] == 6300)
    check("onboarding: race names inside a sentence are in sentence case",
          onboarding._race_name(42.195, mid=True) == "marathon" and onboarding._race_name(10, mid=True) == "10K"
          and onboarding._race_name(42.195) == "Marathon")
    easy_row = rows["easy"]["value"].removesuffix(" /km")
    easy_subs = {s["sub"].split(" · ")[-1] for s in r["week1"] if s["type"] in ("easy", "long")}
    check("onboarding: the plan's easy runs use the easy pace the runner confirmed", easy_subs == {easy_row}, f"{easy_subs} vs {easy_row}")
    check("onboarding: a real race is said as the runner's own", r["because"].endswith("Prediction uses your 10K of 43:30."), r["because"])
    for key in ("reveal_empty", "reveal_questions"):
        rv = c[key]
        check(f"onboarding: {key}: no race to go on, no prediction, and it doesn't call the assumed 10K theirs",
              rv["predicted"] is None and rv["target"] == "3:15" and "your 10K" not in rv["because"]
              and "No prediction without a recent race; paces use a 10K of 45:00 for now." in rv["because"], rv["because"])
    check("onboarding: nothing imported says no '0 km weeks'",
          "0 km" not in c["reveal_empty"]["because"] and c["reveal_empty"]["because"].startswith("Phases follow from your 20 weeks."),
          c["reveal_empty"]["because"])
    check("onboarding: the import's basis carries the confirmed easy range and whether the race is assumed",
          c["import"]["basis"]["easy"] == [310, 320] and c["import"]["basis"]["assumed"] is False
          and empty["basis"]["assumed"] is True and c["questions"]["basis"]["assumed"] is True
          and c["questions_race"]["basis"]["assumed"] is False)


# -- the Coach's reply guard ----------------------------------------------------------------

def coach_guard_checks() -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    import domain_fixtures as fx  # the corpus, labelled by hand

    wrong = [t for t in fx.REPLIES_OK if coach_guard.kinds(t)]
    check(f"the corpus's {len(fx.REPLIES_OK)} good replies pass (observations, the medical line, proposals)",
          len(fx.REPLIES_OK) >= 15 and not wrong, f"flagged: {wrong}")
    wrong = [(t, coach_guard.kinds(t), label) for t, label in fx.REPLIES_BAD if coach_guard.kinds(t) != label]
    check(f"the corpus's {len(fx.REPLIES_BAD)} bad replies get exactly their hand labels",
          len(fx.REPLIES_BAD) >= 20 and not wrong, f"{wrong}")
    for kind in coach_guard.KINDS:
        check(f"the corpus has bad replies of kind {kind}", sum(kind in label for _, label in fx.REPLIES_BAD) >= 3)
    check("the build prompt's own examples: a cause flagged, the observation not",
          coach_guard.kinds("In line with your short sleep.") == ["cause"]
          and coach_guard.kinds("Rep 6 peaked at 172 bpm, 4 above rep 5.") == [])
    check("curly apostrophes read the same as straight ones",
          coach_guard.kinds("That’s why your legs felt heavy.") == coach_guard.kinds("That's why your legs felt heavy.") == ["cause"])
    found = coach_guard.check("You must rest. Because your HRV dropped, you are tired.")
    check("findings say what matched and where, in reading order",
          [(f["kind"], f["at"]) for f in found] == [("certainty", 0), ("cause", 15)] and found[0]["match"] == "You must",
          f"{found}")
    wrong = [(t, coach_guard.unmatched_numbers(t, p), label) for t, p, label in fx.REPLY_NUMBERS
             if coach_guard.unmatched_numbers(t, p) != label]
    check("every number in a reply is checked against what the model was given", not wrong, f"{wrong}")
    check("numbers are compared as written, without leading zeros or a trailing .0",
          coach_guard.numbers("Synced 07:02, 6.0 km, 4:01 /km") == ["7:02", "6", "4:01"])
    rules = json.loads((ROOT / "garmin_mcp" / "domain" / "fixtures" / "coach_replies.json").read_text())["cases"]["rules"]
    check("the fixture carries the rules the app's twin reads", rules == coach_guard.RULES)


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
    modules = (readiness, load, backtoback, adjust, paces, predict, dedupe, takeaways, proposal, postrun, connections, onboarding,
               coach_guard)
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
    today_and_options_checks()
    pace_checks()
    dedupe_checks()
    takeaway_checks()
    postrun_checks()
    connections_checks()
    onboarding_checks()
    coach_guard_checks()
    config_checks()
    print()
    if failures:
        print(f"{len(failures)} failed: {', '.join(failures)}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

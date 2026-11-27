"""A proposed change to the plan, as the app's shared diff and watch confirm draw it
(build prompt, sections 5.4 and 5.8). Pure: an Adjustment in, plain JSON out.

The same view serves every source: Life happened, Today's "Make it easier",
"Rest and recover" and "Swap a day", and "Trim week N" from Trends. The numbers
(kilometres a week, the band) come from the engines; the words are the ones the
app shows. The ops are exactly what the watch confirm lists and the app's watch
queue sends: nothing here writes anything.
"""

from __future__ import annotations

from datetime import date, timedelta

from .adjust import REASON_WORDS, Adjustment, sessions_changed
from .config import DEFAULT, DomainConfig
from .load import band
from .model import Session, monday, week_of

TYPE_WORDS = {
    "easy": "Easy", "recovery": "Recovery", "tempo": "Tempo", "intervals": "Intervals", "long": "Long",
    "race": "Race", "strength": "Strength", "rest": "Rest", "other": "Other",
}


def _day(d: date) -> str:
    return f"{d.strftime('%a')} {d.day}"


def _since_words(since: date, today: date) -> str:
    if since == today:
        return "since today"
    if since == today - timedelta(days=1):
        return "since yesterday"
    return f"since {_day(since)}"


def _km(sessions: list[Session]) -> float:
    return round(sum(s.distance_km or 0 for s in sessions), 1)


def _session(s: Session | None) -> dict | None:
    if s is None:
        return None
    label = TYPE_WORDS.get(s.type, "Other")
    size = f"{s.distance_km:g} km" if s.distance_km else f"{s.minutes:g} min" if s.minutes else None
    return {"id": s.id, "date": s.date.isoformat(), "type": s.type, "typeLabel": label, "title": s.title,
            "sub": f"{label} · {size}" if size else label, "distanceKm": s.distance_km}


def _first(sessions: list[Session], d: date) -> Session | None:
    return next((s for s in sessions if s.date == d and s.type != "rest"), None)


def view(a: Adjustment, plan: list[Session], today: date, *, since: date | None = None, focus: date | None = None,
         numbers: dict[date, int] | None = None, id: str | None = None, cfg: DomainConfig = DEFAULT) -> dict:
    """The proposal the app shows for `a`, made against `plan`.

    `focus` is the Monday of the week the change is about (this week unless
    trimming a later one); the load bars show it and the two after it.
    """
    numbers = numbers or {}
    focus = focus or monday(today)
    after = _apply(plan, a)
    changed = {o.date for o in a.ops}

    def week_label(start: date) -> str:
        if start == monday(today):
            return "This week"
        if start == monday(today) + timedelta(weeks=1):
            return "Next week"
        return f"Week {numbers[start]}" if start in numbers else start.strftime("%-d %b")

    # Rows: the changed days, and the planned days around them, faded.
    rows = []
    if changed:
        first, last = min(changed), max(changed) + timedelta(days=2)
        d = first
        while d <= last:
            b, n = _first(plan, d), _first(after, d)
            if d in changed or b or n:
                rows.append({"date": d.isoformat(), "label": f"{d.strftime('%a').upper()} {d.day}",
                             "before": _session(b), "after": _session(n), "changed": d in changed})
            d += timedelta(days=1)

    load = []
    for i in range(3):
        start = focus + timedelta(weeks=i)
        b, n = _km(week_of(plan, start)), _km(week_of(after, start))
        if not b and not n:
            continue  # nothing planned that week either way
        lo, hi = band(b, cfg) if b else (None, None)
        load.append({"label": week_label(start), "before": b, "after": n, "changed": b != n, "low": lo, "high": hi})

    n_ops = sessions_changed(a.ops)
    focus_words = "this week" if focus == monday(today) else (f"week {numbers[focus]}" if focus in numbers else "that week")
    nxt = load[1] if len(load) > 1 else {"changed": False}
    subtitle = (f"{load[0]['before']:g} → {load[0]['after']:g} km {focus_words}. "
                + ("Next week unchanged." if not nxt["changed"] else f"Next week {nxt['before']:g} → {nxt['after']:g} km.")
                if focus == monday(today) else
                f"{load[0]['before']:g} → {load[0]['after']:g} km {focus_words}.") if n_ops else "Your plan already fits."
    kicker = REASON_WORDS.get(a.reason, a.reason)
    if a.reason in ("unwell", "niggle", "travelling", "race_moved", "busy"):
        kicker += f" · {_since_words(since or today, today)}"
    elif a.reason == "trim" and focus in numbers:
        kicker = f"Trim week {numbers[focus]}"
    else:
        kicker += " · today"

    ops = []
    for o in a.ops:
        before, new = _session(o.before), _session(o.after)
        sid = (o.before or o.after).id
        ops.append({"kind": o.op, "sessionId": sid, "date": o.date.isoformat(), "dayLabel": _day(o.date),
                    "what": (before or new)["title"], "withWhat": new["title"] if o.op == "replace" else None,
                    "before": before, "after": new})

    return {
        "id": id or f"{a.reason}-{(since or today).isoformat()}",
        "source": a.reason,
        "kicker": kicker,
        "title": (f"{n_ops} session{'s' if n_ops != 1 else ''} change{'' if n_ops != 1 else 's'}"
                  if n_ops else "Nothing needs to change"),
        "subtitle": subtitle,
        "days": rows,
        "load": load if n_ops else None,
        "rule": a.rule,
        "disclaimer": a.disclaimer,
        "ops": ops,
    }


def _apply(plan: list[Session], a: Adjustment) -> list[Session]:
    out = {s.id: s for s in plan}
    for o in a.ops:
        if o.op == "remove" and o.before is not None:
            out.pop(o.before.id, None)
    for o in a.ops:
        if o.op in ("add", "replace") and o.after is not None:
            out[o.after.id] = o.after
    return sorted(out.values(), key=lambda s: (s.date, s.id))

"""The Monday email: last week in a line, this week's key session, and the way back in.

Two-thirds of people a week into Daash are still using it; the quieter third
drift off between runs. One short email on a Monday morning, from their own
Garmin, is the reminder: what they did, what's next, and a question to ask.
Built from the plan card's own answer (get_plan), so it says nothing the card
wouldn't, and sent only when there is something to say.
"""

from __future__ import annotations

import html
import re
from datetime import date, timedelta
from typing import Any

DAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
# The kinds of session worth naming as the week's key one, most important first.
KEY_TYPES = ("race", "long", "threshold", "intervals", "sub")


def _km(value: Any) -> str:
    r = round(float(value or 0), 1)
    return str(int(r)) if r == int(r) else f"{r:.1f}"


def _name(session: dict[str, Any]) -> str:
    """A session's name as it reads on the watch, without the plan code after the dot."""
    name = re.sub(r"\s*·\s*[A-Z0-9]{1,4}\s*$", "", str(session.get("name") or "").strip())
    name = re.sub(r"(\d)\s*[x×]\s*(\d)", "\\1 × \\2", name)
    return re.sub(r"(\d)(km|k|mi)\b", lambda m: f"{m.group(1)} {'km' if m.group(2) == 'k' else m.group(2)}", name) or "Session"


def _week(answer: dict[str, Any], starts: date) -> dict[str, Any] | None:
    return next((w for w in answer.get("weeks") or [] if isinstance(w, dict) and w.get("starts") == starts.isoformat()), None)


def summary(answer: Any, today: date) -> dict[str, Any] | None:
    """What Monday's email says, from a get_plan answer. None when there's nothing to say."""
    if not isinstance(answer, dict) or "error" in answer:
        return None
    monday = today - timedelta(days=today.weekday())
    last, this = _week(answer, monday - timedelta(days=7)), _week(answer, monday)
    logged = answer.get("source") == "log"
    ride = answer.get("primary") == "ride"

    last_line = None
    if last:
        km = last.get("ride_km" if ride else "run_km") or 0
        count = last.get("rides" if ride else "runs")
        planned, done = last.get("planned") or 0, last.get("done") or 0
        if not logged and planned:
            last_line = f"{done} of {planned} planned sessions done" + (f", {_km(km)} km {'ridden' if ride else 'run'}" if km else "")
            if done == planned:
                last_line = f"Every planned session done" + (f", {_km(km)} km {'ridden' if ride else 'run'}" if km else "")
        elif km:
            things = "rides" if ride else "runs"
            last_line = (f"{count} {things}, " if count else "") + f"{_km(km)} km"

    keys, planned_week = [], None
    if this and not logged:
        ahead = [s for s in this.get("sessions") or [] if isinstance(s, dict)
                 and s.get("status") in ("ahead", "today") and s.get("date", "") >= today.isoformat()]
        if ahead:
            planned_week = {"sessions": len(ahead), "km": sum(float(s.get("planned_km") or 0) for s in ahead)}
            # The hard and long ones, at most two, in the order they come.
            ranked = sorted((s for s in ahead if s.get("type") in KEY_TYPES), key=lambda s: (
                KEY_TYPES.index(s.get("type")), -float(s.get("planned_km") or 0)))[:2]
            for best in sorted(ranked, key=lambda s: s["date"]):
                day = date.fromisoformat(best["date"])
                keys.append({"day": DAY_NAMES[day.weekday()], "name": _name(best),
                             "km": _km(best["planned_km"]) if best.get("planned_km") else None})

    if not last_line and not planned_week:
        return None
    return {
        "last_week": last_line,
        "key_sessions": keys,
        "this_week": planned_week and {"sessions": planned_week["sessions"], "km": _km(planned_week["km"])},
        "no_plan": logged,
        "sport": "riding" if ride else "running",
    }


def compose(s: dict[str, Any], unsubscribe: str) -> tuple[str, str, str]:
    """(subject, plain text, html) for one person's Monday email."""
    keys = s.get("key_sessions") or []
    if keys:
        subject = f"This week: {keys[0]['name']} on {keys[0]['day']}"
    elif s.get("last_week"):
        subject = f"Last week: {s['last_week']}"
    else:
        subject = "Your week ahead"
    lines: list[str] = []
    if s.get("last_week"):
        lines.append(f"Last week: {s['last_week']}.")
    if keys:
        said = [f"{k['day']}, {k['name']}" + (f" ({k['km']} km)" if k.get("km") else "") for k in keys]
        lines.append(("This week's key sessions: " + " and ".join(said) if len(said) > 1
                      else f"This week's key session: {said[0]}") + ".")
    if s.get("this_week"):
        n = s["this_week"]["sessions"]
        lines.append(f"{n} session{'s' if n != 1 else ''} planned this week" + (f", {s['this_week']['km']} km." if s['this_week']['km'] != "0" else "."))
    if s.get("no_plan"):
        lines.append(f"Nothing planned this week. Ask Claude or ChatGPT to build you a plan from your recent {s['sport']}, "
                     "and every session goes on your watch.")
    ask = "How's my training going?"
    lines.append(f"Ask Claude or ChatGPT “{ask}” to see the whole week.")
    foot = ("You get this because you connected Daash to your Garmin. "
            f"Stop these emails: {unsubscribe}")
    text = "\n\n".join(lines) + "\n\n—\nDaash\n" + foot + "\n"
    body = "".join(f"<p style=\"margin:0 0 14px\">{html.escape(line)}</p>" for line in lines)
    page = (
        "<!doctype html><html><body style=\"margin:0;background:#F7F8FA\">"
        "<div style=\"max-width:520px;margin:0 auto;padding:28px 20px;font:16px/1.55 -apple-system,'Segoe UI',Helvetica,Arial,sans-serif;color:#161B26\">"
        "<div style=\"font-weight:900;font-style:italic;font-size:24px;color:#E0312A;margin-bottom:18px\">Daash</div>"
        f"{body}"
        "<p style=\"margin:26px 0 0;font-size:13px;color:#5B6270\">You get this because you connected Daash to your Garmin. "
        f"<a href=\"{html.escape(unsubscribe)}\" style=\"color:#5B6270\">Stop these emails</a>.</p>"
        "</div></body></html>"
    )
    return subject, text, page

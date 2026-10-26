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
from urllib.parse import quote

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


def _keys(week: dict[str, Any] | None, today: date) -> list[dict[str, Any]]:
    """A week's key sessions still to come, at most two, in the order they come."""
    ahead = [x for x in (week or {}).get("sessions") or [] if isinstance(x, dict)
             and x.get("status") in ("ahead", "today") and x.get("date", "") >= today.isoformat()]
    ranked = sorted((x for x in ahead if x.get("type") in KEY_TYPES), key=lambda x: (
        KEY_TYPES.index(x.get("type")), -float(x.get("planned_km") or 0)))[:2]
    out = []
    for best in sorted(ranked, key=lambda x: x["date"]):
        day = date.fromisoformat(best["date"])
        out.append({"date": best["date"], "day": DAY_NAMES[day.weekday()], "name": _name(best),
                    "km": _km(best["planned_km"]) if best.get("planned_km") else None})
    return out


def _block(answer: dict[str, Any]) -> dict[str, Any] | None:
    """The block under way: its short name, which week of how many, and how it's gone so far."""
    plan = answer.get("current_plan")
    if not isinstance(plan, dict):
        return None
    name = str(plan.get("name") or plan.get("goal") or "").split(":")[0].strip()
    week, total = plan.get("week"), plan.get("weeks_total")
    if not name and not week:
        return None
    return {"name": name or None, "week": week, "weeks": total, "percent": answer.get("completion_percent")}


def summary(answer: Any, today: date) -> dict[str, Any] | None:
    """What Monday's email says, from a get_plan answer. None when there's nothing to say."""
    if not isinstance(answer, dict) or "error" in answer:
        return None
    monday = today - timedelta(days=today.weekday())
    last, this = _week(answer, monday - timedelta(days=7)), _week(answer, monday)
    following = _week(answer, monday + timedelta(days=7))
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

    keys, planned_week, schedule = [], None, []
    if this and not logged:
        ahead = [x for x in this.get("sessions") or [] if isinstance(x, dict)
                 and x.get("status") in ("ahead", "today") and x.get("date", "") >= today.isoformat()]
        if ahead:
            planned_week = {"sessions": len(ahead), "km": sum(float(x.get("planned_km") or 0) for x in ahead)}
            keys = _keys(this, today)
            key_at = {(k["date"], k["name"]) for k in keys}
            # The whole week, Monday to Sunday, rest days included: the week at a glance.
            for offset in range(7):
                day = monday + timedelta(days=offset)
                items = [{"name": _name(x), "key": (x.get("date"), _name(x)) in key_at,
                          # "11 km easy" says its distance already; "Sub-T 3 × 3 km" doesn't say its total.
                          "km": _km(x["planned_km"]) if x.get("planned_km")
                          and f"{_km(x['planned_km'])} km" not in _name(x) else None}
                         for x in ahead if x.get("date") == day.isoformat()]
                schedule.append({"day": DAY_NAMES[offset][:3], "items": items})

    if not last_line and not planned_week:
        return None
    return {
        "last_week": last_line,
        "key_sessions": keys,
        "this_week": planned_week and {"sessions": planned_week["sessions"], "km": _km(planned_week["km"])},
        "schedule": schedule or None,
        "block": None if logged else _block(answer),
        "next_week": _keys(following, monday + timedelta(days=7)) if following and not logged else [],
        "no_plan": logged,
        "sport": "riding" if ride else "running",
    }


def _short(name: str) -> str:
    """A session's name for a subject line: "Long 19 km w 4 × 2 km sub-T" is "Long 19 km"."""
    return re.split(r"\s+(?:w|with|\+)\s+", name, maxsplit=1)[0]


# Both apps open a new chat with the question already typed, from a link.
ASK_LINKS = {"Claude": "https://claude.ai/new?q=", "ChatGPT": "https://chatgpt.com/?q="}


def compose(s: dict[str, Any], unsubscribe: str, app: str | None = None) -> tuple[str, str, str]:
    """(subject, plain text, html) for one person's Monday email."""
    keys = s.get("key_sessions") or []
    block = s.get("block") or {}
    if keys:
        lead = (f"Week {block['week']} of {block['weeks']}" if block.get("week") and block.get("weeks") else "This week")
        subject = f"{lead}: " + ", ".join(f"{_short(k['name'])} {k['day'][:3]}" for k in keys)
    elif s.get("last_week"):
        subject = f"Last week: {s['last_week']}"
    else:
        subject = "Your week ahead"

    context = " · ".join(x for x in (
        block.get("name"),
        f"week {block['week']} of {block['weeks']}" if block.get("week") and block.get("weeks") else None,
        f"{block['percent']}% of sessions done so far" if block.get("percent") is not None else None) if x)
    question = ("Build me a training plan from my recent running and put it on my Garmin."
                if s.get("no_plan") else "How's my training going this week?")
    asked = f"Ask Daash: {question}"
    links = {a: base + quote(asked) for a, base in ASK_LINKS.items()}
    chosen = {app: links[app]} if app in links else links

    # Plain text, line by line.
    parts: list[str] = []
    if context:
        parts.append(context)
    if s.get("last_week"):
        parts.append(f"Last week: {s['last_week']}.")
    if keys:
        parts.append(("This week's key sessions:" if len(keys) > 1 else "This week's key session:") + "".join(
            f"\n- {k['day']}: {k['name']}" + (f" ({k['km']} km)" if k.get("km") else "") for k in keys))
    if s.get("schedule"):
        rows = [f"{d['day']}  " + (" + ".join(i["name"] + (f" ({i['km']} km)" if i.get("km") else "") for i in d["items"])
                                   or "Rest") for d in s["schedule"]]
        total = s.get("this_week") or {}
        parts.append("Your week:\n" + "\n".join(rows)
                     + (f"\n{total['sessions']} sessions, {total['km']} km" if total else ""))
    nxt = s.get("next_week") or []
    if nxt:
        parts.append("Next week: " + " and ".join(f"{k['name']} on {k['day']}" for k in nxt) + ".")
    if s.get("no_plan"):
        parts.append(f"Nothing planned this week. Ask Daash to build you a plan from your recent {s['sport']}, "
                     "and every session goes on your watch.")
    parts.append(f"Ask Daash: {question}\n" + "\n".join(f"{a}: {u}" for a, u in chosen.items()))
    foot = ("You get this because you connected Daash to your Garmin. "
            f"Stop these emails: {unsubscribe}")
    text = "\n\n".join(parts) + "\n\n—\nDaash\n" + foot + "\n"

    # HTML, styled inline for email.
    e = html.escape
    body = ""
    if context:
        body += f"<p style=\"margin:0 0 16px;font-size:13px;color:#5B6270\">{e(context)}</p>"
    if s.get("last_week"):
        body += f"<p style=\"margin:0 0 14px\">Last week: {e(s['last_week'])}.</p>"
    if keys:
        body += (f"<p style=\"margin:0 0 6px\">{'This week' + chr(39) + 's key sessions:' if len(keys) > 1 else 'This week' + chr(39) + 's key session:'}</p>"
                 "<ul style=\"margin:0 0 16px;padding-left:22px\">"
                 + "".join(f"<li style=\"margin:0 0 4px\"><strong>{e(k['day'])}</strong>: {e(k['name'])}"
                           + (f" ({e(k['km'])} km)" if k.get("km") else "") + "</li>" for k in keys) + "</ul>")
    if s.get("schedule"):
        rows = ""
        for d in s["schedule"]:
            if d["items"]:
                cell = "<br>".join((f"<strong>{e(i['name'])}</strong>" if i.get("key") else e(i["name"]))
                                   + (f" <span style=\"color:#5B6270\">· {e(i['km'])} km</span>" if i.get("km") else "")
                                   for i in d["items"])
            else:
                cell = "<span style=\"color:#9AA1AD\">Rest</span>"
            rows += (f"<tr><td style=\"padding:7px 10px 7px 0;color:#5B6270;vertical-align:top;width:44px\">{e(d['day'])}</td>"
                     f"<td style=\"padding:7px 0;vertical-align:top\">{cell}</td></tr>")
        total = s.get("this_week") or {}
        body += ("<p style=\"margin:0 0 6px\">Your week:</p>"
                 "<table role=\"presentation\" style=\"width:100%;border-collapse:collapse;margin:0 0 6px;"
                 "border-top:1px solid #E6E8EC;font-size:15px\">" + rows + "</table>"
                 + (f"<p style=\"margin:0 0 16px;font-size:13px;color:#5B6270\">{total['sessions']} sessions, "
                    f"{e(total['km'])} km</p>" if total else ""))
    if nxt:
        body += ("<p style=\"margin:0 0 16px\">Next week: "
                 + " and ".join(f"{e(k['name'])} on {e(k['day'])}" for k in nxt) + ".</p>")
    if s.get("no_plan"):
        body += (f"<p style=\"margin:0 0 16px\">Nothing planned this week. Ask Daash to build you a plan from your "
                 f"recent {e(s['sport'])}, and every session goes on your watch.</p>")
    label = "Ask Daash to build a plan" if s.get("no_plan") else "Ask Daash about your week"
    if len(chosen) == 1:
        (only, url), = chosen.items()
        body += (f"<p style=\"margin:8px 0 0\"><a href=\"{e(url)}\" style=\"display:inline-block;background:#E0312A;"
                 f"color:#fff;text-decoration:none;font-weight:600;padding:11px 18px;border-radius:10px\">{label} in {e(only)}</a></p>")
    else:
        body += (f"<p style=\"margin:8px 0 0\">{label}: "
                 + " · ".join(f"<a href=\"{e(u)}\" style=\"color:#E0312A;font-weight:600\">in {e(a)}</a>" for a, u in chosen.items())
                 + "</p>")
    page = (
        "<!doctype html><html><body style=\"margin:0;background:#F7F8FA\">"
        "<div style=\"max-width:520px;margin:0 auto;padding:28px 20px;font:16px/1.55 -apple-system,'Segoe UI',Helvetica,Arial,sans-serif;color:#161B26\">"
        "<div style=\"font-weight:900;font-style:italic;font-size:24px;color:#E0312A;margin-bottom:14px\">Daash</div>"
        f"{body}"
        "<p style=\"margin:28px 0 0;font-size:13px;color:#5B6270\">You get this because you connected Daash to your Garmin. "
        f"<a href=\"{e(unsubscribe)}\" style=\"color:#5B6270\">Stop these emails</a>.</p>"
        "</div></body></html>"
    )
    return subject, text, page

"""The Monday email: last week in a line and one thing it showed, this week at a glance, and the way back in.

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
from typing import Any, Callable
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


NUMBERS = ("no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine")
# Sessions worth comparing with the last one of the same kind: same type, so like for like.
COMPARED = ("threshold", "intervals", "sub", "long")


def _pace(value: Any) -> int | None:
    """Seconds per km from "5:09 /km"."""
    m = re.match(r"\s*(\d+):(\d{2})", str(value or ""))
    return int(m.group(1)) * 60 + int(m.group(2)) if m else None


def _reps(session: dict[str, Any]) -> int:
    """Work reps in a session's shape, strides left out."""
    return sum(1 for p in session.get("shape") or [] if isinstance(p, dict)
               and p.get("k") == "work" and float(p.get("km") or 0) >= 0.4)


def _count(n: int) -> str:
    return NUMBERS[n] if 0 <= n < len(NUMBERS) else str(n)


def _done(week: dict[str, Any] | None) -> list[dict[str, Any]]:
    return [x for x in (week or {}).get("sessions") or [] if isinstance(x, dict) and x.get("status") == "done"]


def _insight(answer: dict[str, Any], monday: date, ride: bool) -> str | None:
    """One thing last week showed, in a sentence, from the numbers already on the card. None when
    nothing stands out: a made-up insight is worse than none."""
    last_start = monday - timedelta(days=7)
    weeks = sorted((w for w in answer.get("weeks") or [] if isinstance(w, dict)
                    and str(w.get("starts") or "") <= last_start.isoformat()), key=lambda w: w["starts"])
    if not weeks or weeks[-1].get("starts") != last_start.isoformat():
        return None
    last, earlier = weeks[-1], weeks[:-1]

    def ago(then: dict[str, Any]) -> str:
        gap = (last_start - date.fromisoformat(then["_week"])).days // 7
        return "the week before" if gap <= 1 else f"{_count(gap)} weeks before"

    def day_of(x: dict[str, Any]) -> str:
        return DAY_NAMES[date.fromisoformat(x["date"]).weekday()]

    # 1. A hard session against the last one of its kind: more reps or quicker, at what heart rate.
    if answer.get("source") != "log":
        hard = sorted((x for x in _done(last) if x.get("type") in COMPARED and x.get("actual_bpm")
                       and _pace(x.get("actual_pace"))), key=lambda x: COMPARED.index(x["type"]))
        for now in hard:
            before = [x | {"_week": w["starts"]} for w in earlier for x in _done(w) if x.get("type") == now["type"]
                      and x.get("actual_bpm") and _pace(x.get("actual_pace"))]
            if not before:
                continue
            then = before[-1]
            bpm, dhr = int(now["actual_bpm"]), int(now["actual_bpm"]) - int(then["actual_bpm"])
            dpace = _pace(now["actual_pace"]) - _pace(then["actual_pace"])
            more = _reps(now) - _reps(then)
            hr = "the same heart rate as" if dhr >= -1 else f"{-dhr} bpm lower than"
            lead, was = f"{day_of(now)}'s {_name(now)}", f"{_name(then)} {ago(then)}"
            if more > 0 and dhr <= 1:
                return f"{lead} averaged {bpm} bpm, {hr} {was}, with {_count(more)} more rep{'s' if more > 1 else ''}."
            similar = more == 0 and abs(float(now.get("actual_km") or 0) - float(then.get("actual_km") or 0)) \
                <= 0.15 * max(float(then.get("actual_km") or 0), 1)
            if similar and dhr <= 1 and dpace <= -5:
                return f"{lead} was {-dpace} s/km quicker than {was}, at {hr.removesuffix(' as').removesuffix(' than')}."
            if similar and dhr >= 5 and dpace >= -2:
                return (f"{lead} averaged {bpm} bpm, {dhr} higher than {was} at the same pace: tiredness, heat or "
                        "hills can do that, so check how you've recovered before the next hard day.")

        # 2. Easy runs: the same pace for fewer beats, or more beats for the same pace.
        def easy(ws: list[dict[str, Any]]) -> tuple[float, float, float] | None:
            runs = [x for w in ws for x in _done(w) if x.get("type") == "easy" and x.get("kind") == "run"
                    and x.get("actual_bpm") and _pace(x.get("actual_pace")) and x.get("actual_km")]
            km = sum(float(x["actual_km"]) for x in runs)
            if len(runs) < 2 or km <= 0:
                return None
            return (km, sum(float(x["actual_bpm"]) * float(x["actual_km"]) for x in runs) / km,
                    sum(_pace(x["actual_pace"]) * float(x["actual_km"]) for x in runs) / km)
        now_easy, then_easy = easy([last]), easy(earlier)
        if not ride and now_easy and then_easy:
            dhr, dpace = round(now_easy[1] - then_easy[1]), round(now_easy[2] - then_easy[2])
            if dhr <= -3 and dpace <= 3:
                return (f"Easy runs averaged {round(now_easy[1])} bpm, {-dhr} lower than the weeks before at "
                        f"{'the same pace' if dpace >= -3 else 'a quicker pace'}: the easy running is paying off.")
            if dhr >= 4 and dpace >= -3:
                return (f"Easy runs averaged {round(now_easy[1])} bpm, {dhr} higher than the weeks before at the same "
                        "pace or slower: worth keeping them truly easy this week.")

    # 3. The biggest week, or the longest run, of the weeks on the card.
    field = "ride_km" if ride else "run_km"
    before_km = [float(w.get(field) or 0) for w in earlier if float(w.get(field) or 0) > 0]
    now_km = float(last.get(field) or 0)
    if len(before_km) >= 2 and now_km >= max(before_km) + 1:
        return (f"Your biggest week of the last {_count(len(earlier) + 1)}, "
                f"{_km(now_km - before_km[-1])} km up on the one before." if float(earlier[-1].get(field) or 0) > 0
                else f"Your biggest week of the last {_count(len(earlier) + 1)}.")
    kind = "ride" if ride else "run"

    def outings(w: dict[str, Any]) -> list[dict[str, Any]]:
        return [x for x in (w.get("sessions") or []) + (w.get("extra") or []) if isinstance(x, dict)
                and x.get("kind") == kind and x.get("actual_km") and x.get("status") in ("done", "logged", None)]
    longest = max(outings(last), key=lambda x: float(x["actual_km"]), default=None)
    before_long = [float(x["actual_km"]) for w in earlier for x in outings(w)]
    if longest and len(earlier) >= 2 and before_long and float(longest["actual_km"]) >= max(before_long) + 0.5:
        return (f"{day_of(longest)}'s {_km(longest['actual_km'])} km was your longest {kind} "
                f"in {_count(len(earlier) + 1)} weeks.")
    return None


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
        "insight": _insight(answer, monday, ride) if last_line else None,
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
SUBJECT_PREFIX = "Daash - "
# What the email's button asks, by kind: a plan to build, or the week so far.
QUESTIONS = {
    "plan": "Build me a training plan from my recent running and put it on my Garmin.",
    "week": "How's my training going this week?",
}


def ask_link(app: str, kind: str) -> str:
    """The chat link itself: a new chat in that app with the question typed."""
    return ASK_LINKS[app] + quote(f"Ask Daash: {QUESTIONS[kind]}")


def compose(s: dict[str, Any], unsubscribe: str, app: str | None = None,
            ask: Callable[[str, str], str] = ask_link) -> tuple[str, str, str]:
    """(subject, plain text, html) for one person's Monday email.

    `ask` makes each app's link from the app and the kind of question, so the
    sender can route it through a page that counts the click.
    """
    keys = s.get("key_sessions") or []
    block = s.get("block") or {}
    if keys:
        lead = (f"Week {block['week']} of {block['weeks']}" if block.get("week") and block.get("weeks") else "This week")
        subject = f"{lead}: " + ", ".join(f"{_short(k['name'])} {k['day'][:3]}" for k in keys)
    elif s.get("last_week"):
        subject = f"Last week: {s['last_week']}"
    else:
        subject = "Your week ahead"
    # Who it's from, first, so it reads as Daash in an inbox list.
    subject = f"{SUBJECT_PREFIX}{subject}"

    context = " · ".join(x for x in (
        block.get("name"),
        f"week {block['week']} of {block['weeks']}" if block.get("week") and block.get("weeks") else None,
        f"{block['percent']}% of sessions done so far" if block.get("percent") is not None else None) if x)
    kind = "plan" if s.get("no_plan") else "week"
    question = QUESTIONS[kind]
    chosen = {a: ask(a, kind) for a in ([app] if app in ASK_LINKS else ASK_LINKS)}

    # Plain text, line by line.
    parts: list[str] = []
    if context:
        parts.append(context)
    if s.get("last_week"):
        parts.append(f"Last week: {s['last_week']}." + (f" {s['insight']}" if s.get("insight") else ""))
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
        body += f"<p style=\"margin:0 0 {'6' if s.get('insight') else '14'}px\">Last week: {e(s['last_week'])}.</p>"
    if s.get("insight"):
        body += f"<p style=\"margin:0 0 16px;padding:10px 12px;background:#fff;border-left:3px solid #E0312A\">{e(s['insight'])}</p>"
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

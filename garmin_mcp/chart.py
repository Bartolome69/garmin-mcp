"""Draw the training plan as an image, for showing inside a conversation.

The page version needs hosting, a secret URL and a scheduled publish. This
renders the same idea to a PNG the server hands straight back, so the plan
appears in the chat where the coaching is happening.

Sized by duration, like the page: strength work has no distance, so a mileage
bar could never show it honestly.
"""

from __future__ import annotations

import io
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from .formatting import duration

W = 900
PAD = 28
HEADER = 96
WEEK_H = 158
LEGEND_H = 34

INK = (23, 33, 27)
MUTED = (103, 118, 108)
GROUND = (247, 249, 247)
SURFACE = (255, 255, 255)
LINE = (220, 227, 220)
RUN = (47, 107, 79)
STRENGTH = (74, 128, 184)
OTHER = (217, 163, 32)
PLAN = (201, 210, 202)
ACCENT = (188, 90, 42)

COLOURS = {"run": RUN, "strength": STRENGTH, "other": OTHER}

FONTS = [
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
]
BOLD = [
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
]


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    for path in (BOLD if bold else FONTS):
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default(size)


def render(model: dict[str, Any], weeks: list[dict[str, Any]], target: float) -> bytes:
    height = HEADER + WEEK_H * len(weeks) + LEGEND_H + PAD
    image = Image.new("RGB", (W, height), GROUND)
    draw = ImageDraw.Draw(image)

    summary = model["summary"]
    draw.text((PAD, PAD - 4), "This week", font=font(26, True), fill=INK)

    # Headline numbers, spaced along the top.
    x = PAD
    for label, value in (
        ("KILOMETRES", str(summary["km"])),
        ("TIME", summary["time"]),
        ("RUNS", str(summary["runs"])),
        ("TARGET", f"{target:g} km"),
    ):
        draw.text((x, PAD + 34), label, font=font(11), fill=MUTED)
        draw.text((x, PAD + 48), value, font=font(22, True), fill=INK)
        x += 168

    # The tallest thing on show sets the scale for every bar.
    peak = max(
        [d["actual_secs"] for w in weeks for d in w["days"]]
        + [d["planned_secs"] for w in weeks for d in w["days"]]
        + [3600.0]
    )

    top = HEADER
    for week in weeks:
        card = (PAD, top, W - PAD, top + WEEK_H - 14)
        draw.rounded_rectangle(card, 12, fill=SURFACE, outline=LINE)

        label = "This week" if week["current"] else week["start"].strftime("%-d %B")
        draw.text((PAD + 16, top + 12), label, font=font(13, True), fill=
                  ACCENT if week["current"] else MUTED)

        # duration(0) is "0s", which is truthy — check the numbers, not the text.
        did_something = any(d["actual_secs"] for d in week["days"])
        planned_secs = sum(d["planned_secs"] for d in week["days"])
        if did_something:
            totals = f"{week['actual_km']} km · {week['actual_time']}"
        elif week["future"]:
            totals = "to come"
        else:
            totals = "nothing recorded"
        if planned_secs:
            totals += f"  ({duration(planned_secs)} planned)"
        elif week["future"]:
            totals += " · nothing planned yet"
        right = draw.textlength(totals, font=font(12))
        draw.text((W - PAD - 16 - right, top + 13), totals, font=font(12), fill=MUTED)

        # One column per day: actual pill, then the planned one beside it.
        base = top + WEEK_H - 44
        column = (W - PAD * 2 - 32) / 7
        for index, day in enumerate(week["days"]):
            cx = PAD + 16 + column * (index + 0.5)

            def pill(offset: float, secs: float, segments: dict[str, float] | None) -> None:
                if not secs:
                    return
                bar_h = max(6, (secs / peak) * 92)
                x0, x1 = cx + offset - 6, cx + offset + 6
                y0 = base - bar_h
                if segments:
                    # Stack the kinds within the one pill, largest at the bottom.
                    cursor = base
                    for kind, value in sorted(segments.items()):
                        seg_h = bar_h * (value / secs)
                        draw.rounded_rectangle(
                            (x0, cursor - seg_h, x1, cursor), 6,
                            fill=COLOURS.get(kind, OTHER),
                        )
                        cursor -= seg_h
                else:
                    draw.rounded_rectangle((x0, y0, x1, base), 6, fill=PLAN)

            pill(-8, day["actual_secs"], day["bars"] or None)
            pill(8, day["planned_secs"], None)

            name = ["M", "T", "W", "T", "F", "S", "S"][day["date"].weekday()]
            colour = ACCENT if day["is_today"] else MUTED
            number = str(day["date"].day)
            draw.text((cx - draw.textlength(number, font=font(12, True)) / 2,
                       base + 8), number, font=font(12, True), fill=colour)
            draw.text((cx - draw.textlength(name, font=font(10)) / 2,
                       base + 24), name, font=font(10), fill=MUTED)

        top += WEEK_H

    # Legend
    x = PAD + 2
    for colour, label in ((RUN, "running"), (STRENGTH, "strength"),
                          (OTHER, "other"), (PLAN, "planned")):
        draw.rounded_rectangle((x, height - 26, x + 10, height - 16), 3, fill=colour)
        draw.text((x + 16, height - 28), label, font=font(11), fill=MUTED)
        x += 22 + draw.textlength(label, font=font(11)) + 18

    buffer = io.BytesIO()
    image.save(buffer, "PNG", optimize=True)
    return buffer.getvalue()


def build(weeks_back: int = 1, weeks_forward: int = 1) -> bytes:
    """Render the weeks around today."""
    from .plan import collect, load_config, monday_of, organise

    config = load_config()
    model = organise(collect())
    this_monday = monday_of(date.today())
    wanted = {
        this_monday + timedelta(weeks=offset)
        for offset in range(-weeks_back, weeks_forward + 1)
    }
    weeks = [w for w in model["detail"] if w["start"] in wanted]
    return render(model, weeks, float(config.get("weekly_target_km") or 50))

"""A race through the night as a chart: the projected margin over time, the votes counted under it, and the moments
the desk called or retracted (no Discord here).

It draws from the field's own history (field.history(), or a night's field fixture), so it shows what the model said
at each minute, which is not the same as the raw count: see field.py for how a race's margin is worked out.
"""
from __future__ import annotations

import io
from datetime import datetime

from PIL import Image, ImageDraw

import cards as C
import night as N

W, H, SS = 1400, 780, 2
LEFT, RIGHT, TOP, MID, BOTTOM = 110, 60, 176, 566, 700
SIDE = {"D": C.ED_COL["D"], "R": C.ED_COL["R"]}


def series(snaps: list[dict], rid: str) -> list[tuple[float, float, int, str]]:
    """(time, margin, votes, state) for one race at every snapshot that has it, oldest first."""
    return [(s["ts"], *s["races"][rid][:3]) for s in sorted(snaps, key=lambda s: s["ts"]) if rid in s.get("races", {})]


def summary(points: list[tuple[float, float, int, str]]) -> dict | None:
    """The numbers a caption needs: where the projection started and ended, how far it ranged, the votes counted."""
    live = [p for p in points if p[3] != "wait"]
    if not live:
        return None
    proj = [p for p in live if p[3] == "proj"] or live
    margins = [m for _t, m, _v, _s in proj]
    return {"first": proj[0][1], "last": proj[-1][1], "low": min(margins), "high": max(margins),
            "votes": live[-1][2], "from": live[0][0], "to": live[-1][0], "points": len(live),
            "changed_sides": any((a > 0) != (b > 0) for a, b in zip(margins, margins[1:]))}


def _lean(m: float, d: str, r: str) -> str:
    return "even" if abs(m) < 0.05 else f"{d if m > 0 else r} +{abs(m):.1f}"


def render(title: str, points: list[tuple[float, float, int, str]], d_name: str = "D", r_name: str = "R",
           marks: list[tuple[float, str, str | None]] | None = None, bot: tuple[float, str] | None = None) -> io.BytesIO | None:
    """The chart as a PNG, or None if the race never had numbers. marks are the desk's moments: (time, "call" or
    "uncall", side). bot is the moment the desk's own bot would have called it, and for whom."""
    live = [p for p in points if p[3] != "wait"]
    if len(live) < 2:
        return None
    t0, t1 = live[0][0], live[-1][0]
    span = max(t1 - t0, 60)
    # The scale fits the projections. What came before any county finished is often far off (a baseline, or one
    # early batch) and would flatten the part worth reading, so it's drawn clipped to the edge of the panel.
    scaled = [m for _t, m, _v, st in live if st == "proj"] or [m for _t, m, _v, _s in live]
    top = max(5.0, max(abs(m) for m in scaled) * 1.25)
    vmax = max(v for _t, _m, v, _s in live) or 1
    s = SS
    img = Image.new("RGB", (W * s, H * s), C.PAPER)
    d = ImageDraw.Draw(img, "RGBA")
    x = lambda t: (LEFT + (W - LEFT - RIGHT) * (t - t0) / span) * s                   # noqa: E731
    y = lambda m: ((TOP + MID) / 2 - (MID - TOP) / 2 * max(-top, min(top, m)) / top) * s      # noqa: E731
    yv = lambda v: (BOTTOM - (BOTTOM - MID - 40) * v / vmax) * s                      # noqa: E731

    # heading
    d.text((LEFT * s, 44 * s), "DECISION DESK  ·  THE NIGHT IN ONE RACE", font=C.font(C.PLEX, 20 * s), fill=C.GREY)
    d.text((LEFT * s, 68 * s), title, font=C.font(C.SERIF, 46 * s), fill=C.INK)
    end = live[-1][1]
    C.tracked(d, ((W - RIGHT) * s, 108 * s), f"LAST: {_lean(end, d_name, r_name).upper()}", C.font(C.FRANKLIN, 24 * s, 800),
              SIDE["D" if end > 0 else "R"], 0.04, anchor="rs")

    # the margin panel: grid, zero line, shaded lead
    step = 1 if top <= 6 else 5 if top <= 25 else 10
    for g in range(-int(top // step) * step, int(top // step) * step + 1, step):
        d.line([(LEFT * s, y(g)), ((W - RIGHT) * s, y(g))], fill=C.INK if g == 0 else C.RULE, width=(2 if g == 0 else 1) * s)
        label = "EVEN" if g == 0 else f"{'D' if g > 0 else 'R'}+{abs(g)}"
        d.text(((LEFT - 14) * s, y(g)), label, font=C.font(C.PLEX, 17 * s), fill=C.GREY, anchor="rm")
    pts = [(x(t), y(m)) for t, m, _v, _st in live]
    for (xa, ya), (xb, yb), (_t, ma, _v, sta), (_t2, mb, _v2, _s2) in zip(pts, pts[1:], live, live[1:]):
        col = SIDE["D" if (ma + mb) / 2 > 0 else "R"]
        d.polygon([(xa, y(0)), (xa, ya), (xb, yb), (xb, y(0))], fill=col + (46,))
        d.line([(xa, ya), (xb, yb)], fill=col + (255 if sta == "proj" else 120,), width=(4 if sta == "proj" else 2) * s)
    d.text((LEFT * s, (TOP - 26) * s), f"PROJECTED MARGIN  ·  {d_name.upper()} ABOVE THE LINE, {r_name.upper()} BELOW  ·  "
           "THIN LINE: BEFORE ANY COUNTY FINISHED", font=C.font(C.PLEX, 15 * s), fill=C.GREY)

    # votes counted
    d.line([(LEFT * s, BOTTOM * s), ((W - RIGHT) * s, BOTTOM * s)], fill=C.RULE, width=s)
    d.polygon([(x(live[0][0]), BOTTOM * s)] + [(x(t), yv(v)) for t, _m, v, _st in live] + [(x(live[-1][0]), BOTTOM * s)],
              fill=C.GREY + (70,))
    d.text((LEFT * s, (MID + 14) * s), f"VOTES COUNTED  ·  {vmax:,} BY THE END", font=C.font(C.PLEX, 15 * s), fill=C.GREY)

    # the clock along the bottom, on the hour
    hour = datetime.fromtimestamp(t0, N.ET).replace(minute=0, second=0, microsecond=0).timestamp()
    every = 3600 * max(1, round(span / 3600 / 9))
    while hour <= t1:
        if hour >= t0:
            d.line([(x(hour), BOTTOM * s), (x(hour), (BOTTOM + 8) * s)], fill=C.GREY, width=s)
            d.text((x(hour), (BOTTOM + 14) * s), datetime.fromtimestamp(hour, N.ET).strftime("%I %p").lstrip("0"),
                   font=C.font(C.PLEX, 16 * s), fill=C.GREY, anchor="ma")
        hour += every
    d.text(((W - RIGHT) * s, (BOTTOM + 44) * s), "EASTERN TIME", font=C.font(C.PLEX, 14 * s), fill=C.GREY, anchor="ra")

    # the desk's moments, and the bot's
    row = 0
    for when, what, side in sorted(marks or []):
        if not t0 <= when <= t1:
            continue
        col = C.INK if what == "uncall" else SIDE.get(side or "", C.INK)
        d.line([(x(when), TOP * s), (x(when), MID * s)], fill=col + (230,), width=3 * s)
        text = "RETRACTED" if what == "uncall" else f"DESK CALLS {(d_name if side == 'D' else r_name).upper()}"
        flip = x(when) > (W - 320) * s
        d.text((x(when) + (-8 if flip else 8) * s, (TOP + 8 + 24 * (row % 3)) * s), text, font=C.font(C.FRANKLIN, 17 * s, 800),
               fill=col, anchor="ra" if flip else "la")
        row += 1
    if bot and t0 <= bot[0] <= t1:
        for yy in range(TOP * s, MID * s, 14 * s):
            d.line([(x(bot[0]), yy), (x(bot[0]), yy + 7 * s)], fill=C.GREY + (255,), width=2 * s)
        flip = x(bot[0]) > (W - 320) * s
        d.text((x(bot[0]) + (-8 if flip else 8) * s, (MID - 30) * s), f"THE BOT WOULD CALL {(d_name if bot[1] == 'D' else r_name).upper()}",
               font=C.font(C.FRANKLIN, 16 * s, 700), fill=C.GREY, anchor="ra" if flip else "la")

    out = io.BytesIO()
    img.resize((W, H), Image.LANCZOS).save(out, "PNG", optimize=True)
    out.seek(0)
    return out

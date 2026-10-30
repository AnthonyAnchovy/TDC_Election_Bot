"""Call card themes. graphics.call_card() picks one by name; the original glass card lives on there as "classic".

  wire       Party-color field, the state as a halftone print screen, Anton poster type, a wire-service slug,
             a yellow PICKUP tag on flips and a seat strip showing where the chamber stands after the call.
  editorial  A results-page module on paper: serif name, Franklin labels, a locator map, and a seat strip
             (Senate, governors) or a hemicycle on a taller card (House seats, House control).

Fonts ship in assets/fonts (SIL Open Font License, see the OFL-*.txt files beside them).
"""
from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

import competitive as CMP
import graphics as G
import night as N
import races as R

FONTS = Path(__file__).parent / "assets" / "fonts"
ANTON, PLEX, FRANKLIN, SERIF = ("Anton-Regular.ttf", "IBMPlexMono-SemiBold.ttf", "LibreFranklin-Variable.ttf",
                                "DMSerifDisplay-Regular.ttf")
W, H = 1600, 900

PARTY_WORD = {"D": "Democratic", "R": "Republican", "I": "Independent"}
PARTY_NOUN = {"D": "Democrat", "R": "Republican", "I": "Independent"}
SEATS = {"senate": 100, "governor": 50, "house": 435}
NEEDED = {"senate": 51, "governor": 26, "house": 218}
CHAMBER_LABEL = {"senate": "SENATE", "governor": "GOVERNORS", "house": "HOUSE"}

_fonts: dict = {}


def font(name: str, size: int, wght: int | None = None):
    """A bundled font, falling back to graphics.font() if the file is missing."""
    key = (name, size, wght)
    if key not in _fonts:
        try:
            f = ImageFont.truetype(str(FONTS / name), size)
            if wght:
                f.set_variation_by_axes([wght])
        except OSError:
            f = G.font(size)
        _fonts[key] = f
    return _fonts[key]


def fit(d, text, max_w, name, start, minimum=20, wght=None):
    size = start
    while size > minimum and d.textlength(text, font=font(name, size, wght)) > max_w:
        size -= 4
    return font(name, size, wght)


def tracked(d, xy, text, fnt, fill, tracking=0.0, anchor="ls") -> float:
    """Text with letter-spacing (in em). Returns the drawn width."""
    x, y = xy
    widths = [d.textlength(ch, font=fnt) for ch in text]
    total = sum(widths) + tracking * fnt.size * (len(text) - 1)
    if anchor[0] == "r":
        x -= total
    elif anchor[0] == "m":
        x -= total / 2
    for ch, w in zip(text, widths):
        d.text((x, y), ch, font=fnt, fill=fill, anchor="l" + anchor[1])
        x += w + tracking * fnt.size
    return total


def tracked_len(d, text, fnt, tracking=0.0) -> float:
    return sum(d.textlength(ch, font=fnt) for ch in text) + tracking * fnt.size * (len(text) - 1)


def solid(mask: Image.Image, color, alpha: float = 1.0) -> Image.Image:
    layer = Image.new("RGBA", mask.size, tuple(color[:3]) + (0,))
    layer.putalpha(mask.point(lambda v: int(v * alpha)))
    return layer


# ------------------------------------------------------------------------------------------------ race facts
def race_facts(race_id: str, party: str | None, candidate: str | None, tally: str | None, when: datetime | None,
               retracted: bool, counts: dict | None) -> dict:
    r = R.RACES[race_id]
    kind = r["type"]
    st = r["state"]
    if kind == "senate":
        office = "Senate" + (" special" if st in R.SENATE_SPECIAL else "")
    elif kind == "governor":
        office = "Governor"
    elif kind == "house":
        office = "At-large district" if race_id.endswith("-AL") else f"{R._ordinal(int(race_id.split('-')[1]))} District"
    else:
        office = "Majority"
    holder = r.get("holder") or CMP.HOLDER_OF.get(race_id) or CMP.HOLDER.get(race_id)
    when = when or datetime.now(N.ET)
    if when.tzinfo is None:
        when = when.replace(tzinfo=N.ET)
    return {"rid": race_id, "party": party, "kind": kind, "chamber": "house" if kind in ("house", "control") else kind,
            "state": st, "state_name": R.STATE_NAMES.get(st, "U.S. House"), "office": office,
            "name": None if kind == "control" else candidate, "holder": holder,
            "flip": bool(holder and party and holder != party and not retracted), "hold": bool(party and holder == party),
            "rating": CMP.RATING_OF.get(race_id), "retracted": retracted, "tally": tally or "",
            "when": when.astimezone(N.ET), "counts": counts}


def status_text(c: dict) -> str:
    if c["retracted"]:
        return "CALL RETRACTED"
    word = PARTY_WORD[c["party"]].upper()
    if c["kind"] == "control":
        return f"{word} {'HOLD' if c['hold'] else 'TAKEOVER'}"
    return f"{word} {'PICKUP' if c['flip'] else 'HOLD' if c['hold'] else 'WIN'}"


def kicker(c: dict, sep: str) -> str:
    return "U.S. House" + sep + "Majority" if c["kind"] == "control" else f"{c['state_name']}{sep}{c['office']}"


def surname(name: str) -> str:
    return name.split()[-1]


def given(name: str) -> str:
    return " ".join(name.split()[:-1])


def seat_order(counts: dict, total: int) -> list[str]:
    """D seats first, then independents, uncalled, and R seats last."""
    d, r, i = counts.get("D", 0), counts.get("R", 0), counts.get("I", 0)
    return ["D"] * d + ["I"] * i + ["U"] * max(0, total - d - r - i) + ["R"] * r


def called_index(c: dict, total: int) -> int:
    """Which seat to ring as 'the one just called': the innermost seat on the winner's side."""
    counts = c["counts"]
    if c["retracted"] or not counts or not c["party"]:
        return -1
    if c["party"] == "D":
        return counts.get("D", 0) - 1
    if c["party"] == "R":
        return total - counts.get("R", 0)
    return counts.get("D", 0) + counts.get("I", 0) - 1


def seat_strip(draw, x, y, w, order, per_row, gap, cols, highlight=-1, ring=(255, 255, 255)):
    cell = (w - gap * (per_row - 1)) / per_row
    for i, p in enumerate(order):
        cx = x + (i % per_row) * (cell + gap)
        cy = y + (i // per_row) * (cell + gap)
        draw.rectangle([cx, cy, cx + cell, cy + cell], fill=cols[p])
        if i == highlight:
            draw.rectangle([cx - 5, cy - 5, cx + cell + 5, cy + cell + 5], outline=ring, width=4)
    return cell


def us_locator(state: str | None, bw: int, bh: int, base, hi, all_hi: bool = False) -> Image.Image:
    """Lower-48 locator map with one state highlighted. AK and HI are drawn on their own."""
    if state in ("AK", "HI"):
        return solid(G._shape_mask(state, bw, bh), hi)
    rings = G._state_rings(None)          # also loads G._SHAPES
    lats = [y for r in rings for _, y in r]
    k = math.cos(math.radians((max(lats) + min(lats)) / 2))
    pts = [[(x * k, -y) for x, y in r] for r in rings]
    xs = [p[0] for r in pts for p in r]
    ys = [p[1] for r in pts for p in r]
    sc = min(bw / (max(xs) - min(xs)), bh / (max(ys) - min(ys)))
    w, h = int((max(xs) - min(xs)) * sc) + 4, int((max(ys) - min(ys)) * sc) + 4
    S = 3
    img = Image.new("RGBA", (w * S, h * S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    for key, shapes in G._SHAPES.items():
        if key in ("AK", "HI"):
            continue
        for ring in shapes:
            if len(ring) > 2:
                poly = [((x * k - min(xs)) * sc * S + 2 * S, (-y - min(ys)) * sc * S + 2 * S) for x, y in ring]
                if all_hi:
                    d.polygon(poly, fill=tuple(hi) + (255,))
                else:
                    d.polygon(poly, fill=tuple(hi if key == state else base) + (255,), outline=(255, 255, 255, 255), width=S)
    return img.resize((w, h), Image.LANCZOS)


_DISTRICTS: dict | None = None


def district_rings(race_id: str) -> list:
    """2026 district outline (lon/lat rings) from district_shapes.json; built by tools/build_districts.py."""
    global _DISTRICTS
    if _DISTRICTS is None:
        p = Path(__file__).parent / "district_shapes.json"
        try:
            _DISTRICTS = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _DISTRICTS = {}
    return [[((x - 360) if (race_id.startswith("AK") and x > 0) else x, y) for x, y in r]
            for r in _DISTRICTS.get(race_id, [])]


def state_cutout(state: str, race_id: str | None, bw: int, bh: int, base, hi) -> Image.Image:
    """The state's silhouette fitted in bw x bh. With a House race, the state is drawn in `base` and the district
    in `hi`, plus a ring around districts too small to see; otherwise the whole state is `hi`."""
    srings = G._state_rings(state)
    drings = district_rings(race_id) if race_id else []
    whole = not drings or R.HOUSE_SEATS.get(state) == 1
    lats = [y for r in srings for _, y in r]
    k = math.cos(math.radians((max(lats) + min(lats)) / 2))
    xs = [x * k for r in srings for x, _ in r]
    ys = [-y for r in srings for _, y in r]
    sc = min(bw / (max(xs) - min(xs)), bh / (max(ys) - min(ys)))
    pad = 4 if whole else 22                    # room for the ring around a district on the state's edge
    w, h = int((max(xs) - min(xs)) * sc) + 2 * pad, int((max(ys) - min(ys)) * sc) + 2 * pad
    S = 3

    def proj(r):
        return [((x * k - min(xs)) * sc * S + pad * S, (-y - min(ys)) * sc * S + pad * S) for x, y in r]
    img = Image.new("RGBA", (w * S, h * S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    for r in srings:
        if len(r) > 2:
            d.polygon(proj(r), fill=tuple(hi if whole else base) + (255,))
    if not whole:
        pts = []
        for r in drings:
            if len(r) > 2:
                p = proj(r)
                d.polygon(p, fill=tuple(hi) + (255,))
                pts += p
        px = [p[0] for p in pts]
        py = [p[1] for p in pts]
        if pts and max(max(px) - min(px), max(py) - min(py)) < 18 * S:   # a city district: circle it
            cx, cy = (max(px) + min(px)) / 2, (max(py) + min(py)) / 2
            rr = 16 * S
            d.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], outline=tuple(hi) + (255,), width=3 * S)
    return img.resize((w, h), Image.LANCZOS)


# ================================================================================================= wire
WIRE_FIELD = {"D": (20, 60, 168), "R": (186, 24, 32), "I": (92, 52, 170), "X": (34, 36, 42)}
WIRE_DOT = {"D": (110, 170, 255), "R": (255, 120, 104), "I": (190, 150, 255), "X": (120, 124, 134)}
WIRE_SEAT = {"D": (64, 132, 255), "R": (240, 64, 64), "I": (170, 120, 255), "U": (255, 255, 255, 50)}
YELLOW, INK_DARK = (255, 214, 64), (14, 15, 19)


def _halftone(mask: Image.Image, origin, pitch: int, rmax: float, color, outside: float = 0.12,
              size: tuple[int, int] = (W, H)) -> Image.Image:
    """Dots on an offset grid over a canvas of `size`: full size inside the shape, a faint texture outside it."""
    S = 2
    cw, ch = size
    big = Image.new("RGBA", (cw * S, ch * S), (0, 0, 0, 0))
    bd = ImageDraw.Draw(big)
    ox, oy = origin
    blur = mask.filter(ImageFilter.GaussianBlur(pitch * 0.6))
    for gy in range(0, ch + pitch, pitch):
        off = pitch / 2 if (gy // pitch) % 2 else 0
        for gx in range(0, cw + pitch, pitch):
            px, py = gx + off, gy
            mx, my = int(px - ox), int(py - oy)
            v = blur.getpixel((mx, my)) / 255 if 0 <= mx < mask.width and 0 <= my < mask.height else 0
            r = rmax * (outside + (1 - outside) * v)
            if r > 0.6:
                bd.ellipse([(px - r) * S, (py - r) * S, (px + r) * S, (py + r) * S], fill=color + (255,))
    return big.resize((cw, ch), Image.LANCZOS)


def _flip_tag(d, holder: str, party: str, word: str) -> Image.Image:
    """Yellow PICKUP block joined to an ink block with a drawn holder → winner arrow."""
    lf = font(ANTON, 70)
    lw = tracked_len(d, word, lf, 0.04)
    hw = d.textlength(holder, font=lf) + d.textlength(party, font=lf) + 92
    th, padx = 104, 30
    split = int(lw + padx * 2)
    tag = Image.new("RGBA", (int(split + hw + padx * 2), th), (0, 0, 0, 0))
    td = ImageDraw.Draw(tag)
    td.rectangle([0, 0, split, th], fill=YELLOW)
    td.rectangle([split, 0, tag.width, th], fill=INK_DARK)
    tracked(td, (padx, th / 2 + 2), word, lf, INK_DARK, 0.04, anchor="lm")
    ax = split + padx
    td.text((ax, th / 2 + 2), holder, font=lf, fill=(255, 255, 255), anchor="lm")
    ax += d.textlength(holder, font=lf) + 16
    td.line([(ax, th / 2), (ax + 40, th / 2)], fill=YELLOW, width=8)
    td.polygon([(ax + 34, th / 2 - 15), (ax + 58, th / 2), (ax + 34, th / 2 + 15)], fill=YELLOW)
    td.text((ax + 76, th / 2 + 2), party, font=lf, fill=(255, 255, 255), anchor="lm")
    return tag


def wire(c: dict) -> Image.Image:
    key = "X" if c["retracted"] or not c["party"] else c["party"]
    img = Image.new("RGBA", (W, H), WIRE_FIELD[key] + (255,))

    # the state as a halftone, bleeding off the right edge
    if c["state"]:
        m = G._shape_mask(c["state"], 900, 820)
        origin = (W - m.width + 120, (H - m.height) // 2 + 30)
    else:
        m = us_locator(None, 1100, 700, base=(255, 255, 255), hi=(255, 255, 255), all_hi=True).split()[-1]
        origin = (W - m.width + 200, 140)
    if m is not None:
        img.alpha_composite(_halftone(m, origin, 22, 9.5, WIRE_DOT[key]))
    d = ImageDraw.Draw(img)

    # wire slug
    slug = (f"DECISION DESK — {c['when'].strftime('%H:%M')} ET — {c['rid']} — "
            + ("CORRECTION" if c["retracted"] else "CALL"))
    img.alpha_composite(Image.new("RGBA", (W, 70), (0, 0, 0, 70)))
    d = ImageDraw.Draw(img)
    tracked(d, (60, 47), slug, font(PLEX, 26), (255, 255, 255, 230), 0.04)

    x = 60
    tracked(d, (x, 146), kicker(c, " / ").upper(), font(PLEX, 34), (255, 255, 255, 210), 0.06)
    if c["retracted"]:
        word, sub = "UNCALLED", "TOO EARLY TO CALL"
    elif c["kind"] == "control":
        word, sub = {"D": "DEMOCRATS", "R": "REPUBLICANS", "I": "INDEPENDENTS"}[c["party"]], "HOUSE MAJORITY"
    elif c["name"]:
        word, sub = surname(c["name"]).upper(), given(c["name"]).upper()
    else:
        word, sub = PARTY_NOUN[c["party"]].upper(), "PROJECTED WINNER"
    big = fit(d, word, 1180, ANTON, 300, 140)
    shadow = Image.new("L", (W, H), 0)
    ImageDraw.Draw(shadow).text((x - 4, 548), word, font=big, fill=255, anchor="ls")
    img.alpha_composite(solid(shadow.filter(ImageFilter.GaussianBlur(14)), (0, 0, 0), 0.35), (0, 10))
    d = ImageDraw.Draw(img)
    d.text((x - 4, 548), word, font=big, fill=(255, 255, 255), anchor="ls")
    if sub:
        tracked(d, (x, 262), sub, font(ANTON, 74), (255, 255, 255, 235), 0.02)

    # flip tag
    if c["flip"]:
        tag = _flip_tag(d, c["holder"], c["party"], "TAKEOVER" if c["kind"] == "control" else "PICKUP")
        sh = Image.new("L", (W, H), 0)
        sh.paste(255, (W - tag.width - 64, 104, W - 64, 104 + tag.height))
        img.alpha_composite(solid(sh.filter(ImageFilter.GaussianBlur(14)), (0, 0, 0), 0.5))
        img.alpha_composite(tag, (W - tag.width - 70, 96))
        d = ImageDraw.Draw(img)

    # seat strip on a darkened band
    y0 = 618
    img.alpha_composite(Image.new("RGBA", (W, H - y0 + 30), (0, 0, 0, 120)), (0, y0 - 30))
    d = ImageDraw.Draw(img)
    counts = c["counts"]
    if counts:
        ch = c["chamber"]
        total = SEATS[ch]
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))   # own layer so translucent seats blend instead of punching holes
        seat_strip(ImageDraw.Draw(layer), x, y0 + 8, 1000, seat_order(counts, total),
                   per_row=55 if total == 435 else 25, gap=3 if total == 435 else 6, cols=WIRE_SEAT,
                   highlight=called_index(c, total), ring=YELLOW)
        img.alpha_composite(layer)
        d = ImageDraw.Draw(img)
        rx = x + 1040
        tracked(d, (rx, y0 + 34), CHAMBER_LABEL[ch], font(PLEX, 26), (255, 255, 255, 200), 0.08)
        d.text((rx, y0 + 96), f"D {counts.get('D', 0)}", font=font(ANTON, 64), fill=(255, 255, 255), anchor="ls")
        d.text((rx + 230, y0 + 96), f"R {counts.get('R', 0)}", font=font(ANTON, 64), fill=(255, 255, 255), anchor="ls")
        tracked(d, (rx, y0 + 140), f"{NEEDED[ch]} FOR CONTROL", font(PLEX, 24), (255, 255, 255, 170), 0.06)
    foot = status_text(c) + (f"  ·  {c['tally']}" if c["tally"] else "")
    tracked(d, (x, H - 40), foot.upper(), font(PLEX, 24), (255, 255, 255, 190), 0.04)
    return img


# ================================================================================================= editorial
ED_COL = {"D": (33, 92, 189), "R": (196, 42, 47), "I": (118, 76, 182), "X": (110, 112, 118)}
INK, PAPER, GREY, RULE = (18, 19, 22), (252, 252, 250), (104, 108, 116), (222, 223, 226)
ED_SEAT = {"D": ED_COL["D"], "R": ED_COL["R"], "I": ED_COL["I"], "U": (224, 226, 230)}
AMBER = (176, 120, 0)


def _hemicycle(img, cx, base_y, r_out, order, highlight, rows):
    """Parliament arc, D on the left. Drawn at 3x and downsampled for clean dots."""
    total = len(order)
    r_in = r_out * 0.42
    pts = G._hemicycle_points(n=total, rows=rows, r_in=r_in, r_out=r_out)
    dot = (r_out - r_in) / (rows - 1) * 0.40
    S, pad = 3, int(dot * 3)
    lw, lh = int(2 * r_out + 2 * pad), int(r_out + 2 * pad)
    layer = Image.new("RGBA", (lw * S, lh * S), (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    ox, oy = lw / 2, r_out + pad
    for i, ((a, r), p) in enumerate(zip(pts, order)):
        x, y = ox + r * math.cos(a), oy - r * math.sin(a)
        ld.ellipse([(x - dot) * S, (y - dot) * S, (x + dot) * S, (y + dot) * S], fill=ED_SEAT[p])
        if i == highlight:
            rr = dot + 4
            ld.ellipse([(x - rr) * S, (y - rr) * S, (x + rr) * S, (y + rr) * S], outline=INK, width=3 * S)
    img.alpha_composite(layer.resize((lw, lh), Image.LANCZOS), (int(cx - lw / 2), int(base_y - r_out - pad)))
    d = ImageDraw.Draw(img)
    d.text((cx, base_y - 6), "218", font=font(FRANKLIN, int(r_out * 0.24), 800), fill=INK, anchor="ms")
    tracked(d, (cx, base_y + int(r_out * 0.11)), "TO WIN", font(FRANKLIN, max(14, int(r_out * 0.075)), 700),
            GREY, 0.12, anchor="ms")
    return d


def editorial(c: dict) -> Image.Image:
    key = "X" if c["retracted"] or not c["party"] else c["party"]
    col = ED_COL[key]
    house = c["chamber"] == "house"
    h = 1120 if house else H                     # House cards are taller to fit the hemicycle
    img = Image.new("RGBA", (W, h), PAPER + (255,))
    d = ImageDraw.Draw(img)
    x, rx = 80, 1060
    colw = rx - x - 40

    # masthead
    tracked(d, (x, 104), "DECISION DESK", font(FRANKLIN, 30, 800), INK, 0.16)
    tracked(d, (W - 80, 104), c["when"].strftime("%I:%M PM ET").lstrip("0"), font(FRANKLIN, 30, 600), GREY, 0.1, anchor="rs")
    d.line([(x, 132), (W - 80, 132)], fill=INK, width=3)
    d.line([(x, 140), (W - 80, 140)], fill=INK, width=1)
    tracked(d, (x, 222), kicker(c, " · ").upper(), font(FRANKLIN, 34, 700), GREY, 0.12)

    # the name, one line whenever it fits at a readable size
    if c["retracted"]:
        text = "Too early to call"
    elif c["kind"] == "control":
        text = PARTY_NOUN[c["party"]] + "s"
    else:
        text = c["name"] or f"The {PARTY_NOUN[c['party']]}"
    hf = fit(d, text, colw, SERIF, 168, 104)
    lines = [text]
    if d.textlength(text, font=hf) > colw and " " in text:
        lines = [given(text), surname(text)]
        hf = fit(d, max(lines, key=len), colw, SERIF, 124, 90)
    y = 262
    for ln in lines:
        d.text((x - 4, y), ln, font=hf, fill=INK, anchor="lt")
        y += int(hf.size * 0.98)

    # status: party box with a check (or a dash when retracted), the label, one detail line
    y += 26
    if c["retracted"]:
        label, detail = "CALL RETRACTED", "Votes are still being counted"
    else:
        label = status_text(c)
        detail = (f"Flip from {PARTY_WORD[c['holder']]}" if c["flip"] else f"Rated {c['rating']}" if c["rating"] else "")
    d.rectangle([x, y, x + 64, y + 64], fill=col)
    if c["retracted"]:
        d.line([(x + 18, y + 32), (x + 46, y + 32)], fill=PAPER, width=8)
    else:
        d.line([(x + 16, y + 34), (x + 28, y + 46), (x + 49, y + 20)], fill=PAPER, width=8, joint="curve")
    tracked(d, (x + 92, y + 45), label, font(FRANKLIN, 46, 800), AMBER if c["retracted"] else col, 0.06)
    if detail:
        d.text((x + 92, y + 98), detail, font=font(FRANKLIN, 34, 400), fill=GREY, anchor="ls")

    # where the chamber stands: strip for the Senate and governors, hemicycle for the House
    counts = c["counts"]
    if counts:
        ch = c["chamber"]
        total = SEATS[ch]
        order = seat_order(counts, total)
        if house:
            d = _hemicycle(img, x + 300, h - 160, 300, order, called_index(c, total), rows=12)
            bx, by = x + 660, h - 360
            tracked(d, (bx, by), "HOUSE", font(FRANKLIN, 24, 700), GREY, 0.12)
            d.text((bx, by + 52), f"D {counts.get('D', 0)}", font=font(FRANKLIN, 46, 800), fill=ED_COL["D"], anchor="ls")
            d.text((bx, by + 104), f"R {counts.get('R', 0)}", font=font(FRANKLIN, 46, 800), fill=ED_COL["R"], anchor="ls")
            unc = total - counts.get("D", 0) - counts.get("R", 0) - counts.get("I", 0)
            d.text((bx, by + 142), f"{unc} uncalled", font=font(FRANKLIN, 24, 500), fill=GREY, anchor="ls")
        else:
            sy = h - 238
            lx = x + tracked(d, (x, sy), CHAMBER_LABEL[ch], font(FRANKLIN, 26, 700), GREY, 0.12) + 26
            for txt, colr in ((f"D {counts.get('D', 0)}", ED_COL["D"]), (f"R {counts.get('R', 0)}", ED_COL["R"])):
                d.text((lx, sy), txt, font=font(FRANKLIN, 30, 800), fill=colr, anchor="ls")
                lx += d.textlength(txt, font=font(FRANKLIN, 30, 800)) + 22
            d.text((lx, sy), f"·  {NEEDED[ch]} for control", font=font(FRANKLIN, 26, 500), fill=GREY, anchor="ls")
            seat_strip(d, x, sy + 22, colw, order, per_row=50, gap=4 if total <= 100 else 2, cols=ED_SEAT,
                       highlight=called_index(c, total), ring=INK)

    # footer (sits a little lower on House cards, under the hemicycle)
    fy = h - 132 + (40 if house else 0)
    d.rectangle([x, fy, x + 180, fy + 7], fill=col)
    if c["tally"]:
        d.text((x, fy + 44), c["tally"], font=font(FRANKLIN, 30, 500), fill=GREY, anchor="lm")

    # locator column
    d.line([(rx + 30, 196), (rx + 30, h - 88)], fill=RULE, width=2)
    cx = (rx + 60 + W - 80) / 2
    if c["kind"] == "control":
        loc = us_locator(None, 420, 320, base=col, hi=col, all_hi=True)
    else:
        loc = us_locator(c["state"], 420, 320, base=(222, 224, 228), hi=col)
    img.alpha_composite(loc, (int(cx - loc.width / 2), 220))
    d = ImageDraw.Draw(img)
    if c["state"]:
        size = 260 if house else 200                # House cards are taller, and the district needs the room
        cut = state_cutout(c["state"], c["rid"] if c["kind"] == "house" else None, size, size, (222, 224, 228), col)
        sy = 220 + loc.height + 56
        img.alpha_composite(cut, (int(cx - cut.width / 2), sy))
        d = ImageDraw.Draw(img)
        tracked(d, (cx, sy + cut.height + 54), c["rid"], font(FRANKLIN, 30, 700), INK, 0.12, anchor="ms")
    else:
        tracked(d, (cx, 220 + loc.height + 70), "218 FOR MAJORITY", font(FRANKLIN, 28, 700), INK, 0.1, anchor="ms")
    return img


THEMES = {"wire": wire, "editorial": editorial}


def arrow(d, x: float, y: float, length: float, color, width: int = 4) -> float:
    """A drawn → (the bundled fonts don't all have the glyph). Returns the x after it."""
    d.line([(x, y), (x + length - width * 2, y)], fill=color, width=width)
    d.polygon([(x + length - width * 3.5, y - width * 2), (x + length, y), (x + length - width * 3.5, y + width * 2)], fill=color)
    return x + length


# ================================================================================================= chamber maps
# The map posted after each Senate or governor call (and by /map). graphics.tile_map stays the classic one.
def map_facts(kind: str, calls: dict[str, str], highlight: str | None, when: datetime | None) -> dict:
    """Per-state status for the Senate or governors: (party or None, holder), plus the chamber tally."""
    holders = R.SENATE if kind == "senate" else R.GOVERNOR
    suffix = "SEN" if kind == "senate" else "GOV"
    states = {st: (calls.get(f"{st}-{suffix}"), h) for st, h in holders.items()}
    counts = {"D": 0, "R": 0, "I": 0}
    base = dict(R.SENATE_HOLDOVER) if kind == "senate" else {"D": 0, "R": 0}
    if kind == "governor":
        for p in R.GOVERNOR_NOT_UP.values():
            base[p] += 1
    for party, _h in states.values():
        if party:
            counts[party] += 1
    counts["D"] += base["D"]
    counts["R"] += base["R"]
    when = when or datetime.now(N.ET)
    if when.tzinfo is None:
        when = when.replace(tzinfo=N.ET)
    hl = highlight.split("-")[0] if highlight and highlight.endswith(suffix) else None
    flips = sorted(st for st, (p, h) in states.items() if p and h and p != h)
    return {"kind": kind, "states": states, "counts": counts, "highlight": hl, "flips": flips,
            "uncalled": sum(1 for p, _ in states.values() if not p), "when": when.astimezone(N.ET)}


def _map_heading(m: dict) -> tuple[str, str]:
    if m["kind"] == "senate":
        return "SENATE", f"{NEEDED['senate']} FOR CONTROL"
    return "GOVERNORS", f"{NEEDED['governor']} FOR A MAJORITY"


def wire_map(m: dict) -> Image.Image:
    """Tile cartogram on dark ink: party tiles, a yellow ring on the state just called, yellow corners on flips."""
    MW, MH = 1600, 1000
    img = Image.new("RGBA", (MW, MH), INK_DARK + (255,))
    # tile grid geometry first: the halftone US behind it is fitted to the same footprint
    tile, gap = 80, 8
    gx, gy = 520, 82
    grid_w, grid_h = 12 * (tile + gap) - gap, 8 * (tile + gap) - gap
    us = us_locator(None, grid_w - 60, grid_h - 60, base=(255, 255, 255), hi=(255, 255, 255), all_hi=True).split()[-1]
    origin = (gx + (grid_w - us.width) // 2, gy + (grid_h - us.height) // 2)
    img.alpha_composite(_halftone(us, origin, 18, 6.5, (44, 48, 60), outside=0.0, size=(MW, MH)))
    d = ImageDraw.Draw(img)
    img.alpha_composite(Image.new("RGBA", (MW, 70), (0, 0, 0, 90)))
    d = ImageDraw.Draw(img)
    title, need = _map_heading(m)
    tracked(d, (60, 47), f"DECISION DESK — {m['when'].strftime('%H:%M')} ET — {title} MAP", font(PLEX, 26),
            (255, 255, 255, 230), 0.04)

    # left column: the tally
    x = 60
    tracked(d, (x, 150), f"2026 {title}", font(PLEX, 30), (255, 255, 255, 200), 0.08)
    d.text((x - 4, 320), f"D {m['counts']['D']}", font=font(ANTON, 170), fill=WIRE_SEAT["D"], anchor="ls")
    d.text((x - 4, 500), f"R {m['counts']['R']}", font=font(ANTON, 170), fill=WIRE_SEAT["R"], anchor="ls")
    lines = [f"{m['uncalled']} UNCALLED"] + ([f"I {m['counts']['I']}"] if m["counts"]["I"] else [])
    if m["flips"]:
        lines.append("FLIPS: " + " ".join(m["flips"]))
    for i, ln in enumerate(lines):
        tracked(d, (x, 570 + i * 40), ln, font(PLEX, 26), (255, 255, 255, 200), 0.06)

    # tile grid
    holders = m["states"]
    for st, (c, r) in G.TILES.items():
        tx, ty = gx + c * (tile + gap), gy + r * (tile + gap)
        if st not in holders:
            d.rectangle([tx, ty, tx + tile, ty + tile], fill=(30, 33, 41))
            d.text((tx + tile / 2, ty + tile / 2 + 2), st, font=font(ANTON, 30), fill=(70, 75, 88), anchor="mm")
            continue
        party, holder = holders[st]
        if party:
            d.rectangle([tx, ty, tx + tile, ty + tile], fill=WIRE_SEAT[party])
            if holder and party != holder:
                d.polygon([(tx + tile - 30, ty), (tx + tile, ty), (tx + tile, ty + 30)], fill=YELLOW)
            fg = (255, 255, 255)
        else:
            d.rectangle([tx, ty, tx + tile, ty + tile], fill=(52, 56, 68), outline=(120, 126, 142), width=2)
            fg = (200, 204, 214)
        d.text((tx + tile / 2, ty + tile / 2 + 2), st, font=font(ANTON, 34), fill=fg, anchor="mm")
        if st == m["highlight"]:
            d.rectangle([tx - 7, ty - 7, tx + tile + 7, ty + tile + 7], outline=YELLOW, width=5)

    # seat strip band
    y0 = 806
    img.alpha_composite(Image.new("RGBA", (MW, MH - y0 + 24), (0, 0, 0, 110)), (0, y0 - 24))
    d = ImageDraw.Draw(img)
    total = SEATS[m["kind"]]
    layer = Image.new("RGBA", (MW, MH), (0, 0, 0, 0))
    seat_strip(ImageDraw.Draw(layer), x, y0 + 4, 1200, seat_order(m["counts"], total),
               per_row=50, gap=5 if total == 100 else 6, cols=WIRE_SEAT)
    img.alpha_composite(layer)
    d = ImageDraw.Draw(img)
    rx = x + 1240
    tracked(d, (rx, y0 + 30), title, font(PLEX, 26), (255, 255, 255, 200), 0.08)
    tracked(d, (rx, y0 + 70), need, font(PLEX, 24), (255, 255, 255, 170), 0.06)
    lx, ly, sw = x, MH - 52, 24
    for label in ("CALLED", "UNCALLED", "FLIP", "JUST CALLED"):
        if label == "CALLED":
            d.rectangle([lx, ly, lx + sw, ly + sw], fill=WIRE_SEAT["D"])
        elif label == "UNCALLED":
            d.rectangle([lx, ly, lx + sw, ly + sw], fill=(52, 56, 68), outline=(120, 126, 142), width=2)
        elif label == "FLIP":
            d.rectangle([lx, ly, lx + sw, ly + sw], fill=WIRE_SEAT["R"])
            d.polygon([(lx + sw - 10, ly), (lx + sw, ly), (lx + sw, ly + 10)], fill=YELLOW)
        else:
            d.rectangle([lx, ly, lx + sw, ly + sw], fill=(52, 56, 68))
            d.rectangle([lx - 4, ly - 4, lx + sw + 4, ly + sw + 4], outline=YELLOW, width=3)
        lx += sw + 14
        lx += tracked(d, (lx, ly + sw / 2 + 1), label, font(PLEX, 22), (255, 255, 255, 170), 0.04, anchor="lm") + 40
    return img


def _geo_projection(bw: int, bh: int):
    """Projection for the lower 48 fitted to bw x bh: returns (to_px, width, height)."""
    rings = G._state_rings(None)
    lats = [y for r in rings for _, y in r]
    k = math.cos(math.radians((max(lats) + min(lats)) / 2))
    xs = [x * k for r in rings for x, _ in r]
    ys = [-y for r in rings for _, y in r]
    sc = min(bw / (max(xs) - min(xs)), bh / (max(ys) - min(ys)))

    def to_px(lon, lat):
        return (lon * k - min(xs)) * sc, (-lat - min(ys)) * sc
    return to_px, int((max(xs) - min(xs)) * sc) + 2, int((max(ys) - min(ys)) * sc) + 2


def _centroid(points):
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return sum(xs) / len(xs), sum(ys) / len(ys)


# AP style state abbreviations, used when a full name doesn't fit
AP_STATE = {"AL": "Ala.", "AZ": "Ariz.", "AR": "Ark.", "CA": "Calif.", "CO": "Colo.", "CT": "Conn.", "DE": "Del.",
            "FL": "Fla.", "GA": "Ga.", "IL": "Ill.", "IN": "Ind.", "KS": "Kan.", "KY": "Ky.", "LA": "La.", "MD": "Md.",
            "MA": "Mass.", "MI": "Mich.", "MN": "Minn.", "MS": "Miss.", "MO": "Mo.", "MT": "Mont.", "NE": "Neb.",
            "NV": "Nev.", "NH": "N.H.", "NJ": "N.J.", "NM": "N.M.", "NY": "N.Y.", "NC": "N.C.", "ND": "N.D.",
            "OK": "Okla.", "OR": "Ore.", "PA": "Pa.", "RI": "R.I.", "SC": "S.C.", "SD": "S.D.", "TN": "Tenn.",
            "VT": "Vt.", "VA": "Va.", "WA": "Wash.", "WV": "W.Va.", "WI": "Wis.", "WY": "Wyo."}


def _flip_corner(shape_mask: Image.Image, size: float, color) -> Image.Image:
    """A folded-corner triangle at the shape's top-right extreme, clipped to the shape (the editorial flip mark)."""
    bbox = shape_mask.getbbox()
    if not bbox:
        return Image.new("RGBA", shape_mask.size, (0, 0, 0, 0))
    px = shape_mask.load()
    # the shape pixel furthest toward the top-right
    best, best_score = None, None
    x0, y0, x1, y1 = bbox
    step = max(1, (x1 - x0) // 200)
    for yy in range(y0, y1, step):
        for xx in range(x1 - 1, x0 - 1, -step):
            if px[xx, yy] > 128:
                if best_score is None or xx - yy > best_score:
                    best, best_score = (xx, yy), xx - yy
                break
    vx, vy = best
    tri = Image.new("L", shape_mask.size, 0)
    ImageDraw.Draw(tri).polygon([(vx - size * 1.6, vy - size * 0.2), (vx + size * 0.2, vy - size * 0.2),
                                 (vx + size * 0.2, vy + size * 1.6)], fill=255)
    return solid(ImageChops.multiply(tri, shape_mask), color)


def editorial_map(m: dict) -> Image.Image:
    """A results-page map: called states in party color, uncalled races grey, states without a race near-white,
    an ink outline on the state just called and an ink dot on flips. Alaska and Hawaii as insets."""
    MW, MH = 1600, 1120
    img = Image.new("RGBA", (MW, MH), PAPER + (255,))
    d = ImageDraw.Draw(img)
    x = 80
    title, need = _map_heading(m)
    tracked(d, (x, 104), "DECISION DESK", font(FRANKLIN, 30, 800), INK, 0.16)
    tracked(d, (MW - 80, 104), m["when"].strftime("%I:%M PM ET").lstrip("0"), font(FRANKLIN, 30, 600), GREY, 0.1, anchor="rs")
    d.line([(x, 132), (MW - 80, 132)], fill=INK, width=3)
    d.line([(x, 140), (MW - 80, 140)], fill=INK, width=1)
    tracked(d, (x, 222), f"2026 {title} · RESULTS SO FAR", font(FRANKLIN, 34, 700), GREY, 0.12)
    # headline tally, set in the serif
    hf = font(SERIF, 132)
    hx = x - 4
    for lab, party in (("Democrats", "D"), ("Republicans", "R")):
        n = str(m["counts"][party])
        d.text((hx, 262), n, font=hf, fill=ED_COL[party], anchor="lt")
        hx += d.textlength(n, font=hf) + 16
        d.text((hx, 300), lab, font=font(FRANKLIN, 34, 600), fill=INK, anchor="lt")
        hx += d.textlength(lab, font=font(FRANKLIN, 34, 600)) + 60
    extra = f"{m['uncalled']} uncalled" + (f" · {m['counts']['I']} independent" if m["counts"]["I"] else "")
    d.text((hx, 300), extra, font=font(FRANKLIN, 34, 400), fill=GREY, anchor="lt")

    # the map
    to_px, mw, mh = _geo_projection(1060, 560)
    ox, oy = x, 420
    S = 3
    layer = Image.new("RGBA", (mw * S, mh * S), (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    marks = []
    holders = m["states"]

    def fill_for(st):
        if st not in holders:
            return (240, 240, 238)
        party, _ = holders[st]
        return ED_COL[party] if party else (206, 208, 214)
    for st, shapes in G._SHAPES.items():
        if st in ("AK", "HI"):
            continue
        pts_all = []
        for ring in shapes:
            if len(ring) > 2:
                poly = [(px * S, py * S) for px, py in (to_px(lon, lat) for lon, lat in ring)]
                ld.polygon(poly, fill=fill_for(st) + (255,), outline=(255, 255, 255, 255), width=2 * S)
                pts_all.append(poly)
        if pts_all and st in holders:
            marks.append((st, max(pts_all, key=len)))
    for st, poly in marks:          # second pass so outlines sit on top of neighbours
        party, holder = holders[st]
        if st == m["highlight"]:
            ld.polygon(poly, outline=INK + (255,), width=5 * S)
        if party and holder and party != holder:
            polys = [[(px * S, py * S) for px, py in (to_px(lon, lat) for lon, lat in ring)]
                     for ring in G._SHAPES[st] if len(ring) > 2]
            bx0 = int(min(x for p in polys for x, _ in p)) - 4
            by0 = int(min(y for p in polys for _, y in p)) - 4
            bx1 = int(max(x for p in polys for x, _ in p)) + 4
            by1 = int(max(y for p in polys for _, y in p)) + 4
            sm = Image.new("L", (bx1 - bx0, by1 - by0), 0)        # mask cropped to the state, so this stays fast
            for poly_pts in polys:
                ImageDraw.Draw(sm).polygon([(x - bx0, y - by0) for x, y in poly_pts], fill=255)
            sm = sm.filter(ImageFilter.MinFilter(2 * S + 1))      # keep the white state border visible around it
            layer.alpha_composite(_flip_corner(sm, 16 * S, INK), (bx0, by0))
    img.alpha_composite(layer.resize((mw, mh), Image.LANCZOS), (ox, oy))
    # Alaska and Hawaii insets, bottom-left of the map
    for i, st in enumerate(("AK", "HI")):
        if st not in holders and st not in G._SHAPES:
            continue
        party, holder = holders.get(st, (None, None))
        col = fill_for(st)
        mask = G._shape_mask(st, 150 if st == "AK" else 110, 110)
        ix, iy = ox + (0 if st == "AK" else 180), oy + mh - 120
        img.alpha_composite(solid(mask, col), (ix, iy))
        if st == m["highlight"]:
            edge = ImageChops.subtract(mask.filter(ImageFilter.MaxFilter(9)), mask)
            img.alpha_composite(solid(edge, INK), (ix, iy))
        if party and holder and party != holder:
            img.alpha_composite(_flip_corner(mask, 14, INK), (ix, iy))
    d = ImageDraw.Draw(img)

    # right column: the flips so far
    rx = x + 1140
    d.line([(rx - 20, 420), (rx - 20, 1000)], fill=RULE, width=2)
    ky = 450
    d.polygon([(rx, ky - 20), (rx + 18, ky - 20), (rx + 18, ky - 2)], fill=INK)
    tracked(d, (rx + 28, ky), "FLIPS", font(FRANKLIN, 24, 700), GREY, 0.12)
    if not m["flips"]:
        d.text((rx, ky + 30), "None yet", font=font(FRANKLIN, 28, 500), fill=GREY, anchor="lt")
    else:
        for i, st in enumerate(m["flips"]):
            party, holder = holders[st]
            base = ky + 54 + i * 38                    # shared text baseline for the row
            f8, f6 = font(FRANKLIN, 28, 800), font(FRANKLIN, 28, 600)
            # fixed columns, so every row's swap lines up: old party, arrow, new party (letters centred in slots)
            new_x = MW - 80 - 12
            old_x = new_x - 74
            name = R.STATE_NAMES[st]
            if d.textlength(name, font=f6) > old_x - 14 - rx - 16:
                name = AP_STATE.get(st, st)            # New Hampshire -> N.H., in the paper's own style
            d.text((rx, base), name, font=f6, fill=INK, anchor="ls")
            d.text((old_x, base), holder, font=f8, fill=ED_COL[holder], anchor="ms")
            d.text((new_x, base), party, font=f8, fill=ED_COL[party], anchor="ms")
            mid = base + f8.getbbox("D", anchor="ls")[1] / 2     # half the cap height above the baseline
            arrow(d, old_x + 19, mid, new_x - old_x - 38, INK, 3)

    # seat strip across the bottom
    sy = MH - 100
    lx = x + tracked(d, (x, sy), title, font(FRANKLIN, 26, 700), GREY, 0.12) + 26
    for txt, colr in ((f"D {m['counts']['D']}", ED_COL["D"]), (f"R {m['counts']['R']}", ED_COL["R"])):
        d.text((lx, sy), txt, font=font(FRANKLIN, 30, 800), fill=colr, anchor="ls")
        lx += d.textlength(txt, font=font(FRANKLIN, 30, 800)) + 22
    d.text((lx, sy), "·  " + need.lower(), font=font(FRANKLIN, 26, 500), fill=GREY, anchor="ls")
    total = SEATS[m["kind"]]
    seat_strip(d, x, sy + 22, MW - 2 * x, seat_order(m["counts"], total), per_row=total, gap=4 if total == 50 else 3,
               cols=ED_SEAT)
    return img


MAPS = {"wire": wire_map, "editorial": editorial_map}

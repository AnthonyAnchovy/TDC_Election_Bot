"""Image generation: call cards, state tile maps (Senate/Governor), House hemicycle.

Everything is drawn with Pillow, so there are no map files or browsers to install.
Custom art: drop a finished graphic at assets/calls/<RACE>_<PARTY>.png (e.g. TX-SEN_D.png)
and it is posted as-is instead of the generated card. Candidate photos go in
assets/candidates/<RACE>_<PARTY>.png or .jpg and get placed on the generated card.
"""
from __future__ import annotations

import io
import math
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

import races as R

ASSETS = Path(__file__).parent / "assets"

BG = (17, 19, 24)
PANEL = (28, 31, 38)
WHITE = (240, 242, 245)
MUTED = (140, 146, 158)
PARTY = {"D": (43, 108, 217), "R": (214, 58, 58), "I": (154, 92, 214)}
PENDING = (88, 93, 105)
NO_RACE = (36, 39, 46)

# Tile-grid layout (col, row) — a standard cartogram so every state is the same size
TILES = {
    "AK": (0, 0), "ME": (11, 0),
    "WI": (6, 1), "VT": (10, 1), "NH": (11, 1),
    "WA": (1, 2), "ID": (2, 2), "MT": (3, 2), "ND": (4, 2), "MN": (5, 2), "IL": (6, 2),
    "MI": (7, 2), "NY": (9, 2), "MA": (10, 2),
    "OR": (1, 3), "NV": (2, 3), "WY": (3, 3), "SD": (4, 3), "IA": (5, 3), "IN": (6, 3),
    "OH": (7, 3), "PA": (8, 3), "NJ": (9, 3), "CT": (10, 3), "RI": (11, 3),
    "CA": (1, 4), "UT": (2, 4), "CO": (3, 4), "NE": (4, 4), "MO": (5, 4), "KY": (6, 4),
    "WV": (7, 4), "VA": (8, 4), "MD": (9, 4), "DE": (10, 4),
    "AZ": (2, 5), "NM": (3, 5), "KS": (4, 5), "AR": (5, 5), "TN": (6, 5), "NC": (7, 5),
    "SC": (8, 5),
    "OK": (4, 6), "LA": (5, 6), "MS": (6, 6), "AL": (7, 6), "GA": (8, 6),
    "HI": (0, 7), "TX": (4, 7), "FL": (9, 7),
}

_BOLD = ["arialbd.ttf", "Arial Bold.ttf", "segoeuib.ttf", "DejaVuSans-Bold.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]
_REG = ["arial.ttf", "Arial.ttf", "segoeui.ttf", "DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]
_font_cache: dict = {}


def font(size: int, bold: bool = True):
    key = (size, bold)
    if key not in _font_cache:
        for name in (_BOLD if bold else _REG):
            try:
                _font_cache[key] = ImageFont.truetype(name, size)
                break
            except OSError:
                continue
        else:
            _font_cache[key] = ImageFont.load_default(size=size)
    return _font_cache[key]


def fit(draw, text, max_w, start, bold=True, min_size=18):
    size = start
    while size > min_size and draw.textlength(text, font=font(size, bold)) > max_w:
        size -= 2
    return font(size, bold)


def to_png(img: Image.Image) -> io.BytesIO:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    buf.seek(0)
    return buf


def _find_asset(folder: str, race_id: str, party: str) -> Path | None:
    for ext in ("png", "jpg", "jpeg", "webp", "gif"):
        p = ASSETS / folder / f"{race_id}_{party}.{ext}"
        if p.exists():
            return p
    return None


# ---------------------------------------------------------------------------------
# Call cards (styled after Anthony's "Key Race Alert" card)
# ---------------------------------------------------------------------------------
YELLOW = (250, 214, 62)
GREEN = (52, 199, 89)
BG_GRAD = {"D": ((14, 52, 130), (24, 98, 196)), "R": ((110, 18, 24), (196, 44, 48)),
           "I": ((62, 30, 110), (128, 72, 190)), "X": ((40, 43, 50), (84, 89, 100))}
PANEL_FILL = {"D": (13, 30, 66), "R": (58, 14, 18), "I": (36, 20, 64), "X": (26, 28, 34)}


def _gradient(size, top, bottom):
    w, h = size
    col = Image.new("RGB", (1, h))
    for y in range(h):
        t = y / (h - 1)
        col.putpixel((0, y), tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)))
    return col.resize((w, h))


def _check(d, x, y, s, fill=GREEN):
    pts = [(x, y + s * .55), (x + s * .18, y + s * .38), (x + s * .38, y + s * .58),
           (x + s * .82, y + s * .08), (x + s, y + s * .26), (x + s * .38, y + s * .92)]
    d.polygon(pts, fill=fill, outline=(0, 0, 0), width=4)


def _split_name(name):
    parts = name.upper().split()
    if len(parts) <= 1:
        return [name.upper()]
    return [" ".join(parts[:-1]), parts[-1]]


_SHAPES = None


def _state_rings(state: str | None):
    """Polygons (lists of (x, y)) for a state, or the lower 48 when state is None."""
    global _SHAPES
    if _SHAPES is None:
        p = Path(__file__).parent / "state_shapes.json"
        _SHAPES = __import__("json").loads(p.read_text()) if p.exists() else {}
    keys = [state] if state else [k for k in _SHAPES if k not in ("AK", "HI")]
    rings = []
    for k in keys:
        for ring in _SHAPES.get(k, []):
            rings.append([((x - 360) if (k == "AK" and x > 0) else x, y) for x, y in ring])
    return rings


def _shape_mask(state: str | None, box_w: int, box_h: int) -> Image.Image | None:
    """An L-mode mask of the state's outline, fitted inside box_w x box_h, tight-cropped."""
    rings = _state_rings(state)
    if not rings:
        return None
    lats = [y for r in rings for _, y in r]
    k = math.cos(math.radians((max(lats) + min(lats)) / 2))
    pts = [[(x * k, -y) for x, y in r] for r in rings]
    xs = [p[0] for r in pts for p in r]
    ys = [p[1] for r in pts for p in r]
    sc = min(box_w / (max(xs) - min(xs)), box_h / (max(ys) - min(ys)))
    w = int((max(xs) - min(xs)) * sc) + 4
    h = int((max(ys) - min(ys)) * sc) + 4
    m = Image.new("L", (w * 2, h * 2), 0)  # 2x supersample for smooth edges
    md = ImageDraw.Draw(m)
    for r in pts:
        if len(r) > 2:
            md.polygon([((x - min(xs)) * sc * 2 + 4, (y - min(ys)) * sc * 2 + 4) for x, y in r], fill=255)
    return m.resize((w, h), Image.LANCZOS)


def _rounded_mask(size, box, radius):
    m = Image.new("L", size, 0)
    ImageDraw.Draw(m).rounded_rectangle(box, radius=radius, fill=255)
    return m


def _knockout(size, shape_mask: Image.Image, text_mask: Image.Image, color=(255, 255, 255)):
    """A solid layer in shape_mask with text_mask punched out (fully transparent letters)."""
    from PIL import ImageChops
    layer = Image.new("RGBA", size, color + (0,))
    layer.putalpha(ImageChops.subtract(shape_mask, text_mask))
    return layer


def _text_mask(size, xy, text, fnt, anchor="mm"):
    m = Image.new("L", size, 0)
    ImageDraw.Draw(m).text(xy, text, font=fnt, fill=255, anchor=anchor)
    return m


def _glow(mask: Image.Image, color, radius=18, strength=1.0):
    from PIL import ImageFilter
    a = mask.filter(ImageFilter.GaussianBlur(radius)).point(lambda v: min(255, int(v * strength)))
    g = Image.new("RGBA", mask.size, color + (0,))
    g.putalpha(a)
    return g


GLASS = {"D": ((20, 64, 160), (46, 120, 232)), "R": ((128, 18, 30), (222, 58, 58)),
         "I": ((70, 30, 130), (150, 90, 220)), "X": ((44, 47, 56), (92, 98, 112))}


def call_card(race_id: str, party: str, *, candidate: str | None = None,
              tally: str | None = None, when: datetime | None = None,
              retracted: bool = False) -> io.BytesIO:
    """Transparent PNG: a floating glass card with knockout lettering and the state's silhouette."""
    from PIL import ImageFilter
    if not retracted:
        custom = _find_asset("calls", race_id, party)
        if custom:
            return io.BytesIO(custom.read_bytes())

    race = R.RACES[race_id]
    key = "X" if retracted else party
    W, H = 1480, 900
    px0, py0, px1, py1 = 50, 70, W - 130, H - 60         # the glass panel
    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    panel = _rounded_mask((W, H), [px0, py0, px1, py1], 46)

    # 1. soft drop shadow under the panel (invisible on dark Discord, a lift on light mode)
    sh = panel.filter(ImageFilter.GaussianBlur(22)).point(lambda v: int(v * 0.55))
    shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0)); shadow.putalpha(sh)
    canvas.alpha_composite(shadow, (0, 16))

    # 2. glass: diagonal party gradient at ~92% opacity
    top, bot = GLASS[key]
    grad = Image.new("RGBA", (W, H))
    gd = ImageDraw.Draw(grad)
    for i in range(W + H):
        t = i / (W + H)
        c = tuple(int(top[j] + (bot[j] - top[j]) * t) for j in range(3))
        gd.line([(i, 0), (i - H, H)], fill=c + (255,), width=2)
    grad.putalpha(panel.point(lambda v: int(v * 0.92)))
    canvas.alpha_composite(grad)

    # 3. subtle diagonal light streaks inside the glass
    streak = Image.new("L", (W, H), 0)
    sd = ImageDraw.Draw(streak)
    for x in range(-H, W, 46):
        sd.line([(x, H), (x + H, 0)], fill=16, width=14)
    from PIL import ImageChops
    streak = ImageChops.multiply(streak, panel)
    sl = Image.new("RGBA", (W, H), (255, 255, 255, 0)); sl.putalpha(streak)
    canvas.alpha_composite(sl)

    # 4. giant ghost silhouette of the state, bleeding off the right of the panel (clipped to it)
    state = race.get("state")
    ghost = _shape_mask(state, 760, 860)
    if ghost:
        full = Image.new("L", (W, H), 0)
        gx, gy = px1 - ghost.width + 110, (H - ghost.height) // 2 + 10
        full.paste(ghost, (gx, gy))
        inside = ImageChops.multiply(full, panel).point(lambda v: int(v * 0.16))
        gh = Image.new("RGBA", (W, H), (255, 255, 255, 0)); gh.putalpha(inside)
        canvas.alpha_composite(gh)
        # where the state spills past the glass, it continues as a party-colored outline
        edge = ImageChops.subtract(full.filter(ImageFilter.MaxFilter(7)), full.filter(ImageFilter.MinFilter(3)))
        outside = ImageChops.multiply(edge, ImageChops.invert(panel))
        ol = Image.new("RGBA", (W, H), GLASS[key][1] + (0,)); ol.putalpha(outside)
        canvas.alpha_composite(ol)

    d = ImageDraw.Draw(canvas)

    # 5. "KEY RACE ALERT" knockout pill straddling the panel's top edge
    tag = "CALL RETRACTED" if retracted else "KEY RACE ALERT"
    tf = font(34)
    tw = int(d.textlength(tag, font=tf)) + 64
    pill = _rounded_mask((W, H), [px0 + 56, py0 + 40, px0 + 56 + tw, py0 + 98], 29)
    words = _text_mask((W, H), (px0 + 56 + tw / 2, py0 + 69), tag, tf)
    canvas.alpha_composite(_knockout((W, H), pill, words, (255, 255, 255)))

    # 6. solid glowing state badge in the top-right of the glass
    badge = _shape_mask(state, 210, 170)
    if badge:
        bx, by = px1 - badge.width - 56, py0 + 40
        bm = Image.new("L", (W, H), 0); bm.paste(badge, (bx, by))
        canvas.alpha_composite(_glow(bm, (255, 255, 255), 16, 0.8))
        solid = Image.new("RGBA", (W, H), (255, 255, 255, 0)); solid.putalpha(bm)
        canvas.alpha_composite(solid)
    d = ImageDraw.Draw(canvas)

    # 7. race title
    tx = px0 + 60
    title = race["label"].upper().replace(" (SPECIAL)", " \u00b7 SPECIAL")
    d.text((tx, py0 + 122), title, font=fit(d, title, (px1 - 300) - tx, 64), fill=(255, 255, 255, 235), anchor="lt")

    # 8. optional headshot: circular, with a white ring and glow
    photo = None if retracted else _find_asset("candidates", race_id, party)
    if photo:
        size = 300
        pic = ImageOps.fit(Image.open(photo).convert("RGBA"), (size, size))
        circle = Image.new("L", (size, size), 0)
        ImageDraw.Draw(circle).ellipse([0, 0, size - 1, size - 1], fill=255)
        pic.putalpha(circle)
        hx, hy = tx, py0 + 240
        ring = Image.new("L", (W, H), 0)
        ImageDraw.Draw(ring).ellipse([hx - 10, hy - 10, hx + size + 10, hy + size + 10], fill=255)
        canvas.alpha_composite(_glow(ring, (255, 255, 255), 14, 0.7))
        rl = Image.new("RGBA", (W, H), (255, 255, 255, 0)); rl.putalpha(ring)
        canvas.alpha_composite(rl)
        canvas.alpha_composite(pic, (hx, hy))
        tx = hx + size + 60
        d = ImageDraw.Draw(canvas)

    max_w = px1 - 70 - tx
    if retracted:
        d.text((tx, py0 + 250), "TOO EARLY", font=fit(d, "TOO EARLY", max_w, 130), fill=YELLOW + (255,), anchor="lt")
        d.text((tx, py0 + 400), "TO CALL", font=fit(d, "TO CALL", max_w, 130), fill=(255, 255, 255, 255), anchor="lt")
        if party:
            note = f"Previous call: {R.PARTY_NAMES[party]}"
            d.text((tx, py0 + 560), note, font=fit(d, note, max_w, 40, bold=False),
                   fill=(255, 255, 255, 170), anchor="lt")
    else:
        _check(d, tx, py0 + 252, 78)
        d.text((tx + 98, py0 + 294), "WINNER", font=fit(d, "WINNER", max_w - 98, 70), fill=YELLOW + (255,), anchor="lm")
        lines = _split_name(candidate) if candidate else [R.PARTY_NAMES[party].upper()]
        y = py0 + 350
        for line in lines:
            f = fit(d, line, max_w, 132 if len(lines) > 1 else 150)
            # soft shadow behind the name for depth
            sm = _text_mask((W, H), (tx + 4, y + 6), line, f, anchor="lt")
            canvas.alpha_composite(_glow(sm, (0, 0, 0), 8, 0.6))
            d = ImageDraw.Draw(canvas)
            d.text((tx, y), line, font=f, fill=(255, 255, 255, 255), anchor="lt")
            y += f.size + 14
        if not candidate:
            d.text((tx, y), "PROJECTED", font=fit(d, "PROJECTED", max_w, 56), fill=(255, 255, 255, 150), anchor="lt")

    # 9. translucent footer strip with tally + time, and a knockout FLIP/HOLD badge
    strip = _rounded_mask((W, H), [px0, py1 - 86, px1, py1], 46)
    cover = Image.new("L", (W, H), 0)
    ImageDraw.Draw(cover).rectangle([px0, py1 - 86, px1, py1], fill=255)
    strip = ImageChops.multiply(cover, panel).point(lambda v: int(v * 0.28))
    st = Image.new("RGBA", (W, H), (0, 0, 0, 0)); st.putalpha(strip)
    canvas.alpha_composite(st)
    d = ImageDraw.Draw(canvas)
    foot = " \u00b7 ".join(x for x in [tally, (when or datetime.now()).strftime("%I:%M %p").lstrip("0")] if x)
    d.text((px0 + 60, py1 - 43), foot, font=font(28, False), fill=(255, 255, 255, 200), anchor="lm")
    h = race.get("holder")
    if not retracted and h:
        label = f"FLIP {h} \u2192 {party}" if h != party else f"{party} HOLD"
        bf = font(34)
        bw = int(d.textlength(label, font=bf)) + 56
        bx1 = px1 - 50
        pm = _rounded_mask((W, H), [bx1 - bw, py1 - 70, bx1, py1 - 16], 27)
        tm = _text_mask((W, H), (bx1 - bw / 2, py1 - 43), label, bf)
        canvas.alpha_composite(_knockout((W, H), pm, tm, YELLOW if h != party else (255, 255, 255)))

    # 10. meme cutout: allowed to break out past the panel's bottom-left edge
    meme = None if retracted else _find_asset("memes", race_id, party)
    if meme:
        m = Image.open(meme).convert("RGBA")
        m.thumbnail((430, 560))
        canvas.alpha_composite(m, (max(0, px0 - 60), H - m.height))

    return to_png(canvas)


# ---------------------------------------------------------------------------------
# Tile maps (Senate, Governor)
# ---------------------------------------------------------------------------------
def tile_map(kind: str, calls: dict[str, str], *, title: str | None = None,
             marks: dict[str, bool] | None = None, pending_word: str = "uncalled",
             width: int | None = None, brand: bool = True) -> io.BytesIO:
    """calls: race -> party. marks: race -> True (pick right) / False (pick wrong), drawn as badges.
    width: downscale the PNG (used for the fast-updating prediction picker)."""
    assert kind in ("senate", "governor")
    marks = marks or {}
    holders = R.SENATE if kind == "senate" else R.GOVERNOR
    suffix = "SEN" if kind == "senate" else "GOV"

    W, H = 1200, 1030
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    title = (title or ("2026 SENATE" if kind == "senate" else "2026 GOVERNORS")).upper()
    d.text((52, 56), title, font=fit(d, title, W - 400, 48), fill=WHITE, anchor="lm")
    if brand:
        d.text((W - 52, 56), "DECISION DESK", font=font(26), fill=MUTED, anchor="rm")

    tile, gap = 84, 8
    x0, y0 = 52, 112
    flips = 0
    for st, (c, r) in TILES.items():
        x, y = x0 + c * (tile + gap), y0 + r * (tile + gap)
        rid = f"{st}-{suffix}"
        if st not in holders:
            fill, txt = NO_RACE, (78, 83, 94)
        elif rid in calls:
            fill, txt = PARTY[calls[rid]], WHITE
        else:
            fill, txt = PENDING, WHITE
        d.rounded_rectangle([x, y, x + tile, y + tile], radius=8, fill=fill)
        if st in holders and rid not in calls:
            d.rounded_rectangle([x, y, x + tile, y + tile], radius=8, outline=(130, 136, 150), width=2)
        if rid in calls and holders.get(st) and calls[rid] != holders[st]:
            flips += 1
            d.polygon([(x + tile - 30, y), (x + tile - 8, y), (x + tile, y + 8), (x + tile, y + 30)],
                      fill=WHITE)
        d.text((x + tile / 2, y + tile / 2), st, font=font(28), fill=txt, anchor="mm")
        if rid in marks:
            ok = marks[rid]
            bx, by_ = x + 4, y + tile - 30
            d.ellipse([bx, by_, bx + 26, by_ + 26], fill=(52, 199, 89) if ok else (20, 20, 20),
                      outline=WHITE, width=2)
            d.text((bx + 13, by_ + 13), "\u2713" if ok else "\u2717", font=font(18), fill=WHITE, anchor="mm")

    # tally
    up = [f"{st}-{suffix}" for st in holders]
    called = [calls[rid] for rid in up if rid in calls]
    if kind == "senate":
        base, total, control = dict(R.SENATE_HOLDOVER), 100, R.SENATE_MAJORITY
        base_note = "includes 65 seats not up"
    else:
        base = {"D": 0, "R": 0}
        for p in R.GOVERNOR_NOT_UP.values():
            base[p] += 1
        total, control = 50, 26
        base_note = "includes 14 governorships not up"
    dem = base["D"] + called.count("D")
    rep = base["R"] + called.count("R")
    ind = called.count("I")
    unc = total - dem - rep - ind

    by = y0 + 8 * (tile + gap) + 30
    d.text((52, by), f"DEM {dem}", font=font(40), fill=PARTY["D"], anchor="lt")
    d.text((W - 52, by), f"REP {rep}", font=font(40), fill=PARTY["R"], anchor="rt")
    mid = f"{unc} {pending_word}" + (f"  ·  IND {ind}" if ind else "") + (f"  ·  {flips} flip{'s' * (flips != 1)}" if flips else "")
    d.text((W / 2, by + 20), mid, font=font(26, False), fill=MUTED, anchor="mm")

    bar_y, bar_h, bw = by + 62, 34, W - 104
    segs = [("D", dem), ("I", ind), ("U", unc), ("R", rep)]
    bx = 52
    for p, n in segs:
        w = bw * n / total
        if w > 0:
            d.rectangle([bx, bar_y, bx + w, bar_y + bar_h], fill=PENDING if p == "U" else PARTY[p])
        bx += w
    cx = 52 + bw / 2
    d.line([cx, bar_y - 8, cx, bar_y + bar_h + 8], fill=WHITE, width=3)
    d.text((cx, bar_y + bar_h + 26), f"{control} for {'control' if kind == 'senate' else 'a majority'}  ·  {base_note}",
           font=font(22, False), fill=MUTED, anchor="mm")
    if width:
        img = img.resize((width, round(H * width / W)), Image.LANCZOS)
    return to_png(img)


# ---------------------------------------------------------------------------------
# House hemicycle
# ---------------------------------------------------------------------------------
def _hemicycle_points(n=435, rows=13, r_in=210, r_out=520):
    radii = [r_in + (r_out - r_in) * i / (rows - 1) for i in range(rows)]
    raw = [n * r / sum(radii) for r in radii]
    counts = [int(x) for x in raw]
    for i in sorted(range(rows), key=lambda i: raw[i] - counts[i], reverse=True)[: n - sum(counts)]:
        counts[i] += 1
    pts = []
    for r, k in zip(radii, counts):
        for j in range(k):
            a = math.pi * (1 - j / (k - 1)) if k > 1 else math.pi / 2
            pts.append((a, r))
    pts.sort(key=lambda p: (-p[0], p[1]))
    return pts


def house_chart(calls: dict[str, str]) -> io.BytesIO:
    house = [calls[rid] for rid in R.races_of("house") if rid in calls]
    dem, rep, ind = house.count("D"), house.count("R"), house.count("I")
    unc = R.HOUSE_TOTAL - dem - rep - ind

    W, H = 1200, 760
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.text((52, 56), "2026 HOUSE", font=font(48), fill=WHITE, anchor="lm")
    d.text((W - 52, 56), "DECISION DESK", font=font(26), fill=MUTED, anchor="rm")

    colors = ([PARTY["D"]] * dem + [PARTY["I"]] * ind + [PENDING] * unc + [PARTY["R"]] * rep)
    cx, cy, dot = W / 2, 660, 11
    for (a, r), col in zip(_hemicycle_points(), colors):
        x, y = cx + r * math.cos(a), cy - r * math.sin(a)
        d.ellipse([x - dot, y - dot, x + dot, y + dot], fill=col)

    d.text((cx, cy - 110), f"{R.HOUSE_MAJORITY}", font=font(72), fill=WHITE, anchor="mm")
    d.text((cx, cy - 55), "for majority", font=font(26, False), fill=MUTED, anchor="mm")
    d.text((cx, cy - 15), f"{unc} uncalled" + (f" · IND {ind}" if ind else ""),
           font=font(26, False), fill=MUTED, anchor="mm")
    d.text((52, cy + 50), f"DEM {dem}", font=font(44), fill=PARTY["D"], anchor="lm")
    d.text((W - 52, cy + 50), f"REP {rep}", font=font(44), fill=PARTY["R"], anchor="rm")
    return to_png(img)


# ---------------------------------------------------------------------------------
# Prediction card: one person's full set of picks in a single image
# ---------------------------------------------------------------------------------
def prediction_card(name: str, picks: dict[str, str], house_seats: int | None = None,
                    marks: dict[str, bool] | None = None, battleground: list | None = None) -> io.BytesIO:
    """Senate map (left), governor map + House pick + battleground chips (right)."""
    marks = marks or {}
    battleground = battleground or []
    W, H = 1860, 1190
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    title = f"{name.upper()}'S 2026 PREDICTIONS"
    d.text((40, 48), title, font=fit(d, title, W - 420, 54), fill=WHITE, anchor="lm")
    d.text((W - 40, 48), "DECISION DESK", font=font(28), fill=MUTED, anchor="rm")
    if marks:
        right = sum(1 for v in marks.values() if v)
        d.text((W - 40, 84), f"{right}/{len(marks)} right so far", font=font(24, False), fill=MUTED, anchor="rm")

    def sub(kind):
        return {r: p for r, p in picks.items() if R.RACES.get(r, {}).get("type") == kind}

    sen = Image.open(tile_map("senate", sub("senate"), title="Senate",
                              marks={r: v for r, v in marks.items() if r.endswith("-SEN")},
                              pending_word="unpicked", brand=False))
    img.paste(sen, (0, 100))

    col_x, col_w = 1220, 610
    gov = Image.open(tile_map("governor", sub("governor"), title="Governors",
                              marks={r: v for r, v in marks.items() if r.endswith("-GOV")},
                              pending_word="unpicked", brand=False))
    gov = gov.resize((col_w, round(gov.height * col_w / gov.width)), Image.LANCZOS)
    img.paste(gov, (col_x, 100))

    # House control
    y = 100 + gov.height + 16
    house = picks.get("US-HOUSE")
    fill = PARTY[house] if house else PANEL
    d.rounded_rectangle([col_x, y, col_x + col_w, y + 120], radius=14, fill=fill)
    d.text((col_x + 24, y + 30), "HOUSE CONTROL", font=font(24), fill=WHITE if house else MUTED, anchor="lm")
    big = {"D": "DEMOCRATS", "R": "REPUBLICANS"}.get(house, "NOT PICKED")
    d.text((col_x + 24, y + 80), big, font=fit(d, big, col_w - 260, 52), fill=WHITE, anchor="lm")
    if "US-HOUSE" in marks:
        d.text((col_x + col_w - 24, y + 30), "✓ RIGHT" if marks["US-HOUSE"] else "✗ WRONG",
               font=font(24), fill=WHITE, anchor="rm")
    if house_seats is not None:
        d.text((col_x + col_w - 24, y + 84), f"D {house_seats} seats", font=font(30, False), fill=WHITE, anchor="rm")

    # Battleground chips
    y += 140
    picked = [r for r in battleground if r in picks]
    dn = sum(1 for r in picked if picks[r] == "D")
    rn = sum(1 for r in picked if picks[r] == "R")
    d.text((col_x, y), "HOUSE BATTLEGROUNDS", font=font(24), fill=WHITE, anchor="lt")
    d.text((col_x + col_w, y), f"D {dn} · R {rn}" + (f" · {len(battleground) - len(picked)} skipped"
                                                          if len(picked) < len(battleground) else ""),
           font=font(22, False), fill=MUTED, anchor="rt")
    y += 38
    per_row, gap = 6, 6
    cw = (col_w - gap * (per_row - 1)) / per_row
    ch = 30
    for i, rid in enumerate(battleground):
        cx = col_x + (i % per_row) * (cw + gap)
        cy = y + (i // per_row) * (ch + gap)
        p = picks.get(rid)
        d.rounded_rectangle([cx, cy, cx + cw, cy + ch], radius=6, fill=PARTY[p] if p else NO_RACE)
        label = rid + ((" ✓" if marks[rid] else " ✗") if rid in marks else "")
        d.text((cx + cw / 2, cy + ch / 2), label, font=font(17), fill=WHITE if p else (110, 115, 125), anchor="mm")
    return to_png(img)

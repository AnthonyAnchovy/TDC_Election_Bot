"""Draw the finished projection art into assets/calls/: a glass card with the state and the winner's photo, and for
the races listed in projection_memes.json, the meme worked in.

    python tools/make_projection_art.py                   every race and winner
    python tools/make_projection_art.py GA-SEN_D TX-SEN   just these (a race id means all its winners)
    python tools/make_projection_art.py --memes           only the races with a meme
    python tools/make_projection_art.py --sheet           also write assets/kit/_sheet_memes.jpg, a contact sheet
    python tools/make_projection_art.py --kit GA-SEN_D    write each layer as its own PNG to assets/kit/GA-SEN_D/ for editing

These files count as yours, so the bot posts them exactly as they are. Running this again only redraws files it drew
itself and you haven't changed (assets/calls/_art.json keeps track). Edit one by hand and it's safe from then on.
docs/projection-art.md explains the layouts and how to fit a new meme.
"""
from __future__ import annotations

import hashlib
import io
import json
import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageOps

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import cards as C          # noqa: E402
import graphics as G       # noqa: E402
import races as R          # noqa: E402
sys.path.insert(0, str(HERE / "tools"))
import make_call_pngs as P # noqa: E402

CALLS = G.ASSETS / "calls"
MANIFEST = CALLS / "_art.json"
PHOTOS = G.ASSETS / "candidates"
CUTOUTS = PHOTOS / "cutouts"
TEMPLATES = G.ASSETS / "memes" / "templates"
KIT = G.ASSETS / "kit"
PLAN = HERE / "projection_memes.json"

W, H = 1600, 900
SIZE = (W, H)
PANEL = (50, 70, 1500, 840)
RADIUS = 46
FOOT = 86
TILT = {"D": (120, 180, 255), "R": (255, 150, 140), "I": (205, 170, 255)}     # rim light on the portrait
NAVY = {"D": (10, 28, 74), "R": (64, 10, 16), "I": (34, 16, 64)}              # shadow tint on the portrait


class Stack:
    """The picture as named layers, so a kit can hand them out separately."""

    def __init__(self):
        self.layers: list[tuple[str, Image.Image]] = []

    def add(self, name: str, img: Image.Image, xy: tuple[int, int] = (0, 0)) -> None:
        if img.size != SIZE or xy != (0, 0):
            full = Image.new("RGBA", SIZE, (0, 0, 0, 0))
            full.alpha_composite(img.convert("RGBA"), (max(0, xy[0]), max(0, xy[1])),
                                 (max(0, -xy[0]), max(0, -xy[1])))
            img = full
        self.layers.append((name, img))

    def flat(self) -> Image.Image:
        out = Image.new("RGBA", SIZE, (0, 0, 0, 0))
        for _, layer in self.layers:
            out.alpha_composite(layer)
        return out


def blank() -> Image.Image:
    return Image.new("RGBA", SIZE, (0, 0, 0, 0))


def tinted(mask: Image.Image, color, alpha: float = 1.0) -> Image.Image:
    return C.solid(mask, color, alpha)


# ------------------------------------------------------------------------------------------------ the glass
def glass(st: Stack, party: str, box: tuple[int, int, int, int], name: str = "glass", opacity: float = 0.92) -> Image.Image:
    """The party gradient with light streaks, the same recipe as the original card. Returns its mask."""
    panel = G._rounded_mask(SIZE, box, RADIUS)
    shadow = blank()
    shadow.putalpha(panel.filter(ImageFilter.GaussianBlur(22)).point(lambda v: int(v * 0.55)))
    st.add(f"{name}-shadow", ImageChops.offset(shadow, 0, 16))
    top, bot = G.GLASS[party]
    grad = Image.new("RGBA", SIZE)
    gd = ImageDraw.Draw(grad)
    for i in range(W + H):
        t = i / (W + H)
        gd.line([(i, 0), (i - H, H)], fill=tuple(int(top[j] + (bot[j] - top[j]) * t) for j in range(3)) + (255,), width=2)
    grad.putalpha(panel.point(lambda v: int(v * opacity)))
    streak = Image.new("L", SIZE, 0)
    sd = ImageDraw.Draw(streak)
    for x in range(-H, W, 46):
        sd.line([(x, H), (x + H, 0)], fill=16, width=14)
    light = blank()
    light.putalpha(ImageChops.multiply(streak, panel))
    grad.alpha_composite(light)
    st.add(name, grad)
    return panel


def state_masks(f: dict, box_w: int, box_h: int) -> tuple[Image.Image | None, Image.Image | None]:
    """(state silhouette, district inside it or None) fitted to the box, same size and position."""
    if not f["state"]:
        return C.us_locator(None, box_w, box_h, base=(255, 255, 255), hi=(255, 255, 255), all_hi=True).split()[-1], None
    if f["kind"] != "house" or R.HOUSE_SEATS.get(f["state"]) == 1:
        return G._shape_mask(f["state"], box_w, box_h), None
    cut = C.state_cutout(f["state"], f["rid"], box_w, box_h, (120, 120, 120), (255, 255, 255))
    alpha = cut.split()[-1]
    return alpha, ImageChops.multiply(cut.convert("L").point(lambda v: 255 if v > 190 else 0), alpha)


def ghost(st: Stack, f: dict, party: str, panel: Image.Image, box: tuple[int, int, int, int], *, bw=820, bh=880,
          strength: float = 0.16) -> None:
    """The giant state, bleeding off the panel's right side: a ghost inside the glass, a party-color outline past it."""
    mask, district = state_masks(f, bw, bh)
    if mask is None:
        return
    x = box[2] - mask.width + 120
    y = (H - mask.height) // 2 + 10
    full = Image.new("L", SIZE, 0)
    full.paste(mask, (x, y))
    inside = ImageChops.multiply(full, panel)
    st.add("state-ghost", tinted(inside, (255, 255, 255), strength))
    edge = ImageChops.subtract(full.filter(ImageFilter.MaxFilter(7)), full.filter(ImageFilter.MinFilter(3)))
    st.add("state-outline", tinted(ImageChops.multiply(edge, ImageChops.invert(panel)), G.GLASS[party][1]))
    st.add("state-edge", tinted(ImageChops.multiply(edge, panel), (255, 255, 255), 0.30))
    if district is not None:
        dm = Image.new("L", SIZE, 0)
        dm.paste(district, (x, y))
        st.add("district", tinted(ImageChops.multiply(dm, panel), (255, 255, 255), 0.42))


# ------------------------------------------------------------------------------------------------ the portrait
def _faces() -> dict:
    try:
        return json.loads((CUTOUTS / "_faces.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


FACES = _faces()


def has_portrait(rid: str, party: str) -> bool:
    return (CUTOUTS / f"{rid}_{party}.png").exists() and f"{rid}_{party}" in FACES


def soften_cropped_sides(alpha: Image.Image, width: int = 55) -> Image.Image:
    """A photo framed tight cuts the shoulders off in a straight line at its edge: fade that side out."""
    w, h = alpha.size
    ramp = Image.linear_gradient("L").rotate(90, expand=True).resize((width, h))     # dark on the left
    for side in ("left", "right"):
        x = 2 if side == "left" else w - 3
        kept = sum(alpha.crop((x, 0, x + 1, h)).histogram()[129:])
        if kept > h * 0.08:
            fader = Image.new("L", (w, h), 255)
            fader.paste(ramp if side == "left" else ImageOps.mirror(ramp), (0 if side == "left" else w - width, 0))
            alpha = ImageChops.multiply(alpha, fader)
    return alpha


def portrait(st: Stack, rid: str, party: str, cx: int, floor: int, face_h: int, *, clip: Image.Image | None = None,
             max_top: int = 6, fade: int = 14) -> tuple[int, int, int, int] | None:
    """The winner, graded to sit in the scene: contrast-matched, party light on the edges, a soft shadow behind.
    cx is where the middle of the face lands, floor is the y the cutout runs down to (hidden behind the footer)."""
    if not has_portrait(rid, party):
        return None
    cut = Image.open(CUTOUTS / f"{rid}_{party}.png").convert("RGBA")
    fx, fy, fw, fh = FACES[f"{rid}_{party}"]["face"]
    scale = face_h / fh
    # head and shoulders: about three face heights from the top of the head to the floor, and a photo cropped high
    # still has to reach the floor, or the shoulders would end in mid-air
    y = max(max_top, int(floor - face_h * 3.05 - fy * scale))
    if y + cut.height * scale < floor:
        scale = (floor - y) / cut.height
    new = (max(1, int(cut.width * scale)), max(1, int(cut.height * scale)))
    cut = cut.resize(new, Image.LANCZOS)
    x = int(cx - (fx + fw / 2) * scale)

    alpha = cut.split()[-1]
    rgb = cut.convert("RGB")
    rgb = ImageOps.autocontrast(rgb, cutoff=0.5)
    rgb = ImageEnhance.Contrast(rgb).enhance(1.06)
    rgb = ImageEnhance.Color(rgb).enhance(1.05)
    # pull the shadows toward the panel color, so the photo looks lit by the same scene
    grey = rgb.convert("L")
    shade = Image.new("RGB", new, NAVY[party])
    rgb = Image.composite(rgb, shade, grey.point(lambda v: min(255, 190 + v)))
    # eat a pixel of the matte so there's no halo from the photo's old background
    alpha = alpha.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.GaussianBlur(0.8))
    alpha = soften_cropped_sides(alpha)
    # rim light from the panel
    inner = ImageChops.subtract(alpha, alpha.filter(ImageFilter.MinFilter(9)).filter(ImageFilter.GaussianBlur(4)))
    rim = Image.new("RGB", new, TILT[party])
    rgb = Image.composite(rim, rgb, inner.point(lambda v: int(v * 0.42)))
    ramp = Image.linear_gradient("L").rotate(180).resize((new[0], fade))
    fader = Image.new("L", new, 255)
    fader.paste(ramp, (0, max(0, floor - y - fade)))
    if floor - y < new[1]:
        ImageDraw.Draw(fader).rectangle([0, floor - y, new[0], new[1]], fill=0)
    alpha = ImageChops.multiply(alpha, fader)
    out = rgb.convert("RGBA")
    out.putalpha(alpha)

    canvas = blank()
    canvas.alpha_composite(out, (max(0, x), y), (max(0, -x), 0))
    if clip is not None:
        canvas.putalpha(ImageChops.multiply(canvas.split()[-1], clip))
    sil = canvas.split()[-1]
    shadow = tinted(ImageChops.offset(sil, -14, 12).filter(ImageFilter.GaussianBlur(18)), (0, 0, 0), 0.5)
    st.add("portrait-shadow", shadow)
    st.add("portrait", canvas)
    return sil.getbbox()


# ------------------------------------------------------------------------------------------------ type
def result_label(f: dict, party: str) -> str:
    if f["kind"] == "control":
        return "TAKEOVER" if f["flip"] else "HOLD"
    return f"FLIP {f['holder']} \u2192 {party}" if f["flip"] else f"{party} HOLD"


def footer(st: Stack, f: dict, party: str, panel: Image.Image, box: tuple[int, int, int, int], badge_right: int | None = None,
           text: str = "DECISION DESK  ·  PROJECTED WINNER") -> None:
    px0, _, px1, py1 = box
    cover = Image.new("L", SIZE, 0)
    ImageDraw.Draw(cover).rectangle([px0, py1 - FOOT, px1, py1], fill=255)
    layer = blank()
    layer.putalpha(ImageChops.multiply(cover, panel).point(lambda v: int(v * 0.42)))
    d = ImageDraw.Draw(layer)
    d.text((px0 + 60, py1 - 43), text, font=G.font(28, False), fill=(255, 255, 255, 215), anchor="lm")
    st.add("footer", layer)
    if not f["holder"]:
        return
    label = result_label(f, party)
    bf = G.font(34)
    bw = int(d.textlength(label, font=bf)) + 56
    bx1 = badge_right or px1 - 50
    pm = G._rounded_mask(SIZE, [bx1 - bw, py1 - 70, bx1, py1 - 16], 27)
    tm = G._text_mask(SIZE, (bx1 - bw / 2, py1 - 43), label, bf)
    st.add("flip-badge", G._knockout(SIZE, pm, tm, G.YELLOW if f["flip"] else (255, 255, 255)))


def pill(st: Stack, x: int, y: int, text: str = "KEY RACE ALERT") -> None:
    d = ImageDraw.Draw(blank())
    tf = G.font(34)
    tw = int(d.textlength(text, font=tf)) + 64
    pm = G._rounded_mask(SIZE, [x, y, x + tw, y + 58], 29)
    st.add("pill", G._knockout(SIZE, pm, G._text_mask(SIZE, (x + tw / 2, y + 29), text, tf), (255, 255, 255)))


def badge(st: Stack, f: dict, right: int, top: int, bw=190, bh=150) -> None:
    mask, district = state_masks(f, bw, bh)
    if mask is None:
        return
    bm = Image.new("L", SIZE, 0)
    x = right - mask.width
    bm.paste(mask, (x, top))
    if district is not None:                   # the state pale, the district solid
        st.add("badge", tinted(bm, (255, 255, 255), 0.4))
        dm = Image.new("L", SIZE, 0)
        dm.paste(district, (x, top))
        st.add("badge-district", tinted(dm, (255, 255, 255)))
    else:
        st.add("badge-glow", G._glow(bm, (255, 255, 255), 14, 0.7))
        st.add("badge", tinted(bm, (255, 255, 255)))


def title_of(f: dict) -> str:
    if f["kind"] == "control":
        return "U.S. HOUSE MAJORITY"
    return R.RACES[f["rid"]]["label"].upper().replace(" (SPECIAL)", " · SPECIAL")


def winner_lines(f: dict, party: str, name: str | None) -> list[str]:
    if f["kind"] == "control":
        return [{"D": "DEMOCRATS", "R": "REPUBLICANS"}[party]]
    return G._split_name(name) if name else [C.PARTY_NOUN[party].upper()]


def name_block(st: Stack, f: dict, party: str, name: str | None, x: int, y: int, max_w: int, size: int, *,
               one_line: bool = False) -> int:
    """WINNER with the check, then the name in one or two lines. Returns the y below it."""
    layer = blank()
    d = ImageDraw.Draw(layer)
    check = int(size * 0.56)
    G._check(d, x, y, check)
    d.text((x + check + 20, y + check // 2 + 2), "WINNER", font=C.fit(d, "WINNER", max_w, "Anton-Regular.ttf", int(size * 0.5)),
           fill=G.YELLOW + (255,), anchor="lm")
    yy = y + check + 12
    lines = winner_lines(f, party, name)
    for line in [" ".join(lines)] if one_line else lines:
        fnt = G.fit(d, line, max_w, size, min_size=38)
        sm = G._text_mask(SIZE, (x + 4, yy + 6), line, fnt, anchor="lt")
        layer.alpha_composite(G._glow(sm, (0, 0, 0), 8, 0.6))
        d = ImageDraw.Draw(layer)
        d.text((x, yy), line, font=fnt, fill=(255, 255, 255, 255), anchor="lt")
        yy += fnt.size + 12
    st.add("name", layer)
    return yy


def race_title(st: Stack, f: dict, x: int, y: int, max_w: int, size: int = 58) -> None:
    layer = blank()
    d = ImageDraw.Draw(layer)
    text = title_of(f)
    d.text((x, y), text, font=G.fit(d, text, max_w, size), fill=(255, 255, 255, 240), anchor="lt")
    st.add("title", layer)


def meme_caption(st: Stack, text: str, x: int, y: int, max_w: int, *, start: int = 150, anchor: str = "lt", angle: float = -2.0,
                 color=(255, 255, 255)) -> int:
    """Poster type with a heavy black edge, a bit crooked. A | in the text starts a new line. Returns the y below it."""
    layer = blank()
    d = ImageDraw.Draw(layer)
    lines = text.split("|")
    fnt = C.fit(d, max(lines, key=lambda s: d.textlength(s, font=C.font(C.ANTON, start))), max_w, C.ANTON, start, 40)
    yy = y
    for line in lines:
        d.text((x, yy), line, font=fnt, fill=color + (255,), stroke_width=max(4, fnt.size // 16), stroke_fill=(0, 0, 0, 255), anchor=anchor)
        yy += int(fnt.size * 1.16)
    if angle:
        layer = layer.rotate(angle, resample=Image.BICUBIC, center=(x, y))
    shadow = tinted(layer.split()[-1].filter(ImageFilter.GaussianBlur(10)), (0, 0, 0), 0.45)
    st.add("caption-shadow", ImageChops.offset(shadow, 4, 8))
    st.add("caption", layer)
    return yy


# ------------------------------------------------------------------------------------------------ memes
def load_meme(item: dict, rid: str, party: str) -> Image.Image:
    img = ImageOps.exif_transpose(Image.open(TEMPLATES / item["meme"])).convert("RGB")
    if item.get("crop"):
        l, t, r, b = item["crop"]        # fractions of the picture
        img = img.crop((int(l * img.width), int(t * img.height), int(r * img.width), int(b * img.height)))
    if item.get("photo_slot"):           # the winner's photo goes into the meme itself
        photo = G._find_asset("candidates", rid, party)
        if photo:
            l, t, r, b = item["photo_slot"]
            box = (int(l * img.width), int(t * img.height), int(r * img.width), int(b * img.height))
            face = FACES.get(f"{rid}_{party}", {})
            pic = ImageOps.exif_transpose(Image.open(photo)).convert("RGB")
            img.paste(ImageOps.fit(pic, (box[2] - box[0], box[3] - box[1]), Image.LANCZOS,
                                   centering=(0.5, 0.2 if face else 0.3)), box[:2])
    return img


def upscale(img: Image.Image, w: int, h: int) -> Image.Image:
    """Fit inside w x h. Small memes get a little sharpening after enlarging. Crunchy is fine, mushy isn't."""
    scale = min(w / img.width, h / img.height)
    out = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))), Image.LANCZOS)
    if scale > 1.4:
        out = out.filter(ImageFilter.UnsharpMask(radius=2.2, percent=70, threshold=2))
    return out


def layout_stage(st: Stack, f: dict, party: str, name: str | None, item: dict, meme: Image.Image) -> None:
    """The meme fills the stage, its own blurred copy behind it, with a glass lower third carrying the state and name."""
    box = (40, 30, 1560, 870)
    third_top = 640
    stage = G._rounded_mask(SIZE, box, RADIUS)
    fill = ImageOps.fit(meme, (box[2] - box[0], box[3] - box[1]), Image.LANCZOS).filter(ImageFilter.GaussianBlur(38))
    fill = ImageEnhance.Brightness(fill).enhance(0.55).convert("RGBA")
    tint = Image.new("RGBA", fill.size, G.GLASS[party][0] + (110,))
    fill.alpha_composite(tint)
    back = blank()
    back.alpha_composite(fill, box[:2])
    back.putalpha(stage)
    shadow = blank()
    shadow.putalpha(stage.filter(ImageFilter.GaussianBlur(22)).point(lambda v: int(v * 0.55)))
    st.add("stage-shadow", ImageChops.offset(shadow, 0, 14))
    st.add("stage", back)

    third = (box[0], third_top, box[2], box[3])
    cover = Image.new("L", SIZE, 0)
    ImageDraw.Draw(cover).rectangle(third, fill=255)
    panel = ImageChops.multiply(cover, stage)
    mask, district = state_masks(f, 560, 560)
    full = edge = None
    if mask is not None:
        full = Image.new("L", SIZE, 0)
        gx, gy = box[2] - mask.width + 40, third_top - 120
        full.paste(mask, (gx, gy))
        edge = ImageChops.subtract(full.filter(ImageFilter.MaxFilter(5)), full.filter(ImageFilter.MinFilter(3)))
        st.add("state-outline-back", tinted(edge, (255, 255, 255), 0.6))      # the part above the lower third runs behind the meme

    has = has_portrait(f["rid"], party) and not item.get("no_portrait")
    area_w = box[2] - box[0] - (440 if has else 60)
    area_h = third_top - box[1] - 34
    pic = upscale(meme, area_w, area_h)
    mx = box[0] + 30 + (area_w - pic.width) // 2
    my = box[1] + 18 + (area_h - pic.height) // 2
    frame = blank()
    ImageDraw.Draw(frame).rectangle([mx - 5, my - 5, mx + pic.width + 4, my + pic.height + 4], fill=(255, 255, 255, 255))
    frame.alpha_composite(pic.convert("RGBA"), (mx, my))
    fsh = tinted(frame.split()[-1].filter(ImageFilter.GaussianBlur(16)), (0, 0, 0), 0.55)
    st.add("meme-shadow", ImageChops.offset(fsh, 0, 8))
    st.add("meme", frame)

    if has and item.get("portrait_behind"):
        portrait(st, f["rid"], party, box[2] - 225, third_top + 200, int(item.get("face", 205)), max_top=box[1] + 6, clip=stage)
    top, bot = G.GLASS[party]
    grad = Image.new("RGBA", SIZE)
    gd = ImageDraw.Draw(grad)
    for i in range(W):
        t = i / W
        gd.line([(i, 0), (i, H)], fill=tuple(int(top[j] + (bot[j] - top[j]) * t) for j in range(3)) + (255,))
    grad.putalpha(panel.point(lambda v: int(v * 0.92)))
    st.add("lower-third", grad)
    if full is not None:
        st.add("state-ghost", tinted(ImageChops.multiply(full, panel), (255, 255, 255), 0.16))
        st.add("state-edge", tinted(ImageChops.multiply(edge, panel), (255, 255, 255), 0.6))
        if district is not None:
            dm = Image.new("L", SIZE, 0)
            dm.paste(district, (gx, gy))
            st.add("district", tinted(ImageChops.multiply(dm, panel), (255, 255, 255), 0.5))
    if has and not item.get("portrait_behind"):
        portrait(st, f["rid"], party, box[2] - 225, third_top + 200, int(item.get("face", 205)), max_top=box[1] + 6,
                 clip=stage, fade=70)
    x = box[0] + 50
    text_w = 1000 if has else 1200
    race_title(st, f, x, third_top + 18, text_w, 38)
    line = item.get("headline") or " ".join(winner_lines(f, party, name))
    layer = blank()
    dd = ImageDraw.Draw(layer)
    dd.text((x, third_top + 164), line, font=C.fit(dd, line, text_w, C.ANTON, 104, 50), fill=(255, 255, 255, 255), anchor="ls")
    row = box[3] - 32
    G._check(dd, x, row - 20, 40)
    tag = item.get("tag", "WINNER")
    small = C.font(C.ANTON, 34)
    dd.text((x + 54, row), tag, font=small, fill=G.YELLOW + (255,), anchor="lm")
    if f["holder"]:
        dd.text((x + 54 + dd.textlength(tag, font=small) + 26, row), result_label(f, party), font=small, fill=(255, 255, 255, 235), anchor="lm")
    st.add("name", layer)
    if item.get("caption"):
        meme_caption(st, item["caption"], mx + pic.width // 2, my + 8, pic.width - 40, start=int(item.get("caption_size", 92)),
                     anchor="mt", angle=0)


def layout_side(st: Stack, f: dict, party: str, name: str | None, item: dict, meme: Image.Image) -> None:
    """The base card with the meme as a tilted print on the right, for tall pictures and screenshots."""
    panel = glass(st, party, PANEL)
    ghost(st, f, party, panel, PANEL, strength=0.12)
    px0, py0, _, py1 = PANEL
    tx = px0 + 60
    has = has_portrait(f["rid"], party) and not item.get("no_portrait")
    floor = py1 - FOOT
    if has:
        portrait(st, f["rid"], party, tx + 300, floor, int(item.get("face", 190)), clip=panel, max_top=py0 + 120, fade=40)
        scrim = Image.new("L", SIZE, 0)                # darkens the chest so the name reads over it
        ramp = Image.linear_gradient("L").point(lambda v: min(255, v * 2)).resize((PANEL[2] - px0, floor - 390))
        scrim.paste(ramp, (px0, 390))
        st.add("name-scrim", tinted(ImageChops.multiply(scrim, panel), NAVY[party], 0.86))
    footer(st, f, party, panel, PANEL, badge_right=int(item.get("badge_right", 830)), text="DECISION DESK")
    pill(st, px0 + 56, py0 + 36)
    race_title(st, f, tx, py0 + 110, 720, 50)
    name_block(st, f, party, name, tx, 490 if has else py0 + 220, 720, 104 if has else 118)
    pic = upscale(meme, int(item.get("max_w", 690)), int(item.get("max_h", 850)))
    border = 10
    card = Image.new("RGBA", (pic.width + border * 2, pic.height + border * 2), (255, 255, 255, 255))
    card.paste(pic, (border, border))
    card = card.rotate(float(item.get("tilt", 2.5)), resample=Image.BICUBIC, expand=True)
    cx = int(item.get("cx", 1210))
    x, y = cx - card.width // 2, (H - card.height) // 2
    full = blank()
    full.alpha_composite(card, (max(0, x), max(0, y)), (max(0, -x), max(0, -y)))
    st.add("meme-shadow", ImageChops.offset(tinted(full.split()[-1].filter(ImageFilter.GaussianBlur(18)), (0, 0, 0), 0.6), -6, 12))
    st.add("meme", full)
    if item.get("caption"):
        meme_caption(st, item["caption"], tx, 400, 720, start=int(item.get("caption_size", 92)))


def layout_base(st: Stack, f: dict, party: str, name: str | None, item: dict | None) -> None:
    """The plain card, and text-only memes: the caption sits over the name."""
    item = item or {}
    panel = glass(st, party, PANEL)
    ghost(st, f, party, panel, PANEL)
    px0, py0, px1, py1 = PANEL
    has = has_portrait(f["rid"], party)
    if has:
        portrait(st, f["rid"], party, px1 - 370, py1 - FOOT, 225, max_top=8)
    footer(st, f, party, panel, PANEL)
    pill(st, px0 + 56, py0 + 36)
    badge(st, f, px1 - 36 if has else px1 - 56, py0 + 30, *((130, 105) if has else (210, 170)))
    tx = px0 + 60
    text_w = 800 if has else 1030
    race_title(st, f, tx, py0 + 112, text_w, 54)
    if item.get("caption"):
        lines = item["caption"].count("|") + 1
        name_block(st, f, party, name, tx, PANEL[3] - FOOT - 170, text_w, 96, one_line=True)
        meme_caption(st, item["caption"], tx - 8, py0 + 182, text_w + 40, start=int(item.get("caption_size", 190 if lines == 1 else 150)))
    else:
        name_block(st, f, party, name, tx, py0 + 210, text_w, 150 if not has else 128)


LAYOUTS = {"stage": layout_stage, "side": layout_side}


def render(rid: str, party: str, names: dict, plan: dict) -> Stack:
    name = names.get(party)
    f = C.race_facts(rid, party, name, None, None, False, None)
    item = plan.get(f"{rid}_{party}")
    st = Stack()
    if item and item.get("meme"):
        LAYOUTS[item.get("layout", "stage")](st, f, party, name, item, load_meme(item, rid, party))
    else:
        layout_base(st, f, party, name, item)
    return st


# ------------------------------------------------------------------------------------------------ running it
def png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def load_plan() -> dict:
    return {k: v for k, v in json.loads(PLAN.read_text(encoding="utf-8")).items() if not k.startswith("_")}


def contact_sheet(paths: list[Path], out: Path, cols: int = 4, tw: int = 480) -> None:
    th = int(tw * H / W)
    rows = (len(paths) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * tw, rows * (th + 26)), (54, 57, 63))     # Discord's dark grey
    d = ImageDraw.Draw(sheet)
    for i, p in enumerate(paths):
        im = Image.open(p).convert("RGBA")
        im.thumbnail((tw, th), Image.LANCZOS)
        x, y = (i % cols) * tw, (i // cols) * (th + 26)
        sheet.paste(im, (x, y), im)
        d.text((x + 6, y + th + 4), p.stem, font=G.font(16), fill=(230, 232, 236))
    sheet.save(out, quality=85)


def main(argv):
    args = [a for a in argv[1:] if not a.startswith("--")]
    plan = load_plan()
    cands = P.candidates()
    jobs = []
    for rid in P.races(False, cands):
        names = cands.get(rid) or {}
        for party in sorted(set(names) | {"D", "R"}):
            jobs.append((rid, party, names))
    if args:
        jobs = [j for j in jobs if f"{j[0]}_{j[1]}" in args or j[0] in args]
    if "--memes" in argv:
        jobs = [j for j in jobs if f"{j[0]}_{j[1]}" in plan]

    if "--kit" in argv:
        for rid, party, names in jobs:
            folder = KIT / f"{rid}_{party}"
            folder.mkdir(parents=True, exist_ok=True)
            for i, (name, layer) in enumerate(render(rid, party, names, plan).layers):
                layer.save(folder / f"{i:02d}-{name}.png")
            print(f"Layers written to {folder}")
        return

    made_by_us = json.loads(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.exists() else {}
    stand = G.stand_ins()
    made = kept = 0
    try:
        for rid, party, names in jobs:
            path = CALLS / f"{rid}_{party}.png"
            others = [CALLS / f"{rid}_{party}.{e}" for e in ("jpg", "jpeg", "webp", "gif")]
            if any(o.exists() for o in others):
                kept += 1
                continue
            if path.exists():
                sha = hashlib.sha256(path.read_bytes()).hexdigest()
                if not (stand.get(path.name) == sha or made_by_us.get(path.name) == sha):
                    kept += 1                         # you've changed this one
                    continue
            data = png_bytes(render(rid, party, names, plan).flat())
            path.write_bytes(data)
            made_by_us[path.name] = hashlib.sha256(data).hexdigest()
            stand.pop(path.name, None)
            made += 1
            print(f"\r{made} drawn", end="", flush=True)
    finally:                                          # a crash halfway mustn't make finished art look hand-edited
        MANIFEST.write_text(json.dumps(made_by_us, indent=0, sort_keys=True), encoding="utf-8")
        G.STAND_INS.write_text(json.dumps(stand, indent=0, sort_keys=True), encoding="utf-8")
    print(f"\r{made} drawn, {kept} of your own left alone. Folder: {CALLS}")
    if "--sheet" in argv:
        shots = [CALLS / f"{k}.png" for k in plan if (CALLS / f"{k}.png").exists()]
        KIT.mkdir(parents=True, exist_ok=True)
        contact_sheet(shots, KIT / "_sheet_memes.jpg")


if __name__ == "__main__":
    main(sys.argv)

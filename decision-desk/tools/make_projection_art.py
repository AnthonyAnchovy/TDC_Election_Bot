"""Draw the finished projection art into assets/calls/: a glass card with the state and the winner's photo, and, unless
a Republican won, the loser standing in the fog under THE FOG CLAIMETH.

    python tools/make_projection_art.py                   every race and winner
    python tools/make_projection_art.py GA-SEN_D TX-SEN   just these (a race id means all its winners)
    python tools/make_projection_art.py --sheet           also write assets/kit/_sheet_all.jpg, a contact sheet of the cards drawn
    python tools/make_projection_art.py --template        write the one master file to edit, assets/kit/projection-template-fog.psd
    python tools/make_projection_art.py --psd GA-SEN_D    write a layered Photoshop file to assets/kit/psd/GA-SEN_D.psd
                                                          (leaves an existing one alone unless you add --replace)
    python tools/make_projection_art.py --kit GA-SEN_D    write each layer as its own PNG to assets/kit/GA-SEN_D/ for editing

These files count as yours, so the bot posts them exactly as they are. Running this again only redraws files it drew
itself and you haven't changed (assets/calls/_art.json keeps track). Edit one by hand and it's safe from then on.
docs/projection-art.md explains the card and how to edit it.
"""
from __future__ import annotations

import hashlib
import io
import json
import random
import shutil
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageOps

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import cards as C          # noqa: E402
import graphics as G       # noqa: E402
import races as R          # noqa: E402
sys.path.insert(0, str(HERE / "tools"))
import make_call_pngs as P # noqa: E402
import psd_writer as PSD   # noqa: E402

CALLS = G.ASSETS / "calls"
MANIFEST = CALLS / "_art.json"
PHOTOS = G.ASSETS / "candidates"
CUTOUTS = PHOTOS / "cutouts"
KIT = G.ASSETS / "kit"
PSD_DIR = KIT / "psd"
TEMPLATE = KIT / "projection-template-fog.psd"
TEMPLATE_ORIGINAL = KIT / "_projection-template-fog-original.psd"
FOG = G.ASSETS / "memes" / "fog.webp"
FOG_SLOT = "PUT YOUR IMAGES IN THIS GROUP (between the fog)"
FOG_CAPTION = G.ASSETS / "memes" / "fog-claimeth.png"       # his THE FOG COMETH bar, reworded from its own letters
FOG_CAPTION_POS = (446, 676)                                # the middle of its top edge, where he put it
# Pictures made by hand to stand in the fog instead of the loser's photo, with where they go
FOG_ART = {"OH-SEN_D": (G.ASSETS / "memes" / "fog-husted.png", (394, 725))}
FOG_FIGURE = {"cx": 446, "top": 725, "floor": 974, "face": 62, "max_face": 96}    # sized from his cartoon, which fills this box

W, H = 1680, 1120
SIZE = (W, H)
PANEL = (50, 70, 1500, 1060)
BRAND = "TDC DECISION DESK"
RADIUS = 46
FOOT = 86
TILT = {"D": (120, 180, 255), "R": (255, 150, 140), "I": (205, 170, 255)}     # rim light on the portrait
NAVY = {"D": (10, 28, 74), "R": (64, 10, 16), "I": (34, 16, 64)}              # shadow tint on the portrait


class Stack:
    """The picture as named layers, so a kit can hand them out separately."""

    def __init__(self):
        self.layers: list[tuple[str, Image.Image]] = []
        self.live: dict[str, list] = {}      # what a layer becomes in a Photoshop file, when it holds editable text

    def add(self, name: str, img: Image.Image, xy: tuple[int, int] = (0, 0)) -> None:
        if img.size != SIZE or xy != (0, 0):
            full = Image.new("RGBA", SIZE, (0, 0, 0, 0))
            full.alpha_composite(img.convert("RGBA"), (max(0, xy[0]), max(0, xy[1])),
                                 (max(0, -xy[0]), max(0, -xy[1])))      # the source box handles anything off the left or top
            img = full
        self.layers.append((name, img))

    def slot(self, name: str) -> None:
        """An empty group in the Photoshop file. It draws nothing on the card."""
        self.layers.append((name, None))

    def flat(self) -> Image.Image:
        out = Image.new("RGBA", SIZE, (0, 0, 0, 0))
        for name, layer in self.layers:
            if layer is not None:
                out.alpha_composite(layer)
        return out


def blank() -> Image.Image:
    return Image.new("RGBA", SIZE, (0, 0, 0, 0))


def tinted(mask: Image.Image, color, alpha: float = 1.0) -> Image.Image:
    return C.solid(mask, color, alpha)


POSTSCRIPT = {"arialbd.ttf": "Arial-BoldMT", "Anton-Regular.ttf": "Anton-Regular"}


def live_text(x: float, y: float, text: str, fnt, color, anchor: str) -> PSD.Text:
    """The same words Pillow draws at (x, y) with this anchor, as Photoshop type. Anchors: lt, lm or mm."""
    ascent, descent = fnt.getmetrics()
    base = y - fnt.getbbox(text, anchor="ls")[1] if anchor[1] == "t" else y + (ascent - descent) / 2    # t is the top of these letters
    return PSD.Text(text, POSTSCRIPT[Path(fnt.path).name], fnt.size, x, base, tuple(color[:3]), fnt.getlength(text), ascent, descent,
                    PSD.CENTER if anchor[0] == "m" else PSD.LEFT)


def punched_label(st: Stack, name: str, text: str, fnt, center: tuple[float, float], shape: Image.Image, color) -> None:
    """A pill with its words punched through to the glass. Type can't be punched through, so in the Photoshop file
    the pill is solid and the words are type in the color of the glass behind them."""
    words = G._text_mask(SIZE, center, text, fnt)
    behind = st.flat().crop(words.getbbox()).convert("RGB").resize((1, 1), Image.BOX).getpixel((0, 0))
    st.add(name, G._knockout(SIZE, shape, words, color))
    st.live[name] = [PSD.Layer(name, tinted(shape, color)),
                     PSD.Layer(f"{name}-text", tinted(words, behind), text=live_text(*center, text, fnt, behind, "mm"))]


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
    st.add(name, grad)
    st.add(f"{name}-streaks", light)
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


def ghost(st: Stack, f: dict, party: str, panel: Image.Image, box: tuple[int, int, int, int], *, bw=840, bh=1020,
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
    shade_mask = grey.point(lambda v: 255 - min(255, 190 + v))
    # eat a pixel of the matte so there's no halo from the photo's old background
    alpha = alpha.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.GaussianBlur(0.8))
    alpha = soften_cropped_sides(alpha)
    # rim light from the panel
    inner = ImageChops.subtract(alpha, alpha.filter(ImageFilter.MinFilter(9)).filter(ImageFilter.GaussianBlur(4)))
    rim_mask = inner.point(lambda v: int(v * 0.42))
    ramp = Image.linear_gradient("L").rotate(180).resize((new[0], fade))
    fader = Image.new("L", new, 255)
    fader.paste(ramp, (0, max(0, floor - y - fade)))
    if floor - y < new[1]:
        ImageDraw.Draw(fader).rectangle([0, floor - y, new[0], new[1]], fill=0)
    alpha = ImageChops.multiply(alpha, fader)

    def placed(color: Image.Image, mask: Image.Image) -> Image.Image:
        out = color.convert("RGBA")
        out.putalpha(mask)
        canvas = blank()
        canvas.alpha_composite(out, (max(0, x), y), (max(0, -x), 0))
        if clip is not None:
            canvas.putalpha(ImageChops.multiply(canvas.split()[-1], clip))
        return canvas

    photo = placed(rgb, alpha)
    sil = photo.split()[-1]
    shadow = tinted(ImageChops.offset(sil, -14, 12).filter(ImageFilter.GaussianBlur(18)), (0, 0, 0), 0.5)
    st.add("portrait-shadow", shadow)
    st.add("portrait-photo", photo)
    st.add("portrait-shadow-tint", placed(Image.new("RGB", new, NAVY[party]), ImageChops.multiply(shade_mask, alpha)))
    st.add("portrait-rim-light", placed(Image.new("RGB", new, TILT[party]), ImageChops.multiply(rim_mask, alpha)))
    return sil.getbbox()


# ------------------------------------------------------------------------------------------------ the fog
def _soft(v: int) -> int:
    t = v / 255
    return int(255 * t * t * (3 - 2 * t))


def fog_mask(zone: tuple[int, int, int, int], clip: Image.Image | None, top: int, left: int, right: int = 0) -> Image.Image:
    """Solid in the zone, fading out toward its top and left (and right, when it isn't clipped by the card)."""
    x0, y0, x1, y1 = zone
    w, h = x1 - x0, y1 - y0
    ramp_down = Image.linear_gradient("L").resize((w, top)).point(_soft)
    ramp_across = Image.linear_gradient("L").rotate(90, expand=True).resize((left, h)).point(_soft)
    m = Image.new("L", (w, h), 255)
    m.paste(ramp_down, (0, 0))
    across = Image.new("L", (w, h), 255)
    across.paste(ramp_across, (0, 0))
    m = ImageChops.multiply(m, across)
    if right:
        far = Image.new("L", (w, h), 255)
        far.paste(ImageOps.mirror(Image.linear_gradient("L").rotate(90, expand=True).resize((right, h)).point(_soft)), (w - right, 0))
        m = ImageChops.multiply(m, far)
    full = Image.new("L", SIZE, 0)
    full.paste(m, (x0, y0))
    return ImageChops.multiply(full, clip) if clip is not None else full


def fog_back(st: Stack, zone: tuple[int, int, int, int], clip: Image.Image | None, party: str, *, feather: int = 24) -> tuple[Image.Image, Image.Image]:
    """The forest in the fog, melted into the card: soft on three sides, and standing on the footer strip below.
    Returns the forest and its shape, for the faint copy that goes in front of the slot."""
    x0, y0, x1, y1 = zone
    pic = ImageOps.fit(Image.open(FOG).convert("RGB"), (x1 - x0, y1 - y0), Image.LANCZOS)
    pic = ImageEnhance.Contrast(pic).enhance(1.14)
    pic = ImageEnhance.Brightness(pic).enhance(1.06)
    pic = Image.blend(pic, Image.new("RGB", pic.size, G.GLASS[party][0]), 0.14).convert("RGBA")
    inset = feather * 2
    soft = Image.new("L", SIZE, 0)
    ImageDraw.Draw(soft).rounded_rectangle([x0 + inset, y0 + inset, x1 - inset, y1 + feather * 4], radius=feather * 2, fill=255)
    soft = soft.filter(ImageFilter.GaussianBlur(feather))
    floor = Image.new("L", SIZE, 0)                # nothing past the bottom of the picture
    ImageDraw.Draw(floor).rectangle([0, 0, W, y1 - 1], fill=255)
    soft = ImageChops.multiply(soft, floor)
    if clip is not None:
        soft = ImageChops.multiply(soft, clip)
    haze = ImageChops.multiply(soft.filter(ImageFilter.GaussianBlur(feather * 2.4)), clip if clip is not None else floor)
    st.add("fog-haze", tinted(ImageChops.multiply(haze, floor), (196, 205, 222), 0.3))
    layer = blank()
    layer.paste(pic, zone[:2])
    layer.putalpha(soft)
    st.add("fog-back", layer)
    return layer, soft


def fog_trees(st: Stack, forest: Image.Image, shape: Image.Image) -> None:
    """The forest showing faintly in front of whatever is in the slot."""
    trees = forest.copy()
    trees.putalpha(ImageChops.multiply(shape, Image.new("L", SIZE, 118)))
    st.add("fog-trees", trees)


def loser_party(rid: str, party: str, names: dict) -> str | None:
    """Who stands in the fog when this party wins: the R when D or I wins, the D when R wins, and failing those the
    other name on the ballot. The House majority has no single loser."""
    if R.RACES[rid]["type"] == "control":
        return None
    order = {"D": "RI", "I": "RD", "R": "DI"}[party]
    return next((p for p in order if names.get(p) and names[p] != names.get(party)), None)


def fog_figure(st: Stack, rid: str, party: str) -> None:
    """The loser's cutout, about the size of the cartoon on OH-SEN_D. It has to run down to the fog's floor, so a photo
    that stops early gets bigger, and past a point it sinks lower instead."""
    cut = Image.open(CUTOUTS / f"{rid}_{party}.png").convert("RGBA")
    fx, fy, fw, fh = FACES[f"{rid}_{party}"]["face"]
    head = cut.split()[-1].getbbox()[1]
    want = FOG_FIGURE
    reach = (want["floor"] - want["top"]) / (cut.height - head)
    scale = max(want["face"] / fh, min(reach, want["max_face"] / fh))
    y = max(int(want["top"] - head * scale), int(want["floor"] - cut.height * scale))
    new = (max(1, int(cut.width * scale)), max(1, int(cut.height * scale)))
    cut = cut.resize(new, Image.LANCZOS)
    alpha = soften_cropped_sides(cut.split()[-1].filter(ImageFilter.MinFilter(3)).filter(ImageFilter.GaussianBlur(0.6)), width=max(12, new[0] // 12))
    cut.putalpha(alpha)
    layer = blank()
    x = int(want["cx"] - (fx + fw / 2) * scale)
    layer.alpha_composite(cut, (max(0, x), max(0, y)), (max(0, -x), max(0, -y)))
    ImageDraw.Draw(layer).rectangle([0, want["floor"], W, H], fill=(0, 0, 0, 0))
    st.add("fog-loser", layer)


def fog_slot(st: Stack, rid: str, party: str, names: dict) -> None:
    """What stands in the fog: the hand-made picture if this card has one, or the loser's photo, and always the caption."""
    st.slot(FOG_SLOT)
    other = loser_party(rid, party, names)
    art = FOG_ART.get(f"{rid}_{party}")
    if art:
        st.add("fog-loser", Image.open(art[0]).convert("RGBA"), art[1])
    elif other and has_portrait(rid, other):
        fog_figure(st, rid, other)
    bar = Image.open(FOG_CAPTION).convert("RGBA")
    st.add("fog-caption", bar, (FOG_CAPTION_POS[0] - bar.width // 2, FOG_CAPTION_POS[1]))


def fog_front(st: Stack, zone: tuple[int, int, int, int], clip: Image.Image | None, *, strength: float = 0.72, top: int = 340,
              left: int = 460, right: int = 0) -> None:
    """Drifting mist over whatever is in the slot: thicker low down, so things seem to sink into it."""
    w, h = zone[2] - zone[0], zone[3] - zone[1]
    rng = random.Random(2026)                    # the same mist on every card
    cloud = Image.new("L", (w, h), 128)
    for gw, gh, weight in ((9, 5, 0.6), (18, 10, 0.35), (40, 22, 0.25), (90, 50, 0.12)):
        grid = Image.frombytes("L", (gw, gh), rng.randbytes(gw * gh)).resize((w, h), Image.BICUBIC)
        cloud = Image.blend(cloud, grid, weight)
    cloud = ImageOps.autocontrast(cloud).filter(ImageFilter.GaussianBlur(10))
    thick = cloud.point(lambda v: int(255 * (0.12 + 0.88 * v / 255) * strength))
    mask = Image.new("L", SIZE, 0)
    mask.paste(thick, zone[:2])
    layer = tinted(ImageChops.multiply(mask, fog_mask(zone, clip, top, left, right)), (198, 206, 220))
    st.add("fog-front", layer)


# ------------------------------------------------------------------------------------------------ type
def result_label(f: dict, party: str) -> str:
    if f["kind"] == "control":
        return "TAKEOVER" if f["flip"] else "HOLD"
    return f"FLIP {f['holder']} \u2192 {party}" if f["flip"] else f"{party} HOLD"


def footer(st: Stack, f: dict, party: str, panel: Image.Image, box: tuple[int, int, int, int]) -> None:
    px0, _, px1, py1 = box
    cover = Image.new("L", SIZE, 0)
    ImageDraw.Draw(cover).rectangle([px0, py1 - FOOT, px1, py1], fill=255)
    layer = blank()
    layer.putalpha(ImageChops.multiply(cover, panel).point(lambda v: int(v * 0.42)))
    st.add("footer-strip", layer)
    d = ImageDraw.Draw(layer)
    if not f["holder"]:
        return
    label = result_label(f, party)
    bf = G.font(34)
    bw = int(d.textlength(label, font=bf)) + 56
    bx1 = px1 - 50
    pm = G._rounded_mask(SIZE, [bx1 - bw, py1 - 70, bx1, py1 - 16], 27)
    punched_label(st, "flip-badge", label, bf, (bx1 - bw / 2, py1 - 43), pm, G.YELLOW if f["flip"] else (255, 255, 255))


def pill(st: Stack, x: int, y: int, text: str = "KEY RACE ALERT") -> None:
    """The alert pill, with the desk's name beside it."""
    d = ImageDraw.Draw(blank())
    tf = G.font(34)
    tw = int(d.textlength(text, font=tf)) + 64
    pm = G._rounded_mask(SIZE, [x, y, x + tw, y + 58], 29)
    punched_label(st, "pill", text, tf, (x + tw / 2, y + 29), pm, (255, 255, 255))
    brand(st, x + tw + 24, y + 29, "lm")


def brand(st: Stack, x: int, y: int, anchor: str) -> None:
    layer = blank()
    ImageDraw.Draw(layer).text((x, y), BRAND, font=G.font(32), fill=(255, 255, 255, 245), anchor=anchor)
    st.add("brand", layer)
    st.live["brand"] = [PSD.Layer("brand", layer, text=live_text(x, y, BRAND, G.font(32), (255, 255, 255), anchor))]


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


def check_mark(x: int, y: int, size: int) -> Image.Image:
    layer = blank()
    G._check(ImageDraw.Draw(layer), x, y, size)
    return layer


def name_block(st: Stack, f: dict, party: str, name: str | None, x: int, y: int, max_w: int, size: int) -> int:
    """WINNER with the check, then the name in one or two lines. Returns the y below it."""
    d = ImageDraw.Draw(blank())
    check = int(size * 0.56)
    st.add("check", check_mark(x, y, check))
    word = blank()
    word_font = C.fit(d, "WINNER", max_w, "Anton-Regular.ttf", int(size * 0.5))
    ImageDraw.Draw(word).text((x + check + 20, y + check // 2 + 2), "WINNER", fill=G.YELLOW + (255,), anchor="lm", font=word_font)
    st.add("winner-label", word)
    st.live["winner-label"] = [PSD.Layer("winner-label", word, text=live_text(x + check + 20, y + check // 2 + 2, "WINNER", word_font, G.YELLOW, "lm"))]
    yy = y + check + 12
    lines = winner_lines(f, party, name)
    for i, line in enumerate(lines, 1):
        fnt = G.fit(d, line, max_w, size, min_size=38)
        sm = G._text_mask(SIZE, (x + 4, yy + 6), line, fnt, anchor="lt")
        st.add(f"name-line-{i}-shadow", G._glow(sm, (0, 0, 0), 8, 0.6))
        text = blank()
        ImageDraw.Draw(text).text((x, yy), line, font=fnt, fill=(255, 255, 255, 255), anchor="lt")
        st.add(f"name-line-{i}", text)
        st.live[f"name-line-{i}"] = [PSD.Layer(f"name-line-{i}", text, text=live_text(x, yy, line, fnt, (255, 255, 255), "lt"), shadow=True)]
        yy += fnt.size + 12
    return yy


def race_title(st: Stack, f: dict, x: int, y: int, max_w: int, size: int = 58) -> None:
    layer = blank()
    d = ImageDraw.Draw(layer)
    text = title_of(f)
    fnt = G.fit(d, text, max_w, size)
    d.text((x, y), text, font=fnt, fill=(255, 255, 255, 240), anchor="lt")
    st.add("title", layer)
    st.live["title"] = [PSD.Layer("title", layer, text=live_text(x, y, text, fnt, (255, 255, 255), "lt"))]


def plain_card(st: Stack, f: dict, party: str, name: str | None, names: dict) -> None:
    """The one card: state and winner on the glass, and the loser in the fog below the name.
    The fog is for Republicans losing, so a Republican win has no fog and the name takes the room instead."""
    panel = glass(st, party, PANEL)
    ghost(st, f, party, panel, PANEL)
    px0, py0, px1, py1 = PANEL
    has = has_portrait(f["rid"], party)
    fog = party != "R"
    if fog:
        forest, shape = fog_back(st, (60, 620, 850, py1 - FOOT), panel, party)
        fog_slot(st, f["rid"], party, names)
        fog_trees(st, forest, shape)
        fog_front(st, (90, 760, 800, py1 - FOOT), panel, strength=0.62, top=170, left=170, right=170)
    if has:
        above = Image.new("L", SIZE, 0)
        ImageDraw.Draw(above).rectangle([px0, 0, px1, py0 + RADIUS], fill=255)
        portrait(st, f["rid"], party, px1 - 290, py1 - FOOT, 205, max_top=8, clip=ImageChops.lighter(panel, above))
    footer(st, f, party, panel, PANEL)
    pill(st, px0 + 56, py0 + 36)
    badge(st, f, px1 - 36 if has else px1 - 56, py0 + 30, *((130, 105) if has else (210, 170)))
    tx = px0 + 60
    text_w = 800 if has else 1030
    race_title(st, f, tx, py0 + 112, text_w, 54)
    if fog:
        size = 128 if has or len(winner_lines(f, party, name)) > 1 else 150  # two big lines would run into the caption
        name_block(st, f, party, name, tx, py0 + 210, text_w, size)
    else:
        tall = name_block(Stack(), f, party, name, 0, 0, text_w, 170)        # measured, to sit in the middle of the room left
        top, bottom = py0 + 180, py1 - FOOT
        name_block(st, f, party, name, tx, top + (bottom - top - tall) // 2, text_w, 170)


def render(rid: str, party: str, names: dict) -> Stack:
    name = names.get(party)
    st = Stack()
    plain_card(st, C.race_facts(rid, party, name, None, None, False, None), party, name, names)
    return st


# ------------------------------------------------------------------------------------------------ Photoshop files
GROUPS = (("Text", ("pill", "brand", "title", "name-line", "check", "winner-label", "flip-badge")),
          ("State", ("state-", "district", "badge")),
          ("Fog", ("fog-", FOG_SLOT)),
          ("Portrait", ("portrait-",)),
          ("Card", ("glass", "footer-strip")))


def group_of(name: str) -> str:
    return next(label for label, prefixes in GROUPS if name.startswith(prefixes))


def grouped(st: Stack) -> list:
    """The layers in their stacking order, in groups. A group appears twice when something from another group sits
    between its layers: the footer strip in front of the portrait, say. Text comes out as live type, and a name's
    shadow comes out as a layer style on the name rather than a picture that would stay behind when the name changes."""
    items: list = []
    for name, image in st.layers:
        if name.startswith("name-line") and name.endswith("-shadow"):
            continue
        if name in ("fog-loser", "fog-caption"):           # these go in the slot itself
            items[-1].children[-1].children.append(PSD.Layer(name, image))
            continue
        label = group_of(name)
        if not (items and items[-1].name == label):
            items.append(PSD.Group(label))
        if name in st.live:
            items[-1].children.extend(st.live[name])
            continue
        items[-1].children.append(PSD.Group(name) if image is None else PSD.Layer(name, image))
    return items


def discord_background() -> PSD.Layer:
    return PSD.Layer("Discord dark background (preview only, leave hidden)", Image.new("RGBA", SIZE, (54, 57, 63, 255)), hidden=True)


def write_template(cands: dict, replace: bool) -> str:
    """The one master file to edit, the plain card with the Ohio Senate race on it.
    The first save from here is kept beside it as the original, so edits can be read off by comparing the two."""
    if TEMPLATE.exists() and not replace:
        return f"{TEMPLATE.name} is already there, left alone. Add --replace to start it over."
    plain = render("OH-SEN", "D", cands["OH-SEN"])
    items: list = [discord_background(), PSD.Group("PLAIN CARD", grouped(plain))]
    KIT.mkdir(parents=True, exist_ok=True)
    count = PSD.write_psd(TEMPLATE, items, plain.flat())
    shutil.copyfile(TEMPLATE, TEMPLATE_ORIGINAL)
    return f"{TEMPLATE}: {count} layers, {TEMPLATE.stat().st_size / 1e6:.1f} MB"


def write_race_psd(job: tuple) -> str:
    """One race's Photoshop file."""
    rid, party, names, replace = job
    path = PSD_DIR / f"{rid}_{party}.psd"
    if path.exists() and not replace:
        return f"{path.name}: already there, left alone"
    st = render(rid, party, names)
    items = [discord_background(), *grouped(st)]
    PSD_DIR.mkdir(parents=True, exist_ok=True)
    count = PSD.write_psd(path, items, st.flat())
    return f"{path.name}: {count} layers, {path.stat().st_size / 1e6:.1f} MB"


# ------------------------------------------------------------------------------------------------ running it
def png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


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
    cands = P.candidates()
    jobs = []
    for rid in P.races(False, cands):
        names = cands.get(rid) or {}
        for party in sorted(set(names) | {"D", "R"}):
            jobs.append((rid, party, names))
    if args:
        jobs = [j for j in jobs if f"{j[0]}_{j[1]}" in args or j[0] in args]

    if "--template" in argv:
        print(write_template(cands, "--replace" in argv))
        return

    if "--psd" in argv:
        with ProcessPoolExecutor() as pool:
            for line in pool.map(write_race_psd, [(rid, party, names, "--replace" in argv) for rid, party, names in jobs]):
                print(line, flush=True)
        print(f"Folder: {PSD_DIR}")
        return

    if "--kit" in argv:
        for rid, party, names in jobs:
            folder = KIT / f"{rid}_{party}"
            folder.mkdir(parents=True, exist_ok=True)
            for i, (name, layer) in enumerate(render(rid, party, names).layers):
                if layer is not None:
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
            data = png_bytes(render(rid, party, names).flat())
            spare = path.with_suffix(".tmp")                   # the bot can post at any moment, so never leave half a file
            spare.write_bytes(data)
            spare.replace(path)
            made_by_us[path.name] = hashlib.sha256(data).hexdigest()
            stand.pop(path.name, None)
            made += 1
            print(f"\r{made} drawn", end="", flush=True)
    finally:                                          # a crash halfway mustn't make finished art look hand-edited
        MANIFEST.write_text(json.dumps(made_by_us, indent=0, sort_keys=True), encoding="utf-8")
        G.STAND_INS.write_text(json.dumps(stand, indent=0, sort_keys=True), encoding="utf-8")
    print(f"\r{made} drawn, {kept} of your own left alone. Folder: {CALLS}")
    if "--sheet" in argv:
        shots = [CALLS / f"{j[0]}_{j[1]}.png" for j in jobs if (CALLS / f"{j[0]}_{j[1]}.png").exists()]
        KIT.mkdir(parents=True, exist_ok=True)
        contact_sheet(shots, KIT / "_sheet_all.jpg")


if __name__ == "__main__":
    main(sys.argv)

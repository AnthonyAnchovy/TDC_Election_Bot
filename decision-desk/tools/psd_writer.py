"""Write a layered Photoshop file from Pillow images, with layer groups. Needs nothing beyond Pillow.

Layers are given bottom first as Layer or Group. Each is stored cropped to what it actually paints, RLE compressed.
Layout follows Adobe's "Photoshop File Formats Specification".

A Layer given a Text is a live type layer. Photoshop's text data is too long to write from nothing, so psd_text_model/
holds a text layer and a drop shadow saved by Photoshop itself, and each new layer is that one with its details changed.
"""
from __future__ import annotations

import re
import struct
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

RUN = re.compile(rb"(.)\1{2,}", re.S)      # three or more of the same byte
MODEL = Path(__file__).resolve().parent / "psd_text_model"
LEFT, RIGHT, CENTER = 0, 1, 2              # Photoshop's numbers for text alignment


@dataclass
class Text:
    text: str
    font: str                     # PostScript name, like Arial-BoldMT
    size: int
    x: float                      # where the baseline starts (LEFT), ends (RIGHT) or has its middle (CENTER)
    y: float                      # the baseline
    color: tuple[int, int, int]
    width: float                  # how wide it is as drawn, and how far it reaches above and below the baseline
    ascent: float
    descent: float
    align: int = LEFT


@dataclass
class Layer:
    name: str
    image: Image.Image            # RGBA, the size of the canvas
    hidden: bool = False
    blend: bytes = b"norm"        # Photoshop's four-letter blend mode key
    text: Text | None = None      # makes it a live type layer, with image as what it looks like until it's edited
    shadow: bool = False          # a drop shadow as a layer style, so it follows the text when the text changes


@dataclass
class Group:
    name: str
    children: list = field(default_factory=list)     # bottom first
    hidden: bool = False


def _packbits(raw: bytes) -> bytes:
    """One row of one channel, PackBits encoded. Runs of 3 or more repeat; everything else goes out as literals."""
    out = bytearray()
    literal_from = 0

    def literals(upto: int) -> None:
        pos = literal_from
        while pos < upto:
            count = min(128, upto - pos)
            out.append(count - 1)
            out.extend(raw[pos:pos + count])
            pos += count

    for match in RUN.finditer(raw):
        literals(match.start())
        left = match.end() - match.start()
        while left > 0:
            count = min(128, left)
            out += bytes((0 if count == 1 else 257 - count, raw[match.start()]))
            left -= count
        literal_from = match.end()
    literals(len(raw))
    return bytes(out)


def _rle(channel: Image.Image) -> tuple[bytes, bytes]:
    """(the table of row lengths, the rows)"""
    width = channel.width
    raw = channel.tobytes()
    rows = [_packbits(raw[y * width:(y + 1) * width]) for y in range(channel.height)]
    return b"".join(struct.pack(">H", len(r)) for r in rows), b"".join(rows)


def _tagged(key: bytes, data: bytes) -> bytes:
    if len(data) % 2:
        data += b"\0"
    return b"8BIM" + key + struct.pack(">I", len(data)) + data


def _engine_string(text: str) -> bytes:
    """A string the way the text engine stores it: UTF-16 with a byte order mark, and its brackets escaped."""
    return re.sub(rb"([()\\])", rb"\\\1", b"\xfe\xff" + text.encode("utf-16-be"))


def _once(data: bytes, old: bytes, new: bytes) -> bytes:
    assert old in data, old
    return data.replace(old, new, 1)


def _type_block(t: Text) -> bytes:
    """The TySh block: the model layer's, with this text, font, size, color and position."""
    model = (MODEL / "TySh.bin").read_bytes()
    engine_at = model.index(b"EngineDatatdta") + 14
    engine_len = struct.unpack(">I", model[engine_at:engine_at + 4])[0]
    engine = model[engine_at + 4:engine_at + 4 + engine_len]

    start = engine.index(b"/Text (") + 7                # the model's text has no brackets in it to confuse this
    engine = engine[:start] + _engine_string(t.text + "\r") + engine[engine.index(b")", start):]
    length = len(t.text) + 1
    engine = engine.replace(b"/RunLengthArray [ 12 ]", b"/RunLengthArray [ %d ]" % length)
    engine = _once(engine, b"/FontSize 128.0", b"/FontSize %d.0" % t.size)
    engine = _once(engine, b"/Leading 153.60001", b"/Leading %.5f" % (t.size * 1.2))
    engine = _once(engine, b"/Values [ 1.0 .98039 .83922 .24314 ]", b"/Values [ 1.0 %.5f %.5f %.5f ]" % tuple(c / 255 for c in t.color))
    engine = _once(engine, b"/Justification 0", b"/Justification %d" % t.align)
    engine = engine.replace("Arial-BoldMT".encode("utf-16-be"), t.font.encode("utf-16-be"))

    left = {LEFT: 0, RIGHT: -t.width, CENTER: -t.width / 2}[t.align]
    bounds = (left, -t.ascent, left + t.width, t.descent)
    body = model[50:engine_at] + struct.pack(">I", len(engine)) + engine + model[engine_at + 4 + engine_len:]
    head = body.index(b"Txt TEXT") + 8
    old_chars = struct.unpack(">I", body[head:head + 4])[0]
    body = (body[:head] + struct.pack(">I", length) + t.text.encode("utf-16-be") + b"\0\0"
            + body[head + 4 + old_chars * 2:])
    for box in (b"boundsObjc", b"boundingBoxObjc"):
        at = body.index(box)
        for key, value in zip((b"LeftUntF#Pnt", b"Top UntF#Pnt", b"RghtUntF#Pnt", b"BtomUntF#Pnt"), bounds):
            at = body.index(key, at) + 12
            body = body[:at] + struct.pack(">d", value) + body[at + 8:]
    return struct.pack(">H6d", 1, 1, 0, 0, 1, t.x, t.y) + body


def _record(name: str, bounds: tuple[int, int, int, int], channels: list[tuple[int, int]], *, blend: bytes = b"norm",
            flags: int = 0x08, section: int | None = None, blocks: bytes = b"") -> bytes:
    """One layer record. bounds = top, left, bottom, right. channels = (id, data length)."""
    head = struct.pack(">iiiiH", *bounds, len(channels))
    head += b"".join(struct.pack(">hI", cid, size) for cid, size in channels)
    head += b"8BIM" + blend + struct.pack(">BBBB", 255, 0, flags, 0)
    short = name.encode("latin-1", "replace")[:255]
    pascal = bytes((len(short),)) + short
    pascal += b"\0" * (-len(pascal) % 4)
    wide = name.encode("utf-16-be")
    extra = struct.pack(">II", 0, 0) + pascal + _tagged(b"luni", struct.pack(">I", len(name)) + wide)
    if section is not None:
        extra += _tagged(b"lsct", struct.pack(">I", section) + b"8BIMpass")
    extra += blocks
    return head + struct.pack(">I", len(extra)) + extra


def _flatten(items: list) -> list:
    """Groups become their closing marker, their children, then their folder: the order Photoshop stores them in."""
    out = []
    for item in items:
        if isinstance(item, Group):
            out.append(("close", item))
            out += _flatten(item.children)
            out.append(("open", item))
        else:
            out.append(("pixels", item))
    return out


def write_psd(path: Path, items: list, merged: Image.Image) -> int:
    """Write the file and return how many pixel layers went in. merged is the flat picture, for previews."""
    width, height = merged.size
    records, data = [], []
    drawn = 0
    empty = [(cid, 2) for cid in (-1, 0, 1, 2)]
    for kind, item in _flatten(items):
        if kind != "pixels":
            name = "</Layer group>" if kind == "close" else item.name
            records.append(_record(name, (0, 0, 0, 0), empty, blend=b"pass" if kind == "open" else b"norm",
                                   flags=0x18 if kind == "close" else 0x08 | (0x02 if item.hidden else 0),
                                   section=3 if kind == "close" else 1))
            data.append(b"\0\0" * 4)
            continue
        bbox = item.image.split()[-1].getbbox()
        if bbox is None:
            continue
        crop = item.image.crop(bbox)
        red, green, blue, alpha = crop.split()
        clear = alpha.point(lambda v: 255 if v else 0)     # the color under fully clear pixels doesn't matter, and zero compresses
        black = Image.new("L", crop.size, 0)
        left, top, right, bottom = bbox
        channels = []
        chunks = []
        for cid, plane in ((-1, alpha), (0, Image.composite(red, black, clear)), (1, Image.composite(green, black, clear)),
                           (2, Image.composite(blue, black, clear))):
            table, rows = _rle(plane)
            channels.append((cid, 2 + len(table) + len(rows)))
            chunks.append(struct.pack(">H", 1) + table + rows)
        blocks = b""
        if item.shadow:
            blocks += b"".join(_tagged(key, (MODEL / f"{key.decode()}.bin").read_bytes()) for key in (b"lfx2", b"lrFX"))
        if item.text:
            blocks += _tagged(b"TySh", _type_block(item.text))
        records.append(_record(item.name, (top, left, bottom, right), channels, blend=item.blend,
                               flags=0x08 | (0x02 if item.hidden else 0), blocks=blocks))
        data.append(b"".join(chunks))
        drawn += 1

    info = struct.pack(">h", -len(records)) + b"".join(records) + b"".join(data)   # negative: the merged image has alpha
    info += b"\0" * (len(info) % 2)
    section = struct.pack(">I", len(info)) + info + struct.pack(">I", 0)

    tables, rows = zip(*(_rle(plane) for plane in merged.convert("RGBA").split()))
    with open(path, "wb") as fh:
        fh.write(b"8BPS" + struct.pack(">H", 1) + b"\0" * 6 + struct.pack(">HIIHH", 4, height, width, 8, 3))
        fh.write(struct.pack(">I", 0))               # color mode data
        fh.write(struct.pack(">I", 0))               # image resources
        fh.write(struct.pack(">I", len(section)) + section)
        fh.write(struct.pack(">H", 1) + b"".join(tables) + b"".join(rows))
    return drawn

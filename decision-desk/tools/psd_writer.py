"""Write a layered Photoshop file from Pillow images, with layer groups. Needs nothing beyond Pillow.

Layers are given bottom first as Layer or Group. Each is stored cropped to what it actually paints, RLE compressed.
Layout follows Adobe's "Photoshop File Formats Specification".
"""
from __future__ import annotations

import re
import struct
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

RUN = re.compile(rb"(.)\1{2,}", re.S)      # three or more of the same byte


@dataclass
class Layer:
    name: str
    image: Image.Image            # RGBA, the size of the canvas
    hidden: bool = False
    blend: bytes = b"norm"        # Photoshop's four-letter blend mode key


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


def _record(name: str, bounds: tuple[int, int, int, int], channels: list[tuple[int, int]], *, blend: bytes = b"norm",
            flags: int = 0x08, section: int | None = None) -> bytes:
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
        records.append(_record(item.name, (top, left, bottom, right), channels, blend=item.blend, flags=0x08 | (0x02 if item.hidden else 0)))
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

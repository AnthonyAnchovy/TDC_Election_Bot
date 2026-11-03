"""Cut the background out of every photo in assets/candidates/ and find the face, for make_projection_art.py.

    pip install "rembg[cpu]" "opencv-python-headless<5"      (not in requirements.txt: the bot itself never needs them)
    python tools/cut_portraits.py        only photos without a cutout yet
    python tools/cut_portraits.py GA-SEN_D PA-07_R      redo these

Writes assets/candidates/cutouts/<RACE>_<PARTY>.png and the face box of each in cutouts/_faces.json. If a cutout comes
out badly, fix it by hand in any image editor and save it over the file: this script won't touch it unless you name it.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps
from rembg import new_session, remove

HERE = Path(__file__).resolve().parent.parent
SRC = HERE / "assets" / "candidates"
OUT = SRC / "cutouts"
FACES = OUT / "_faces.json"
MAX_SIDE = 1400
FACE_MAX = 300


def find_face(rgb: np.ndarray, alpha: np.ndarray) -> list[int] | None:
    """The largest face on the kept part of the picture, as [x, y, w, h]."""
    cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    grey = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    found = cascade.detectMultiScale(grey, 1.08, 6, minSize=(max(40, rgb.shape[1] // 14),) * 2)
    best = None
    for x, y, w, h in found:
        if alpha[y + h // 2, x + w // 2] > 128 and y + h / 2 < rgb.shape[0] * 0.6 and (best is None or w * h > best[2] * best[3]):
            best = [int(x), int(y), int(w), int(h)]
    return best


def head_from_mask(alpha: np.ndarray) -> list[int]:
    """No face found (profile, glasses glare): guess the head from the top of the silhouette."""
    ys, xs = np.where(alpha > 128)
    top, width = int(ys.min()), int(xs.max() - xs.min())
    band = np.where(alpha[top: top + max(8, alpha.shape[0] // 6)].max(axis=0) > 128)[0]
    w = int(min(band.max() - band.min(), width * 0.6))
    return [int((band.max() + band.min()) / 2 - w / 2), top + w // 5, w, w]


def keep_largest(cut: Image.Image) -> Image.Image:
    """Drop whatever isn't joined to the person: scraps of a flag, a specimen of the backdrop."""
    arr = np.array(cut)
    count, labels, stats, _ = cv2.connectedComponentsWithStats((arr[..., 3] > 40).astype(np.uint8), connectivity=8)
    if count > 2:
        biggest = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
        arr[..., 3][labels != biggest] = 0
    return Image.fromarray(arr)


def finish(cut: Image.Image, face: list[int]) -> tuple[Image.Image, list[int]]:
    """Crop to the person and shrink so the face is at most FACE_MAX tall: the cards never draw it bigger."""
    cut = keep_largest(cut)
    x0, y0, x1, y1 = cut.getbbox()
    cut = cut.crop((x0, y0, x1, y1))
    face = [face[0] - x0, face[1] - y0, face[2], face[3]]
    k = FACE_MAX / face[3]
    if k < 1:
        cut = cut.resize((int(cut.width * k), int(cut.height * k)), Image.LANCZOS)
        face = [int(v * k) for v in face]
    return cut, face


def main(argv):
    only = set(argv[1:])
    OUT.mkdir(parents=True, exist_ok=True)
    faces = json.loads(FACES.read_text(encoding="utf-8")) if FACES.exists() else {}
    session = new_session("u2net_human_seg")
    done = 0
    for path in sorted(p for p in SRC.iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")):
        out = OUT / f"{path.stem}.png"
        if (only and path.stem not in only) or (not only and out.exists() and path.stem in faces):
            continue
        img = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
        img.thumbnail((MAX_SIDE, MAX_SIDE), Image.LANCZOS)
        cut = remove(img, session=session, post_process_mask=True).convert("RGBA")
        arr = np.array(cut)
        if arr[..., 3].max() < 128:
            print(f"{path.stem}: nothing kept, skipped")
            continue
        face = find_face(np.array(img), arr[..., 3])
        guessed = face is None
        if guessed:
            face = head_from_mask(arr[..., 3])
        reaches = cut.getbbox()[3] >= img.height - 2
        cut, face = finish(cut, face)
        faces[path.stem] = {"face": face, "guessed": guessed, "reaches_bottom": reaches}
        cut.save(out, optimize=True)
        FACES.write_text(json.dumps(faces, indent=0, sort_keys=True), encoding="utf-8")
        done += 1
        print(f"{path.stem}{' (face guessed)' if guessed else ''}", flush=True)
    print(f"{done} cut out. Folder: {OUT}")


if __name__ == "__main__":
    main(sys.argv)

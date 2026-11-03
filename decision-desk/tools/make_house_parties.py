"""Build house_parties.json: every 2026 House candidate and their party, from the FEC's candidate master file.

Decision Desk HQ's call posts name a winner but not a party, and candidates.json only has the battleground seats, so
the Bluesky watcher looks names up here.

    python tools/make_house_parties.py
"""
from __future__ import annotations

import io
import json
import re
import sys
import unicodedata
import urllib.request
import zipfile
from pathlib import Path

URL = "https://www.fec.gov/files/bulk-downloads/2026/cn26.zip"
OUT = Path(__file__).resolve().parent.parent / "house_parties.json"
PARTY = {"DEM": "D", "DFL": "D", "REP": "R"}        # anything else is treated as an independent
SUFFIX = {"JR", "SR", "II", "III", "IV", "V", "MD", "PHD", "ESQ"}


def clean(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().upper()
    return re.sub(r"[^A-Z ]", "", s.replace("-", " ")).strip()


def main():
    req = urllib.request.Request(URL, headers={"User-Agent": "decision-desk"})
    with urllib.request.urlopen(req, timeout=120) as r:
        z = zipfile.ZipFile(io.BytesIO(r.read()))
    rows = z.read(z.namelist()[0]).decode("latin-1").splitlines()
    out: dict[str, list] = {}
    for line in rows:
        f = line.split("|")
        if len(f) < 8 or f[5] != "H" or f[3] != "2026" or "," not in f[1]:
            continue
        last, first = f[1].split(",", 1)
        first = [w for w in clean(first).split() if w not in SUFFIX and w not in ("MR", "MRS", "MS", "DR")]
        entry = [clean(last), first[0] if first else "", PARTY.get(f[2], "I"), f[6]]
        if entry not in out.setdefault(f[4], []):
            out[f[4]].append(entry)
    n = sum(len(v) for v in out.values())
    if n < 1000:
        sys.exit(f"Only {n} candidates found, so the file looks wrong. Keeping the old {OUT.name}.")
    note = "Built by tools/make_house_parties.py from " + URL + ". state -> [last, first, party, district]."
    OUT.write_text(json.dumps({"_note": note, "states": out}, separators=(",", ":")), encoding="utf-8")
    print(f"{n} candidates in {len(out)} states -> {OUT.name}")


if __name__ == "__main__":
    main()

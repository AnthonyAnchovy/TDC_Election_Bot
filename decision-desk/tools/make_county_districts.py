"""Build countywatch/county_districts.json: which 2026 U.S. House districts each County Watch county is in, for the
results DMs. Run it again only if a map changes. The bot itself needs nothing beyond the JSON.

    python tools/make_county_districts.py <folder holding cd120.zip>

cd120.zip is Census's 120th Congress block equivalency file (public, about 55 MB):
https://www2.census.gov/programs-surveys/decennial/rdo/mapping-files/2027/120-congressional-district-befs/cd120.zip

A block's first five digits are its county, so a county's districts are the ones its blocks fall in. A district with
under 5% of a county's blocks is left out, so a sliver across a line doesn't read as that county's district.
"""
from __future__ import annotations

import collections
import csv
import io
import json
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import cw_model as M

OUT = Path(__file__).resolve().parent.parent / "countywatch" / "county_districts.json"
SMALLEST_SHARE = 0.05


def main(folder: Path):
    states = {c["fips"]: st for st, counties in M.load().items() for c in counties if st != "AK"}
    blocks = collections.defaultdict(collections.Counter)
    with zipfile.ZipFile(folder / "cd120.zip") as z, z.open("NationalCD120.txt") as f:
        rd = csv.reader(io.TextIOWrapper(f, encoding="utf-8-sig"))
        next(rd)
        for row in rd:
            county = row[0][:5]
            if county in states and row[-1].isdigit():
                blocks[county][row[-1]] += 1
    out = {}
    for county, st in sorted(states.items()):
        total = sum(blocks[county].values())
        kept = [d for d, n in blocks[county].most_common() if n / total >= SMALLEST_SHARE] if total else []
        out[county] = [f"{st}-{d}" for d in kept]
    missing = [c for c, ds in out.items() if not ds]
    OUT.write_text(json.dumps(out, indent=0) + "\n", encoding="utf-8")
    print(f"{len(out)} counties, {sum(len(d) > 1 for d in out.values())} split across districts, "
          f"{len(missing)} with none: {missing[:10]}")


if __name__ == "__main__":
    main(Path(sys.argv[1]))

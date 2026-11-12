"""Build countywatch/ak_modes24.json: Alaska's 2024 presidential vote by vote type, which cw_alaska measures 2026 against.

    python tools/make_ak_modes.py [path to a saved copy of the state's file]
"""
import json
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import cw_feeds as FD  # noqa: E402

URL = "https://www.elections.alaska.gov/results/24GENR/ENRbyPrecinct.csv"


def main(argv):
    raw = Path(argv[1]).read_bytes() if len(argv) > 1 else requests.get(URL, timeout=60, headers={"User-Agent": "Mozilla/5.0"}).content
    _, ed, modes = FD.ak_tally(raw, "U.S. President", {})
    out = {"source": URL, "contest": "U.S. President 2024, [D, R]", "ed": ed, **modes}
    (ROOT / "countywatch" / "ak_modes24.json").write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
    print(f"{len(ed)} precincts; Election Day {sum(map(sum, ed.values())):,}, "
          + ", ".join(f"{k} {sum(map(sum, v.values())):,}" for k, v in modes.items()))


if __name__ == "__main__":
    main(sys.argv)

"""Draw a projection graphic for every race and winner into assets/calls/, ready to swap for your own.

    python tools/make_call_pngs.py                  Senate, governors, House control and the rated House seats
    python tools/make_call_pngs.py --all-house      every House seat as well (about 870 more files)
    python tools/make_call_pngs.py --theme editorial

Files are named <RACE>_<PARTY>.png (GA-SEN_D.png, NE-SEN_I.png, US-HOUSE_R.png, PA-07_D.png): D and R for every
race, plus I where candidates.json lists an independent. The ones drawn here are stand-ins, recorded in
assets/calls/_stand-ins.json: while a file is untouched the bot draws that card fresh when the race is called, so its
seat counts and time are current. Edit or replace a file (a .jpg, .gif or .webp of the same name works too) and the
bot posts yours exactly as it is. Running this again redraws the stand-ins (after a theme or name change, say) and
never touches a file you've changed.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import competitive as CMP  # noqa: E402
import graphics as G       # noqa: E402
import races as R          # noqa: E402

OUT = G.ASSETS / "calls"


def candidates() -> dict:
    data = json.loads((HERE / "candidates.json").read_text(encoding="utf-8"))
    return {k.upper(): {p: n for p, n in v.items() if n} for k, v in data.items() if not k.startswith("_")}


def races(all_house: bool, cands: dict) -> list[str]:
    out = [rid for rid, r in R.RACES.items() if r["type"] in ("control", "senate", "governor")]
    return out + [rid for rid, r in R.RACES.items()
                  if r["type"] == "house" and (all_house or rid in CMP.RATING_OF or rid in cands)]


def main(argv):
    theme = argv[argv.index("--theme") + 1] if "--theme" in argv else \
        json.loads((HERE / "config.json").read_text(encoding="utf-8")).get("card_theme", "wire")
    if theme not in G.CARD_THEMES:
        sys.exit(f"Unknown theme {theme!r}: pick one of {', '.join(G.CARD_THEMES)}")
    OUT.mkdir(parents=True, exist_ok=True)
    cands = candidates()
    stand = G.stand_ins()
    made = kept = 0
    for rid in races("--all-house" in argv, cands):
        names = cands.get(rid) or {}
        for party in sorted(set(names) | {"D", "R"}):
            path = OUT / f"{rid}_{party}.png"
            if path.exists() and not G.is_stand_in(path):
                kept += 1
                continue
            data = G.call_card(rid, party, candidate=names.get(party), theme=theme, use_custom=False).getvalue()
            path.write_bytes(data)
            stand[path.name] = hashlib.sha256(data).hexdigest()
            made += 1
        print(f"\r{made} drawn", end="", flush=True)
    G.STAND_INS.write_text(json.dumps(stand, indent=0, sort_keys=True), encoding="utf-8")
    print(f"\r{made} stand-ins drawn in the {theme} theme, {kept} of your own graphics left alone. Folder: {OUT}")


if __name__ == "__main__":
    main(sys.argv)

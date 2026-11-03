"""Download a free-licensed portrait of every candidate in candidates.json from Wikipedia into assets/candidates/.

    python tools/fetch_portraits.py            only the candidates who don't have a photo yet
    python tools/fetch_portraits.py --review   list who was matched to which page, without downloading

A photo is named <RACE>_<PARTY>.jpg (GA-SEN_D.jpg). Drop your own under the same name and it's left alone. A page is
only accepted if its text calls the person a politician or similar and mentions their state, so someone who merely
shares a candidate's name doesn't end up on a card. Who took each photo and its license go in assets/candidates/credits.json.
"""
from __future__ import annotations

import json
import re
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import races as R  # noqa: E402

OUT = HERE / "assets" / "candidates"
CREDITS = OUT / "credits.json"
API = "https://en.wikipedia.org/w/api.php"
UA = {"User-Agent": "TDC-Decision-Desk/1.0 (https://github.com/AnthonyAnchovy; election-night Discord bot)"}
THUMB = 960
POLITICAL = re.compile(r"politician|senator|representative|governor|congress|legislat|state house|state senate|"
                       r"attorney general|nominee|candidate|mayor|secretary of state|lieutenant", re.I)
# The page that scored best is someone else: a relative, or a namesake in the same line of work.
WRONG_PERSON = {"Eric Flores": "Mayra Flores", "Carlos De La Cruz": "Monica De La Cruz", "Stefany Shaheen": "Jeanne Shaheen",
                "Jake Johnson": "a North Carolina legislator", "Michael Bouchard": "the Oakland County sheriff, not the nominee"}
OTHER_JOBS = re.compile(r"\b(cyclist|footballer|radio announcer|model|actor|singer|musician|cricketer|rugby)\b", re.I)


def api(**params) -> dict:
    url = API + "?" + urllib.parse.urlencode({"action": "query", "format": "json", **params})
    for attempt in range(6):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as resp:
                return json.load(resp)
        except OSError:
            time.sleep(5 + attempt * 10)      # mostly Wikipedia's rate limit: back off and try again
    raise RuntimeError(f"Wikipedia didn't answer: {url[:120]}")


def plain(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if not unicodedata.combining(c)).lower()


def lookup(titles: list[str]) -> list[dict]:
    pages = api(redirects=1, titles="|".join(titles), prop="pageimages|description|extracts|pageprops",
                piprop="thumbnail|name", pithumbsize=THUMB, pilicense="free", exintro=1, explaintext=1, exlimit="max",
                ppprop="disambiguation")["query"].get("pages", {})
    return [p for p in pages.values() if "missing" not in p]


def score(page: dict, name: str, state: str, office: str) -> int:
    """How sure we are this page is this candidate. Below 5 is not used. The office breaks ties between namesakes:
    Dan Sullivan the senator over Dan Sullivan the Anchorage mayor."""
    if "disambiguation" in page.get("pageprops", {}):
        return -1
    title, text = plain(page["title"]), plain(page.get("description", "") + " " + page.get("extract", ""))
    parts = plain(name).replace(".", "").split()
    if parts[-1] not in title and not (parts[-1] == "jr" and parts[-2] in title):
        return -1
    pts = 0
    if OTHER_JOBS.search(page.get("description", "")) and not POLITICAL.search(page.get("description", "")):
        return -1
    if POLITICAL.search(text):
        pts += 3
    if plain(R.STATE_NAMES.get(state, "")) in text:
        pts += 3
    if "american" in text or "united states" in text:
        pts += 1
    if "2026" in text:
        pts += 2
    if re.search(office, plain(page.get("description", "") + " " + page["title"])):
        pts += 2
    elif re.search(office, text):
        pts += 1
    return pts


def find_page(name: str, state: str, office: str) -> tuple[dict | None, int]:
    guesses = [name, f"{name} (politician)", f"{name} (American politician)", f"{name} ({R.STATE_NAMES[state]} politician)",
               f"{name} (U.S. politician)", f"{name} (U.S. senator)", f"{name} (U.S. representative)"]
    pages = lookup(guesses)
    surname = plain(name).replace(".", "").split()[-1 if not name.endswith("Jr.") else -2]
    hits = api(list="search", srsearch=f'"{name}" {R.STATE_NAMES[state]} politician', srlimit=5)["query"]["search"]
    titles = [h["title"] for h in hits if surname in plain(h["title"]) and h["title"] not in {p["title"] for p in pages}]
    if titles:
        pages += lookup(titles)
    ranked = sorted(((score(p, name, state, office), p) for p in pages), key=lambda t: -t[0])
    return (ranked[0][1], ranked[0][0]) if ranked and ranked[0][0] >= 5 else (None, ranked[0][0] if ranked else -1)


def download(src: str) -> bytes:
    for attempt in range(6):
        try:
            with urllib.request.urlopen(urllib.request.Request(src, headers=UA), timeout=60) as resp:
                return resp.read()
        except OSError:
            time.sleep(5 + attempt * 10)
    raise RuntimeError(f"Couldn't download {src}")


def credit(file_name: str) -> dict:
    pages = api(titles=f"File:{file_name}", prop="imageinfo", iiprop="extmetadata|url")["query"]["pages"]
    info = next(iter(pages.values())).get("imageinfo", [{}])[0]
    meta = info.get("extmetadata", {})
    strip = lambda key: re.sub(r"<[^>]+>", "", meta.get(key, {}).get("value", "")).strip()   # noqa: E731
    return {"file": file_name, "page": info.get("descriptionurl", ""), "author": strip("Artist"), "license": strip("LicenseShortName")}


def main(argv):
    review = "--review" in argv
    OUT.mkdir(parents=True, exist_ok=True)
    cands = json.loads((HERE / "candidates.json").read_text(encoding="utf-8"))
    credits = json.loads(CREDITS.read_text(encoding="utf-8")) if CREDITS.exists() else {}
    found: dict[tuple[str, str], tuple] = {}
    got = missing = 0
    for rid, names in cands.items():
        if rid.startswith("_") or rid == "US-HOUSE":
            continue
        state = rid.split("-")[0]
        for party, name in names.items():
            if not name:
                continue
            path = OUT / f"{rid}_{party}.jpg"
            if not review and any((OUT / f"{rid}_{party}.{e}").exists() for e in ("jpg", "jpeg", "png", "webp")):
                continue
            if name in WRONG_PERSON:
                print(f"{rid}_{party:<2} {name:<28} skipped: the best match is {WRONG_PERSON[name]}", flush=True)
                missing += 1
                continue
            key = (name, state)
            if key not in found:
                kind = rid.split("-")[1]
                found[key] = find_page(name, state, {"SEN": "senat", "GOV": "governor"}.get(kind, "representative|congress"))
                time.sleep(0.3)
            page, pts = found[key]
            src = (page or {}).get("thumbnail", {}).get("source")
            print(f"{rid}_{party:<2} {name:<28} {pts:>2}  "
                  + (f"{page['title']} | {page.get('description', '')}{'' if src else ' | NO FREE PHOTO'}" if page else "no page"),
                  flush=True)
            if review or not src:
                missing += not src
                continue
            path.write_bytes(download(src))
            credits[path.name] = {"name": name, "wikipedia": page["title"], **credit(page["pageimage"])}
            CREDITS.write_text(json.dumps(credits, indent=1, sort_keys=True, ensure_ascii=False), encoding="utf-8")
            got += 1
            time.sleep(0.25)
    CREDITS.write_text(json.dumps(credits, indent=1, sort_keys=True, ensure_ascii=False), encoding="utf-8")
    print(f"{got} downloaded, {missing} without a usable free photo. Folder: {OUT}")


if __name__ == "__main__":
    main(sys.argv)

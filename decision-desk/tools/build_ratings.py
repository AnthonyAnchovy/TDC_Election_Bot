"""Build history/ratings.csv: what the expert raters said before each election.

One row per race per rater. The source is the ratings tables on Wikipedia's
election pages (text under CC BY-SA 4.0, https://creativecommons.org/licenses/by-sa/4.0/).
The ratings themselves belong to the raters (Cook Political Report, Sabato's
Crystal Ball, Inside Elections and others); Wikipedia editors typed them in.

Columns: race, rater, rating, level, as_of
  race    {year}-{ST}-{OFFICE}, OFFICE is SEN, SEN2 (a second, special Senate race in the
          same state and year), GOV or PRES. The same id the history table uses.
  rater   Cook, Sabato, Inside (Inside Elections, once called the Rothenberg Report), or
          the name the table gives (CQ, RCP, Politico, ...).
  rating  as the table writes it, e.g. "Lean R", "Safe D (flip)", "Tossup".
  level   -3 Safe/Solid R, -2 Likely R, -1 Lean R, -0.5 Tilt R, 0 Toss-up, and the same
          for D with a plus sign.
  as_of   the date in the column heading when it has one, else blank.

The tables come in two cell layouts, so there are two cell readers sharing one table reader:
  template  cells hold {{USRaceRating|Lean|R}}   (most pages)
  text      cells hold plain words such as "Safe R" or {{sort|108|Safe R}}
            (Senate 2014, governor 2018 and 2022, a few older tables)
Each cell is read by whichever it matches, so a page may mix them. The table reader
(rows, columns that span, header dates, which row is which state) is the same for all.

Run from decision-desk/:  python tools/build_ratings.py [--fetch] [--report]
  --fetch   download any page that is missing from research/sources/wp/ (raw wikitext)
  --report  print the checks (rows by year/office/rater, unrated races, unmapped text)

Standard library only. Nothing is guessed: a cell that does not read as a rating is
left out and listed by --report.
"""
import csv
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "research", "sources", "wp")
OUT = os.path.join(ROOT, "history", "ratings.csv")

SENATE_YEARS = range(2000, 2027, 2)
GOV_YEARS = range(2000, 2027, 2)
# page title -> (year, office); president pages differ by year
PAGES = {}
for _y in SENATE_YEARS:
    PAGES[f"{_y}_United_States_Senate_elections"] = (_y, "SEN")
for _y in GOV_YEARS:
    PAGES[f"{_y}_United_States_gubernatorial_elections"] = (_y, "GOV")
for _y in (2004, 2008, 2012):
    PAGES[f"{_y}_United_States_presidential_election"] = (_y, "PRES")
PAGES["2020_United_States_presidential_election_predictions"] = (2020, "PRES")
PAGES["Statewide_opinion_polling_for_the_2024_United_States_presidential_election"] = (2024, "PRES")

STATES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA", "colorado": "CO",
    "connecticut": "CT", "delaware": "DE", "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS", "kentucky": "KY", "louisiana": "LA",
    "maine": "ME", "maryland": "MD", "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY", "north carolina": "NC",
    "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR", "pennsylvania": "PA",
    "rhode island": "RI", "south carolina": "SC", "south dakota": "SD", "tennessee": "TN", "texas": "TX",
    "utah": "UT", "vermont": "VT", "virginia": "VA", "washington": "WA", "west virginia": "WV",
    "wisconsin": "WI", "wyoming": "WY", "district of columbia": "DC",
}

MAGNITUDE = {"safe": 3.0, "solid": 3.0, "likely": 2.0, "lean": 1.0, "leans": 1.0, "tilt": 0.5, "tossup": 0.0}
MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}

# header text (lower case, markup stripped) -> the rater's name in the CSV
RATER_NAMES = [
    ("cook", "Cook"), ("sabato", "Sabato"), ("crystal ball", "Sabato"),
    ("inside elections", "Inside"), ("rothenberg", "Inside"), ("gonzales", "Inside"),
]
RATER_EXACT = {"ie": "Inside", "cpr": "Cook", "cprc": "Cook", "cb": "Sabato", "scb": "Sabato"}


# ---------------------------------------------------------------- fetching

def page_path(title):
    return os.path.join(SRC, title + ".txt")


def fetch(title):
    """Download one page's raw wikitext into research/sources/wp/ (polite: one at a time)."""
    url = "https://en.wikipedia.org/w/index.php?action=raw&title=" + urllib.parse.quote(title)
    req = urllib.request.Request(url, headers={"User-Agent": "tdc-decision-desk-research/1.0 (non-commercial)"})
    data = urllib.request.urlopen(req, timeout=60).read()
    os.makedirs(SRC, exist_ok=True)
    with open(page_path(title), "wb") as f:
        f.write(data)
    time.sleep(2)


# ---------------------------------------------------------------- wikitext reading

def strip_templates(s, names):
    """Remove {{name ...}} (balanced braces) for the given lower-case template names."""
    out, i = [], 0
    while i < len(s):
        m = re.compile(r"\{\{\s*([^|{}\s]+)", re.S).match(s, i) if s.startswith("{{", i) else None
        if m and m.group(1).lower() in names:
            depth, j = 0, i
            while j < len(s):
                if s.startswith("{{", j):
                    depth += 1
                    j += 2
                elif s.startswith("}}", j):
                    depth -= 1
                    j += 2
                    if depth == 0:
                        break
                else:
                    j += 1
            i = j
            continue
        out.append(s[i])
        i += 1
    return "".join(out)


def prepare(text):
    """Take out comments, footnotes and references so cells hold only what a reader sees."""
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    text = re.sub(r"<ref\b[^>]*/>", "", text, flags=re.S)
    text = re.sub(r"<ref\b[^>]*>.*?</ref>", "", text, flags=re.S)
    text = strip_templates(text, {"efn", "efn-ua", "refn", "notelist"})
    return text


def find_tables(text):
    """Top-level {| ... |} blocks, as strings."""
    tables, depth, start = [], 0, None
    pos = 0
    for line in text.split("\n"):
        s = line.strip()
        if s.startswith("{|"):
            if depth == 0:
                start = pos
            depth += 1
        elif s.startswith("|}") and depth:
            depth -= 1
            if depth == 0:
                tables.append(text[start:pos + len(line)])
        elif depth and (s.lower().startswith("{{collapse bottom") or s.startswith("==")):
            # an editor left the table without its closing |}; a heading or the end of the
            # collapsed box is where it really stops
            depth = 0
            tables.append(text[start:pos])
        pos += len(line) + 1
    return tables


def split_top(s, sep):
    """Split s at sep, ignoring any inside [[ ]] or {{ }}."""
    parts, depth, cur, i = [], 0, [], 0
    while i < len(s):
        two = s[i:i + 2]
        if two in ("[[", "{{"):
            depth += 1
            cur.append(two)
            i += 2
        elif two in ("]]", "}}"):
            depth = max(0, depth - 1)
            cur.append(two)
            i += 2
        elif depth == 0 and s.startswith(sep, i):
            parts.append("".join(cur))
            cur = []
            i += len(sep)
        else:
            cur.append(s[i])
            i += 1
    parts.append("".join(cur))
    return parts


def read_cell(raw):
    """A cell's text after its attributes, plus the attribute text."""
    pieces = split_top(raw, "|")
    if len(pieces) > 1:
        return {"attrs": pieces[0], "text": "|".join(pieces[1:]).strip()}
    return {"attrs": "", "text": raw.strip()}


def span(attrs, name):
    m = re.search(name + r"\s*=\s*[\"']?(\d+)", attrs, re.I)
    return int(m.group(1)) if m else 1


def read_table(table):
    """rows: list of lists of {"header": bool, "attrs", "text", "colspan", "rowspan"}."""
    rows, cur = [], None
    for line in table.split("\n")[1:]:
        s = line.rstrip()
        t = s.lstrip()
        if t.startswith("|}"):
            break
        if t.startswith("|-"):
            cur = []
            rows.append(cur)
            continue
        if t.startswith("|+"):
            continue
        if t[:1] in ("!", "|"):
            if cur is None:
                cur = []
                rows.append(cur)
            header = t[0] == "!"
            body = t[1:]
            for raw in (split_top(body, "!!") if header else [body]):
                subs = split_top(raw, "||")  # a header line may also use || between cells
                for sub in subs:
                    c = read_cell(sub)
                    c["header"] = header
                    c["colspan"] = span(c["attrs"], "colspan")
                    c["rowspan"] = span(c["attrs"], "rowspan")
                    cur.append(c)
        elif cur and cur:
            cur[-1]["text"] += "\n" + t  # a cell that runs on to the next line
    return [r for r in rows if r]


def clean(s):
    """Plain text for a cell: links, sort templates, small print and markup removed."""
    s = re.sub(r"<br\s*/?>", " ", s, flags=re.I)
    rules = [  # tried in this order; after any change start again, so inner templates go first
        (r"\{\{\s*(?:sort|sortname)\s*\|[^{}|]*\|([^{}]*)\}\}", r"\1"),
        (r"\{\{\s*(?:small|nowrap|nobold)\s*\|([^{}]*)\}\}", r"\1"),
        (r"\{\{[^{}]*\}\}", ""),
    ]
    changed = True
    while changed:
        changed = False
        for pattern, repl in rules:
            new = re.sub(pattern, repl, s, flags=re.I)
            if new != s:
                s, changed = new, True
                break
    s = re.sub(r"\[\[(?:[^\]|]*\|)?([^\]]*)\]\]", r"\1", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = s.replace("'''", "").replace("''", "").replace("&nbsp;", " ").replace("&ndash;", "-")
    return re.sub(r"\s+", " ", s).strip()


# ---------------------------------------------------------------- reading a rating from a cell

TEMPLATE = re.compile(r"\{\{\s*USRaceRating\s*\|([^{}]*)\}\}", re.I)
TEXT = re.compile(
    r"^(?P<w>(?i:safe|solid|likely|leans?|tilt|toss[\s-]?up))\s*(?P<p>[DRI])?(?![A-Za-z])(?P<rest>.*)$")


def level_of(word, party):
    """-3..+3 for a rating word and party letter, or None when it can't be told."""
    w = re.sub(r"[\s-]", "", word.lower())
    if w not in MAGNITUDE:
        return None
    if w == "tossup":
        return 0.0
    if party not in ("D", "R"):
        return None
    mag = MAGNITUDE[w]
    return mag if party == "D" else -mag


def read_rating(cell_text):
    """(rating as written, level) for one cell; (None, None) for an empty cell;
    ("?", text) when there is text that does not read as a rating."""
    m = TEMPLATE.search(cell_text)
    if m:  # layout 1: the template
        bits = [b.strip() for b in m.group(1).split("|")]
        word = bits[0]
        party = bits[1].upper() if len(bits) > 1 and bits[1].upper() in ("D", "R") else ""
        flip = any(b.lower() == "flip" for b in bits[1:])
        level = level_of(word, party)
        if level is None:
            return "?", m.group(0)
        shown = word.capitalize() if word.lower() not in ("tossup", "toss-up") else "Tossup"
        return (shown + (" " + party if party else "") + (" (flip)" if flip else "")), level
    text = clean(cell_text)
    text = text.strip(" ^*†‡§")
    if not text or text in ("-", "—", "–", "N/A", "n/a", "NA", "?", "Not rated", "not rated", "TBD"):
        return None, None
    m = TEXT.match(text)  # layout 2: words in the cell
    if m:
        rest = re.sub(r"[\W_]+|flip|retiring", "", m.group("rest"), flags=re.I)
        party = (m.group("p") or "").upper()
        level = level_of(m.group("w"), party)
        if party == "I" and not rest and re.sub(r"[\s-]", "", m.group("w").lower()) != "tossup":
            # the table colours this seat Independent: no party, so no sign for the level
            return m.group("w").capitalize() + " I" + (" (flip)" if "flip" in text.lower() else ""), ""
        if level is not None and not rest:
            w = m.group("w")
            shown = "Tossup" if re.sub(r"[\s-]", "", w.lower()) == "tossup" else w.capitalize()
            return (shown + (" " + party if party else "") + (" (flip)" if "flip" in text.lower() else "")), level
    return "?", text


# ---------------------------------------------------------------- header: rater names and dates

ALIASES = {  # heading with spaces and punctuation removed -> the name in the CSV
    "realclearpolitics": "RCP", "rcp": "RCP", "fivethirtyeight": "538", "538": "538",
    "270towin": "270toWin", "cqpolitics": "CQ", "cq": "CQ", "congressionalquarterly": "CQ",
    "roth": "Inside", "fox": "Fox News", "foxnews": "Fox News", "ddhq": "DDHQ", "decisiondeskhq": "DDHQ",
    "ddhqthehill": "DDHQ", "econ": "Economist", "theeconomist": "Economist", "economist": "Economist",
    "nyt": "NYT", "newyorktimes": "NYT", "thenewyorktimes": "NYT", "dailykos": "Daily Kos",
    "cbs": "CBS News", "cbsnews": "CBS News", "gov": "Governing", "dke": "Daily Kos", "nbcnews": "NBC News", "abcnews": "ABC News",
}


def rater_name(header_text):
    label = clean(header_text)
    label = re.split(r"\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z.]*\s+\d", label)[0].strip(" ,:")
    low = label.lower()
    key = re.sub(r"[^a-z0-9]", "", low)
    if key in RATER_EXACT:
        return RATER_EXACT[key]
    if "partisan voting" not in low and "pvi" not in low:
        for frag, name in RATER_NAMES:
            if frag in low:
                return name
    return ALIASES.get(key, label)


def header_date(header_text):
    """ISO date from a column heading such as 'Cook Oct. 29, 2014', else ''."""
    s = re.sub(r"<br\s*/?>", " ", header_text, flags=re.I)
    s = clean(s)
    m = re.search(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z.]*\s+(\d{1,2}),?\s+(\d{4})", s, re.I)
    if not m:
        return ""
    return "%04d-%02d-%02d" % (int(m.group(3)), MONTHS[m.group(1).lower()[:3]], int(m.group(2)))


# ---------------------------------------------------------------- one table -> ratings

def header_grid(rows):
    """Leading all-header rows laid on a grid (colspan, rowspan honoured). Returns (grid, n_header_rows)."""
    n = 0
    while n < len(rows) and all(c["header"] for c in rows[n]):
        n += 1
    grid = {}
    for r in range(n):
        c_ix = 0
        for cell in rows[r]:
            while (r, c_ix) in grid:
                c_ix += 1
            for dr in range(cell["rowspan"]):
                for dc in range(cell["colspan"]):
                    grid[(r + dr, c_ix + dc)] = (cell, r)
            c_ix += cell["colspan"]
    return grid, n


def column_labels(rows, width):
    """{column index: raw heading text} using the lowest non-empty heading cell over each column.
    Only the first `width` columns count: editors often give a heading a colspan wider than the table."""
    grid, n = header_grid(rows)
    labels = {}
    for c in range(width):
        best = None
        for r in range(n):
            if (r, c) in grid:
                cell, start = grid[(r, c)]
                if clean(cell["text"]) and (best is None or start >= best[1]):
                    best = (cell, start)
        if best:
            labels[c] = best[0]["text"]
    return labels


def table_width(body):
    """How many cells a data row has: the commonest row length (a few summary rows are shorter)."""
    lengths = Counter(len(r) for r in body if len(r) > 2)
    return lengths.most_common(1)[0][0] if lengths else 0


def parse_table(table):
    """-> (list of {label, ratings:[(rater, rating, level, as_of)]}, list of unmapped (label, rater, text),
    list of rows skipped because their cells did not line up)."""
    rows = read_table(table)
    grid, n = header_grid(rows)
    body = [r for r in rows[n:] if any(clean(c["text"]) for c in r)]
    width = table_width(body)
    labels = column_labels(rows, width)
    # which columns are rating columns: at least one cell reads as a rating
    hits = Counter()
    for r in body:
        if len(r) == width:
            for i, c in enumerate(r):
                if read_rating(c["text"])[0] not in (None, "?"):
                    hits[i] += 1
    races, unmapped, skipped = [], [], []
    for r in body:
        label = clean(r[0]["text"])
        if not label:
            continue
        if len(r) != width:
            skipped.append((label, len(r), width))
            continue
        ratings = []
        for i in sorted(hits):
            rating, level = read_rating(r[i]["text"])
            if rating is None:
                continue
            if rating == "?":
                unmapped.append((label, rater_name(labels[i]), level))
                continue
            ratings.append((rater_name(labels[i]), rating, level, header_date(labels[i])))
        races.append({"label": label, "ratings": ratings})
    return races, unmapped, skipped


# ---------------------------------------------------------------- state names to race ids

def state_of(label, office):
    """(postal code, is_special, note) from a row label like 'Georgia (special)'; code None if not a state."""
    low = label.lower()
    special = "special" in low
    if re.search(r"['’]s\b|\bdistrict\b|\bcd\b", low):  # "Nebraska's 2nd" is a district, not the state
        return None, special, ""
    base = re.sub(r"\(.*?\)|\bspecial\b|\bregular\b|\bgeneral\b|\bclass \w+\b|\bat[- ]large\b|#.*", " ", low)
    base = re.sub(r"[^a-z ]", " ", base)
    base = re.sub(r"\s+", " ", base).strip()
    if base in STATES:
        return STATES[base], special, ""
    for name in sorted(STATES, key=len, reverse=True):  # "Georgia 2", "Oklahoma Class 2"
        if base.startswith(name):
            return STATES[name], special, ""
    return None, special, ""


def page_rows(title, text):
    """-> (rows [(race, rater, rating, level, as_of)], unmapped, skipped, notes) for one page."""
    year, office = PAGES[title]
    text = prepare(text)
    raw_races, unmapped_all, skipped_all, notes = [], [], [], []
    for t_ix, table in enumerate(find_tables(text)):
        if "USRaceRating" not in table and not re.search(r"\b(?:Safe|Solid|Likely|Lean|Tilt|Toss-?up)\s+[DR]\b", table):
            continue
        races, unmapped, skipped = parse_table(table)
        if not any(r["ratings"] for r in races):
            continue
        for r in races:
            code, special, _ = state_of(r["label"], office)
            if code is None:
                if r["ratings"]:
                    notes.append(f"{year} {office}: row '{r['label']}' is not a state, left out")
                continue
            raw_races.append({"state": code, "special": special, "table": t_ix, "label": r["label"], "ratings": r["ratings"]})
        unmapped_all += [(year, office) + u for u in unmapped if state_of(u[0], office)[0]]
        skipped_all += [(year, office) + s_ for s_ in skipped if state_of(s_[0], office)[0]]
    out = []
    by_state = defaultdict(list)
    for r in raw_races:
        by_state[r["state"]].append(r)
    for code, entries in sorted(by_state.items()):
        if office != "SEN":
            if len(entries) > 1:
                notes.append(f"{year} {office} {code}: {len(entries)} rows ({[e['label'] for e in entries]}); first kept")
            ids = [(entries[0], f"{year}-{code}-{office}")]
        elif len(entries) == 1:
            ids = [(entries[0], f"{year}-{code}-SEN")]
        else:
            specials = [e for e in entries if e["special"]]
            regulars = [e for e in entries if not e["special"]]
            if len(entries) == 2 and len(specials) == 1:
                ids = [(regulars[0], f"{year}-{code}-SEN"), (specials[0], f"{year}-{code}-SEN2")]
            else:
                notes.append(f"{year} SEN {code}: {len(entries)} rows ({[e['label'] for e in entries]}) can't be told apart; left out")
                continue
        for e, rid in ids:
            if not e["ratings"]:
                notes.append(f"{rid}: listed in the table but no rater gave a rating")
            seen = set()
            for rater, rating, level, as_of in e["ratings"]:
                if rater in seen:
                    notes.append(f"{rid}: rater '{rater}' appears twice, second left out")
                    continue
                seen.add(rater)
                out.append((rid, rater, rating, level, as_of))
    return out, unmapped_all, skipped_all, notes


# ---------------------------------------------------------------- whole build

def build(fetch_missing=False):
    rows, unmapped, skipped, notes, missing = [], [], [], [], []
    for title in PAGES:
        path = page_path(title)
        if not os.path.exists(path):
            if fetch_missing:
                try:
                    fetch(title)
                except Exception as exc:  # a page that doesn't exist is a gap, not a crash
                    missing.append(f"{title} ({exc})")
                    continue
            else:
                missing.append(title)
                continue
        with open(path, encoding="utf-8") as f:
            r, u, s, n = page_rows(title, f.read())
        rows += r
        unmapped += u
        skipped += s
        notes += n
    rows.sort(key=lambda r: (r[0].split("-")[0], r[0].split("-")[2], r[0].split("-")[1], r[1]))
    return rows, unmapped, skipped, notes, missing


def write_csv(rows, path=OUT):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["race", "rater", "rating", "level", "as_of"])
        for rid, rater, rating, level, as_of in rows:
            w.writerow([rid, rater, rating, "" if level == "" else ("%g" % level), as_of])


def main(argv):
    rows, unmapped, skipped, notes, missing = build("--fetch" in argv)
    write_csv(rows)
    print(f"wrote {len(rows)} rows to {OUT}")
    if "--report" in argv:
        by = Counter((r[0].split("-")[0], r[0].split("-")[2]) for r in rows)
        print("\nrows by year and office")
        for k in sorted(by):
            print("  ", k, by[k])
        byr = Counter((r[0].split("-")[0], r[0].split("-")[2], r[1]) for r in rows)
        print("\nrows by year, office, rater")
        for k in sorted(byr):
            print("  ", k, byr[k])
        blank = [r for r in rows if r[3] == ""]
        print("\nrows kept with a blank level (rated Independent in the table)", len(blank))
        print("  ", sorted(Counter(r[0] for r in blank).items()))
        print("\nrows with a date", sum(1 for r in rows if r[4]), "of", len(rows))
        hist = os.path.join(ROOT, "history", "pre_history.csv")
        if os.path.exists(hist):
            with open(hist, newline="", encoding="utf-8") as f:
                races = {r["race"] for r in csv.DictReader(f)}
            rated = {r[0] for r in rows}
            pages_years = {(y, o) for y, o in {(PAGES[t][0], PAGES[t][1]) for t in PAGES} if os.path.exists(page_path(
                next(t for t in PAGES if PAGES[t] == (y, o))))}
            office_ok = lambda rid: (int(rid.split("-")[0]), "SEN" if rid.endswith("SEN2") else rid.split("-")[2]) in pages_years
            print("\nrated races not in pre_history.csv", sorted(rated - races))
            norating = sorted(r for r in races if office_ok(r) and r not in rated)
            print("\npre_history races whose year/office has a ratings page but no rating", len(norating))
            print("  by year/office:", sorted(Counter((r.split("-")[0], r.split("-")[2]) for r in norating).items()))
        print("\nunmapped text", len(unmapped))
        for u in unmapped:
            print("  ", u)
        print("\nskipped rows (cells did not line up)", len(skipped))
        for s in skipped:
            print("  ", s)
        print("\nnotes")
        for n in notes:
            print("  ", n)
        print("\nmissing pages", missing)


if __name__ == "__main__":
    main(sys.argv[1:])

"""Poll tables from Wikipedia, turned into the rows of a pack's `polls.csv` (no Discord, no outside libraries).

Wikipedia is the one open source that allows automated reading of polls, so this reads the wikitext of a page and
pulls out every opinion-poll table on it. Wikipedia's text is CC BY-SA 4.0: whoever shows or passes on the polls
must credit the page (see `credit()`); the command line writes that line to `polls_credit.txt` beside the CSV.

  parse(wikitext, race, options)   rows for cw_polls, plus plain-English notes on what was skipped and why
  fetch(title)                     the raw wikitext of a page (the network; the tests never call it)
  write_csv(rows, path)            append to or create polls.csv, only if cw_polls.check() passes the result
  python cw_polls_wiki.py "<page title>" --race GA-SEN --map "Ossoff=D,Collins=R" --out polls.csv

`options` maps a column heading (a candidate's name, as the page writes it) to the race's option key:
{"Ossoff": "D", "Collins": "R"}. A heading matches by its full name or its last name, ignoring case, a trailing
party tag like "(R)" and line breaks, so "Jon<br/>Ossoff (D)" matches "Ossoff" and "Jon Ossoff".

What a table needs to be read: a pollster column and a date column. The rest is optional. Tables of poll
aggregators ("Source of poll aggregation") and rows like "Average" are not polls and are skipped.

Rules for which tables count:
  - Every mapped candidate must have a column, or the table is skipped with a note.
  - A table with candidate columns that are not mapped is a different match-up (a hypothetical) and is skipped
    with a note, unless `allow_extra=True`, which ignores those columns. Other, Undecided, margin of error, lead
    and similar columns are never options.
  - `under="First round"` keeps only tables below a heading matching that text (a runoff section and a first
    round section of one page are different races).

Rows: one per poll per option, in the same layout as cw_polls. A poll's "(R)" or "(D)" after the pollster, a
sponsor column, or a footnote that says who commissioned the poll gives `sponsor`, but only when the sponsor is a
party or a campaign: cw_polls drops a poll whose sponsor it cannot place, so a newspaper is never written as one.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import html
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

import cw_polls

USER_AGENT = "DecisionDeskPollImporter/1.0 (election-results bot; reads Wikipedia polling tables)"
DELAY = 1.5  # seconds kept between two requests
_last_request = [0.0]

MONTHS = {m: i for i, names in enumerate(
    ["jan january", "feb february", "mar march", "apr april", "may", "jun june", "jul july", "aug august",
     "sep sept september", "oct october", "nov november", "dec december"], start=1) for m in names.split()}
MONTH_RE = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?"

# What a column heading is, tested in this order.
_KINDS = [
    ("rating", r"\brating|\bgrade\b|\brank\b"),
    ("sponsor", r"\bsponsor|\bcommissioned|\bclient\b|\bpaid for"),
    ("moe", r"\berror\b|\bmoe\b|\bmargin of"),
    ("date", r"\bdates?\b|\bconducted|\bfieldwork|\badministered|\bwhen\b"),
    ("pop", r"\bpopulation|\buniverse|\bvoters$|^type$"),
    ("sample", r"\bsample|\bsize\b|\brespondents|^n$"),
    ("aggregator", r"\baggregat"),
    ("pollster", r"\bpoll|\bsource|\bfirm\b|\borgani[sz]ation|\bby\b"),
    ("nonoption", r"^(others?|undecided|unsure|refused|neither|none|lead|spread|margin|net|total|don.?t know|"
                  r"not sure|no opinion|would not|wouldn.?t|won.?t vote|abstain|blank|someone else|no one|"
                  r"remain|\d+)\b|other\b|undecided|undecided"),
]
NOTE_ROW_WORDS = re.compile(r"average|aggregat|\bresults?\b|\bmean\b|\bmedian\b|polling average|election", re.I)
PARTISAN = re.compile(r"democrat|republican|\bgop\b|\bparty\b|campaign|committee|\bpac\b|super pac|dnc|rnc|dscc|nrsc|"
                      r"\bfor (?:senate|congress|governor|mayor|president)\b|\bfund\b|leadership|victory", re.I)


class Parsed(list):
    """The rows (a list of dicts). `.notes` says in plain words what was skipped, and `.tables` counts the tables read."""

    def __init__(self, rows=(), notes=None):
        super().__init__(rows)
        self.notes = list(notes or [])
        self.tables = 0


def credit(title):
    """The line Wikipedia's licence asks for."""
    page = urllib.parse.quote(title.replace(" ", "_"), safe="_:(),'")
    return (f'Poll data from the Wikipedia article "{title}" (https://en.wikipedia.org/wiki/{page}), '
            f"available under CC BY-SA 4.0 (https://creativecommons.org/licenses/by-sa/4.0/). "
            f"Authors are listed in the article's page history. Changes: tables reformatted into rows.")


# ---------------------------------------------------------------- cleaning wikitext

def _wrap_templates(text):
    """Remove {{templates}} but keep the words of the ones that only wrap text."""
    keep_last = {"sort"}
    keep_first = {"nowrap", "nobold", "small", "big", "abbr", "nobr", "hs", "noitalic", "smaller", "center"}
    joined = {"sortname"}
    dashes = {"sdash": "–", "snd": "–", "ndash": "–", "spaced ndash": " – ", "endash": "–", "mdash": "—"}
    spaces = {"nbsp", "hair space", "thin space", "space"}

    def one(m):
        parts = m.group(1).split("|")
        name = parts[0].strip().lower()
        args = [p for p in parts[1:] if "=" not in p.split("{")[0] or p.startswith(" ")]
        if name in dashes:
            return dashes[name]
        if name in spaces:
            return " "
        if name in keep_first and args:
            return args[0]
        if name in keep_last and len(args) > 1:
            return args[-1]
        if name in joined and args:
            return " ".join(a for a in args[:2] if a)
        return ""
    for _ in range(12):
        new = re.sub(r"\{\{([^{}]*)\}\}", one, text)
        if new == text:
            break
        text = new
    return re.sub(r"\{\{|\}\}", "", text)


def clean(text):
    """Plain text from a wikitext cell: no notes, templates, links, tags or quote marks."""
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    text = re.sub(r"<ref[^>]*?/>", "", text)
    text = re.sub(r"<ref[^>]*>.*?</ref>", "", text, flags=re.S | re.I)
    text = re.sub(r"\[\[(?:File|Image|Category):[^\]]*\]\]", "", text, flags=re.I)
    text = re.sub(r"\[\[[^\]|]*\|([^\]]*)\]\]", r"\1", text)
    text = re.sub(r"\[\[([^\]]*)\]\]", r"\1", text)
    text = re.sub(r"\[(?:https?:)?//\S+\s+([^\]]*)\]", r"\1", text)
    text = re.sub(r"\[(?:https?:)?//\S+\]", "", text)
    text = _wrap_templates(text)
    text = re.sub(r"<br\s*/?>|<wbr\s*/?>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text).replace("\xa0", " ")
    text = re.sub(r"'{2,}", "", text)
    if "|" in text:  # leftover "attributes | content"
        text = text.rsplit("|", 1)[1]
    return re.sub(r"\s+", " ", text).strip()


def _footnotes(raw):
    """The text of every <ref>...</ref> in a raw cell: titles and publishers of citations, and loose words."""
    out = []
    for m in re.finditer(r"<ref[^>]*>(.*?)</ref>", raw, flags=re.S | re.I):
        body = m.group(1)
        bits = re.findall(r"\|\s*(?:title|publisher|work|website|quote|trans-title)\s*=\s*([^|}]*)", body)
        loose = re.sub(r"\{\{.*?\}\}", " ", body, flags=re.S)
        loose = re.sub(r"\[(?:https?:)?//\S+\s*([^\]]*)\]", r"\1", loose)
        out.append(" ".join(bits + [loose]))
    return [re.sub(r"\s+", " ", html.unescape(t)).strip() for t in out]


# ---------------------------------------------------------------- tables

def _split_top(line, sep):
    """Split on `sep` outside [[ ]] and {{ }}."""
    parts, depth, cur, i = [], 0, "", 0
    while i < len(line):
        two = line[i:i + 2]
        if two in ("[[", "{{"):
            depth += 1
            cur += two
            i += 2
        elif two in ("]]", "}}"):
            depth = max(0, depth - 1)
            cur += two
            i += 2
        elif depth == 0 and line.startswith(sep, i):
            parts.append(cur)
            cur = ""
            i += len(sep)
        else:
            cur += line[i]
            i += 1
    parts.append(cur)
    return parts


_ATTRS = re.compile(r"^\s*(?:[A-Za-z-]+\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s\"|]+)\s*)+$")


def _cell(raw, header):
    """(attributes dict, content) from the raw text after the cell's leading | or !."""
    attrs = {}
    pieces = _split_top(raw, "|")
    if len(pieces) >= 2 and "[[" not in pieces[0] and "{{" not in pieces[0] and _ATTRS.match(pieces[0]):
        for k, v in re.findall(r"([A-Za-z-]+)\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s\"|]+)", pieces[0]):
            attrs[k.lower()] = v.strip("\"'")
        raw = "|".join(pieces[1:])
    return attrs, raw.strip(), header


def _int(v, default=1):
    try:
        return max(1, min(int(re.sub(r"\D", "", str(v))), 50))
    except ValueError:
        return default


def _tables(wikitext):
    """Yield (headings, year_heading, table_lines) for each top-level table, in page order."""
    lines = wikitext.split("\n")
    headings, year, year_level, i = [], None, 0, 0
    while i < len(lines):
        line = lines[i]
        h = re.match(r"^(={2,6})\s*(.*?)\s*\1\s*$", line)
        if h:
            level, text = len(h.group(1)), clean(h.group(2))
            headings = [x for x in headings if x[0] < level] + [(level, text)]
            y = re.fullmatch(r"(?:in )?((?:19|20)\d\d)", text, re.I)
            if y:
                year, year_level = int(y.group(1)), level
            elif year is not None and level <= year_level:
                year = None  # a new section of the same or higher rank ends the year heading
        if line.lstrip().startswith("{|"):
            depth, body = 1, [line]
            i += 1
            while i < len(lines) and depth:
                s = lines[i].lstrip()
                if s.startswith("{|"):
                    depth += 1
                elif s.startswith("|}"):
                    depth -= 1
                body.append(lines[i])
                i += 1
            yield [t for _, t in headings], year, body
            continue
        i += 1


def _grid(table_lines):
    """The table as rows of (text, is_header, raw) with row and column spans filled in."""
    rows, cur = [], None
    for line in table_lines[1:]:
        s = line.strip()
        if s.startswith("|}"):
            break
        if s.startswith("|-"):
            cur = None
            continue
        if s.startswith("|+"):
            continue
        if s[:1] in ("|", "!"):
            if cur is None:
                cur = []
                rows.append(cur)
            hdr = s[0] == "!"
            body = s[1:]
            for piece in _split_top(body, "!!" if hdr else "||"):
                attrs, content, _ = _cell(piece, hdr)
                cur.append({"attrs": attrs, "raw": content, "header": hdr})
        elif cur:
            cur[-1]["raw"] += "\n" + line
    grid, carry = [], {}
    for row in rows:
        out, col, cells = [], 0, iter(row)
        pending = list(row)
        while pending or any(c >= col for c in carry):
            if col in carry:
                text, hdr, raw, left = carry[col]
                out.append((text, hdr, raw))
                if left <= 1:
                    del carry[col]
                else:
                    carry[col] = (text, hdr, raw, left - 1)
                col += 1
                continue
            if not pending:
                if any(c > col for c in carry):
                    out.append(("", False, ""))
                    col += 1
                    continue
                break
            c = pending.pop(0)
            span, rspan = _int(c["attrs"].get("colspan", 1)), _int(c["attrs"].get("rowspan", 1))
            text = clean(c["raw"])
            for k in range(span):
                out.append((text, c["header"], c["raw"]))
                if rspan > 1:
                    carry[col + k] = (text, c["header"], c["raw"], rspan - 1)
            col += span
        grid.append(out)
    return grid


def _kind(heading):
    h = re.sub(r"\s+", " ", heading.lower()).strip(" :")
    for kind, pat in _KINDS:
        if re.search(pat, h):
            return kind
    return "candidate"


def _norm(name):
    name = clean(name)
    name = re.sub(r"\s*\((?:[A-Z]{1,4}|[^)]*party[^)]*)\)\s*$", "", name)
    name = re.sub(r"[\"'“”‘’.,]", "", name)
    return re.sub(r"\s+", " ", name).casefold().strip()


def _match(heading, options):
    """The option key a candidate heading maps to, or None."""
    h = _norm(heading)
    if not h:
        return None
    for name, key in options.items():
        n = _norm(name)
        if h == n or h.split()[-1] == n or n.split()[-1] == h or h.endswith(" " + n) or n.endswith(" " + h):
            return key
    return None


def _is_header_row(row):
    if not row or not all(h for _, h, _ in row):
        return False
    return not any(re.search(r"\d+(?:\.\d+)?\s*%|\b(?:19|20)\d\d\b", t) and not re.search(r"[A-Za-z]{4,}", t.replace("size", ""))
                   for t, _, _ in row)


# ---------------------------------------------------------------- dates, samples, shares

_DASH = re.compile(r"\s*(?:[-–—‒―]|\bto\b|\bthrough\b|&ndash;)\s*")


def parse_date(text, default_year=None):
    """(start, end) dates from how Wikipedia writes a poll's fieldwork, or None. Handles "Oct 1–5, 2026",
    "September 28 – October 2, 2026", "May 21, 2026", "1–5 October 2026", "2026-10-05" and, given
    `default_year`, the same without a year."""
    t = re.sub(r"(\d)(?:st|nd|rd|th)\b", r"\1", text.strip(), flags=re.I)
    t = re.sub(r"\(.*?\)|\bapprox\.?|~|\bc\.", "", t).strip(" .,;")
    iso = re.fullmatch(r"(\d{4})-(\d\d)-(\d\d)", t)
    if iso:
        d = dt.date(*map(int, iso.groups()))
        return d, d
    parts = _DASH.split(t, maxsplit=1)
    if len(parts) == 2 and not parts[1]:
        return None

    def bits(p):
        m = re.search(MONTH_RE, p, re.I)
        y = re.search(r"\b((?:19|20)\d\d)\b", p)
        rest = re.sub(MONTH_RE, " ", p, flags=re.I)
        rest = re.sub(r"\b(?:19|20)\d\d\b", " ", rest)
        d = re.search(r"\b(\d{1,2})\b", rest)
        return (MONTHS[m.group(1).lower()] if m else None, int(d.group(1)) if d else None, int(y.group(1)) if y else None)
    a = bits(parts[0])
    b = bits(parts[1]) if len(parts) == 2 else a
    if len(parts) == 1:
        b = a
    am, ad, ay = a
    bm, bd, by = b
    if len(parts) == 2:
        am = am or bm
        bm = bm or am
        if ad is None and bd is None:
            return None
    if None in (am, ad) or None in (bm, bd):
        return None
    year_end = by or ay or default_year
    if year_end is None:
        return None
    year_start = ay or year_end
    try:
        end = dt.date(year_end, bm, bd)
        start = dt.date(year_start, am, ad)
    except ValueError:
        return None
    if start > end:
        if ay is None:
            try:
                start = dt.date(year_end - 1, am, ad)
            except ValueError:
                return None
        elif by is None:
            try:
                end = dt.date(year_end + 1, bm, bd)
            except ValueError:
                return None
    return (start, end) if start <= end else None


_POPS = {"lv": "lv", "likely": "lv", "likely voters": "lv", "rv": "rv", "registered": "rv", "registered voters": "rv",
         "a": "a", "adults": "a", "adult": "a", "all adults": "a", "ad": "a"}


def parse_sample(text):
    """(n, population) from "1,024 (LV)", "800 RV", "~500 (A)" or "–"."""
    t = text.strip()
    n = None
    m = re.search(r"(\d[\d,]*)", t)
    if m:
        try:
            n = int(m.group(1).replace(",", ""))
        except ValueError:
            n = None
        if n is not None and n < 10:
            n = None
    pop = ""
    word = re.sub(r"[\d,~≈()\[\].±]", " ", t).strip().lower()
    word = re.sub(r"\s+", " ", word)
    for k in (word, word.split()[0] if word else ""):
        if k in _POPS:
            pop = _POPS[k]
            break
    return n, pop


def parse_share(text):
    """Percent from "49%", "<1%" or "45 (+3)"; None for dashes and blanks."""
    t = text.strip()
    if t.startswith("<"):
        return 0.5 if re.match(r"<\s*1\b", t) else None
    m = re.match(r"^~?(\d{1,3}(?:\.\d+)?)\s*%?(?:\s|\(|$)", t)
    return float(m.group(1)) if m else None


# ---------------------------------------------------------------- sponsors

def _sponsor_key(text, options):
    """Who a partisan or campaign sponsor text means: an option key, "D" or "R", the text itself, or ""."""
    text = text.strip()
    if not text or re.fullmatch(r"[-–—]|n/?a|none|self|—", text, re.I):
        return ""
    for name, key in options.items():
        n = _norm(name)
        if n and (n in text.casefold() or n.split()[-1] in re.findall(r"[\w'-]+", text.casefold())):
            return key
    if re.search(r"democrat|\bdnc\b|dscc|dccc|\(D\)", text, re.I):
        return "D"
    if re.search(r"republican|\bgop\b|\brnc\b|nrsc|nrcc|\(R\)", text):
        return "R"
    return text if PARTISAN.search(text) else ""


_WHO = re.compile(r"(?:commissioned|sponsored|paid for|funded|conducted|prepared|released)\s+(?:by|for)\s+(?:the\s+)?"
                  r"([A-Z][A-Za-z.&' -]{2,60})")


def _sponsor_from_notes(notes, options):
    for note in notes:
        for m in _WHO.finditer(note):
            s = _sponsor_key(m.group(1).strip(" .,-"), options)
            if s:
                return s
        m = re.search(r"internal (?:poll|memo|survey)(?: (?:for|by|from) (?:the )?([A-Z][A-Za-z.&' -]{2,60}))?", note, re.I)
        if m and m.group(1):
            s = _sponsor_key(m.group(1).strip(" .,-"), options)
            if s:
                return s
    return ""


# ---------------------------------------------------------------- parse

def parse(wikitext, race, options, default_year=None, today=None, allow_extra=False, under=None):
    """Every opinion-poll table on the page as cw_polls rows (a list of dicts with the polls.csv columns).

    The result's `.notes` explains every table and row left out. `default_year` is used for dates with no year when
    no year heading or year row gives one; `today` (default: the real today) drops rows dated after it."""
    today = today or dt.date.today()
    if isinstance(today, str):
        today = dt.date.fromisoformat(today)
    out = Parsed()
    seen = set()
    for headings, year, lines in _tables(wikitext):
        where = f"the table under '{headings[-1]}'" if headings else "a table at the top of the page"
        if under and not any(re.search(under, h, re.I) for h in headings):
            continue
        grid = _grid(lines)
        hdr_rows = []
        for row in grid:
            if _is_header_row(row):
                hdr_rows.append(row)
            else:
                break
        if not hdr_rows:
            continue
        width = max(len(r) for r in hdr_rows)
        heads = []
        for c in range(width):
            texts = []
            for r in hdr_rows:
                if c < len(r) and r[c][0] and (not texts or texts[-1] != r[c][0]):
                    texts.append(r[c][0])
            heads.append(" ".join(texts))
        kinds = [_kind(h) if h else "blank" for h in heads]
        if "aggregator" in kinds:
            out.notes.append(f"Skipped {where}: it is a list of polling averages, not polls.")
            continue
        if "pollster" not in kinds or "date" not in kinds:
            continue
        out.tables += 1
        col = {k: kinds.index(k) for k in ("pollster", "date", "sample", "sponsor", "pop") if k in kinds}
        cands = [(i, _match(h, options), h) for i, (h, k) in enumerate(zip(heads, kinds)) if k == "candidate"]
        mapped = {key for _, key, _ in cands if key}
        missing = [n for n, k in options.items() if k not in mapped]
        if missing:
            out.notes.append(f"Skipped {where}: no column for {', '.join(missing)} (columns: "
                             f"{', '.join(h for _, _, h in cands) or 'none'}).")
            continue
        extra = [h for _, key, h in cands if not key]
        if extra and not allow_extra:
            out.notes.append(f"Skipped {where}: it has columns for other candidates ({', '.join(extra)}), so it is a "
                             f"different match-up.")
            continue
        cols = {key: i for i, key, _ in cands if key}
        yr, kept, dropped = year, 0, 0
        for row in grid[len(hdr_rows):]:
            texts = [t for t, _, _ in row]
            filled = [t for t in texts if t]
            if len(set(texts)) == 1 and len(row) > 2 or len(filled) == 1 and len(row) > 2:
                ym = re.fullmatch(r"(?:in )?((?:19|20)\d\d)", filled[0].strip(), re.I) if filled else None
                if ym:
                    yr = int(ym.group(1))
                continue
            if len(row) <= max(col["pollster"], col["date"]):
                continue
            ym = re.fullmatch(r"(?:in )?((?:19|20)\d\d)", texts[0].strip(), re.I)
            if ym and not texts[col["date"]].strip():
                yr = int(ym.group(1))
                continue
            raw_pollster = row[col["pollster"]][2]
            pollster = texts[col["pollster"]]
            sponsor = ""
            m = re.search(r"\s*\(([DR])\)\s*$", pollster)
            if m:
                sponsor, pollster = m.group(1), pollster[:m.start()].strip()
            if not pollster or NOTE_ROW_WORDS.search(pollster) and not re.search(r"poll|research|survey", pollster, re.I):
                continue
            span = parse_date(texts[col["date"]], yr or default_year)
            if not span:
                out.notes.append(f"Skipped a poll by {pollster} in {where}: its date '{texts[col['date']]}' could not "
                                 f"be read (no year given, or not a day range).")
                dropped += 1
                continue
            if span[1] > today or span[0] > today:
                out.notes.append(f"Skipped {pollster} ({texts[col['date']]}): dated after today.")
                dropped += 1
                continue
            n, pop = parse_sample(texts[col["sample"]]) if "sample" in col and col["sample"] < len(row) else (None, "")
            if "pop" in col and col["pop"] < len(row) and not pop:
                pop = parse_sample(texts[col["pop"]])[1] or _POPS.get(texts[col["pop"]].strip().lower(), "")
            if "sponsor" in col and col["sponsor"] < len(row):
                sponsor = _sponsor_key(texts[col["sponsor"]], options) or sponsor
            if not sponsor:
                sponsor = _sponsor_from_notes(_footnotes(raw_pollster), options)
            shares = {}
            for key, i in cols.items():
                s = parse_share(texts[i]) if i < len(row) else None
                if s is not None:
                    shares[key] = s
            if len(shares) < len(cols):
                # a candidate left out of a poll is fine, but a poll that names none of them is not a poll of this race
                if not shares:
                    dropped += 1
                    continue
            if sum(shares.values()) > 100.0001:
                out.notes.append(f"Skipped {pollster} ({texts[col['date']]}): the shares add up to "
                                 f"{sum(shares.values()):g}, over 100.")
                dropped += 1
                continue
            ident = (pollster, span[0], span[1], pop)
            if ident in seen:
                out.notes.append(f"Skipped a second row for {pollster} ({texts[col['date']]}) with the same dates "
                                 f"and kind of voter.")
                dropped += 1
                continue
            seen.add(ident)
            kept += 1
            for key, s in shares.items():
                out.append({"race": race, "pollster": pollster, "start": span[0].isoformat(), "end": span[1].isoformat(),
                            "n": n if n else "", "population": pop, "sponsor": sponsor, "option": key, "share": s})
        out.notes.append(f"Read {where}: {kept} polls" + (f", {dropped} rows skipped." if dropped else "."))
    if not out:
        if out.tables:
            why = "polling tables were found but none could be used (see the other notes)"
        else:
            why = "the page has no opinion-poll table (no table with a pollster column and a date column)"
        out.notes.insert(0, f"No polls: {why}.")
    return out


# ---------------------------------------------------------------- network and files

class FetchError(RuntimeError):
    pass


def fetch(title, delay=DELAY, timeout=30):
    """The raw wikitext of an English Wikipedia page. Waits so two calls are at least `delay` seconds apart."""
    wait = _last_request[0] + delay - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    url = ("https://en.wikipedia.org/w/index.php?title=" + urllib.parse.quote(title.replace(" ", "_"), safe="_:(),'")
           + "&action=raw")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        raise FetchError(f"Wikipedia answered {e.code} for '{title}'" + (" (no such page)." if e.code == 404 else ".")) from e
    except (urllib.error.URLError, OSError) as e:
        raise FetchError(f"Could not reach Wikipedia: {e}") from e
    finally:
        _last_request[0] = time.monotonic()


def write_csv(rows, path, races=None, today=None):
    """Append rows to `path` (creating it) and run cw_polls.check() on the result. A file that fails is left exactly as
    it was and the reasons are returned; an empty list means it was written. Rows already in the file (same race,
    pollster, dates, kind of voter and option) are not added twice."""
    if not rows:
        return ["There are no poll rows to write."]
    path = os.path.abspath(path)
    old = ""
    have = set()
    if os.path.exists(path):
        with open(path, encoding="utf-8-sig", newline="") as f:
            old = f.read()
        for r in csv.DictReader(old.splitlines()):
            have.add((r.get("race"), r.get("pollster"), r.get("start"), r.get("end"), r.get("population"), r.get("option")))
    new = [r for r in rows if (r["race"], r["pollster"], r["start"], r["end"], r["population"], r["option"]) not in have]
    if not new and old:
        return []
    body = tempfile.SpooledTemporaryFile(mode="w+", newline="", encoding="utf-8")
    w = csv.writer(body, lineterminator="\n")
    if not old.strip():
        w.writerow(cw_polls.COLUMNS)
        old = ""
    for r in new:
        w.writerow([r[c] for c in cw_polls.COLUMNS])
    body.seek(0)
    text = old + ("" if not old or old.endswith("\n") else "\n") + body.read()
    body.close()
    fd, tmp = tempfile.mkstemp(suffix=".csv", dir=os.path.dirname(path) or ".")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        problems = cw_polls.check(tmp, races=races, today=today)
        if problems:
            return problems
        os.replace(tmp, path)
        tmp = None
        return []
    finally:
        if tmp and os.path.exists(tmp):
            os.remove(tmp)


def _parse_map(text):
    options = {}
    for part in text.split(","):
        if "=" not in part:
            raise SystemExit(f"--map wants Name=Key pairs separated by commas, but got '{part.strip()}'.")
        name, key = part.rsplit("=", 1)
        options[name.strip()] = key.strip()
    return options


def main(argv=None):
    ap = argparse.ArgumentParser(description="Read a Wikipedia page's opinion-poll tables into a polls.csv.")
    ap.add_argument("title", help="Wikipedia page title, e.g. '2026 United States Senate election in Georgia'")
    ap.add_argument("--race", required=True, help="the pack's race id, e.g. GA-SEN")
    ap.add_argument("--map", required=True, help="candidate heading to option key, e.g. 'Ossoff=D,Collins=R'")
    ap.add_argument("--out", required=True, help="the polls.csv to append to (made if missing)")
    ap.add_argument("--under", help="only tables below a heading matching this text, e.g. 'General election'")
    ap.add_argument("--allow-extra", action="store_true", help="read tables that also list other candidates")
    ap.add_argument("--default-year", type=int, help="year for dates that carry none")
    ap.add_argument("--from-file", help="read this saved wikitext file instead of fetching")
    ap.add_argument("--today", help="latest date a poll may have (default: today)")
    a = ap.parse_args(argv)
    options = _parse_map(a.map)
    try:
        if a.from_file:
            with open(a.from_file, encoding="utf-8") as f:
                text = f.read()
        else:
            text = fetch(a.title)
    except (FetchError, OSError) as e:
        print(e)
        return 2
    rows = parse(text, a.race, options, default_year=a.default_year, today=a.today,
                 allow_extra=a.allow_extra, under=a.under)
    for n in rows.notes:
        print(n)
    if not rows:
        return 1
    problems = write_csv(rows, a.out, today=a.today)
    if problems:
        print(f"Nothing written to {a.out}. The result would not pass the polls-file check:")
        for p in problems:
            print("  " + p)
        return 1
    polls = len({(r["pollster"], r["start"], r["end"], r["population"]) for r in rows})
    print(f"Wrote {polls} polls ({len(rows)} rows) to {a.out}.")
    credit_file = os.path.join(os.path.dirname(os.path.abspath(a.out)), "polls_credit.txt")
    line = credit(a.title)
    try:
        existing = open(credit_file, encoding="utf-8").read() if os.path.exists(credit_file) else ""
        if line not in existing:
            with open(credit_file, "a", encoding="utf-8") as f:
                f.write(line + "\n")
    except OSError:
        pass
    print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())

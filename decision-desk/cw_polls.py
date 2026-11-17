"""The polls file and the polling average (no Discord, no network, no outside libraries).

The file. A pack keeps `polls.csv` beside its other County Watch files: one row per poll per option.
Columns: race (the pack's race id), pollster, start and end (ISO dates), n (people asked, may be blank),
population (lv = likely voters, rv = registered voters, a = adults, or blank), sponsor (blank, or who paid for
the poll), option (the race's option key: D, R, or a candidate key like A) and share (percent).
`check()` lists what is wrong with a file, one plain sentence per bad row. `load()` refuses a bad file with all
those reasons, and never hands back half of it.

The average. `average(polls, race, as_of)` blends the polls for one race that ended on or before `as_of`:

  - A poll counts for half as much for every HALF_LIFE days since the middle of its fieldwork.
  - Bigger polls count more, by the square root of their size, but a size above N_CAP counts as N_CAP, so one
    huge poll cannot rule. A blank size counts as BLANK_N. The cap is deliberately low: how many people were
    asked is a weaker sign of how accurate a poll is than the textbook says, so size should not count for much.
  - When a pollster ran the same survey on more than one kind of voter, only the best kind is kept:
    likely voters, then registered voters, then adults.
  - One pollster counts mostly once: its newest poll in full, each older one at SAME_POLLSTER of its weight.
  - A poll paid for by a party or campaign is, by default (SPONSORED = "shift"), kept at SPONSOR_WEIGHT of its
    weight and moved SPONSOR_SHIFT points against its sponsor before averaging: in a race with D and R the
    margin moves that far toward the other side; for a candidate who is not a party, that candidate's share
    goes down by that many points. "drop" throws such polls away and "keep" leaves them as they are. To know
    whose side a sponsor is on, the sponsor must be an option key (D, R, A...), a party name (Democratic,
    Republican, GOP...), or be listed in the `sponsors` argument ({"Smith for Senate": "A"}). A sponsor we
    cannot place is dropped, since we cannot tell which way to move it. The result says how many were shifted
    and how many dropped.
  - Off by default (STALE_SHIFT = 0): an old poll can be moved by part of how far the national mood has moved
    since it was taken. Give `national`, a dict of date to the national margin (D minus R, as in the generic
    ballot); a poll's margin then moves by STALE_SHIFT times (the mood at `as_of` minus the mood at the middle
    of the poll's dates), the mood on a day being the latest value on or before it. Only polls with just D and
    R are moved, and nothing moves when the series does not reach back to the poll.

Each option gets its own average share. Whatever is left to 100 is reported as `undecided`; it is not handed
out here. An option a poll did not list is simply left out of that option's average. If the race has D and R,
the result also has their margin (D minus R).

The result also says how many polls it effectively rests on: (sum of weights) squared over the sum of the
squared weights. Ten equal polls give 10; one poll with nearly all the weight gives about 1.

The order of the rows never matters.
"""
from __future__ import annotations

import csv
import datetime as dt
import math

HALF_LIFE = 14        # days until a poll counts for half as much
N_CAP = 1500          # a poll of more people than this counts as this many
BLANK_N = 600         # what a poll with no size counts as
SAME_POLLSTER = 0.25  # each older poll from one pollster keeps this share of its weight
SPONSORED = "shift"   # "shift", "drop" or "keep"
SPONSOR_WEIGHT = 2 / 3  # a sponsored poll keeps this share of its weight when shifted
SPONSOR_SHIFT = 4.0   # points a sponsored poll is moved against its sponsor
STALE_SHIFT = 0.0     # share of the national mood's movement since the poll applied to it (0 = off)

HP = {"HALF_LIFE": HALF_LIFE, "N_CAP": N_CAP, "BLANK_N": BLANK_N, "SAME_POLLSTER": SAME_POLLSTER,
      "SPONSORED": SPONSORED, "SPONSOR_WEIGHT": SPONSOR_WEIGHT, "SPONSOR_SHIFT": SPONSOR_SHIFT,
      "STALE_SHIFT": STALE_SHIFT}

COLUMNS = ("race", "pollster", "start", "end", "n", "population", "sponsor", "option", "share")
POPULATIONS = ("lv", "rv", "a", "")
# Which kind of voter to trust when a pollster asked more than one kind. A blank is the least known.
POP_RANK = {"lv": 3, "rv": 2, "a": 1, "": 0}


class PollFileError(ValueError):
    """The polls file has problems. `.problems` lists them in words."""

    def __init__(self, problems):
        self.problems = list(problems)
        super().__init__("polls file refused:\n" + "\n".join(self.problems))


def _date(text):
    if isinstance(text, dt.datetime):
        return text.date()
    if isinstance(text, dt.date):
        return text
    return dt.date.fromisoformat(str(text).strip())


def _read(path, races, today):
    """Read the file into poll records and a list of problems. Records are only trustworthy when no problems."""
    today = _date(today) if today else dt.date.today()
    problems = []
    polls = {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        missing = [c for c in COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            return [], [f"The file has no column called {', '.join(missing)}."]
        for i, row in enumerate(reader, start=2):
            where = f"Line {i}"
            if not any((v or "").strip() for v in row.values()):
                continue
            bad = []
            race = (row["race"] or "").strip()
            pollster = (row["pollster"] or "").strip()
            option = (row["option"] or "").strip()
            pop = (row["population"] or "").strip().lower()
            sponsor = (row["sponsor"] or "").strip()
            if not race:
                bad.append("has no race")
            if not pollster:
                bad.append("has no pollster")
            if not option:
                bad.append("has no option")
            if pop not in POPULATIONS:
                bad.append(f"has population '{pop}' (use lv, rv, a or leave blank)")
            if races is not None and race and race not in races:
                bad.append(f"is for race '{race}', which this pack does not have")
            elif isinstance(races, dict) and race in races and option and option not in list(races[race]):
                bad.append(f"has option '{option}', which race {race} does not have")
            dates = {}
            for key in ("start", "end"):
                try:
                    dates[key] = _date(row[key])
                except (ValueError, TypeError):
                    bad.append(f"has {key} date '{row[key]}', which is not a date like 2026-10-31")
            if len(dates) == 2:
                if dates["end"] < dates["start"]:
                    bad.append("ends before it starts")
                if dates["end"] > today or dates["start"] > today:
                    bad.append(f"has a date after today ({today.isoformat()})")
            n = None
            if (row["n"] or "").strip():
                try:
                    n = float(row["n"])
                    if not math.isfinite(n) or n <= 0:
                        raise ValueError
                except ValueError:
                    bad.append(f"has sample size '{row['n']}', which is not a number above zero")
                    n = None
            share = None
            try:
                share = float(row["share"])
                if not math.isfinite(share) or share < 0:
                    raise ValueError
            except (ValueError, TypeError):
                bad.append(f"has share '{row['share']}', which is not a percent of 0 or more")
            if share is not None and share > 100:
                bad.append(f"has a share of {share:g}, over 100")
            if bad:
                problems.append(f"{where}: this row " + "; it ".join(bad) + ".")
                continue
            key = (race, pollster, dates["start"], dates["end"], pop)
            p = polls.get(key)
            if p is None:
                polls[key] = {"race": race, "pollster": pollster, "start": dates["start"], "end": dates["end"],
                              "n": n, "population": pop, "sponsor": sponsor, "shares": {option: share},
                              "line": i}
                continue
            if option in p["shares"]:
                problems.append(f"{where}: the same poll ({pollster}, {race}, {p['start']} to {p['end']}) "
                                f"is listed twice for option {option}.")
            else:
                if (p["n"], p["sponsor"]) != (n, sponsor):
                    problems.append(f"{where}: this row disagrees with the first row of the same poll "
                                    f"(line {p['line']}) about its size or sponsor.")
                p["shares"][option] = share
    for p in polls.values():
        total = sum(p["shares"].values())
        if total > 100.0001:
            problems.append(f"Line {p['line']}: the shares in the {p['pollster']} poll of {p['race']} "
                            f"({p['start']} to {p['end']}) add up to {total:g}, over 100.")
    problems.sort(key=lambda s: int(s.split(":")[0].split()[1]) if s.startswith("Line ") else 0)
    records = sorted(polls.values(), key=lambda p: (p["race"], p["end"], p["pollster"], p["start"], p["population"]))
    for p in records:
        p.pop("line")
    return records, problems


def check(path, races=None, today=None):
    """Problems in the file, in plain words, one per bad row. An empty list means it is fine.

    `races` may be a dict of race id -> option keys (checks both), or a list of race ids (checks races only).
    `today` is the latest date a poll may have; it defaults to the real today."""
    if races is not None and not isinstance(races, dict):
        races = set(races)
    return _read(path, races, today)[1]


def load(path, races=None, today=None):
    """The polls in the file, one record per poll: race, pollster, start, end (dates), n, population, sponsor and
    shares ({option: percent}). Raises PollFileError, listing every problem, if the file is not clean."""
    records, problems = _read(path, set(races) if races is not None and not isinstance(races, dict) else races, today)
    if problems:
        raise PollFileError(problems)
    return records


PARTY_WORDS = {"d": "D", "dem": "D", "dems": "D", "democrat": "D", "democrats": "D", "democratic": "D",
               "r": "R", "rep": "R", "reps": "R", "republican": "R", "republicans": "R", "gop": "R"}


def _sponsor_option(poll, sponsors):
    """The option key whose side paid for this poll, or None when we cannot tell."""
    text = poll["sponsor"].strip()
    if sponsors and text in sponsors:
        return sponsors[text] if sponsors[text] in poll["shares"] else None
    for key in poll["shares"]:
        if key.lower() == text.lower():
            return key
    words = text.lower().split()
    guess = PARTY_WORDS.get(text.lower()) or (PARTY_WORDS.get(words[0]) if words else None)
    return guess if guess in poll["shares"] else None


def _shifted_against(poll, option, points):
    """A copy of the poll moved `points` against `option`'s side."""
    shares = dict(poll["shares"])
    if option in ("D", "R") and "D" in shares and "R" in shares:
        # the margin (D minus R) moves `points`: D and R each take half
        away = points / 2 if option == "R" else -points / 2
        shares["D"] = max(0.0, shares["D"] + away)
        shares["R"] = max(0.0, shares["R"] - away)
    else:
        shares[option] = max(0.0, shares[option] - points)
    return dict(poll, shares=shares)


def _mood(series, day):
    """The national margin on a day: the latest value on or before it, or None if the series starts later."""
    best = None
    for d, v in series.items():
        d = _date(d)
        if d <= day and (best is None or d > best[0]):
            best = (d, float(v))
    return best[1] if best else None


def _stale_moved(poll, series, as_of, fraction):
    """A copy of a two-sided poll moved by `fraction` of the national mood's change since the poll, else None."""
    if set(poll["shares"]) != {"D", "R"}:
        return None
    mid = dt.date.fromordinal((poll["start"].toordinal() + poll["end"].toordinal()) // 2)
    then, now = _mood(series, mid), _mood(series, as_of)
    if then is None or now is None:
        return None
    move = fraction * (now - then)
    shares = dict(poll["shares"])
    shares["D"] = max(0.0, shares["D"] + move / 2)
    shares["R"] = max(0.0, shares["R"] - move / 2)
    return dict(poll, shares=shares)


def _weights(polls, as_of, hp):
    """(poll, weight) for each poll that counts, after every rule except the sponsor rule."""
    # Several versions of one survey (same pollster, same dates): keep the best kind of voter.
    best = {}
    for p in polls:
        k = (p["pollster"], p["start"], p["end"])
        best[k] = max(best.get(k, -1), POP_RANK.get(p["population"], 0))
    polls = [p for p in polls if POP_RANK.get(p["population"], 0) == best[(p["pollster"], p["start"], p["end"])]]
    # A pollster's newest poll counts in full; the order below is fixed so the file's row order never matters.
    def newest_first(p):
        return (p["end"], p["start"], sorted(p["shares"].items()), p["n"] or 0)
    out = []
    by_pollster = {}
    for p in polls:
        by_pollster.setdefault(p["pollster"], []).append(p)
    for group in by_pollster.values():
        group.sort(key=newest_first, reverse=True)
        for rank, p in enumerate(group):
            mid = p["start"].toordinal() + (p["end"].toordinal() - p["start"].toordinal()) / 2
            age = max(0.0, as_of.toordinal() - mid)
            w = 0.5 ** (age / hp["HALF_LIFE"])
            w *= p.get("_w", 1.0)
            w *= math.sqrt(min(p["n"] if p["n"] else hp["BLANK_N"], hp["N_CAP"]))
            if rank:
                w *= hp["SAME_POLLSTER"]
            out.append((p, w))
    return out


def average(polls, race, as_of, hp=None, national=None, sponsors=None):
    """The blended result for one race as of a date. A dict with:

      shares            {option: average percent}; empty when no poll counts
      margin            D minus R, or None when the race lacks either
      undecided         100 minus the shares added up (None when no poll counts)
      effective_polls   how many equal polls the weights amount to (0 when none)
      newest_age        days from the newest poll's end to `as_of` (None when none)
      polls_used        how many polls counted
      sponsored_shifted how many sponsored polls were kept at lower weight and moved
      sponsored_dropped how many polls the sponsor rule set aside
      stale_moved       how many polls the national-mood switch moved

    `national` is an optional {date: national margin} series and `sponsors` an optional
    {sponsor name: option key}; see the top of this file.
    """
    h = dict(HP)
    h.update(hp or {})
    if h["SPONSORED"] not in ("shift", "drop", "keep"):
        raise ValueError("SPONSORED must be 'shift', 'drop' or 'keep'")
    as_of = _date(as_of)
    mine = [p for p in polls if p["race"] == race and p["end"] <= as_of]
    dropped = shifted = 0
    if h["SPONSORED"] != "keep":
        kept = []
        for p in mine:
            side = _sponsor_option(p, sponsors) if p["sponsor"] else None
            if not p["sponsor"]:
                kept.append(p)
            elif h["SPONSORED"] == "shift" and side:
                q = _shifted_against(p, side, h["SPONSOR_SHIFT"])
                q["_w"] = h["SPONSOR_WEIGHT"]
                kept.append(q)
                shifted += 1
            else:
                dropped += 1
        mine = kept
    moved = 0
    if national and h["STALE_SHIFT"]:
        out = []
        for p in mine:
            q = _stale_moved(p, national, as_of, h["STALE_SHIFT"])
            moved += q is not None
            out.append(q or p)
        mine = out
    weighted = [(p, w) for p, w in _weights(mine, as_of, h) if w > 0]
    result = {"shares": {}, "margin": None, "undecided": None, "effective_polls": 0.0, "newest_age": None,
              "polls_used": len(weighted), "sponsored_shifted": shifted,
              "sponsored_dropped": dropped, "stale_moved": moved}
    if not weighted:
        return result
    total = sum(w for _, w in weighted)
    result["effective_polls"] = total * total / sum(w * w for _, w in weighted)
    result["newest_age"] = as_of.toordinal() - max(p["end"] for p, _ in weighted).toordinal()
    options = sorted({o for p, _ in weighted for o in p["shares"]})
    for o in options:
        have = [(p["shares"][o], w) for p, w in weighted if o in p["shares"]]
        result["shares"][o] = sum(s * w for s, w in have) / sum(w for _, w in have)
    result["undecided"] = max(0.0, 100.0 - sum(result["shares"].values()))
    if "D" in result["shares"] and "R" in result["shares"]:
        result["margin"] = result["shares"]["D"] - result["shares"]["R"]
    return result

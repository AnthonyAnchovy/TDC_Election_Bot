"""Ratings for the active data pack: which races are competitive, which are safe, and who is favored.

The pack's "ratings" block holds them:
  rated         [[race, rating, holder]] for every seat a rater has as anything but Safe. 2026's is the average of
                Cook, Inside Elections and Sabato's Crystal Ball compiled by Adam Carlson (@admcrlsn) on Oct 23, 2026.
  battleground  the bonus seats in the prediction game, same shape
  safe          {race: option} pre-filled in the picker so people only click the real contests
  early_watch_until  "19:30": rated seats in states whose polls close by then are the early-warning seats
The house body's "ratings_baseline" is the seat count a party reaches if every seat it's favored in goes its way.

A rating ends in the option it favors ("Lean D", "Likely R"); anything else is a toss-up. Edit the pack and restart
the bot after changes.
"""
import night as _N
import packs

_EARLY_RATINGS = ("Likely D", "Lean D", "Tilt D", "Toss-up", "Tilt R", "Lean R")

RATINGS: list = []          # (race, rating, holder)
BATTLEGROUND: list = []
RATING_OF: dict = {}
HOLDER_OF: dict = {}
RATING: dict = {}
HOLDER: dict = {}
SAFE: dict = {}
EARLY_WATCH: list = []
D_BASELINE = 0


def favored(rid: str) -> str | None:
    r = RATING_OF.get(rid, "")
    return "D" if r.endswith(" D") else "R" if r.endswith(" R") else None


@packs.on_activate
def _fill(pack: packs.Pack):
    global RATINGS, BATTLEGROUND, RATING_OF, HOLDER_OF, RATING, HOLDER, SAFE, EARLY_WATCH, D_BASELINE
    rat = pack.ratings
    RATINGS = [tuple(x) for x in rat.get("rated", [])]
    BATTLEGROUND = [tuple(x) for x in rat.get("battleground", [])]
    RATING_OF = {rid: rating for rid, rating, _ in RATINGS}
    HOLDER_OF = {rid: holder for rid, _, holder in RATINGS}
    RATING = {rid: rating for rid, rating, _ in BATTLEGROUND}
    HOLDER = {rid: holder for rid, _, holder in BATTLEGROUND}
    SAFE = dict(rat.get("safe") or {})
    D_BASELINE = (pack.bodies.get("house", {}).get("ratings_baseline") or {}).get("D", 0)
    EARLY_WATCH = []
    until = rat.get("early_watch_until")
    if until:
        _N._fill(pack)          # closings have to be this pack's before they're read
        h, m = map(int, until.split(":"))
        cutoff = _N.ELECTION_DAY.replace(hour=h, minute=m)
        EARLY_WATCH = sorted(
            (rid for rid, rating, _ in RATINGS
             if rating in _EARLY_RATINGS and rid in pack.races and _N.first_close(pack.races[rid]["state"]) <= cutoff),
            key=lambda r: (_N.first_close(pack.races[r]["state"]), _EARLY_RATINGS.index(RATING_OF[r]), r))

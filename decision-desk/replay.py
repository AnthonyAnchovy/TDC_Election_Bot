"""Feed a recorded election night back through the desk's rules, at its own timestamps (no Discord here).

    python replay.py                      replay 2026 and compare with what the bot did on the night
    python replay.py --until 2026-11-03T22:55-06:00    stop there and show where the desk stood

A night is a fixture folder made by tools/build_night_fixture.py:
  night.fixture.json      every vote, override, retraction and outlet call in order, the roster, the predictions,
                          and what the bot did with them (`expected`)
  field.fixture.json.gz   the Senate field's snapshots: projected margin and votes counted, about once a minute
  desk.fixture.sqlite     the night's database itself, people as numbers (fixture_db() hands out a copy)

replay() runs the events through desk.py on a fresh Store. FieldHistory answers "what did the model say about this
race at this moment", which is what a feature that shows margins beside a call needs to be tested against.
"""
from __future__ import annotations

import argparse
import bisect
import gzip
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import clock
import desk as DESK
import scoring as S
from store import Store

HERE = Path(__file__).parent
NIGHT_2026 = HERE / "tests" / "fixtures" / "2026"


def fixture_db(into: Path, folder: Path = NIGHT_2026) -> Path:
    """A working copy of a night's database, in `into`. The fixture itself is never opened for writing."""
    import shutil
    copy = Path(into) / "desk.db"
    shutil.copyfile(Path(folder) / "desk.fixture.sqlite", copy)
    return copy


def load_countywatch(folder: Path = NIGHT_2026) -> dict:
    """The county counts held that night: {state: {fips: {"D", "R", "O", "pct_in", "complete"}}}."""
    data = json.loads((Path(folder) / "countywatch.fixture.json").read_text(encoding="utf-8"))
    return {k: v for k, v in data.items() if not k.startswith("_")}


def load_night(folder: Path = NIGHT_2026) -> dict:
    return json.loads((Path(folder) / "night.fixture.json").read_text(encoding="utf-8"))


# ------------------------------------------------------------------ the desk
@dataclass
class Replay:
    """Where a replay ended up. log has one entry per event: the event, and what the rules did with it."""
    store: Store
    roster: DESK.Roster
    log: list[tuple[dict, object]] = field(default_factory=list)

    def history(self) -> list[dict]:
        """Every official call and retraction in order, in the same shape as a fixture's expected history."""
        rows = self.store.db.execute("SELECT action, race_id, party, detail, ts FROM history ORDER BY id").fetchall()
        return [{"action": a, "race": rid, "party": p, "how": json.loads(d or "{}").get("how"),
                 "outlet": json.loads(d or "{}").get("outlet"), "t": ts} for a, rid, p, d, ts in rows]

    def standing_votes(self) -> list[list]:
        return sorted([u, rid, kind, party] for u, rid, kind, party in self.store.db.execute(
            "SELECT user_id, race_id, kind, party FROM desk_votes WHERE active = 1"))

    def procalls(self) -> list[list]:
        return sorted([p["race_id"], p["outlet"], p["party"]] for p in self.store.procalls())

    def safe_seats_queued(self) -> int:
        return sum(1 for _e, out in self.log if out == "queued")

    def results(self) -> dict[str, str]:
        """What the leaderboard scores against, with the same rule the bot uses: a call from before a race's polls
        closed doesn't count."""
        full = self.store.all_full()
        return S.truth({rid: c["party"] for rid, c in full.items()}, self.store.procalls(),
                       called_at={rid: c["called_at"] for rid, c in full.items()})


def apply(store: Store, roster: DESK.Roster, e: dict):
    """One recorded event through the same rule the bot's command would have used."""
    do, rid = e["do"], e["race"]
    if do == "vote":
        return DESK.cast_call_vote(store, roster, e["user"], rid, e["party"])
    if do == "unvote":
        return store.withdraw(e["user"], rid)
    if do == "settle":
        return DESK.settle(store, roster, rid)
    if do == "uncallvote":
        v = DESK.cast_uncall_vote(store, roster, e["user"], rid)
        if v.reached:
            DESK.retract(store, rid, v.how)
        return v
    if do == "realcall":
        return DESK.realcall(store, e["user"], f"user{e['user']}", rid, e["party"])
    if do == "forceuncall":
        return DESK.retract(store, rid, f"Retracted by user{e['user']}")
    if do == "outlet":
        if e["via"] == "auto":
            step = DESK.auto_call(store, rid, e["party"], e["outlet"])
            return DESK.outlet_call(store, rid, e["party"], e["outlet"], 0) if step == "post" else step
        if e["via"] == "callnyt":
            return DESK.follow_outlet(store, rid, e["party"], e["outlet"], e["user"])
        return DESK.outlet_call(store, rid, e["party"], e["outlet"], e["user"])
    raise ValueError(f"unknown event {do!r}")


def replay(night: dict, until: float | None = None, store: Store | None = None) -> Replay:
    """Run a night's events, in order, up to `until` (Unix seconds; the whole night if None)."""
    r = night["roster"]
    out = Replay(store or Store(":memory:"),
                 DESK.Roster(frozenset(r["junkies"]), frozenset(r["counted"]), r.get("threshold", "majority")))
    for e in night["events"]:
        if until is not None and e["t"] > until:
            break
        if e["do"] == "roster":     # the desk changed: who counts, or how many votes a call needs
            out.roster = DESK.Roster(frozenset(e.get("junkies", out.roster.junkies)),
                                     frozenset(e.get("counted", out.roster.counted)),
                                     e.get("threshold", out.roster.threshold))
            continue
        with clock.at(e["t"]):
            out.log.append((e, apply(out.store, out.roster, e)))
    return out


def scores(night: dict, results: dict[str, str], points: dict | None = None) -> list[dict]:
    """The prediction leaderboard for a night's entries against `results`."""
    entries = {int(n): (p["picks"], {"house_dem_seats": p["house_dem_seats"]}) for n, p in night["predictions"].items()}
    return S.leaderboard(entries, results, points, None)


def differences(night: dict, got: Replay, slack: float = 5.0, most: int | None = 20) -> list[str]:
    """Where a full replay departs from what the bot did on the night. Empty means it reproduced it. A call may be
    stamped up to `slack` seconds from the night's: the bot of 2026 recorded a majority call after replying to the
    voter, which took a moment, and desk.py records it at once."""
    want, out = night["expected"], []
    mine = got.history()
    if len(mine) != len(want["history"]):
        out.append(f"{len(mine)} calls and retractions, the night had {len(want['history'])}")
    for i, (a, b) in enumerate(zip(mine, want["history"])):
        same = all(a[k] == b[k] for k in ("action", "race", "party", "how", "outlet"))
        if not same or abs(a["t"] - b["t"]) > slack:
            out.append(f"#{i}: replay {a} / night {b}")
            if most is not None and len(out) > most:
                return out + ["(more not shown)"]
    if got.store.all() != want["calls"]:
        out.append("the final official calls differ")
    if got.standing_votes() != sorted(want["standing_votes"]):
        out.append("the standing votes differ")
    if got.procalls() != sorted(want["procalls"]):
        out.append("the outlet calls differ")
    return out


# ------------------------------------------------------------------ the field
class FieldHistory:
    """The Senate field through the night. Each snapshot's races are {rid: [margin, votes, state, called, side]}:
    margin is the model's projection in points (D side positive), votes is the count so far, and state is "wait"
    (nothing counted), "count" (counting, no county finished) or "proj" (projected from finished counties)."""

    def __init__(self, snaps: list[dict]):
        self.snaps = sorted(snaps, key=lambda s: s["ts"])
        self._ts = [s["ts"] for s in self.snaps]

    @classmethod
    def load(cls, folder: Path = NIGHT_2026, kind: str = "senate") -> "FieldHistory":
        with gzip.open(Path(folder) / "field.fixture.json.gz", "rt", encoding="utf-8") as f:
            return cls(json.load(f).get(kind, []))

    def at(self, ts: float) -> dict | None:
        """The latest snapshot taken at or before ts, or None if the field hadn't started."""
        i = bisect.bisect_right(self._ts, ts) - 1
        return self.snaps[i] if i >= 0 else None

    def race(self, rid: str, ts: float) -> dict | None:
        """What the field knew about one race at ts, and how old that was."""
        snap = self.at(ts)
        if not snap or rid not in snap["races"]:
            return None
        m, votes, state, called, _side = snap["races"][rid]
        return {"margin": m, "votes": votes, "state": state, "called": called, "age": ts - snap["ts"]}

    def series(self, rid: str) -> list[tuple[float, float, int, str]]:
        """(time, margin, votes, state) for one race at every snapshot."""
        return [(s["ts"], *s["races"][rid][:3]) for s in self.snaps if rid in s["races"]]


# ------------------------------------------------------------------ command line
def main():
    ap = argparse.ArgumentParser(description="Replay a recorded election night through the desk's rules.")
    ap.add_argument("--night", type=Path, default=NIGHT_2026, help="fixture folder (default: 2026)")
    ap.add_argument("--until", help="stop at this time, e.g. 2026-11-03T22:55-06:00")
    a = ap.parse_args()
    night = load_night(a.night)
    until = datetime.fromisoformat(a.until).timestamp() if a.until else None
    got = replay(night, until)
    calls = got.store.all()
    print(f"{len(got.log)} events replayed · {len(calls)} races called · "
          f"{sum(1 for h in got.history() if h['action'] == 'uncall')} retractions · "
          f"{got.safe_seats_queued()} Safe seats sent to roll-ups")
    for kind in ("senate", "house"):
        print(S.path_text(kind, calls).replace("**", ""))
    for row in scores(night, got.results()):
        print(f"  user{row['user_id']}: {row['points']} pts, {row['flips']} flips")
    if until is None:
        diff = differences(night, got)
        print("Matches the night exactly." if not diff else "Differs from the night:\n  " + "\n  ".join(diff))
        raise SystemExit(1 if diff else 0)


if __name__ == "__main__":
    main()

"""The desk's rules: what a vote, an override, a retraction or an outlet's call does to the record (no Discord here).

bot.py checks who is allowed, calls one of these, and words the posts from what comes back. replay.py calls the same
functions with a recorded night, so the rules can be tested without Discord. Every function takes the Store it works
on, and none of them awaits, so two commands arriving together can't both make the same race official.
"""
from __future__ import annotations

from dataclasses import dataclass

import clock
import competitive as CMP
import races as R

# /procall from one of these makes a House seat official. Lower case, without dots, as people actually type them.
HOUSE_CALLERS = {"ap", "the ap", "associated press", "decision desk hq", "ddhq", "nyt", "new york times",
                 "the new york times", "dave wasserman", "wasserman"}


@dataclass(frozen=True)
class Roster:
    """Who is on the desk right now. `junkies` hold the role, and their number sets how many votes a call needs.
    `counted` is everyone whose vote counts: the role holders, plus admins without the role."""
    junkies: frozenset
    counted: frozenset
    threshold: str | int = "majority"

    def need(self) -> int:
        n = max(len(self.junkies), 1)
        return n // 2 + 1 if self.threshold == "majority" else max(1, min(int(self.threshold), n))


# ------------------------------------------------------------------ official calls
def claim_official(store, rid: str, party: str, how: str, outlet: str | None = None) -> bool:
    """Record the official call unless the race is already called. `outlet` marks a call that follows an outlet's,
    so /scoreboard doesn't time the desk against it."""
    if store.get(rid):
        return False
    store.call(rid, party, {"how": how, "outlet": outlet} if outlet else {"how": how})
    return True


def retract(store, rid: str, how: str) -> dict | None:
    """Take an official call back. Returns the call that was retracted, or None if there wasn't one."""
    prev = store.get(rid)
    if prev:
        store.uncall(rid, {"how": how})
    return prev


def is_house_caller(outlet: str) -> bool:
    return outlet.strip().lower().replace(".", "") in HOUSE_CALLERS


def is_safe_seat(rid: str) -> bool:
    """A House seat no rater has as competitive: anything that isn't Likely, Lean, Tilt or Toss-up."""
    return R.RACES[rid]["type"] == "house" and rid not in CMP.RATING_OF


def queue_safe_seat(store, rid: str, party: str):
    """Saved, not held in memory, so a restart can't lose a seat that hasn't been announced yet."""
    store.set_setting("safe_batch", store.setting("safe_batch", []) + [[rid, party, clock.time()]])


def rival_house_caller(store, rid: str, party: str) -> str | None:
    return next((p["outlet"] for p in store.procalls(rid) if p["party"] != party and is_house_caller(p["outlet"])), None)


# ------------------------------------------------------------------ votes
@dataclass
class Vote:
    """What a call vote did. status: "already_called" (official holds the call), "duplicate" (they'd already voted
    this way) or "recorded". For a recorded vote: prev is the vote it replaced, support the votes now standing for
    this winner, and reached says this vote made the call official (recorded under `how`)."""
    status: str
    official: dict | None = None
    prev: dict | None = None
    support: int = 0
    need: int = 0
    how: str = ""
    reached: bool = False


def cast_call_vote(store, roster: Roster, user_id: int, rid: str, party: str) -> Vote:
    official = store.get(rid)
    if official:
        return Vote("already_called", official=official)
    prev = store.active_vote(user_id, rid, "call")
    if prev and prev["party"] == party:
        return Vote("duplicate", prev=prev)
    store.cast(user_id, rid, "call", party)
    support = sum(1 for u, p in store.active_votes(rid, "call").items() if p == party and u in roster.counted)
    need = roster.need()
    how = f"Called by {support} of {len(roster.junkies)} Election Junkies"
    reached = support >= need and claim_official(store, rid, party, how)
    return Vote("recorded", prev=prev, support=support, need=need, how=how, reached=reached)


def settle(store, roster: Roster, rid: str) -> Vote | None:
    """Make an uncalled race official if the votes already standing are a majority, without a new vote. That can
    happen when the roster changes under standing votes (someone's vote starts to count, or fewer are needed).
    bot.py has no command for this yet; in 2026 it was done once by hand, and replay.py needs it to follow that."""
    if store.get(rid):
        return None
    need = roster.need()
    tally: dict[str, int] = {}
    for u, p in store.active_votes(rid, "call").items():
        if u in roster.counted:
            tally[p] = tally.get(p, 0) + 1
    for party, support in tally.items():
        how = f"Called by {support} of {len(roster.junkies)} Election Junkies"
        if support >= need and claim_official(store, rid, party, how):
            return Vote("recorded", support=support, need=need, how=how, reached=True)
    return None


@dataclass
class RetractVote:
    """What a vote to retract did. status: "not_called", "duplicate" or "recorded". When reached is True the caller
    retracts the call with retract(store, rid, how)."""
    status: str
    official: dict | None = None
    support: int = 0
    need: int = 0
    how: str = ""
    reached: bool = False


def cast_uncall_vote(store, roster: Roster, user_id: int, rid: str) -> RetractVote:
    official = store.get(rid)
    if not official:
        return RetractVote("not_called")
    if store.active_vote(user_id, rid, "uncall"):
        return RetractVote("duplicate", official=official)
    store.cast(user_id, rid, "uncall", official["party"])
    support = sum(1 for u in store.active_votes(rid, "uncall") if u in roster.counted)
    need = roster.need()
    return RetractVote("recorded", official=official, support=support, need=need, reached=support >= need,
                       how=f"Retracted by {support} of {len(roster.junkies)} Election Junkies")


def realcall(store, user_id: int, name: str, rid: str, party: str) -> str | None:
    """Make a call official without a majority. Returns how it's recorded, or None if the race was already called.
    The override counts as this person's call vote too, so the scoreboard credits them."""
    how = f"Called by {name} (override)"
    if not claim_official(store, rid, party, how):
        return None
    mine = store.active_vote(user_id, rid, "call")
    if not mine or mine["party"] != party:
        store.cast(user_id, rid, "call", party)
    return how


# ------------------------------------------------------------------ outlet calls
@dataclass
class OutletCall:
    """What logging an outlet's call did. official is the desk's call as it stood before; rival is a trusted outlet
    that called a House seat the other way; projected says this call made the seat official (recorded under `how`)."""
    official: dict | None
    rival: str | None
    projected: bool
    how: str


def outlet_call(store, rid: str, party: str, outlet: str, user_id: int) -> OutletCall:
    """Log an outlet's call. From a trusted outlet, with no trusted outlet disagreeing, it also makes a House seat
    official."""
    store.procall(rid, outlet, party, user_id)
    official = store.get(rid)
    house_caller = R.RACES[rid]["type"] == "house" and is_house_caller(outlet)
    rival = rival_house_caller(store, rid, party) if house_caller else None
    how = f"Projected by {outlet}"
    projected = bool(house_caller and not rival and claim_official(store, rid, party, how, outlet=outlet))
    return OutletCall(official, rival, projected, how)


def auto_call(store, rid: str, party: str, outlet: str) -> str:
    """A House call read from an outlet's feed. Returns what the caller should do next:
      "duplicate"  already logged from this outlet: nothing
      "post"       a rated seat, or a call that disagrees with another: log and post it with outlet_call()
      "queued"     a Safe seat, now official: it joins the next roll-up
      "logged"     a Safe seat the desk had already called this way: logged, no post
    Hundreds of Safe seats can come in right after polls close, and a post for each would bury the seats people are
    watching."""
    if any(p["outlet"] == outlet and p["party"] == party for p in store.procalls(rid)):
        return "duplicate"
    official = store.get(rid)
    if not is_safe_seat(rid) or rival_house_caller(store, rid, party) or (official and official["party"] != party):
        return "post"
    store.procall(rid, outlet, party, 0)
    if claim_official(store, rid, party, f"Projected by {outlet}", outlet=outlet):
        queue_safe_seat(store, rid, party)
        return "queued"
    return "logged"


def follow_outlet(store, rid: str, party: str, outlet: str, user_id: int) -> str | None:
    """Log an outlet's call and make the race official on the strength of it, whatever kind of race it is (/callnyt
    for Senate and governor). Returns how it's recorded, or None if the race was already called."""
    how = f"Projected by {outlet}"
    store.procall(rid, outlet, party, user_id)
    return how if claim_official(store, rid, party, how, outlet=outlet) else None

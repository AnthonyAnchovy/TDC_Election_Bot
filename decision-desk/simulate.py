"""Play a recorded election night through the running bot, into a real Discord server.

The replay harness (replay.py) checks the rules without Discord. This goes the rest of the way: every vote,
/realcall, retraction and outlet call from a night's fixture goes through the same handlers a slash command reaches,
so the cards, maps, flavor lines, roll-ups, alerts and field posts really land in the server's channels, in order.

Start the bot with DESK_SIMULATE set to a fixture folder (tests/fixtures/2026) and it begins once connected:

    DESK_SIMULATE=tests/fixtures/2026 python bot.py

  DESK_SIM_SPEED     how much faster than the night to run (default 240: an hour passes in 15 seconds)
  DESK_SIM_MAX_GAP   the longest real wait between two events, in seconds (default 2)

It only runs when records are kept apart with DESK_DATA, and it sets aside whatever records that server already
had (desk.before-sim.db). What it changes while it runs, and why:

  the clock      the bot's time is each event's own time, so alerts, leads and roll-ups fall where they did
  the desk       who votes and how many votes a call needs come from the fixture, not from the server's roles.
                 The night's people aren't in the server, so they post as user1, user2...
  the field      Senate field snapshots come from the fixture's history; live county feeds aren't pulled, since
                 today they hold final results. For the same reason a vote has no count beside it
  automatic calls  outlets aren't read; their calls are played from the record at the moment they came in

Events run one after another. The night's real pile-ups (several people pressing at once) aren't reproduced.
Every event, the bot's private reply to it and how long it took is written to simulation.jsonl beside the records.
"""
from __future__ import annotations

import asyncio
import gzip
import json
import logging
import os
import shutil
import time as real_time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import clock
import desk as DESK
import field as F
import night as N
import packs
import replay as RP

log = logging.getLogger("desk.sim")
SOURCE = "Played back from the night's record."


class _Time:
    """Stands in for the `time` module inside bot.py: time() is the simulated moment, everything else is real."""

    def time(self) -> float:
        return clock.time()

    def __getattr__(self, name):
        return getattr(real_time, name)


class _Reply:
    """Catches what a command would have said privately to the person who ran it."""

    def __init__(self):
        self.said: list[str] = []

    async def send_message(self, content=None, **_kw):
        self.said.append(str(content or ""))

    async def defer(self, **_kw):
        pass

    def is_done(self) -> bool:
        return bool(self.said)


def person(bot, n: int):
    """One of the night's people, as the handlers see a server member. They can vote, and act as admins, as the
    people who ran those commands on the night could."""
    return SimpleNamespace(id=n, display_name=f"user{n}", bot=False, mention=f"user{n}",
                           roles=[SimpleNamespace(name=bot.CFG["junkie_role"])],
                           guild_permissions=SimpleNamespace(manage_guild=True))


def pressed(bot, guild, n: int):
    return SimpleNamespace(guild=guild, guild_id=guild.id, user=person(bot, n), channel=None, message=None,
                           response=_Reply(), command=None, namespace=SimpleNamespace())


def load(folder: Path) -> tuple[dict, list[dict]]:
    night = RP.load_night(folder)
    try:
        snaps = json.loads(gzip.open(folder / "field.fixture.json.gz").read()).get("senate", [])
    except OSError:
        snaps = []
    return night, sorted(snaps, key=lambda s: s["ts"])


def set_aside(db: Path) -> Path | None:
    """Move a server's existing records out of the way, so the night starts from nothing. Returns where they went."""
    if not db.exists():
        return None
    kept = db.with_name("desk.before-sim.db")
    shutil.move(str(db), str(kept))
    for extra in (db.with_name(db.name + "-journal"), db.with_name(db.name + "-wal"), db.with_name(db.name + "-shm")):
        extra.unlink(missing_ok=True)
    return kept


class Night:
    """One run. `bot` is the bot module; nothing here imports it, so tests can hand in a stand-in server."""

    def __init__(self, bot, guild, folder: Path, speed: float = 240.0, max_gap: float = 2.0, out: Path | None = None):
        self.bot, self.guild, self.folder = bot, guild, Path(folder)
        self.speed, self.max_gap = max(speed, 1.0), max_gap
        self.night, self.snaps = load(self.folder)
        r = self.night["roster"]
        self.roster = DESK.Roster(frozenset(r["junkies"]), frozenset(r["counted"]), r.get("threshold", "majority"))
        self.people = {int(u) for u in self.night["predictions"]} | set(r["counted"]) | \
                      {e["user"] for e in self.night["events"] if e.get("user")}
        self.out = out
        self.done = self.failed = 0
        self.kept: dict = {}

    # -------------------------------------------------------------- the bot, on the night's clock and desk
    def take_over(self):
        bot = self.bot
        self.kept = {k: getattr(bot, k) for k in ("time", "now", "cw_night", "roster", "name_of", "numbers_line")}
        real_name_of = bot.name_of
        bot.time = _Time()
        bot.now = lambda: datetime.fromtimestamp(clock.time(), N.ET)
        bot.cw_night = lambda: False        # no live county pulls, and the field comes from the fixture instead
        bot.roster = lambda _guild: self.roster

        async def no_numbers(_rid, wait: float = 0):     # today's feeds hold final counts, not the night's
            return None
        bot.numbers_line = no_numbers
        bot.name_of = lambda guild, uid: f"user{uid}" if uid in self.people else real_name_of(guild, uid)

    def hand_back(self):
        for k, v in self.kept.items():
            setattr(self.bot, k, v)

    def note(self, **row):
        if self.out:
            with open(self.out, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, default=str) + "\n")

    # -------------------------------------------------------------- one recorded event
    async def play(self, e: dict) -> list[str]:
        bot, guild, store = self.bot, self.guild, self.bot.store
        do, rid = e["do"], e.get("race")
        if do == "roster":
            self.roster = DESK.Roster(frozenset(e.get("junkies", self.roster.junkies)),
                                      frozenset(e.get("counted", self.roster.counted)),
                                      e.get("threshold", self.roster.threshold))
            return []
        who = pressed(bot, guild, e.get("user") or 0)
        if do == "vote":
            await bot.cast_call_vote(who, rid, e["party"])
        elif do == "unvote":
            await bot.unvote_cmd.callback(who, rid)
        elif do == "uncallvote":
            await bot.uncall_cmd.callback(who, rid)
        elif do == "forceuncall":
            await bot.forceuncall_cmd.callback(who, rid)
        elif do == "realcall":
            await bot.override_call(who, rid, e["party"])
        elif do == "settle":                    # done by hand on the night: standing votes had become a majority
            vote = DESK.settle(store, self.roster, rid)
            if vote:
                await bot.make_official(guild, rid, store.get(rid)["party"], vote.how)
        elif do == "outlet" and e["via"] == "auto":
            await bot.take_auto_call(guild, rid, e["party"], e["outlet"], SOURCE)
        elif do == "outlet" and e["via"] == "callnyt":
            how = DESK.follow_outlet(store, rid, e["party"], e["outlet"], e["user"])
            if how:
                await bot.make_official(guild, rid, e["party"], how, None, outlet=e["outlet"])
        elif do == "outlet":
            await bot.log_outlet_call(guild, rid, e["party"], e["outlet"], e["user"])
        else:
            raise ValueError(f"unknown event {do!r}")
        return who.response.said

    async def field(self, snap: dict):
        """A Senate field snapshot from the night, with this run's own calls on it."""
        store = self.bot.store
        snap = {**snap, "races": {rid: [*v[:3], (store.get(rid) or {}).get("party"), *v[4:]]
                                  for rid, v in snap["races"].items()}}

        async def given(_kind):
            return snap
        await self.bot.field_pass(self.guild, ["senate"], keep=True, snapshot=given)

    # -------------------------------------------------------------- the whole night
    async def run(self) -> dict:
        bot, guild, events = self.bot, self.guild, self.night["events"]
        started = real_time.time()
        self.take_over()
        try:
            clock.set(min(events[0]["t"], self.snaps[0]["ts"] if self.snaps else events[0]["t"]) - 60)
            for uid, p in self.night["predictions"].items():
                with clock.at(p.get("saved_at") or clock.time()):
                    bot.store.save_predictions(int(uid), p["picks"], p.get("house_dem_seats"))
            first, last = (datetime.fromtimestamp(events[i]["t"], N.ET) for i in (0, -1))
            await bot.post(guild, "board_channel", content=(
                f"🧪 **Simulation: {packs.active().name}, played back from the record.** {len(events)} events from "
                f"{first:%b %d, %I:%M %p} to {last:%b %d, %I:%M %p} ET, about {self.speed:.0f} times faster than they "
                f"happened. The night's people post as user1 to user{max(self.people)}. Nothing here is live."))
            fi, alerts_at, prev = 0, 0.0, events[0]["t"]
            for i, e in enumerate(events):
                while fi < len(self.snaps) and self.snaps[fi]["ts"] <= e["t"]:
                    clock.set(self.snaps[fi]["ts"])
                    await self.safely("field", self.field(self.snaps[fi]))
                    fi += 1
                await asyncio.sleep(min(max(e["t"] - prev, 0) / self.speed, self.max_gap))
                prev = e["t"]
                clock.set(e["t"])
                if e["t"] - alerts_at >= 60:        # poll-closing alerts and reminders, on the night's clock
                    alerts_at = e["t"]
                    await self.safely("alerts", bot.alert_pass(guild))
                t0 = real_time.time()
                said = await self.safely(e, self.play(e))
                await self.safely("roll-up", bot.flush_safe_seats(guild))
                self.note(i=i, event=e, replied=said, seconds=round(real_time.time() - t0, 3))
                self.done += 1
                if self.done % 100 == 0:
                    log.info("Simulation: %d of %d events, at %s", self.done, len(events),
                             datetime.fromtimestamp(e["t"], N.ET).strftime("%b %d %I:%M %p ET"))
            while fi < len(self.snaps):
                clock.set(max(self.snaps[fi]["ts"], clock.time()))
                await self.safely("field", self.field(self.snaps[fi]))
                fi += 1
            clock.set(events[-1]["t"] + 6 * 60)     # past the last roll-up's wait
            await self.safely("roll-up", bot.flush_safe_seats(guild))
            result = self.result(real_time.time() - started)
            await bot.post(guild, "board_channel", content=result["text"])
            return result
        finally:
            # The clock stays where the night ended, so /scoreboard, /leaderboard and /trend answer about the night.
            self.hand_back_except_clock()

    def hand_back_except_clock(self):
        keep_now, keep_time = self.bot.now, self.bot.time
        self.hand_back()
        self.bot.now, self.bot.time = keep_now, keep_time

    async def safely(self, what, work):
        """One event failing is written down and the night goes on."""
        try:
            return await work
        except Exception as exc:
            self.failed += 1
            log.exception("Simulation: %s failed", what)
            self.note(failed=what, error=f"{type(exc).__name__}: {exc}"[:400])
            return None

    def result(self, took: float) -> dict:
        got = RP.Replay(self.bot.store, self.roster)
        wrong = RP.differences(self.night, got)
        own = getattr(self.bot, "BOT_OUTLET", None)     # the desk's own bot didn't exist on the night it's replaying
        mine = [p for p in got.procalls() if p[1] != own]
        if "the outlet calls differ" in wrong and mine == sorted(map(list, self.night["expected"]["procalls"])):
            wrong.remove("the outlet calls differ")
        bot_calls = len(got.procalls()) - len(mine)
        calls = len(got.history())
        text = (f"🧪 **Simulation finished** in {took / 60:.0f} minutes: {self.done} events, {calls} calls and "
                f"retractions, {self.failed} step{'s' * (self.failed != 1)} failed. "
                + ("The desk's record matches the night exactly." if not wrong else
                   f"The desk's record departs from the night in {len(wrong)} place{'s' * (len(wrong) != 1)}; "
                   "they're in the bot's log.")
                + (f" The desk's own bot made {bot_calls} calls of its own." if bot_calls else "")
                + " The clock is held where the night ended, so `/scoreboard`, `/leaderboard`, `/predictions` and "
                  "`/trend` answer about it.")
        for line in wrong:
            log.warning("Simulation differs: %s", line)
        log.info("Simulation finished: %d events, %d calls and retractions, %d failed, %d differences, %.0f s",
                 self.done, calls, self.failed, len(wrong), took)
        return {"events": self.done, "calls": calls, "failed": self.failed, "differences": wrong, "text": text,
                "bot_calls": bot_calls}


def refusal(env=None) -> str | None:
    """Why a simulation won't start, or None."""
    env = os.environ if env is None else env
    if not env.get("DESK_DATA"):
        return ("DESK_SIMULATE is set but DESK_DATA isn't. A simulation fills a server's records with a whole night, "
                "so it only runs on records kept apart: set DESK_DATA=data-test.")
    folder = Path(env.get("DESK_SIMULATE", ""))
    if not (folder / "night.fixture.json").exists():
        return f"DESK_SIMULATE={folder}: there is no night.fixture.json in that folder."
    return None


async def start(bot, guild) -> dict | None:
    """Called once the bot is connected, when DESK_SIMULATE is set."""
    why = refusal()
    if why:
        log.error(why)
        return None
    if guild is None or not bot.find_channel(guild, "board_channel") or not bot.find_channel(guild, "desk_channel"):
        log.error("The simulation needs the home server (GUILD_ID) with both channels in it.")
        return None
    db = packs.db_path(packs.active().id, guild.id)
    bot.TEN.forget()
    kept = set_aside(db)
    if kept:
        log.info("Simulation: this server's earlier records are set aside in %s", kept)
    F.clear_history()
    out = db.with_name("simulation.jsonl")
    out.unlink(missing_ok=True)
    night = Night(bot, guild, Path(os.environ["DESK_SIMULATE"]), float(os.getenv("DESK_SIM_SPEED", "240")),
                  float(os.getenv("DESK_SIM_MAX_GAP", "2")), out)
    log.info("Simulation starting: %d events, %d field snapshots", len(night.night["events"]), len(night.snaps))
    with bot.TEN.scope(guild.id):
        return await night.run()

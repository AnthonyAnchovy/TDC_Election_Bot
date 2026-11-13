"""Decision Desk bot.

Before election night: /predict opens a clickable picker (Senate, House majority, optional
governors and battleground House seats) and /leaderboard scores everyone once results come in.

On election night: /call is one person's vote to call a race. Each vote is announced in
#decision-desk-board. When a majority of the Election Junkie role agrees, the bot posts the
official call (card + map) in #decision-desk. /realcall makes a call official without a
majority. /procall logs an outlet's call (AP, NYT, Wasserman...) for the scoreboard, and a call from AP,
Decision Desk HQ, NYT or Wasserman makes a House seat official. DDHQ's and Wasserman's calls are also read
from Bluesky automatically, and NYT's from its results data (/autocalls). Safe House seats post in text roll-ups
every few minutes; only rated seats get a card.
"""
from __future__ import annotations

import asyncio
import functools
import json
import logging
import os
import random
import re
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import tasks
from dotenv import load_dotenv

import autocaller as AUTO
import bluesky as BSKY
import capture
import competitive as CMP
import countywatch_run as CWR
import cw_early as CE
import cw_feeds as CF
import cw_model as CM
import desk as DESK
import ddhq_grab as DG
import desk_help as H
import drinking as DR
import feeds as FEEDS
import field as F
import flavor as FL
import graphics as G
import night as N
import nyt_house as NYT
import races as R
import remaining as REM
import results as LIVE
import packs
import picks as P
import scoring as S
import tenancy as TEN
import trend as TR

HERE = Path(__file__).parent
COUNTY_CARD = HERE / "assets" / "county_watch_card.png"
load_dotenv(HERE / ".env")
CFG = json.loads((HERE / "config.json").read_text(encoding="utf-8"))
TZ = ZoneInfo(CFG.get("timezone", "America/Chicago"))
log = logging.getLogger("desk")
NO_PINGS = discord.AllowedMentions.none()

# Whether a server reads outlets' calls before anyone has said so with /autocalls. DESK_AUTOCALLS=off in .env starts
# every server quiet, which is what a test server wants: nothing is called by itself until an admin turns it on.
AUTOCALLS_DEFAULT = os.getenv("DESK_AUTOCALLS", "on").strip().lower() not in ("off", "0", "false", "no")

intents = discord.Intents.default()
intents.members = True  # needed to know who holds the Election Junkie role
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)
# `store` is the records of whichever server the work in hand is for: the server a command came from, each server
# in turn in the background passes, and the home server (GUILD_ID) otherwise. See tenancy.py.
store = TEN.store


CW_COMMANDS = {"watchstate", "resultsdm", "countyfile", "counties", "needs", "environment", "earlyvote", "countyset",
               "countyclear", "countytest", "countyfeed", "countyrefresh", "excellink", "trend"}


async def _enter_server(interaction: discord.Interaction) -> bool:
    TEN.enter(interaction.guild_id)
    name = interaction.command.name if interaction.command else ""
    if name in CW_COMMANDS and not FEEDS.countywatch() and interaction.type == discord.InteractionType.application_command:
        await interaction.response.send_message(
            f"**{packs.active().name}** has no County Watch, so `/{name}` has nothing to read. It needs county "
            "baselines and feeds in the data pack (docs/data-packs.md).", ephemeral=True)
        return False
    return True


tree.interaction_check = _enter_server      # every slash command starts in its own server's records


class ScopedView(discord.ui.View):
    """A view whose buttons work on the records of the server they were pressed in."""

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await _enter_server(interaction)


async def each_server(work):
    """Run a background pass once for every server the bot is in, on that server's records. One server's failure
    doesn't stop the next."""
    for guild in list(client.guilds):
        with TEN.scope(guild.id):
            try:
                await work(guild)
            except Exception:
                log.exception("%s failed in %s", getattr(work, "__name__", "A pass"), guild.name)


async def post_everywhere(key: str, **kwargs):
    """Post the same thing to that channel in every server: what a shared feed found, which is news to all of
    them. A server without the channel is skipped, and one server's failure doesn't stop the next."""
    async def one(guild):
        if find_channel(guild, key):
            await post(guild, key, **kwargs)
    await each_server(one)

PARTY_CHOICES = [
    app_commands.Choice(name="Democrat", value="D"),
    app_commands.Choice(name="Republican", value="R"),
    app_commands.Choice(name="Independent", value="I"),
]
class Pick:
    """What someone typed for a winner, and the option of the race it names (None if it names none of them)."""

    def __init__(self, value: str | None, raw: str):
        self.value, self.raw, self.name = value, raw, raw


class WinnerOf(app_commands.Transformer):
    """The `winner` of a command: one of the options of the race typed before it. Discord offers that race's
    candidates by name; a party letter, a party name or a surname typed by hand works too."""

    @staticmethod
    def _race(interaction: discord.Interaction) -> str | None:
        return resolve(str(getattr(interaction.namespace, "race", "") or ""))

    async def transform(self, interaction: discord.Interaction, value: str) -> Pick:
        rid = self._race(interaction)
        return Pick(P.resolve_option(rid, value, load_candidates()) if rid else None, value)

    async def autocomplete(self, interaction: discord.Interaction, current: str):
        rid = self._race(interaction)
        if not rid:
            return [app_commands.Choice(name="Pick the race first", value=current or "?")]
        typed, out = current.strip().lower(), []
        for key in options_for(rid):
            name = cand(rid, key)
            text = f"{name} ({key})" if key in packs.active().options else name
            if not typed or typed in text.lower() or typed == key.lower():
                out.append(app_commands.Choice(name=text[:100], value=key))
        return out[:25]


Winner = app_commands.Transform[Pick, WinnerOf]

OUTLETS = ["AP", "Decision Desk HQ", "NYT", "CNN", "NBC", "ABC", "CBS", "Fox News", "Dave Wasserman"]
EMBED_COLOR = {"D": 0x2B6CD9, "R": 0xD63A3A, "I": 0x9A5CD6, None: 0x5A5F6B}
DEFAULT_NAME = {"D": "the Democrat", "R": "the Republican", "I": "the independent"}


# =========================================================================== basics
def now() -> datetime:
    """Current time. Set DESK_FAKE_NOW=2026-11-03T19:05-05:00 in .env to rehearse."""
    fake = os.getenv("DESK_FAKE_NOW")
    return datetime.fromisoformat(fake) if fake else datetime.now(N.ET)


def clock(ts: float | datetime) -> str:
    dt = ts if isinstance(ts, datetime) else datetime.fromtimestamp(ts, TZ)
    return dt.astimezone(TZ).strftime("%I:%M %p").lstrip("0")


def load_candidates() -> dict:
    p = packs.active().path("candidates.json")
    if not p.exists():
        return {}
    data = json.loads(p.read_text(encoding="utf-8"))
    return {k.upper(): {p: n for p, n in v.items() if n} for k, v in data.items() if not k.startswith("_")}


def cand(rid: str, party: str) -> str:
    """Who an option is in a race: the name its pack gives it, else the nominee on file, else 'the Democrat'."""
    return P.name_of(rid, party, load_candidates())


def options_for(rid: str) -> list[str]:
    """Who can win a race: the options its pack lists, or D and R plus an independent where one is on file."""
    return P.options_for(rid, load_candidates())


def flavor(kind: str, **fmt) -> str | None:
    """A line for a moment that isn't a call ("desk_ahead", "desk_wrong"...), from flavor.json by way of flavor.py,
    which doesn't hand out the same line twice until the rest have had a turn."""
    return FL.line(store, kind, **fmt)


def race_names(rid: str) -> dict[str, str]:
    """Who is in a race, by option: what a flavor line needs to name the winner and the loser."""
    return {key: cand(rid, key) for key in options_for(rid)}


def resolve(race: str) -> str | None:
    """The race someone means: its ID, a loose form of it ("tx 15"), or words that fit exactly one race
    ("georgia senate", "ohio gov")."""
    if race in R.RACES:
        return race
    found = R.normalize(race)
    if not found and race.strip():
        hits = R.search(race, 2)
        found = hits[0] if len(hits) == 1 else None
    return found


def label(rid: str) -> str:
    return R.RACES[rid]["label"]


def race_phrase(rid: str) -> str:
    return f"control of the {R.chamber_name(rid)}" if R.RACES[rid]["type"] == "control" else f"the {label(rid)} race"


def name_of(guild, uid: int) -> str:
    m = guild.get_member(uid) if guild else None
    return m.display_name if m else f"user {uid}"


def results() -> dict[str, str]:
    """What each race is scored against. A call made before the race's polls closed doesn't count, unless this is a
    rehearsal on a fake clock, where every call is one of those."""
    full = store.all_full()
    return S.truth({rid: c["party"] for rid, c in full.items()}, store.procalls(),
                   CFG.get("score_against", "pros_then_desk"),
                   called_at={rid: c["called_at"] for rid, c in full.items()},
                   finals=store.setting("final_results", {}), gate=not os.getenv("DESK_FAKE_NOW"))


# =========================================================================== roles & channels
def _roles(member) -> set[str]:
    return {r.name for r in getattr(member, "roles", [])}


def is_junkie(member) -> bool:
    return CFG["junkie_role"] in _roles(member) or is_admin(member)


def is_admin(member) -> bool:
    perms = getattr(member, "guild_permissions", None)
    given = getattr(member, "resolved_permissions", None)     # what Discord itself says, which counts the server owner
    return bool(perms and perms.manage_guild) or bool(given and given.manage_guild) or CFG.get("admin_role", "") in _roles(member)


def wipe_refusal(interaction: discord.Interaction, confirm: str, word: str) -> str | None:
    """Why a command that erases data won't run, or None. Capitals and stray spaces in the word don't matter."""
    if not is_admin(interaction.user):
        return "Nothing was changed. Admins only: you need the Manage Server permission."
    if confirm.strip().upper() != word:
        return f"Nothing was changed. To confirm, type {word} in the `confirm` box. It received: {confirm[:40]!r}"
    return None


def junkie_ids(guild) -> set[int]:
    return {m.id for m in guild.members if not m.bot and CFG["junkie_role"] in _roles(m)}


def counted_voters(guild) -> set[int]:
    """Everyone whose vote counts: the same people is_junkie lets vote, so admins without the role too. The number of
    votes needed still comes from the role alone (votes_needed)."""
    return {m.id for m in guild.members if not m.bot and is_junkie(m)}


def roster(guild) -> DESK.Roster:
    """Who is on the desk right now, in the form desk.py's rules take."""
    return DESK.Roster(frozenset(junkie_ids(guild)), frozenset(counted_voters(guild)), CFG.get("threshold", "majority"))


def votes_needed(guild) -> int:
    return roster(guild).need()


def find_channel(guild, key: str):
    val = str(CFG.get(key, "")).lstrip("#")
    if not val or guild is None:
        return None
    if val.isdigit():
        return guild.get_channel(int(val))
    return discord.utils.get(guild.text_channels, name=val)


def main_guild():
    gid = os.getenv("GUILD_ID")
    if gid:
        return client.get_guild(int(gid))
    return client.guilds[0] if client.guilds else None


async def post(guild, key: str, fallback=None, **kwargs):
    """Send to the board/desk channel; fall back to where the command was used."""
    ch = find_channel(guild, key) or fallback
    if ch is None:
        log.warning("No channel for %s", key)
        return None
    kwargs.setdefault("allowed_mentions", NO_PINGS)
    files = [kwargs["file"]] if kwargs.get("file") else list(kwargs.get("files") or [])
    capture.event("post", channel=key, content=kwargs.get("content"), files=[f.filename for f in files],
                  embeds=[e.title for e in ([kwargs["embed"]] if kwargs.get("embed") else kwargs.get("embeds") or [])])
    try:
        return await ch.send(**kwargs)
    except discord.HTTPException as exc:        # Discord turned it down or kept failing: keep a record of what was lost
        capture.event("error", where=f"post to {key}", error=f"{type(exc).__name__}: {exc}"[:300],
                      content=(kwargs.get("content") or "")[:200])
        raise


def channel_note(guild, key: str) -> str:
    ch = find_channel(guild, key)
    return ch.mention if ch else f"this channel (I couldn't find #{CFG.get(key)})"


# =========================================================================== official calls
async def map_file(kind: str, calls: dict | None = None, highlight: str | None = None) -> discord.File:
    """The chamber map: the dark tile map for Senate and governors and the House chart, in every card theme. Calls are
    read here, and the drawing runs in a worker thread."""
    calls = dict(store.all() if calls is None else calls)
    if kind in ("house", "control"):
        render = functools.partial(G.house_chart, calls)
    else:
        render = functools.partial(G.tile_map, kind, calls)
    buf = await asyncio.to_thread(render)
    return discord.File(buf, filename=f"{kind}_map.png")


THEME_CHOICES = [app_commands.Choice(name="Wire (halftone poster)", value="wire"),
                 app_commands.Choice(name="Editorial (results page)", value="editorial"),
                 app_commands.Choice(name="Classic (glass card)", value="classic")]


def card_theme() -> str:
    """The call card look: what an admin picked with /cardtheme, else config.json's card_theme."""
    theme = store.setting("card_theme") or CFG.get("card_theme", "wire")
    return theme if theme in G.CARD_THEMES else "wire"


async def card_file(rid: str, party: str | None, how: str, *, retracted: bool = False,
                    theme: str | None = None) -> discord.File:
    """The call card, with the chamber's seat counts as they stand right now. Counts are read here (SQLite stays
    on this thread); the drawing runs in a worker thread so a slow render never stalls the bot."""
    counts = S.chamber_counts(R.chamber(rid), store.all())
    buf = await asyncio.to_thread(
        G.call_card, rid, party, candidate=load_candidates().get(rid, {}).get(party) if party else None, tally=how,
        when=now(), retracted=retracted, counts=counts, theme=theme or card_theme(),
        use_custom=not theme and store.setting("card_art", True))
    return discord.File(buf, filename=f"{rid}_{'retracted' if retracted else party}.{G.image_ext(buf.getvalue())}")


def house_line() -> str:
    c = S.chamber_counts("house", store.all())
    return (f"House seats called: **D {c['D']}** · **R {c['R']}**" + (f" · I {c['I']}" if c["I"] else "")
            + f" · {c['uncalled']} uncalled · {R.HOUSE_MAJORITY} for majority")


async def follow_up(guild, rid: str, fallback):
    kind = R.RACES[rid]["type"]
    calls = store.all()
    if kind == "house":
        extra = f"\n{S.house_tracker_text(results())}" if rid in CMP.RATING_OF else ""
        await post(guild, "desk_channel", fallback, content=f"{house_line()}\n{S.path_text('house', calls)}{extra}")
    elif rid == "US-SENATE":
        await post(guild, "desk_channel", fallback, content=S.path_text("senate", calls),
                   file=await map_file("senate", calls))
    elif kind == "control":
        # the wire and editorial cards already show the House (seat strip / hemicycle), so only classic gets the chart
        if card_theme() == "classic":
            await post(guild, "desk_channel", fallback, content=S.path_text("house", calls),
                       file=await map_file("house", calls))
        else:
            await post(guild, "desk_channel", fallback, content=f"{house_line()}\n{S.path_text('house', calls)}")
    elif CFG.get("post_map_after_call", True):
        await post(guild, "desk_channel", fallback, content=S.path_text(kind, calls),
                   file=await map_file(kind, calls, highlight=rid))
    await nudge_control(guild, rid, fallback)


async def nudge_control(guild, rid: str, fallback):
    """Once the desk's own calls give a party the Senate or the House, say so on the board, once, with a button to
    vote on control. In 2026 nobody could call the Senate at all, and the House vote came from someone noticing."""
    control = {"senate": "US-SENATE", "house": "US-HOUSE"}.get(R.chamber(rid))
    if not control or R.RACES[rid]["type"] == "control" or store.get(control):
        return
    party = S.clinched(R.chamber(control), store.all())
    key = f"clinch-{control}-{party}"
    if not party or store.alert_sent(key):
        return
    store.mark_alert(key)
    name = R.chamber_name(control)
    await post(guild, "board_channel", fallback,
               content=f"📣 **The {name} math is settled.** The desk's calls give {cand(control, party)} "
                       f"{S.EMOJI[party]} a majority. Vote to call control of the {name} with the button, "
                       f"or `/call {name.upper()}`.",
               view=ScopedView(timeout=None).add_item(AlsoCall(control, party)))


def prediction_check(guild, rid: str, winner: str) -> tuple[list[str], list[str]]:
    """Names of people whose saved prediction for this race was right / wrong."""
    right, wrong = [], []
    for uid in store.predictors():
        picks = store.user_predictions(uid)[0]
        pick = S.senate_control_pick(picks) if rid == "US-SENATE" else picks.get(rid)   # nobody picks that one directly
        if pick is None:
            continue
        (right if pick == winner else wrong).append(name_of(guild, uid))
    return sorted(right, key=str.lower), sorted(wrong, key=str.lower)


def prediction_check_text(guild, rid: str, winner: str) -> str | None:
    right, wrong = prediction_check(guild, rid, winner)
    if not right and not wrong:
        return None
    parts = [f"🎯 **Called it ({len(right)}):** {', '.join(right) if right else 'nobody'}",
             f"❌ **Missed ({len(wrong)}):** {', '.join(wrong) if wrong else 'nobody'}"]
    return "\n".join(parts)


# The rules themselves live in desk.py, where they can be replayed and tested without Discord. There's no await in
# any of them, so when two /call, /realcall or /procall handlers arrive at the same moment, only the first one wins.
def claim_official(rid: str, party: str, how: str, outlet: str | None = None) -> bool:
    return DESK.claim_official(store, rid, party, how, outlet)


is_house_caller = DESK.is_house_caller
is_safe_seat = DESK.is_safe_seat


def queue_safe_seat(rid: str, party: str):
    DESK.queue_safe_seat(store, rid, party)


def official_note(guild, rid: str) -> str:
    where = channel_note(guild, "desk_channel")
    return f"It goes in the next safe seat roll-up in {where}." if is_safe_seat(rid) else f"Call posted in {where}."


async def make_official(guild, rid: str, party: str, how: str, fallback=None, outlet: str | None = None):
    """Post the card and map for a call already recorded with claim_official. A Safe House seat only joins the next
    roll-up, so the seats people are watching don't get buried."""
    if is_safe_seat(rid):
        return queue_safe_seat(rid, party)
    who = cand(rid, party)
    card = await card_file(rid, party, how)
    lines = ["🚨 **DECISION DESK PROJECTION**",
             f"**{who[0].upper() + who[1:]}** {S.EMOJI[party]} {'win' if R.RACES[rid]['type'] == 'control' else 'wins'} "
             f"{race_phrase(rid)} **{S.tag(rid, party)}**",
             FL.call_line(store, rid, party, race_names(rid))]
    note = S.upset_note(rid, party)
    if note:
        lines.append(note)
    pros = [p for p in store.procalls(rid)]
    agree = [p for p in pros if p["party"] == party]
    if outlet:
        lines.append(f"-# Projected by {outlet}.")
    elif agree:
        first = agree[0]
        lines.append(flavor("desk_behind", outlet=first["outlet"],
                            lead=S.minutes_text((time.time() - first["ts"]) / 60)))
    elif pros:
        lines.append(f"⚠️ {pros[0]['outlet']} called this for {R.PARTY_NAMES[pros[0]['party']]}.")
    if DR.is_on(store):
        said = FL.context(rid, party, race_names(rid))
        beaten = agree[0]["outlet"] if agree and not outlet else None
        for event in DR.events_for_call(FL.situation(rid, party), None, beaten):
            lines.append(DR.line(store, event, outlet=beaten, winner=said["winner"], seat=said["seat"], place=said["place"]))
    if CFG.get("post_prediction_check", True):
        check = prediction_check_text(guild, rid, party)
        if check:
            lines.append(f"\n**Prediction check**\n{check}")
    await post(guild, "desk_channel", fallback, content="\n".join(x for x in lines if x), file=card)
    await follow_up(guild, rid, fallback)


async def retract_official(guild, rid: str, how: str, fallback=None):
    prev = DESK.retract(store, rid, how)
    if not prev:  # a simultaneous retraction already went through
        return
    text = f"↩️ **CALL RETRACTED:** {label(rid)}\n{FL.retract_line(store, rid) or ''}".strip()
    drink = DR.line(store, "retract", seat=label(rid))
    if drink:
        text += f"\n{drink}"
    extra = {} if is_safe_seat(rid) else {"file": await card_file(rid, prev["party"], how, retracted=True)}
    await post(guild, "desk_channel", fallback, content=text, **extra)
    await follow_up(guild, rid, fallback)


# =========================================================================== autocomplete
async def race_autocomplete(_interaction: discord.Interaction, current: str):
    calls, names = store.all(), load_candidates()
    out = []
    for rid in R.search(current):
        who = names.get(rid, {})
        vs = " v ".join(f"{_surname(who[p])} ({p})" for p in ("D", "R", "I") if who.get(p))
        vs = f" · {vs}" if vs and R.RACES[rid]["type"] != "control" else ""      # the names tell MS from MO from MT
        extra = f" [called {calls[rid]}]" if rid in calls else ""
        out.append(app_commands.Choice(name=f"{rid} — {R.RACES[rid]['label']}{vs}{extra}"[:100], value=rid))
    return out


async def outlet_autocomplete(_interaction: discord.Interaction, current: str):
    cur = current.lower()
    opts = [o for o in OUTLETS if cur in o.lower()]
    if current and current not in opts:
        opts.append(current)
    return [app_commands.Choice(name=o, value=o) for o in opts][:25]


# =========================================================================== /call, /uncall, /unvote
@tree.command(name="call", description="Vote to call a race. A majority of Election Junkies makes it official.")
@app_commands.guild_only()
@app_commands.describe(race="e.g. GA-SEN, AZ-GOV, PA-07, HOUSE", winner="Who you think won")
@app_commands.autocomplete(race=race_autocomplete)
async def call_cmd(interaction: discord.Interaction, race: str, winner: Winner):
    rid = resolve(race)
    if not rid:
        return await interaction.response.send_message(f"I don't recognize `{race}`.", ephemeral=True)
    if winner.value not in options_for(rid):
        return await interaction.response.send_message(no_such_winner(rid, winner.raw), ephemeral=True)
    await cast_call_vote(interaction, rid, winner.value)


def no_such_winner(rid: str, typed: str | None) -> str:
    """What to say when someone names a winner the race doesn't have."""
    return (f"`{typed}` isn't someone who can win {label(rid)}. It's between "
            + " and ".join(f"{cand(rid, p)} {S.EMOJI[p]}" for p in options_for(rid)) + ".")


async def _drop_button(interaction: discord.Interaction):
    """Take the Also call button off the post it was clicked on, once it has no more use. A failure here doesn't matter."""
    if interaction.message:
        try:
            await interaction.message.edit(view=None)
        except discord.HTTPException:
            log.info("Couldn't remove a call button from its post")


class AlsoCall(discord.ui.DynamicItem[discord.ui.Button], template=r"alsocall:(?P<rid>[A-Z0-9-]+):(?P<party>[A-Z0-9_]+)"):
    """The button on a call vote's post: one click casts the same vote as /call. Its ID holds the race and winner, so
    buttons on older posts keep working after a restart."""

    def __init__(self, rid: str, party: str):
        self.rid, self.party = rid, party
        style = {"D": discord.ButtonStyle.primary, "R": discord.ButtonStyle.danger}.get(party, discord.ButtonStyle.secondary)
        super().__init__(discord.ui.Button(label=f"Also call for {cand(rid, party)}"[:80], style=style,
                                           custom_id=f"alsocall:{rid}:{party}"))

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match):
        return cls(match["rid"], match["party"])

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await _enter_server(interaction)

    async def callback(self, interaction: discord.Interaction):
        await cast_call_vote(interaction, self.rid, self.party)


async def numbers_line(rid: str, wait: float = 1.5) -> str | None:
    """What the count says about a race right now, to sit beside a vote on it: for a County Watch Senate race the
    count, the model's finish and what the trailing side needs of the vote still out; for a seat in a state with a
    results file, the count. None when the bot has no numbers for the race, or they don't come back within `wait`
    seconds: a vote is never held up for them. A slow pull is left running, so the next vote finds it ready."""
    st, kind = R.RACES[rid]["state"], R.RACES[rid]["type"]
    try:
        if FEEDS.cw_state_of(rid):
            job = asyncio.ensure_future(asyncio.to_thread(CWR.projection, st, CWR.live_cache()))
            p = await asyncio.wait_for(asyncio.shield(job), wait)
            return REM.headline(REM.breakdown(p), *(_surname(n) for n in CWR.names()[st]))
        if st and kind != "control" and LIVE.state_cfg(st):
            job = asyncio.ensure_future(LIVE.race_result(rid))
            race = await asyncio.wait_for(asyncio.shield(job), wait)
            return live_line(race) + (" · ⚠️ not refreshing" if race.get("stale") else "") if race else None
    except asyncio.TimeoutError:
        log.info("No numbers for %s within %.1fs", rid, wait)
    except Exception:
        log.exception("Couldn't get numbers for %s", rid)
    return None


def with_numbers(text: str, numbers: str | None) -> str:
    return f"{text}\n-# 📊 {numbers}" if numbers else text


async def cast_call_vote(interaction: discord.Interaction, rid: str, party: str):
    """One Election Junkie's vote to call a race, from /call or the Also call button."""
    guild, user = interaction.guild, interaction.user
    if not is_junkie(user):
        return await interaction.response.send_message(
            f"You need the **{CFG['junkie_role']}** role to vote on calls.", ephemeral=True)
    vote = DESK.cast_call_vote(store, roster(guild), user.id, rid, party)    # records the vote, and the call on a majority
    if vote.status == "already_called":
        official = vote.official
        msg = (f"{label(rid)} is already called for {cand(rid, official['party'])}."
               if official["party"] == party else
               f"{label(rid)} is already called for {cand(rid, official['party'])}. "
               f"Use `/uncall` if you think the desk got it wrong.")
        await interaction.response.send_message(msg, ephemeral=True)
        return await _drop_button(interaction)
    if vote.status == "duplicate":
        return await interaction.response.send_message(
            "You've already voted for that call. If it's settled and the desk is stuck waiting for votes, "
            "`/forcecall` makes it official right now.", ephemeral=True)
    prev, need, how, reached = vote.prev, vote.need, vote.how, vote.reached
    await interaction.response.send_message(
        f"Vote recorded: {label(rid)} → {cand(rid, party)}. Posted in {channel_note(guild, 'board_channel')}. "
        f"`/unvote` takes it back.", ephemeral=True)

    switched = f" (switched from {S.EMOJI[prev['party']]})" if prev else ""
    text = (f"🗳️ **{user.display_name}** has voted to call {race_phrase(rid)} for "
            f"**{cand(rid, party)}** {S.EMOJI[party]} **{S.tag(rid, party)}**{switched}\n"
            f"-# {min(vote.support, need)} of {need} votes needed for an official call")
    text = with_numbers(text, await numbers_line(rid))      # so a vote is cast on the count, not on the last vote
    if reached:
        text += f"\n✅ That's a majority. {official_note(guild, rid)}"
    elif store.get(rid):  # another vote completed the majority while this one was being recorded
        text += "\n-# The desk had just called it."
    extra = {}
    if not store.get(rid):      # still open, so the next person can join in with one click
        extra["view"] = ScopedView(timeout=None).add_item(AlsoCall(rid, party))
    await post(guild, "board_channel", interaction.channel, content=text, **extra)
    if store.get(rid):
        await _drop_button(interaction)
    if reached:
        await make_official(guild, rid, party, how, interaction.channel)


@tree.command(name="unvote", description="Take back your vote on a race.")
@app_commands.guild_only()
@app_commands.autocomplete(race=race_autocomplete)
async def unvote_cmd(interaction: discord.Interaction, race: str):
    rid = resolve(race)
    if not rid or not store.withdraw(interaction.user.id, rid):
        return await interaction.response.send_message("You don't have a standing vote on that race.",
                                                       ephemeral=True)
    await interaction.response.send_message("Vote withdrawn.", ephemeral=True)
    await post(interaction.guild, "board_channel", interaction.channel,
               content=f"↩️ **{interaction.user.display_name}** withdrew their vote on {label(rid)}.")


@tree.command(name="uncall", description="Vote to retract an official call. Needs a majority.")
@app_commands.guild_only()
@app_commands.autocomplete(race=race_autocomplete)
async def uncall_cmd(interaction: discord.Interaction, race: str):
    guild, user = interaction.guild, interaction.user
    rid = resolve(race)
    if not rid or not store.get(rid):
        return await interaction.response.send_message("That race hasn't been called.", ephemeral=True)
    if not is_junkie(user):
        return await interaction.response.send_message(f"You need the **{CFG['junkie_role']}** role.",
                                                       ephemeral=True)
    vote = DESK.cast_uncall_vote(store, roster(guild), user.id, rid)
    if vote.status == "not_called":     # retracted between the check above and here
        return await interaction.response.send_message("That race hasn't been called.", ephemeral=True)
    if vote.status == "duplicate":
        return await interaction.response.send_message("You've already voted to retract it.", ephemeral=True)
    official = vote.official
    await interaction.response.send_message("Retraction vote recorded.", ephemeral=True)
    text = (f"🧯 **{user.display_name}** has voted to retract the call in {race_phrase(rid)} "
            f"({cand(rid, official['party'])} {S.EMOJI[official['party']]})\n"
            f"-# {vote.support} of {vote.need} votes needed to retract")
    if vote.reached:
        text += "\n↩️ Majority reached. The call is retracted."
    await post(guild, "board_channel", interaction.channel, content=text)
    if vote.reached:
        await retract_official(guild, rid, vote.how, interaction.channel)


@tree.command(name="forceuncall", description="Admin only: retract an official call without a vote.")
@app_commands.guild_only()
@app_commands.autocomplete(race=race_autocomplete)
async def forceuncall_cmd(interaction: discord.Interaction, race: str):
    rid = resolve(race)
    if not is_admin(interaction.user):
        return await interaction.response.send_message("Admins only.", ephemeral=True)
    if not rid or not store.get(rid):
        return await interaction.response.send_message(
            "That race hasn't been called." + (outlet_note(rid) if rid else ""), ephemeral=True)
    await interaction.response.send_message("Retracted." + outlet_note(rid), ephemeral=True)
    await post(interaction.guild, "board_channel", interaction.channel,
               content=f"↩️ **{interaction.user.display_name}** retracted the call in {label(rid)} (admin).")
    await retract_official(interaction.guild, rid, f"Retracted by {interaction.user.display_name}",
                           interaction.channel)


def outlet_note(rid: str) -> str:
    """A retraction leaves outlet calls in place, and those keep scoring the race. Tell the admin."""
    outlets = [p["outlet"] for p in store.procalls(rid)]
    if not outlets:
        return ""
    return (f" An outlet call is still logged here ({', '.join(outlets)}), so the race keeps scoring for predictions. "
            "If that was a test or a mistake, `/clearrace` removes it.")


def cleared_text(gone: dict) -> str:
    return ", ".join(f"{gone[key]} {name}{'s' * (gone[key] != 1)}" for key, name in
                     (("calls", "official call"), ("votes", "vote"), ("outlets", "outlet call")))


@tree.command(name="clearrace", description="Admin: erase one race's call, votes and outlet calls, so it scores nothing.")
@app_commands.guild_only()
@app_commands.describe(race="e.g. GA-SEN, PA-07, HOUSE", confirm="Type CLEAR to confirm")
@app_commands.autocomplete(race=race_autocomplete)
async def clearrace_cmd(interaction: discord.Interaction, race: str, confirm: str):
    refusal = wipe_refusal(interaction, confirm, "CLEAR")
    if refusal:
        return await interaction.response.send_message(refusal, ephemeral=True)
    rid = resolve(race)
    if not rid:
        return await interaction.response.send_message(f"I don't recognize `{race}`.", ephemeral=True)
    gone = store.clear_race(rid)
    if not any(gone.values()):
        return await interaction.response.send_message(f"Nothing to clear in {label(rid)}.", ephemeral=True)
    await interaction.response.send_message(f"Cleared {cleared_text(gone)} from {label(rid)}. It scores nothing now.",
                                            ephemeral=True)
    await post(interaction.guild, "board_channel", interaction.channel,
               content=f"🧹 **{interaction.user.display_name}** cleared everything logged on {label(rid)} (admin).")


# =========================================================================== /realcall and /procall
@tree.command(name="realcall", description="Make a call official right now, without waiting for a majority.")
@app_commands.guild_only()
@app_commands.autocomplete(race=race_autocomplete)
async def realcall_cmd(interaction: discord.Interaction, race: str, winner: Winner):
    await override_call(interaction, race, winner.value or winner.raw)


@tree.command(name="forcecall", description="Make a call official right now, without a vote. The same as /realcall.")
@app_commands.guild_only()
@app_commands.autocomplete(race=race_autocomplete)
async def forcecall_cmd(interaction: discord.Interaction, race: str, winner: Winner):
    await override_call(interaction, race, winner.value or winner.raw)


async def override_call(interaction: discord.Interaction, race: str, party: str):
    """/realcall and /forcecall: any Election Junkie can make a call official without waiting for a majority."""
    guild, user = interaction.guild, interaction.user
    rid = resolve(race)
    if not rid:
        return await interaction.response.send_message(f"I don't recognize `{race}`.", ephemeral=True)
    if not is_junkie(user):
        return await interaction.response.send_message(f"You need the **{CFG['junkie_role']}** role.",
                                                       ephemeral=True)
    if party not in options_for(rid):
        return await interaction.response.send_message(no_such_winner(rid, party), ephemeral=True)
    how = DESK.realcall(store, user.id, user.display_name, rid, party)      # also counts as this person's call vote
    if not how:
        return await interaction.response.send_message(
            f"{label(rid)} is already called. `/uncall` it first if the desk got it wrong.", ephemeral=True)
    await interaction.response.send_message(
        f"Called. {official_note(guild, rid)}", ephemeral=True)
    await post(guild, "board_channel", interaction.channel,
               content=with_numbers(
                   f"⚡ **{user.display_name}** called {race_phrase(rid)} without waiting for a majority: "
                   f"**{cand(rid, party)}** {S.EMOJI[party]} **{S.tag(rid, party)}**", await numbers_line(rid)))
    await make_official(guild, rid, party, how, interaction.channel)


@tree.command(name="procall", description="Log a call made by an outlet or analyst (AP, NYT, Wasserman...).")
@app_commands.guild_only()
@app_commands.describe(outlet="Who made the call")
@app_commands.autocomplete(race=race_autocomplete, outlet=outlet_autocomplete)
async def procall_cmd(interaction: discord.Interaction, race: str, winner: Winner, outlet: str):
    guild, user = interaction.guild, interaction.user
    rid = resolve(race)
    if not rid:
        return await interaction.response.send_message(f"I don't recognize `{race}`.", ephemeral=True)
    if not is_junkie(user):
        return await interaction.response.send_message(f"You need the **{CFG['junkie_role']}** role.",
                                                       ephemeral=True)
    outlet = outlet.strip()[:40]
    if not outlet:
        return await interaction.response.send_message("Who made the call?", ephemeral=True)
    party = winner.value
    if party not in options_for(rid):
        return await interaction.response.send_message(no_such_winner(rid, winner.raw), ephemeral=True)
    if any(p["outlet"].lower() == outlet.lower() for p in store.procalls(rid)):
        await interaction.response.send_message(f"Updated {outlet}'s call.", ephemeral=True)
    else:
        await interaction.response.send_message(
            f"Logged. Posted in {channel_note(guild, 'board_channel')}.", ephemeral=True)
    await log_outlet_call(guild, rid, party, outlet, user.id, interaction.channel)


def rival_house_caller(rid: str, party: str) -> str | None:
    return DESK.rival_house_caller(store, rid, party)


async def log_outlet_call(guild, rid: str, party: str, outlet: str, user_id: int, fallback=None, source: str | None = None):
    """Log an outlet's call and post it to the board. From a trusted outlet, it also makes a House seat official."""
    logged = DESK.outlet_call(store, rid, party, outlet, user_id)
    official, rival, projected = logged.official, logged.rival, logged.projected
    lines = [f"📡 **{outlet}** has called {race_phrase(rid)} for **{cand(rid, party)}** "
             f"{S.EMOJI[party]} **{S.tag(rid, party)}**"]
    if projected:
        lines.append(f"✅ That makes it official. {official_note(guild, rid)}")
    elif official and official["party"] == party and official["detail"].get("outlet"):
        lines.append(f"-# The desk already projected this from {official['detail']['outlet']}'s call.")
    elif official and official["party"] == party:
        lead = (time.time() - official["called_at"]) / 60
        lines.append(flavor("desk_ahead", outlet=outlet, lead=S.minutes_text(lead)) or "The desk was first.")
    elif official:
        lines.append(flavor("desk_wrong") or "The desk called this the other way.")
    elif rival:
        lines.append(f"⚠️ {rival} called this the other way, so the desk is holding off: `/call` to vote, "
                     "or `/realcall` to make it official.")
    else:
        lines.append("-# The desk hasn't called this yet: `/call` to vote, or `/realcall` to make it official.")
    early = [(ts, uid) for uid, r, p, ts in store.all_call_votes() if r == rid and p == party]
    if early:
        ts, uid = min(early)
        lines.append(f"-# First desk vote for this: {name_of(guild, uid)}, "
                     f"{S.minutes_text((time.time() - ts) / 60)} before {outlet}")
    if source:
        lines.append(f"-# {source}")
    await post(guild, "board_channel", fallback, content="\n".join(lines))
    if projected:
        await make_official(guild, rid, party, logged.how, fallback, outlet=outlet)


# =========================================================================== House calls read automatically
_bsky = {"failures": 0, "warned": False, "checked": None, "error": None, "calls": 0, "reviews": 0}
_nyt = {"failures": 0, "warned": False, "checked": None, "error": None, "calls": 0, "reviews": 0}
_ddhq = {"checked": None, "calls": 0, "reviews": 0, "loaded": False}


async def take_auto_call(guild, rid: str, party: str, outlet: str, source: str):
    """A rated seat gets the full projection, and so does any call that disagrees with another. Otherwise it's a Safe
    seat: it joins the next roll-up, and a later outlet agreeing with it is logged without a post. Hundreds of them can
    come in right after polls close, and a post for each would bury the seats people are watching."""
    if DESK.auto_call(store, rid, party, outlet) == "post":     # anything else is already logged, with no post to make
        await log_outlet_call(guild, rid, party, outlet, 0, source=source)


async def post_safe_seats(guild, safe: dict[str, list[str]]):
    seats = [(party, rid) for party, rids in safe.items() for rid in rids]
    for i in range(0, len(seats), 80):
        part = seats[i:i + 80]
        lines = [f"✅ **Safe House seats projected ({len(part)})**"]
        for party in safe:
            rids = [rid for p, rid in part if p == party]
            if rids:
                lines.append(f"{S.EMOJI[party]} {', '.join(rids)}")
        await post(guild, "desk_channel", content="\n".join(lines + [house_line()]))


async def flush_safe_seats(guild):
    """Post the waiting Safe seats once the oldest has waited safe_batch_minutes. A seat retracted while it waited is
    left out."""
    queue = store.setting("safe_batch", [])
    if not queue or time.time() - queue[0][2] < 60 * CFG.get("safe_batch_minutes", 5):
        return
    safe: dict[str, list[str]] = {}
    for rid, party, _ts in queue:
        if (store.get(rid) or {}).get("party") == party and rid not in safe.get(party, []):
            safe.setdefault(party, []).append(rid)
    await post_safe_seats(guild, safe)
    store.set_setting("safe_batch", store.setting("safe_batch", [])[len(queue):])


@tasks.loop(seconds=30)
async def safe_batch_loop():
    await each_server(flush_safe_seats)


async def read_bluesky(known: set) -> list[dict] | None:
    """Every account's new posts, read once for all servers. `known` is the posts every listening server has
    already dealt with. None, with the failure counted, when an account couldn't be read."""
    posts, error = [], None
    for did in BSKY.SOURCES:
        try:
            posts += await BSKY.fetch(did, known)
        except Exception as exc:    # one account failing shouldn't hold up the other
            error = f"{BSKY.SOURCES[did]}: {exc}"
            log.warning("Couldn't read %s from Bluesky: %s", BSKY.SOURCES[did], exc)
    if error:
        _bsky.update(failures=_bsky["failures"] + 1, error=error)
    else:
        _bsky.update(failures=0, warned=False, error=None, checked=time.time())
    return posts


async def bluesky_pass(guild, posts: list[dict]):
    """Act, in this server, on the posts it hasn't dealt with yet."""
    seen = set(store.setting("bsky_seen", []))
    for p in sorted(posts, key=lambda x: x["when"]):
        if p["uri"] in seen:
            continue
        outlet, link = BSKY.SOURCES[p["did"]], BSKY.post_link(p["did"], p["uri"])
        seen.add(p["uri"])
        store.set_setting("bsky_seen", sorted(seen))    # first, so a post that fails here can't be called twice
        try:
            got = BSKY.read(outlet, p["text"], lambda rid: load_candidates().get(rid))
            if got and got.get("call"):
                _bsky["calls"] += 1
                await take_auto_call(guild, got["rid"], got["party"], outlet, f"Read from Bluesky: <{link}>")
            elif got:
                _bsky["reviews"] += 1
                text = discord.utils.escape_markdown(re.sub(r"\s+", " ", p["text"]))[:220]
                await post(guild, "board_channel",
                           content=f"👀 **{outlet}** posted something a person should check ({got['review']}):\n"
                                   f"> {text}\n<{link}>\n-# If it's a real call, log it with `/procall`.")
        except Exception:
            log.exception("Couldn't act on Bluesky post %s", link)


async def read_nyt() -> dict | None:
    """NYT's House file, read once for all servers. None, with the failure counted, when it couldn't be read."""
    try:
        found = await NYT.fetch()
    except Exception as exc:
        _nyt.update(failures=_nyt["failures"] + 1, error=str(exc)[:200])
        log.warning("Couldn't read NYT's House results: %s", exc)
        return None
    _nyt.update(failures=0, warned=False, error=None, checked=time.time())
    return found


async def nyt_pass(guild, found: dict):
    """Act, in this server, on seats whose winner is new since its last read. A winner that NYT changes or takes back
    goes to a person, because the bot doesn't undo a call on its own. Nothing counts until the state's polls have
    closed, so test data in the file can't make a call."""
    seen = store.setting("nyt_seen", {})
    check = []
    for rid in [rid for rid in seen if rid not in found]:
        check.append(f"{label(rid)}: it no longer lists a winner ({seen.pop(rid)} before)")
    for rid, got in found.items():
        mark = got.get("party", "?")
        if seen.get(rid) == mark or now() < N.first_close(R.RACES[rid]["state"]):
            continue
        before = seen.get(rid)
        seen[rid] = mark
        store.set_setting("nyt_seen", seen)    # first, so a seat that fails here can't be called twice
        try:
            if before:
                check.append(f"{label(rid)}: it changed from {before} to {mark}")
            elif got.get("call"):
                _nyt["calls"] += 1
                await take_auto_call(guild, rid, got["party"], NYT.OUTLET, "Read from NYT's results data (the AP's call).")
            else:
                check.append(f"{label(rid)}: {got['review']}")
        except Exception:
            log.exception("Couldn't act on NYT's call in %s", rid)
    store.set_setting("nyt_seen", seen)
    if check:
        _nyt["reviews"] += len(check)
        text = "\n".join(f"- {c}" for c in check)[:1700]
        await post(guild, "board_channel", content=f"👀 **NYT**'s House results need a person to check:\n{text}\n"
                                                   "-# If it's a real call, log it with `/procall`. To take one back, use `/uncall`.")


async def take_ddhq_file(guild, res: dict):
    """Act on one file from the DDHQ grab button. As with NYT, only seats new since the last file count; a winner that
    changes or goes away goes to a person, because the bot doesn't undo a call on its own."""
    if res.get("error"):
        return await post(guild, "board_channel", content=f"⚠️ Didn't use the DDHQ grab `{res.get('file')}`: {res['error']}.")
    _ddhq["checked"] = time.time()
    if not store.setting("autocalls", AUTOCALLS_DEFAULT):
        return await post(guild, "board_channel", content="DDHQ grab ignored, because `/autocalls` is off.")
    found, seen, check, new = res["found"], store.setting("ddhq_seen", {}), [], 0
    for rid in [rid for rid in seen if rid not in found]:
        check.append(f"{label(rid)}: it no longer lists a winner ({seen.pop(rid)} before)")
    for seat, got in found.items():
        rid, mark = got.get("rid"), got.get("party", "?")
        if not rid:
            check.append(f"{seat}: {got['review']}")
            continue
        if seen.get(rid) == mark or now() < N.first_close(R.RACES[rid]["state"]):
            continue
        before = seen.get(rid)
        seen[rid] = mark
        store.set_setting("ddhq_seen", seen)    # first, so a seat that fails here can't be called twice
        try:
            if before:
                check.append(f"{label(rid)}: it changed from {before} to {mark}")
            elif got.get("call"):
                new += 1
                await take_auto_call(guild, rid, got["party"], DG.OUTLET, "Read from DDHQ's results page with the grab button.")
            else:
                check.append(f"{label(rid)}: {got['review']}")
        except Exception:
            log.exception("Couldn't act on DDHQ's call in %s", rid)
    store.set_setting("ddhq_seen", seen)
    _ddhq["calls"] += new
    if not _ddhq["loaded"]:
        _ddhq["loaded"] = True
        await post(guild, "board_channel", content=f"📥 Loaded DDHQ's House calls with the grab button: D {res['tally'].get('D')}, "
                                                   f"R {res['tally'].get('R')}, {new} new here. Later grabs load quietly.")
    if check:
        _ddhq["reviews"] += len(check)
        text = "\n".join(f"- {c}" for c in check)[:1700]
        await post(guild, "board_channel", content=f"👀 **DDHQ**'s page needs a person to check:\n{text}\n"
                                                   "-# If it's a real call, log it with `/procall`. To take one back, use `/uncall`.")


@tasks.loop(seconds=10)
async def ddhq_loop():
    # same rule as bluesky_loop: nothing may escape
    try:
        guild = main_guild()
        if guild:
            for res in await asyncio.to_thread(DG.scan):
                await take_ddhq_file(guild, res)
    except Exception:
        log.exception("DDHQ grab pass failed")


def listening() -> list:
    """The servers that have automatic calls on."""
    out = []
    for guild in list(client.guilds):
        with TEN.scope(guild.id):
            if store.setting("autocalls", AUTOCALLS_DEFAULT):
                out.append(guild)
    return out


async def outlet_trouble(state: dict, what: str, guilds: list):
    """Tell each listening server, once, that an outlet has been unreadable for about five minutes."""
    if state["failures"] < 10 or state["warned"]:
        return
    state["warned"] = True
    for guild in guilds:
        with TEN.scope(guild.id):
            try:
                await post(guild, "board_channel", content=f"⚠️ I haven't been able to read {what} for about 5 minutes "
                           f"({state['error']}). Log House calls by hand with `/procall` until it's back.")
            except Exception:
                log.exception("Couldn't warn %s about %s", guild.name, what)


@tasks.loop(seconds=30)
async def nyt_loop():
    """The file is the same for every server, so it's read once and each server acts on it for itself."""
    # same rule as bluesky_loop: nothing may escape
    try:
        guilds = listening()
        if not guilds or not NYT.urls().get("house") or datetime.now(N.ET) < FEEDS.reading_since():
            return
        found = await read_nyt()
        if found is None:
            return await outlet_trouble(_nyt, "NYT's House results", guilds)

        async def one(guild):
            if guild in guilds:
                await nyt_pass(guild, found)
        await each_server(one)
    except Exception:
        log.exception("NYT pass failed")


@tasks.loop(seconds=30)
async def bluesky_loop():
    """Each account is read once, and each server acts on the posts it hasn't dealt with."""
    # an exception escaping here would stop the task loop for the rest of the night, so nothing may
    try:
        guilds = listening()
        if not guilds or not BSKY.SOURCES or datetime.now(N.ET) < FEEDS.reading_since():
            return
        seen = []
        for guild in guilds:
            with TEN.scope(guild.id):
                seen.append(set(store.setting("bsky_seen", [])))
        posts = await read_bluesky(set.intersection(*seen))     # skip only what every server has dealt with
        await outlet_trouble(_bsky, "Bluesky", guilds)

        async def one(guild):
            if guild in guilds:
                await bluesky_pass(guild, posts)
        await each_server(one)
    except Exception:
        log.exception("Bluesky pass failed")


async def call_nyt_winners(guild, user_id: int, fallback=None) -> dict:
    """Make official every race NYT lists a winner for. A race already called the same way is only logged. One called
    the other way, by the desk or another trusted outlet, goes to a person. Nothing counts until the state's polls close."""
    found, problems = await NYT.fetch_all()
    out = {"called": [], "already": 0, "check": [], "problems": problems}
    for rid, got in found.items():
        try:
            if got.get("review"):
                out["check"].append(f"{label(rid)}: {got['review']}")
                continue
            party, official = got["party"], store.get(rid)
            if now() < N.first_close(R.RACES[rid]["state"]):
                continue
            if official and official["party"] != party:
                out["check"].append(f"{label(rid)}: NYT says {party}, the desk called it {official['party']}")
            elif official:
                if not any(p["outlet"] == NYT.OUTLET for p in store.procalls(rid)):
                    store.procall(rid, NYT.OUTLET, party, user_id)
                out["already"] += 1
            elif rival_house_caller(rid, party):
                out["check"].append(f"{label(rid)}: NYT says {party}, {rival_house_caller(rid, party)} called it the other way")
            elif R.RACES[rid]["type"] == "house":
                await take_auto_call(guild, rid, party, NYT.OUTLET, "Read from NYT's results data (the AP's call).")
                out["called"].append(rid)
            else:
                how = DESK.follow_outlet(store, rid, party, NYT.OUTLET, user_id)
                if how:
                    await make_official(guild, rid, party, how, fallback, outlet=NYT.OUTLET)
                    out["called"].append(rid)
        except Exception:
            log.exception("Couldn't act on NYT's call in %s", rid)
            out["check"].append(f"{label(rid)}: something went wrong, so check it by hand")
    return out


@tree.command(name="callnyt", description="Admin: make official every House, Senate and governor race NYT has called.")
@app_commands.guild_only()
async def callnyt_cmd(interaction: discord.Interaction):
    if not is_admin(interaction.user):
        return await interaction.response.send_message("Admins only.", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    res = await call_nyt_winners(interaction.guild, interaction.user.id, interaction.channel)
    lines = [f"**Called {len(res['called'])}**" + (f": {', '.join(res['called'])}" if res["called"] else "") +
             f"\nAlready called the same way: {res['already']}."]
    if res["check"]:
        lines.append("**Left alone, for a person to check:**\n" + "\n".join(f"- {c}" for c in res["check"]))
    if res["problems"]:
        lines.append("**Couldn't read:**\n" + "\n".join(f"- {p}" for p in res["problems"]))
    await interaction.followup.send("\n".join(lines)[:1900], ephemeral=True)


@tree.command(name="autocalls", description="Admin: turn House calls read from Bluesky and NYT on or off, or see how they're doing.")
@app_commands.guild_only()
@app_commands.choices(turn=[app_commands.Choice(name="on", value="on"), app_commands.Choice(name="off", value="off")])
async def autocalls_cmd(interaction: discord.Interaction, turn: app_commands.Choice[str] | None = None):
    if not is_admin(interaction.user):
        return await interaction.response.send_message("Admins only.", ephemeral=True)
    if turn:
        store.set_setting("autocalls", turn.value == "on")
    on = store.setting("autocalls", AUTOCALLS_DEFAULT)
    sources = ([f"{', '.join(BSKY.SOURCES.values())} on Bluesky"] if BSKY.SOURCES else []) + \
              ([f"{NYT.OUTLET}'s results data"] if NYT.urls().get("house") else [])
    lines = [f"Automatic House calls are **{'on' if on else 'off'}**. Sources: "
             + (", and ".join(sources) if sources else f"none. **{packs.active().name}** names no outlets to read, so "
                                                         "calls are logged by hand with `/procall`") + "."]
    since = FEEDS.reading_since().astimezone(N.ET)
    if sources and datetime.now(N.ET) < since:
        lines.append(f"They start reading at {since:%I %p}".replace(" 0", " ") + f" ET on {since:%b} {since.day}.")
    if _bsky["checked"]:
        lines.append(f"Bluesky last read {S.minutes_text((time.time() - _bsky['checked']) / 60)} ago. Since the last restart: "
                     f"{_bsky['calls']} calls, {_bsky['reviews']} posts sent to a person.")
    if _nyt["checked"]:
        lines.append(f"{NYT.OUTLET} last read {S.minutes_text((time.time() - _nyt['checked']) / 60)} ago. Since the last restart: "
                     f"{_nyt['calls']} calls, {_nyt['reviews']} seats sent to a person.")
    if _ddhq["checked"]:
        lines.append(f"DDHQ grab button last loaded {S.minutes_text((time.time() - _ddhq['checked']) / 60)} ago. Since the last "
                     f"restart: {_ddhq['calls']} calls, {_ddhq['reviews']} seats sent to a person.")
    if _bsky["error"]:
        lines.append(f"⚠️ Last Bluesky error: {_bsky['error']}")
    if _nyt["error"]:
        lines.append(f"⚠️ Last {NYT.OUTLET} error: {_nyt['error']}")
    if turn and not on:
        lines.append("Anything posted while they're off is picked up when you turn them back on.")
    await interaction.response.send_message("\n".join(lines), ephemeral=True)


# =========================================================================== /predict picker
def chunks(seq, n):
    return [seq[i:i + n] for i in range(0, len(seq), n)]


def build_pages() -> list[dict]:
    """The picker's pages, from the pack's sections: twenty races to a page, a page of its own for a control pick."""
    pages = []
    for sec in P.sections():
        rids = [r["id"] for r in sec["races"]]
        if sec.get("control"):
            pages.append({"title": f"{sec['name']} majority", "races": [sec["control"]["id"]], "map": None,
                          "kind": "house", "control": sec["control"]["id"], "required": True})
        rated = any(rid in CMP.RATING for rid in rids)
        kind = "battleground" if rated else sec.get("category") or sec["key"]
        name = f"{sec['name']} battlegrounds" if rated else sec["name"]
        parts = chunks(rids, 20)
        for i, part in enumerate(parts):
            pages.append({"title": name + (f" {i + 1}/{len(parts)}" if len(parts) > 1 else "")
                          + ("" if sec.get("required") else " (bonus)"), "races": part,
                          "map": kind if kind in G.MAP_KINDS else None, "kind": kind,
                          "required": bool(sec.get("required"))})
    return pages


PAGES = build_pages()
# Discord buttons come in blue, red, green and grey: there is no purple. An independent is purple on every map and
# card, so a green button for one read as something else in 2026. Every picked button now carries its party's dot, and
# an independent is a grey button with the purple dot, so the button and the map say the same thing.
STYLE = {None: discord.ButtonStyle.secondary, "D": discord.ButtonStyle.primary,
         "R": discord.ButtonStyle.danger, "I": discord.ButtonStyle.secondary}


def deadline() -> datetime:
    return packs.active().deadline()      # each data pack sets its own


def predictions_open() -> bool:
    return now() < deadline()


def unlocked_until(user_id: int) -> float:
    """When /unlockpicks stops letting this person change their picks (0 if it never did)."""
    return store.setting("picks_unlocked", {}).get(str(user_id), 0)


def can_pick(user_id: int) -> bool:
    return predictions_open() or now().timestamp() < unlocked_until(user_id)


def predictions_hidden() -> bool:
    return CFG.get("hide_predictions_until_deadline", True) and predictions_open()


def senate_summary(picks: dict[str, str]) -> str:
    s = S.senate_from_picks(picks)
    out = f"D {s['D']} · R {s['R']}" + (f" · I {s['I']}" if s["I"] else "")
    if s["unpicked"]:
        return out + f" · {s['unpicked']} unpicked"
    if s["D"] >= R.SENATE_MAJORITY:
        return out + " → **Democratic control**"
    if s["R"] >= 50:
        return out + " → **Republican control**" + (" (VP tiebreak)" if s["R"] == 50 else "")
    return out + " → control depends on the independents"


class PickButton(discord.ui.Button):
    def __init__(self, rid: str, pick: str | None, row: int, kind: str):
        statewide = kind != "battleground" and R.RACES[rid]["type"] in ("senate", "governor", "president")
        short = R.STATE_SHORT.get(R.RACES[rid]["state"]) if statewide and len(rid.split("-")[0]) == 2 else None
        text = short or (rid if kind == "battleground" else R.RACES[rid]["label"])
        if pick and pick not in packs.active().options:       # a candidate, not a party: say who
            text = f"{text}: {cand(rid, pick)}"
        super().__init__(label=text[:80], style=STYLE.get(pick, discord.ButtonStyle.secondary), row=row,
                         emoji=S.EMOJI[pick] if pick else None)
        self.rid = rid

    async def callback(self, interaction: discord.Interaction):
        view: PredictView = self.view
        opts = options_for(self.rid)
        cur = view.picks.get(self.rid)
        nxt = opts[0] if cur is None else (opts[opts.index(cur) + 1] if opts.index(cur) + 1 < len(opts) else None)
        if nxt:
            view.picks[self.rid] = nxt
        else:
            view.picks.pop(self.rid, None)
        view.note = None
        await view.refresh(interaction)


class HouseButton(discord.ui.Button):
    """One side of a control pick: who wins the House, the Senate, or whatever body the page is for."""

    def __init__(self, rid: str, party: str, picked: str | None):
        verb = "keep" if S.holder(rid) == party else "win"
        super().__init__(label=f"{cand(rid, party)} {verb} the {R.chamber_name(rid)}"[:80], emoji=S.EMOJI[party], row=0,
                         style=STYLE.get(party, discord.ButtonStyle.secondary) if picked == party else discord.ButtonStyle.secondary)
        self.rid, self.party = rid, party

    async def callback(self, interaction: discord.Interaction):
        view: PredictView = self.view
        if view.picks.get(self.rid) == self.party:
            view.picks.pop(self.rid)
        else:
            view.picks[self.rid] = self.party
        view.note = None
        await view.refresh(interaction)


def tiebreak() -> dict:
    """The pack's tiebreaker question, with the wording Discord has room for: {"label", "name", "min", "max"}. A
    box's label can be 45 characters, so a longer question becomes "Guess <what it's called>"."""
    tb = dict(S.GAME.get("tiebreaker") or {})
    name = tb.get("name") or "the tiebreaker"
    label = tb.get("label") or f"Guess {name}"
    return {"name": name, "label": label if len(label) <= 45 else f"Guess {name}"[:45],
            "min": int(tb.get("min", 0)), "max": int(tb.get("max", 435)), "hint": tb.get("hint", "")}


class TiebreakModal(discord.ui.Modal, title="Tiebreaker"):
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await _enter_server(interaction)

    def __init__(self, view: "PredictView"):
        super().__init__()
        self.pv, self.tb = view, tiebreak()
        self.seats = discord.ui.TextInput(label=self.tb["label"], placeholder=f"{self.tb['min']} to {self.tb['max']}",
                                          max_length=len(str(self.tb["max"])))
        if view.house_seats is not None:
            self.seats.default = str(view.house_seats)
        self.add_item(self.seats)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            n = int(str(self.seats.value).strip())
            assert self.tb["min"] <= n <= self.tb["max"]
        except (ValueError, AssertionError):
            return await interaction.response.send_message(
                f"Enter a whole number from {self.tb['min']} to {self.tb['max']}.", ephemeral=True)
        self.pv.house_seats = n
        self.pv.note = None
        await self.pv.refresh(interaction)


class NavButton(discord.ui.Button):
    def __init__(self, action: str, row: int = 4, **kw):
        super().__init__(row=row, **kw)
        self.action = action

    async def callback(self, interaction: discord.Interaction):
        view: PredictView = self.view
        if self.action == "prev":
            view.page = max(0, view.page - 1)
        elif self.action == "next":
            view.page = min(len(PAGES) - 1, view.page + 1)
        elif self.action == "clear":
            for rid in PAGES[view.page]["races"]:
                view.picks.pop(rid, None)
        elif self.action == "ratings":
            added = view.fill_ratings()
            view.note = (f"📊 Filled {added} Tilt/Lean/Likely seat{'s' * (added != 1)} with the party the ratings favor, "
                         f"across all battleground pages. Toss-ups are left for you." if added
                         else "Every rated seat is already picked. Toss-ups are up to you.")
            return await view.refresh(interaction)
        elif self.action == "safe":
            added = view.fill_safe()
            view.note = (f"⚡ Filled {added} safe race{'s' * (added != 1)} (Cook Solid ratings)." if added
                         else "All safe races are already filled in.")
            return await view.refresh(interaction)
        elif self.action == "tiebreak":
            return await interaction.response.send_modal(TiebreakModal(view))
        elif self.action == "save":
            return await view.save(interaction)
        view.note = None
        await view.refresh(interaction)


class PredictView(ScopedView):
    def __init__(self, user: discord.abc.User, picks: dict[str, str], house_seats: int | None, saved: bool):
        super().__init__(timeout=45 * 60)
        self.user = user
        self.picks = dict(picks)
        self.house_seats = house_seats
        self.saved_before = saved
        self.page = 0
        self.note: str | None = None
        if not saved:  # first time: pre-fill the races nobody needs to think about
            n = self.fill_safe()
            self.note = (f"⚡ {n} safe races (rated Solid by Cook) are already filled in. "
                         f"Click any of them to change it.")
        self.render()

    def fill_ratings(self) -> int:
        """Battleground seats: fill unpicked Tilt/Lean/Likely seats with the favored party."""
        added = 0
        for rid, _rating, _h in CMP.BATTLEGROUND:
            fav = CMP.favored(rid)
            if fav and rid in R.RACES and rid not in self.picks:
                self.picks[rid] = fav
                added += 1
        return added

    def fill_safe(self) -> int:
        added = 0
        for rid, party in CMP.SAFE.items():
            if rid not in self.picks:
                self.picks[rid] = party
                added += 1
        return added

    def render(self):
        self.clear_items()
        page = PAGES[self.page]
        if page["kind"] == "house":
            for p in options_for(page["control"]):
                self.add_item(HouseButton(page["control"], p, self.picks.get(page["control"])))
            if S.GAME.get("tiebreaker"):
                self.add_item(NavButton("tiebreak", row=1, label="Tiebreaker", emoji="🔢",
                                        style=discord.ButtonStyle.secondary))
        else:
            for i, rid in enumerate(page["races"]):
                self.add_item(PickButton(rid, self.picks.get(rid), i // 5, page["kind"]))
        last = self.page == len(PAGES) - 1
        self.add_item(NavButton("prev", label="Back", emoji="◀️", style=discord.ButtonStyle.secondary,
                                disabled=self.page == 0))
        nxt = "Last page" if last else f"Next: {PAGES[self.page + 1]['title'].replace(' (bonus)', '')}"
        self.add_item(NavButton("next", label=nxt[:80], emoji="▶️", style=discord.ButtonStyle.primary,
                                disabled=last))
        self.add_item(NavButton("save", label="Save all", emoji="💾", style=discord.ButtonStyle.success))
        if page["kind"] != "house":
            self.add_item(NavButton("clear", label="Clear page", style=discord.ButtonStyle.secondary))
        if page["kind"] == "battleground":
            self.add_item(NavButton("ratings", label="Fill Tilt & Lean by ratings", emoji="📊",
                                    style=discord.ButtonStyle.secondary))
        else:
            self.add_item(NavButton("safe", label="Fill safe races", emoji="⚡", style=discord.ButtonStyle.secondary))

    def embed(self) -> discord.Embed:
        page = PAGES[self.page]
        help_text = {
            "senate": "Click a state to cycle it: grey → 🔵 → 🔴 → grey. Where an independent is running, 🟣 comes after 🔴.",
            "house": "Who controls the House? The tiebreaker settles leaderboard ties.",
            "governor": "Optional. Each correct governor pick is bonus points.",
            "battleground": "Optional. Toss-up, Tilt and Lean seats (avg. of Cook, Inside Elections, Sabato); each correct pick is bonus points.",
        }.get(page["kind"], "Click a race to cycle through who can win it. Click once more to clear it.")
        required = page.get("required", page["kind"] in ("senate", "house"))
        how = ("**Page through with ▶️ Next.** Your clicks carry over between pages, "
               "so you only need to press **💾 Save all** once at the end (or any time to save progress).")
        e = discord.Embed(title=f"Step {self.page + 1} of {len(PAGES)} · {page['title'].replace(' (bonus)', '')}"
                                + (" (required)" if required else " (bonus)"),
                          color=0xFAD63E,
                          description=(f"{self.note}\n\n" if self.note else "") + f"{help_text}\n\n{how}")
        if page["kind"] == "battleground":
            by_rating: dict[str, list[str]] = {}
            for rid in page["races"]:
                pick = self.picks.get(rid)
                by_rating.setdefault(CMP.RATING[rid], []).append(rid + (f" {S.EMOJI[pick]}" if pick else ""))
            for rating, rids in by_rating.items():
                e.add_field(name=rating, value=" · ".join(rids), inline=False)
        indies = [f"**{R.STATE_NAMES[R.RACES[rid]['state']]}:** {cand(rid, 'I')} 🟣 (against {cand(rid, 'D')} 🔵 "
                  f"and {cand(rid, 'R')} 🔴)" for rid in page["races"] if "I" in options_for(rid)]
        if indies:
            e.add_field(name="Independents on this page (a third click)", value="\n".join(indies)[:1024], inline=False)
        done = S.completion(self.picks, {"house_dem_seats": self.house_seats})
        so_far = [f"**{S.PART_NAMES[key][0].upper() + S.PART_NAMES[key][1:]}:** {a}/{b}" for key, (a, b) in done["parts"].items()]
        if R.races_of("senate"):
            so_far[0:0] = [f"**Senate adds up to:** {senate_summary(self.picks)}"]
        if S.GAME.get("tiebreaker"):
            so_far.append(f"**Tiebreaker** ({tiebreak()['name']}): "
                          + (str(self.house_seats) if self.house_seats is not None else "not entered"))
        e.add_field(name="Your picks so far", inline=False, value="\n".join(so_far)[:1024])
        closes = deadline() if predictions_open() else datetime.fromtimestamp(unlocked_until(self.user.id), TZ)
        e.set_footer(text="Nothing is saved until you press Save all · "
                          f"Picks close {closes.astimezone(TZ).strftime('%b %d, %I:%M %p').replace(' 0', ' ')} "
                          f"{closes.astimezone(TZ).tzname()}")
        if page["map"]:
            e.set_image(url="attachment://picks.png")
        return e

    def image(self) -> discord.File | None:
        page = PAGES[self.page]
        if not page["map"]:
            return None
        picks = {r: p for r, p in self.picks.items() if R.RACES[r]["type"] == page["map"]}
        buf = G.tile_map(page["map"], picks, title=f"{self.user.display_name}'s {page['map']} picks",
                         pending_word="unpicked", width=760)
        return discord.File(buf, filename="picks.png")

    async def refresh(self, interaction: discord.Interaction):
        self.render()
        f = self.image()
        await interaction.response.edit_message(embed=self.embed(), view=self,
                                                attachments=[f] if f else [])

    async def save(self, interaction: discord.Interaction):
        if not can_pick(self.user.id):
            return await interaction.response.send_message("Predictions are closed.", ephemeral=True)
        store.save_predictions(self.user.id, self.picks, self.house_seats)
        done = S.completion(self.picks, {"house_dem_seats": self.house_seats})
        required = {c["key"] for c in S.GAME.get("categories") or [] if c.get("required")}
        missing = [f"{b - a} {S.PART_NAMES[key]}" if b > 1 else S.PART_NAMES[key]
                   for key, (a, b) in done["parts"].items() if a < b and key in required]
        if "tiebreak" in done["missing"]:
            missing.append("the tiebreaker")
        self.note = (f"✅ **Saved at {clock(time.time())}.** You've filled in **{done['share']:.0%}** of the game. "
                     "You can keep editing and save again until picks close.")
        skipped = [f"{b - a} {name}" for key, name in (("governor", "governor races"), ("battleground", "House seats"))
                   for a, b in [done["parts"].get(key, (0, 0))] if a < b]
        if skipped:
            self.note += f"\n-# Bonus points still open: {' and '.join(skipped)}. In 2026 the House seats decided the game."
        if missing:
            self.note += f"\n⚠️ Still missing: {', '.join(missing)}. Missing picks score zero."
        elif self.page < len(PAGES) - 1:
            self.note += "\nRequired picks are done. Keep going with ▶️ Next for bonus races, or you're finished."
        await self.refresh(interaction)
        verb = "updated their" if self.saved_before else "locked in their"
        self.saved_before = True
        text = f"🗳️ **{self.user.display_name}** {verb} predictions."
        if not predictions_hidden():
            house = self.picks.get("US-HOUSE")
            text += f"\n-# Senate {senate_summary(self.picks)} · House: " + \
                    (R.PARTY_NAMES[house] if house else "not picked") + f" · {done['share']:.0%} filled in"
        else:
            text += "\n-# Everyone's picks are revealed when predictions close."
        if predictions_hidden():
            await post(interaction.guild, "board_channel", None, content=text)
        else:
            card = prediction_card_file(self.user.display_name, self.picks, self.house_seats)
            await post(interaction.guild, "board_channel", None, content=text, file=card)


def prediction_card_file(name: str, picks: dict, house_seats, marks=None) -> discord.File:
    buf = G.prediction_card(name, picks, house_seats, marks=marks,
                            battleground=[r for r, _, _ in CMP.BATTLEGROUND if r in R.RACES])
    return discord.File(buf, filename="predictions.png")


@tree.command(name="predict", description="Make your 2026 predictions: click states blue or red, then Save.")
@app_commands.guild_only()
async def predict_cmd(interaction: discord.Interaction):
    if not can_pick(interaction.user.id):
        return await interaction.response.send_message(
            "Predictions are closed. Use `/predictions` to see everyone's picks and `/leaderboard` for standings.",
            ephemeral=True)
    picks, meta = store.user_predictions(interaction.user.id)
    view = PredictView(interaction.user, picks, meta["house_dem_seats"] if meta else None, saved=bool(meta))
    f = view.image()
    kwargs = {"embed": view.embed(), "view": view, "ephemeral": True}
    if f:
        kwargs["file"] = f
    await interaction.response.send_message(**kwargs)


# =========================================================================== prediction results
def can_view(viewer, target_id: int) -> bool:
    return viewer.id == target_id or not predictions_hidden()


def grade_text(c: dict) -> str:
    decided = c["right"] + c["wrong"]
    if not c["picked"]:
        return "no picks"
    return f"{c['right']}/{decided} right" + (f", {c['pending']} pending" if c["pending"] else "")


@tree.command(name="predictions", description="See someone's predictions (yours by default) and how they're doing.")
@app_commands.guild_only()
@app_commands.describe(member="Whose predictions")
async def predictions_cmd(interaction: discord.Interaction, member: discord.Member | None = None):
    target = member or interaction.user
    if not can_view(interaction.user, target.id):
        return await interaction.response.send_message(
            "Picks are hidden until predictions close. You can see your own.", ephemeral=True)
    picks, meta = store.user_predictions(target.id)
    if not meta:
        return await interaction.response.send_message(f"{target.display_name} hasn't saved predictions.",
                                                       ephemeral=True)
    g = S.grade(picks, results(), CFG.get("points"))
    house = picks.get("US-HOUSE")
    mark = {True: " ✅", False: " ❌"}.get(g["marks"].get("US-HOUSE"), "")
    e = discord.Embed(title=f"{target.display_name}'s predictions", color=0xFAD63E)
    e.add_field(name="Points", value=f"**{g['points']}**" + (f" · {g['flips']} flip{'s' * (g['flips'] != 1)} called"
                                              if g["flips"] else ""),
                inline=False)
    control = {True: " ✅", False: " ❌"}.get(g["marks"].get("US-SENATE"), "")
    e.add_field(name="Senate", value=f"{senate_summary(picks)}{control}\n{grade_text(g['cats']['senate'])}", inline=False)
    e.add_field(name="House", value=(f"{S.EMOJI[house]} {R.PARTY_NAMES[house]}{mark}" if house else "not picked")
                + (f"\nTiebreaker ({tiebreak()['name']}): {meta['house_dem_seats']}" if meta.get("house_dem_seats") is not None else ""))
    e.add_field(name="Governors (bonus)", value=grade_text(g["cats"]["governor"]))
    e.add_field(name="Battlegrounds (bonus)", value=grade_text(g["cats"]["battleground"]))
    card = prediction_card_file(target.display_name, picks, meta.get("house_dem_seats"), marks=g["marks"])
    e.set_image(url="attachment://predictions.png")
    e.set_footer(text=f"Saved {datetime.fromtimestamp(meta['saved_at'], TZ).strftime('%b %d %I:%M %p')}"
                      " · ✓ right, ✗ wrong")
    await interaction.response.send_message(embed=e, file=card, ephemeral=predictions_hidden())


@tree.command(name="compare", description="Where two people's predictions differ.")
@app_commands.guild_only()
@app_commands.describe(member="Compare with", other="Second person (you by default)")
async def compare_cmd(interaction: discord.Interaction, member: discord.Member,
                      other: discord.Member | None = None):
    a, b = member, other or interaction.user
    if predictions_hidden():
        return await interaction.response.send_message("Picks are hidden until predictions close.", ephemeral=True)
    pa, ma = store.user_predictions(a.id)
    pb, mb = store.user_predictions(b.id)
    if not ma or not mb:
        return await interaction.response.send_message("Both people need saved predictions.", ephemeral=True)
    res = results()
    diffs = S.differences(pa, pb)
    lines = []
    for rid, x, y in diffs:
        def show(p):
            if not p:
                return "—"
            return S.EMOJI[p] + (" ✅" if res.get(rid) == p else " ❌" if rid in res else "")
        lines.append(f"`{rid:<8}` {a.display_name} {show(x)} · {b.display_name} {show(y)}")
    ga, gb = S.grade(pa, res, CFG.get("points")), S.grade(pb, res, CFG.get("points"))
    e = discord.Embed(title=f"{a.display_name} vs {b.display_name}", color=0x2B2D31,
                      description="\n".join(lines[:40]) or "Identical picks.")
    if len(lines) > 40:
        e.description += f"\n…and {len(lines) - 40} more"
    e.set_footer(text=f"{len(diffs)} differences · points {ga['points']}–{gb['points']}")
    await interaction.response.send_message(embed=e)


@tree.command(name="leaderboard", description="Prediction game standings.")
@app_commands.guild_only()
async def leaderboard_cmd(interaction: discord.Interaction):
    guild = interaction.guild
    entries = {uid: store.user_predictions(uid) for uid in store.predictors()}
    if not entries:
        return await interaction.response.send_message("Nobody has saved predictions yet. Try `/predict`.")
    if predictions_hidden():
        lines = []
        for uid, (picks, _meta) in entries.items():
            g = S.grade(picks, {}, CFG.get("points"))
            lines.append(f"**{name_of(guild, uid)}** — " + ("complete ✅" if g["complete"] else "incomplete ⚠️")
                         + f" · {sum(c['picked'] for c in g['cats'].values())} picks")
        e = discord.Embed(title=f"Prediction entries ({len(entries)})", description="\n".join(lines),
                          color=0xFAD63E)
        e.set_footer(text="Picks are revealed and scored once predictions close.")
        return await interaction.response.send_message(embed=e)
    final = store.setting("final_house_dem")
    rows = S.leaderboard(entries, results(), CFG.get("points"), final)
    lines = []
    for i, r in enumerate(rows, 1):
        c = r["cats"]
        house = {True: "✅", False: "❌"}.get(r["marks"].get("US-HOUSE"), "⏳")
        bonus = c["governor"]["right"] + c["battleground"]["right"]
        bits = [f"Senate {c['senate']['right']}/{c['senate']['right'] + c['senate']['wrong']}",
                f"House {house}", f"bonus {bonus}"]
        if "US-SENATE" in r["marks"]:
            bits.insert(1, f"Senate control {'✅' if r['marks']['US-SENATE'] else '❌'}")
        if r["flips"]:
            bits.append(f"{r['flips']} flip{'s' * (r['flips'] != 1)}")
        if r["miss"] is not None:
            bits.append(f"tiebreak off by {r['miss']}")
        lines.append(f"**{i}. {name_of(guild, r['user_id'])}** — **{r['points']}** pts · " + " · ".join(bits))
    pts = {**S.DEFAULT_POINTS, **(CFG.get("points") or {})}
    e = discord.Embed(title="Prediction leaderboard", description="\n".join(lines)[:4000], color=0xFAD63E)
    e.set_footer(text=f"Senate {pts['senate']} · Senate control {pts['senate_control']} · House control "
                      f"{pts['house_control']} · governor "
                      f"{pts['governor']} · battleground {pts['battleground']} · +{pts['flip_bonus']} per "
                      f"correct flip · scored on pro calls, else desk calls")
    await interaction.response.send_message(embed=e)


@tree.command(name="finalhouse", description="Admin: set the final number the tiebreaker is settled against.")
@app_commands.guild_only()
@app_commands.describe(dem_seats="The real number: Democratic House seats in 2026, or whatever this election's tiebreaker asks")
async def finalhouse_cmd(interaction: discord.Interaction, dem_seats: app_commands.Range[int, 0, 1000]):
    if not is_admin(interaction.user):
        return await interaction.response.send_message("Admins only.", ephemeral=True)
    tb = tiebreak()
    if not tb["min"] <= dem_seats <= tb["max"]:
        return await interaction.response.send_message(
            f"The tiebreaker is {tb['name']}, from {tb['min']} to {tb['max']}.", ephemeral=True)
    store.set_setting("final_house_dem", dem_seats)
    await interaction.response.send_message(f"The tiebreaker's answer ({tb['name']}) is set to {dem_seats}.")


@tree.command(name="unlockpicks", description="Admin: let one person use /predict after the deadline, for a set time.")
@app_commands.guild_only()
@app_commands.describe(member="Who can change their picks", minutes="How long they have (0 locks them again)")
async def unlockpicks_cmd(interaction: discord.Interaction, member: discord.Member,
                          minutes: app_commands.Range[int, 0, 180] = 15):
    if not is_admin(interaction.user):
        return await interaction.response.send_message("Admins only.", ephemeral=True)
    unlocked = store.setting("picks_unlocked", {})
    if minutes:
        unlocked[str(member.id)] = until = now().timestamp() + minutes * 60
        text = f"🔓 **{member.display_name}** can change their predictions until {clock(until)}."
    else:
        unlocked.pop(str(member.id), None)
        text = f"🔒 **{member.display_name}**'s predictions are locked again."
    store.set_setting("picks_unlocked", unlocked)
    await interaction.response.send_message(f"{text} This was posted on the board.", ephemeral=True)
    await post(interaction.guild, "board_channel", None, content=text, allowed_mentions=NO_PINGS)


# =========================================================================== /scoreboard (calling)
def closeout_embed(guild) -> discord.Embed:
    entries = {uid: store.user_predictions(uid) for uid in store.predictors()}
    left = S.open_items(results(), store.setting("final_house_dem"), entries)
    e = discord.Embed(title="Closing out the night", color=0xFAD63E if not left["closed"] else 0x2E9E5B)
    for key, title in (("control", "Control"), ("senate", "Senate"), ("governor", "Governors"),
                       ("battleground", "Rated House seats")):
        if left[key]:
            e.add_field(name=f"{title}: {len(left[key])} with no result", inline=False,
                        value=_trim(", ".join(f"`{rid}`" for rid in left[key]), 1024))
    todo = []
    if left["house_seats"]:
        todo.append(f"The tiebreaker's answer ({tiebreak()['name']}) isn't in: `/finalhouse` settles it.")
    if left["no_tiebreak"]:
        todo.append(f"No tiebreaker guess from: {', '.join(name_of(guild, u) for u in left['no_tiebreak'])}.")
    if todo:
        e.add_field(name="Tiebreaker", value="\n".join(todo), inline=False)
    e.description = ("Everything is settled. `/leaderboard` is final." if left["closed"] else
                     "Picks on these races still read *pending*. Call each one, or enter its result with "
                     "`/final race winner`, which outranks every call when scoring.")
    return e


@tree.command(name="closeout", description="What still has to be settled before the leaderboard is final.")
@app_commands.guild_only()
async def closeout_cmd(interaction: discord.Interaction):
    await interaction.response.send_message(embed=closeout_embed(interaction.guild), ephemeral=True)


@tree.command(name="final", description="Admin: enter a race's final result for scoring. Leave winner blank to clear it.")
@app_commands.guild_only()
@app_commands.autocomplete(race=race_autocomplete)
async def final_cmd(interaction: discord.Interaction, race: str, winner: Winner | None = None):
    rid = resolve(race)
    if not is_admin(interaction.user):
        return await interaction.response.send_message("Admins only.", ephemeral=True)
    if not rid:
        return await interaction.response.send_message(f"I don't recognize `{race}`.", ephemeral=True)
    finals = dict(store.setting("final_results", {}))
    if winner and winner.value not in options_for(rid):
        return await interaction.response.send_message(no_such_winner(rid, winner.raw), ephemeral=True)
    if winner:
        finals[rid] = winner.value
        said = f"Final result entered: {label(rid)} → {cand(rid, winner.value)} {S.EMOJI[winner.value]}."
    elif finals.pop(rid, None):
        said = f"Cleared the final result for {label(rid)}. It's scored on calls again."
    else:
        return await interaction.response.send_message(f"{label(rid)} has no final result entered.", ephemeral=True)
    store.set_setting("final_results", finals)
    left = S.open_items(results(), store.setting("final_house_dem"))
    open_n = sum(len(left[k]) for k in ("control", "senate", "governor", "battleground"))
    await interaction.response.send_message(
        f"{said} The map and the desk's calls are unchanged.\n-# {open_n} races still have no result: `/closeout`.",
        ephemeral=True)


@tree.command(name="globalboard", description="The scoreboard across every server that has joined it. Admins: on or off to join or leave.")
@app_commands.guild_only()
@app_commands.choices(join=[app_commands.Choice(name="on: put this server on the global board", value="on"),
                            app_commands.Choice(name="off: take this server off it", value="off")])
async def globalboard_cmd(interaction: discord.Interaction, join: app_commands.Choice[str] | None = None):
    gid = interaction.guild_id
    if join:
        if not is_admin(interaction.user):
            return await interaction.response.send_message("Admins only: you need the Manage Server permission.", ephemeral=True)
        TEN.set_joined(gid, join.value == "on")
        return await interaction.response.send_message(
            "This server is on the global board. Everyone's entry is shared there unless they run `/globalme off`. "
            "It's scored on outlet calls and final results, not this server's own calls." if join.value == "on" else
            "This server is off the global board. Nothing of it is shared.", ephemeral=True)
    if predictions_hidden():
        return await interaction.response.send_message("The global board opens when predictions close.", ephemeral=True)
    rows = TEN.global_board(points=CFG.get("points") if packs.active().data.get("shared_files") else None,
                            gate=not os.getenv("DESK_FAKE_NOW"))
    servers = TEN.joined_servers()
    title = {sv: TEN.store_for(sv).setting("server_name") or f"server {sv[-4:]}" for sv in servers}
    lines = [f"**{i}. {r['name']}** ({title.get(r['server'], r['server'])}) — **{r['points']}** pts"
             for i, r in enumerate(rows[:25], 1)]
    e = discord.Embed(title=f"Global board · {packs.active().name}", color=0xFAD63E,
                      description="\n".join(lines)[:4000] or "Nobody is on it yet.")
    e.set_footer(text=f"{len(servers)} server{'s' * (len(servers) != 1)} joined · scored on outlet calls and final results"
                      + ("" if TEN.joined(gid) else " · this server hasn't joined: /globalboard on"))
    await interaction.response.send_message(embed=e)


@tree.command(name="globalme", description="Keep your own entry off the global board, or put it back.")
@app_commands.guild_only()
@app_commands.choices(share=[app_commands.Choice(name="on: show my entry on the global board", value="on"),
                             app_commands.Choice(name="off: keep my entry in this server only", value="off")])
async def globalme_cmd(interaction: discord.Interaction, share: app_commands.Choice[str]):
    TEN.set_opted_out(interaction.guild_id, interaction.user.id, share.value == "off")
    await interaction.response.send_message(
        "Your entry stays in this server." if share.value == "off" else
        "Your entry is on the global board" + ("." if TEN.joined(interaction.guild_id) else ", once this server joins it."),
        ephemeral=True)


@tree.command(name="pack", description="Which election the bot is running, and the data packs it could run.")
@app_commands.guild_only()
async def pack_cmd(interaction: discord.Interaction, switch_to: str | None = None):
    now_on = packs.active()
    if switch_to:
        if not is_admin(interaction.user):
            return await interaction.response.send_message("Admins only.", ephemeral=True)
        try:
            packs.set_default(switch_to.strip().lower())
        except packs.PackError as exc:
            return await interaction.response.send_message(str(exc)[:1900], ephemeral=True)
        return await interaction.response.send_message(
            f"The bot will run **{packs.load(switch_to.strip().lower()).name}** the next time it starts. Restart it to "
            f"switch. It's still on {now_on.name} until then, and each election keeps its own records.", ephemeral=True)
    lines = []
    for p in packs.available():
        kinds = ", ".join(f"{n} {k}" for k, n in sorted(p["types"].items(), key=lambda kv: -kv[1])[:4])
        lines.append(("▶️ " if p["id"] == now_on.id else "• ") + f"**{p['name']}** `{p['id']}` · {p['election_day']} · {kinds}")
    e = discord.Embed(title="Data packs", description="\n".join(lines)[:4000], color=EMBED_COLOR[None])
    e.set_footer(text="▶️ is running now. Admins: /pack switch_to:<id>, then restart the bot.")
    await interaction.response.send_message(embed=e, ephemeral=True)


@tree.command(name="scoreboard", description="Who called races first, and by how much they beat AP, NYT, Wasserman...")
@app_commands.guild_only()
async def scoreboard_cmd(interaction: discord.Interaction):
    guild = interaction.guild
    pros = store.procalls()
    res = results()
    stats = S.caller_stats(store.all_call_votes(), pros, res)
    e = discord.Embed(title="Race-calling scoreboard", color=0x2B2D31)
    if stats:
        rows = sorted(stats.items(), key=lambda kv: (-(kv[1]["correct"] - kv[1]["wrong"]), -kv[1]["beat"],
                                                     -kv[1]["first"]))
        lines = []
        for uid, s in rows:
            head = (f"**{name_of(guild, uid)}** — {s['correct']} right · {s['wrong']} wrong"
                    + (f" · {s['pending']} pending" if s["pending"] else "")
                    + (f" · first on {s['first']}" if s["first"] else ""))
            outs = [f"{o} {S.signed(sum(v) / len(v))}" for o, v in sorted(s["by_outlet"].items())]
            if outs:
                head += "\n-# avg vs " + " · ".join(outs)
            if s["best"]:
                lead, rid, outlet = s["best"]
                head += f" · best: {rid}, {S.minutes_text(lead)} before {outlet}"
            lines.append(head)
        e.description = "\n".join(lines)[:4000]
    else:
        e.description = "No votes yet. Use `/call` on election night."
    dv = S.desk_vs_pros(store.all_full(), pros)
    if dv["by_outlet"]:
        e.add_field(name="Official desk vs. the pros", inline=False,
                    value=" · ".join(f"{o} {S.signed(sum(v) / len(v))} ({len(v)})"
                                     for o, v in sorted(dv["by_outlet"].items())))
    if dv["disagree"]:
        e.add_field(name="Desk called it the other way", value=", ".join(dv["disagree"]), inline=False)
    if dv["pros_only"]:
        e.add_field(name="Pros called, desk hasn't", value=", ".join(dv["pros_only"][:30]), inline=False)
    e.set_footer(text="+12m = called 12 minutes before that outlet; -5m = 5 minutes after. "
                      "Log outlet calls with /procall.")
    await interaction.response.send_message(embed=e)


# =========================================================================== election night pacing
def wave_embed(when: datetime, states: list[str], preview: bool = False) -> discord.Embed:
    calls = store.all()
    pros = {p["race_id"]: p for p in store.procalls()}
    entries = [] if predictions_hidden() else [store.user_predictions(u)[0] for u in store.predictors()]
    names = []
    for st in states:
        late = N.last_close(st)
        names.append(R.STATE_NAMES[st] + (f" (rest at {N.fmt(late, TZ).split(' / ')[0]})"
                                          if late != N.first_close(st) else ""))
    e = discord.Embed(title=f"{'PREVIEW · ' if preview else ''}🕖 Polls closing · {N.fmt(when, TZ)}",
                      description=", ".join(names), color=0xFAD63E)
    lines = []
    for st in states:
        for suffix in ("SEN", "GOV"):
            rid = f"{st}-{suffix}"
            if rid not in R.RACES:
                continue
            split: dict[str, int] = {}
            for picks in entries:
                if rid in picks:
                    split[picks[rid]] = split.get(picks[rid], 0) + 1
            txt = f"**{rid}** ({R.RACES[rid]['holder']}-held)"
            if split:
                txt += " · group picked " + " ".join(f"{S.EMOJI[p]}{n}" for p, n in sorted(split.items()))
            if rid in calls:
                txt += f" · desk called {calls[rid]}"
            if rid in pros:
                txt += f" · {pros[rid]['outlet']} called {pros[rid]['party']}"
            lines.append(txt)
    if lines:
        e.add_field(name="Senate & governor races", value="\n".join(lines)[:1024], inline=False)
    res = results()
    order = ["Toss-up", "Tilt D", "Tilt R", "Lean D", "Lean R", "Likely D", "Likely R"]
    watch = sorted((rid for rid, _, _ in CMP.RATINGS if rid[:2] in states),
                   key=lambda r: (order.index(CMP.RATING_OF[r]), r))
    if watch:
        lines = []
        for rid in watch:
            mark = (" · called " + S.EMOJI[res[rid]]) if rid in res else ""
            star = "⚠️ " if rid in CMP.EARLY_WATCH else ""
            lines.append(f"{star}**{rid}** {CMP.RATING_OF[rid]} ({CMP.HOLDER_OF[rid]}-held){mark}")
        e.add_field(name="House seats to watch (avg. of Cook, Inside Elections, Sabato)",
                    value="\n".join(lines)[:1024], inline=False)
    return e


async def send_alert(guild, key: str, **kwargs):
    """Post a one-time alert, and mark it sent only once it's actually posted, so a failure is retried next pass."""
    try:
        await post(guild, "board_channel", **kwargs)
    except Exception:
        log.exception("Couldn't post alert %s; retrying on the next pass", key)
        return
    store.mark_alert(key)


@tasks.loop(seconds=30)
async def alert_loop():
    await each_server(alert_pass)


async def alert_pass(guild):
    """Reminders, poll-closing alerts and website saves for one server."""
    try:
        if guild is None or not find_channel(guild, "board_channel"):
            return
        t = now()
        remember_names(guild)
        if t >= deadline() and not store.alert_sent("predictions-locked"):
            n = len(store.predictors())
            await send_alert(guild, "predictions-locked",
                             content=f"🔒 **Predictions are locked.** {n} entr{'y' if n == 1 else 'ies'}. "
                                     f"Use `/predictions` and `/compare` to see everyone's picks.")
        await picks_reminder(guild, t)
        await announce_web_saves(guild)
        await early_vote_digest(guild, t)
        for when, states in N.waves():
            key = f"wave-{when.isoformat()}"
            if t < when or store.alert_sent(key):
                continue
            if (t - when).total_seconds() > 1800:  # bot started late: skip stale alerts
                store.mark_alert(key)
                continue
            await send_alert(guild, key, embed=wave_embed(when, states))
    except Exception:
        log.exception("Alert loop failed")


async def announce_web_saves(guild):
    """Predictions saved on the website go on the board like ones saved with /predict. The site leaves a note in the
    database for each save; several saves by one person since the last pass make one post."""
    saves = store.setting("web_saves", [])
    if not saves:
        return
    store.set_setting("web_saves", [])
    latest = {}
    for s in saves:
        latest[s["uid"]] = {"first": s.get("first") or latest.get(s["uid"], {}).get("first", False)}
    for uid, s in latest.items():
        picks, meta = store.user_predictions(uid)
        name = name_of(guild, uid)
        text = f"🗳️ **{name}** {'locked in their' if s['first'] else 'updated their'} predictions on the website."
        try:
            if predictions_hidden():
                await post(guild, "board_channel", None, content=text + "\n-# Everyone's picks are revealed when predictions close.")
            else:
                house = picks.get("US-HOUSE")
                done = S.completion(picks, meta)
                text += (f"\n-# Senate {senate_summary(picks)} · House: "
                         + (R.PARTY_NAMES[house] if house else "not picked") + f" · {done['share']:.0%} filled in")
                await post(guild, "board_channel", None, content=text,
                           file=prediction_card_file(name, picks, (meta or {}).get("house_dem_seats")))
        except Exception:
            log.exception("Couldn't announce a website save by %s", uid)


def remember_names(guild):
    """Keep everyone's server name where the website can read it: it has no other way to turn an ID into a name."""
    if guild is None:
        return
    names = {str(m.id): m.display_name for m in guild.members if not m.bot and (is_junkie(m) or m.id in set(store.predictors()))}
    if names and names != store.setting("names", {}):
        store.set_setting("names", {**store.setting("names", {}), **names})
    if store.setting("server_name") != guild.name:
        store.set_setting("server_name", guild.name)


async def picks_reminder(guild, t: datetime):
    """A week, a day and three hours before picks close: who has filled in how much, and who hasn't started. In 2026
    three of six people skipped the House seats, and those seats decided the game."""
    if not CFG.get("prediction_reminders", True):
        return
    sent = {k for k in (f"picks-reminder-{lead}" for lead, _ in S.REMINDERS) if store.alert_sent(k)}
    due = S.reminder_due(t.timestamp(), deadline().timestamp(), sent)
    if not due:
        return
    key, lead = due
    rows = []
    for uid in store.predictors():
        picks, meta = store.user_predictions(uid)
        c = S.completion(picks, meta)
        todo = ", ".join(S.PART_NAMES[m] for m in c["missing"])
        rows.append((c["share"], f"**{name_of(guild, uid)}** {c['share']:.0%}" + (f" · still open: {todo}" if todo else " ✅")))
    lines = [text for _share, text in sorted(rows, key=lambda r: -r[0])]
    absent = sorted(name_of(guild, u) for u in counted_voters(guild) - set(store.predictors()))
    if absent and not predictions_hidden():
        lines.append(f"**Not started:** {', '.join(absent)}")
    e = discord.Embed(title=f"Predictions close in {lead}", color=0xFAD63E, description=_trim("\n".join(lines), 4000)
                      or "Nobody has saved predictions yet.")
    e.set_footer(text="/predict to fill in the rest. Every part scores: Senate, House majority, governors, House seats, "
                      "and the tiebreaker.")
    await send_alert(guild, key, embed=e)


async def early_vote_digest(guild, t: datetime):
    """Once a morning until Election Day: the early vote in every Senate state County Watch follows, against 2022,
    with the change since the last post. The numbers are whatever the six-hourly pull last brought in."""
    hour = CFG.get("early_vote_digest_hour", 9)
    et = t.astimezone(N.ET)
    if hour is None or et.hour < int(hour) or et >= N.ELECTION_DAY.replace(hour=18):
        return
    key = f"early-vote-{et.date().isoformat()}"
    if store.alert_sent(key):
        return
    pace = [p for p in (CWR.early_pace() or []) if p[0] in CM.STATES]
    if not pace:
        return                      # nothing loaded yet: try again on the next tick rather than post an empty card
    last = store.setting("early_vote_last", {})
    days = (N.ELECTION_DAY.date() - et.date()).days
    e = discord.Embed(title="Early vote so far · " + ("Election Day" if days == 0 else f"{days} day{'s' * (days != 1)} out"),
                      color=EMBED_COLOR[None], description=_trim("\n".join(CE.digest(pace, last, CM.STATES)), 4000))
    e.set_footer(text="UF Election Lab early vote tracker. Registration gaps are not votes. /earlyvote for one state.")
    await send_alert(guild, key, embed=e)
    store.set_setting("early_vote_last", {st: votes for st, votes, _share, _gap in pace})


@tree.command(name="closings", description="Poll closing schedule for election night.")
@app_commands.guild_only()
@app_commands.describe(preview="Post the next closing alert now, as a preview")
async def closings_cmd(interaction: discord.Interaction, preview: bool = False):
    t = now()
    if preview:
        upcoming = [(w, s) for w, s in N.waves() if w > t] or N.waves()
        return await interaction.response.send_message(embed=wave_embed(*upcoming[0], preview=True))
    lines = [f"{'✅' if w <= t else '⏳'} **{N.fmt(w, TZ)}** — {', '.join(states)}" for w, states in N.waves()]
    e = discord.Embed(title="Poll closings, Nov 3", description="\n".join(lines), color=0x2B2D31)
    e.set_footer(text="When most of each state's polls close. Source: The Green Papers.")
    await interaction.response.send_message(embed=e)


@tree.command(name="next", description="What to watch: races where polls closed and the desk hasn't called.")
@app_commands.guild_only()
async def next_cmd(interaction: discord.Interaction):
    t = now()
    calls = store.all()
    pros = {p["race_id"]: p for p in store.procalls()}
    ready = sorted((N.first_close(r["state"]), rid) for rid, r in R.RACES.items()
                   if r["type"] in ("senate", "governor") or rid in CMP.RATING
                   if rid not in calls and r["state"] and N.is_closed(r["state"], t))
    lines = []
    for when, rid in ready:
        votes = store.active_votes(rid, "call")
        extra = ""
        if votes:
            tally: dict[str, int] = {}
            for p in votes.values():
                tally[p] = tally.get(p, 0) + 1
            extra += " · votes " + " ".join(f"{S.EMOJI[p]}{n}" for p, n in sorted(tally.items()))
        if rid in pros:
            extra += f" · **{pros[rid]['outlet']} called {pros[rid]['party']}**"
        lines.append(f"`{clock(when)}` {rid}{extra}")
    e = discord.Embed(title="Closed and uncalled", color=0x2B2D31,
                      description="\n".join(lines)[:4000] if lines else "Nothing closed and uncalled right now.")
    upcoming = [(w, s) for w, s in N.waves() if w > t]
    if upcoming:
        e.add_field(name="Next closing", value=f"{N.fmt(upcoming[0][0], TZ)}: {', '.join(upcoming[0][1])}")
    await interaction.response.send_message(embed=e)


# =========================================================================== info
@tree.command(name="earlywarning",
              description="Early-closing House seats that tell you how the night is going.")
@app_commands.guild_only()
async def earlywarning_cmd(interaction: discord.Interaction):
    rows = S.early_watch(results())
    by_time: dict[str, list[str]] = {}
    held = lost = gained = 0
    for rid, rating, fav, won in rows:
        when = N.fmt(N.first_close(rid[:2]), TZ)
        if won is None:
            status = "⏳"
        elif fav and won == fav:
            status = f"✅ {S.EMOJI[won]} held"
            held += 1 if fav == "D" else 0
        elif fav and won != fav:
            status = f"🚨 {S.EMOJI[won]} upset"
            lost += 1 if fav == "D" else 0
            gained += 1 if won == "D" else 0
        else:
            status = f"{S.EMOJI[won]} won"
            gained += 1 if won == "D" else 0
        by_time.setdefault(when, []).append(f"`{rid}` {rating} ({CMP.HOLDER_OF[rid]}-held) · {status}")
    e = discord.Embed(title="Early warning seats", color=0xFAD63E,
                      description="Rated House seats in states that close by 7:30 PM ET. Dems losing Tilt/Lean D seats "
                                  "here is the first sign of a bad night for them; Dems winning toss-ups and Tilt/Lean R "
                                  "seats is the first sign of a wave. Ratings: avg. of Cook, Inside Elections and "
                                  "Sabato, Oct 23.")
    for when, lines in by_time.items():
        e.add_field(name=when, value="\n".join(lines)[:1024], inline=False)
    called = sum(1 for r in rows if r[3])
    if called:
        e.add_field(name="So far", inline=False,
                    value=f"{called}/{len(rows)} called · D-favored seats lost: {lost} · "
                          f"D wins in toss-up/R-leaning seats: {gained}\n{S.house_tracker_text(results())}")
    await interaction.response.send_message(embed=e)


@tree.command(name="map", description="Post the current official map.")
@app_commands.guild_only()
@app_commands.choices(chamber=[app_commands.Choice(name="Senate", value="senate"),
                               app_commands.Choice(name="Governors", value="governor"),
                               app_commands.Choice(name="House", value="house")])
async def map_cmd(interaction: discord.Interaction, chamber: app_commands.Choice[str]):
    await interaction.response.defer()
    await interaction.followup.send(content=S.path_text(chamber.value, store.all()), file=await map_file(chamber.value))


@tree.command(name="race", description="Everything about one race: votes, official call, pro calls.")
@app_commands.guild_only()
@app_commands.autocomplete(race=race_autocomplete)
async def race_cmd(interaction: discord.Interaction, race: str):
    rid = resolve(race)
    if not rid:
        return await interaction.response.send_message(f"I don't recognize `{race}`.", ephemeral=True)
    guild = interaction.guild
    r, official = R.RACES[rid], store.get(rid)
    e = discord.Embed(title=r["label"], color=EMBED_COLOR[official["party"] if official else None])
    h = S.holder(rid)
    if h:
        e.add_field(name="Held by", value=R.PARTY_NAMES[h])
    if rid in CMP.RATING_OF:
        e.add_field(name="Rating (avg)", value=CMP.RATING_OF[rid])
    e.add_field(name="Official", value=f"{S.EMOJI[official['party']]} {cand(rid, official['party'])} · "
                f"{clock(official['called_at'])}" if official else "Not called")
    votes = store.active_votes(rid, "call")
    if votes:
        by: dict[str, list[str]] = {}
        for uid, p in votes.items():
            by.setdefault(p, []).append(name_of(guild, uid))
        e.add_field(name=f"Votes (need {votes_needed(guild)})", inline=False,
                    value="\n".join(f"{S.EMOJI[p]} {cand(rid, p)}: {', '.join(n)}" for p, n in by.items()))
    pros = store.procalls(rid)
    if pros:
        e.add_field(name="Outlet calls", inline=False,
                    value="\n".join(f"{p['outlet']}: {S.EMOJI[p['party']]} at {clock(p['ts'])}" for p in pros))
    if r["state"]:
        e.add_field(name="Polls close", value=N.fmt(N.first_close(r["state"]), TZ))
    res = results().get(rid)
    if res:
        check = prediction_check_text(guild, rid, res)
        if check:
            e.add_field(name="Prediction check", value=check[:1024], inline=False)
    else:
        picks: dict[str, list[str]] = {}
        for uid in store.predictors():
            p = store.user_predictions(uid)[0].get(rid)
            if p:
                picks.setdefault(p, []).append(name_of(guild, uid))
        if picks:
            e.add_field(name="Who picked what", inline=False, value="\n".join(
                f"{S.EMOJI[p]} {cand(rid, p)} ({len(n)}): {', '.join(sorted(n, key=str.lower))}"
                for p, n in sorted(picks.items()))[:1024])
    c = load_candidates().get(rid)
    if c:
        e.add_field(name="Candidates", inline=False,
                    value="\n".join(f"{S.EMOJI.get(p, '')} {n}" for p, n in c.items()))
    await interaction.response.send_message(embed=e)


# =========================================================================== seat background & live results
def load_seat_info() -> dict:
    try:
        data = json.loads(packs.active().path("seat_info.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k.upper(): v for k, v in data.items() if not k.startswith("_")}


def _wiki_state(st: str) -> str:
    return R.STATE_NAMES[st].replace(" ", "_")


def race_links(rid: str) -> list[tuple[str, str]]:
    r = R.RACES[rid]
    st = r["state"]
    if not st:
        return [("Wikipedia", "https://en.wikipedia.org/wiki/2026_United_States_House_of_Representatives_elections")]
    name = _wiki_state(st)
    special = "special_" if r["type"] == "senate" and st in R.SENATE_SPECIAL else ""
    poss = "%27" if name.endswith("s") else "%27s"
    if r["type"] == "senate":
        wiki = f"https://en.wikipedia.org/wiki/2026_United_States_Senate_{special}election_in_{name}"
        bp = f"https://ballotpedia.org/United_States_Senate_{special}election_in_{name},_2026"
    elif r["type"] == "governor":
        wiki = f"https://en.wikipedia.org/wiki/2026_{name}_gubernatorial_election"
        bp = f"https://ballotpedia.org/{name}_gubernatorial_election,_2026"
    else:
        base = f"https://en.wikipedia.org/wiki/2026_United_States_House_of_Representatives_elections_in_{name}"
        if rid.endswith("-AL"):
            wiki = base
            bp = f"https://ballotpedia.org/{name}{poss}_At-Large_Congressional_District_election,_2026"
        else:
            n = int(rid.split("-")[1])
            wiki = f"{base}#District_{n}"
            bp = f"https://ballotpedia.org/{name}{poss}_{R._ordinal(n)}_Congressional_District_election,_2026"
    links = [("Wikipedia", wiki), ("Ballotpedia", bp)]
    page = LIVE.page_link(st)
    if page:
        links.append(("Official results", page))
    return links


def links_text(rid: str) -> str:
    return " · ".join(f"[{n}]({u})" for n, u in race_links(rid))


def ratings_math_line(rid: str) -> str | None:
    if rid not in CMP.RATING_OF:
        return None
    base, fav = CMP.D_BASELINE, CMP.favored(rid)
    if fav == "D":
        return f"A D win is already in the {base} baseline. An R win drops Dems to {base - 1}."
    if fav == "R":
        return f"An R win is already priced in. A D win lifts Dems to {base + 1}."
    return f"Toss-up, so neither side is counted in the {base}: a D win lifts Dems to {base + 1}."


def bar(share: float, width: int = 16) -> str:
    filled = max(0, min(width, round(share / 100 * width)))
    return "█" * filled + "░" * (width - filled)


def _surname(name: str) -> str:
    parts = [p for p in name.replace(",", " ").split() if p.upper() not in ("JR.", "JR", "SR.", "III", "II")]
    return parts[-1].title() if parts and parts[-1].isupper() else (parts[-1] if parts else name)


def live_line(race: dict) -> str:
    """One-line summary: '🔴 Luna +6.2 (8,412 votes) · 45% reporting'."""
    s = LIVE.summary(race)
    if not s["total"]:
        return "No votes counted yet."
    lead = s["lead"]
    out = f"{S.EMOJI.get(lead['party'], '⚪')} {_surname(lead['name'])} +{lead['points']:.1f} ({lead['votes']:,} votes)"
    if s["reporting_pct"] is not None:
        out += f" · {s['reporting_pct']:.0f}% " + ("of precincts with votes" if race.get("reporting_note") else "reporting")
    return out


def source_name(st: str) -> str:
    return (LIVE.sources().get("states", {}).get(st) or {}).get("source") or f"{R.STATE_NAMES[st]} results"


def results_embed(rid: str, race: dict) -> discord.Embed:
    st = R.RACES[rid]["state"]
    s = LIVE.summary(race)
    lead = s["lead"]
    e = discord.Embed(title=f"Live count · {label(rid)}", color=EMBED_COLOR[lead["party"] if lead and lead["party"] in "DRI" else None])
    if not s["total"]:
        e.description = "The file is up, but no votes are counted in this race yet."
    else:
        rows = [f"{S.EMOJI.get(c['party'], '⚪')} **{c['name'].title() if c['name'].isupper() else c['name']}**\n"
                f"`{bar(share)}` {share:.1f}% · {c['votes']:,}" for c, share in s["shares"][:4]]
        e.description = "\n".join(rows)
        e.add_field(name="Margin", value=live_line(race), inline=False)
    rep = race.get("reporting")
    if rep:
        what = "precincts with votes so far" if race.get("reporting_note") else "precincts reporting"
        e.add_field(name="Reporting", value=f"{rep[0]:,} of {rep[1]:,} {what}", inline=True)
    if rid in CMP.RATING_OF:
        e.add_field(name="Rated", value=CMP.RATING_OF[rid], inline=True)
    official = store.get(rid)
    e.add_field(name="Desk", inline=True, value=(f"Called: {S.EMOJI[official['party']]} {cand(rid, official['party'])}"
                                                if official else "Not called"))
    old = " · ⚠️ the site isn't answering, so this is the last count the bot got" if race.get("stale") else ""
    e.set_footer(text=f"{source_name(st)} · fetched {clock(race['fetched_at'])}{old} · unofficial count, not a call")
    return e


class RefreshView(ScopedView):
    def __init__(self, rid: str):
        super().__init__(timeout=1800)
        self.rid = rid

    @discord.ui.button(label="Refresh", emoji="🔄", style=discord.ButtonStyle.secondary)
    async def refresh(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await interaction.response.defer()
        try:
            race = await LIVE.race_result(self.rid)
        except Exception as exc:  # network trouble on the night shouldn't crash the button
            return await interaction.followup.send(f"Couldn't reach the results file: {exc}", ephemeral=True)
        await interaction.edit_original_response(embed=results_embed(self.rid, race), view=self)


def feed_error_text(st: str, exc: Exception) -> str:
    msg = str(exc)
    if "404" in msg or "403" in msg:
        return (f"{R.STATE_NAMES[st]}'s 2026 file isn't up yet ({msg.split(' from ')[0]}). States usually post it "
                f"once polls close at {N.fmt(N.first_close(st), TZ)}.")
    return f"Couldn't read {R.STATE_NAMES[st]}'s results file: {msg[:200]}"


@tree.command(name="seat", description="Background on a race: rating, incumbent, nominees, lean, poll close, links.")
@app_commands.guild_only()
@app_commands.describe(race="e.g. FL-13, PA-07, NC-SEN, GA-GOV")
@app_commands.autocomplete(race=race_autocomplete)
async def seat_cmd(interaction: discord.Interaction, race: str):
    rid = resolve(race)
    if not rid:
        return await interaction.response.send_message(f"I don't recognize `{race}`.", ephemeral=True)
    calls, res = store.all(), results()
    if rid == "US-HOUSE":
        e = discord.Embed(title="Control of the House", color=EMBED_COLOR[None],
                          description=f"{S.path_text('house', calls)}\n{S.house_tracker_text(res)}\n\n"
                                      f"`/earlywarning` lists the first rated seats to watch.")
        return await interaction.response.send_message(embed=e)
    await interaction.response.defer()
    r, info = R.RACES[rid], load_seat_info().get(rid, {})
    rating = CMP.RATING_OF.get(rid)
    safe = CMP.SAFE.get(rid)
    fav = CMP.favored(rid) if rating else safe
    title = label(rid) + (f" · {rating}" if rating else f" · Cook: Solid {safe}" if safe else "")
    e = discord.Embed(title=title, color=EMBED_COLOR[fav])
    lines = []
    if rid in CMP.EARLY_WATCH:
        lines.append("⚠️ **Early-warning seat.** Polls close by 7:30 PM ET, so this is one of the first reads of the night.")
    if rating:
        lines.append("-# Rating is Adam Carlson's Oct 23 average of Cook, Inside Elections and Sabato.")
    if info.get("note"):
        lines.append(info["note"])
    e.description = "\n".join(lines) or None

    h = S.holder(rid)
    if h:
        held = R.PARTY_NAMES[h]
        if info.get("incumbent"):
            held += f" · {info['incumbent']} is running"
        elif info.get("open"):
            held += f" · **open seat**: {info['open']}"
        e.add_field(name="Held by", value=held, inline=False)
    nominees = load_candidates().get(rid)
    e.add_field(name="Nominees", inline=True, value="\n".join(
        f"{S.EMOJI.get(p, '')} {n}" for p, n in nominees.items()) if nominees else "Not on file yet (candidates.json)")
    if info.get("pvi"):
        e.add_field(name="Partisan lean", value=f"{info['pvi']} (Cook PVI, 2026 map)", inline=True)
    st = r["state"]
    first, last = N.first_close(st), N.last_close(st)
    e.add_field(name="Polls close", inline=True, value=N.fmt(first, TZ) + (
        f"\nlast polls {N.fmt(last, TZ)}" if last != first else ""))
    math = ratings_math_line(rid)
    if math:
        e.add_field(name="Ratings math", value=math, inline=False)

    official = calls.get(rid)
    pros = store.procalls(rid)
    votes = store.active_votes(rid, "call")
    desk = (f"Called: {S.EMOJI[official]} {cand(rid, official)}" if official
            else f"{len(votes)} vote(s) to call, {votes_needed(interaction.guild)} needed" if votes else "Not called")
    if pros:
        desk += "\n" + " · ".join(f"{p['outlet']} {S.EMOJI[p['party']]}" for p in pros)
    e.add_field(name="Desk", value=desk, inline=True)
    if not predictions_hidden():
        split: dict[str, int] = {}
        for uid in store.predictors():
            p = store.user_predictions(uid)[0].get(rid)
            if p:
                split[p] = split.get(p, 0) + 1
        if split:
            e.add_field(name="Group picks", inline=True,
                        value=" · ".join(f"{S.EMOJI[p]} {n}" for p, n in sorted(split.items())))

    if LIVE.state_cfg(st):
        if N.is_closed(st, now()):
            try:
                live = await LIVE.race_result(rid)
                e.add_field(name="Live count", inline=False,
                            value=live_line(live) + f"\n-# `/results {rid}` for the full count")
            except Exception as exc:
                e.add_field(name="Live count", value=feed_error_text(st, exc), inline=False)
        else:
            e.add_field(name="Live count", inline=False,
                        value=f"`/results {rid}` pulls the official count once polls close.")
    e.add_field(name="Links", value=links_text(rid), inline=False)
    await interaction.followup.send(embed=e)


@tree.command(name="results", description="Live vote counts from official state results files (FL, NC, VA).")
@app_commands.guild_only()
@app_commands.describe(race="One race, e.g. FL-13 or NC-SEN. Leave blank for every tracked race with votes.")
@app_commands.autocomplete(race=race_autocomplete)
async def results_cmd(interaction: discord.Interaction, race: str | None = None):
    if race:
        rid = resolve(race)
        if not rid or rid == "US-HOUSE":
            return await interaction.response.send_message(f"I don't recognize `{race}` as a single race.",
                                                           ephemeral=True)
        st = R.RACES[rid]["state"]
        if not LIVE.state_cfg(st):
            have = ", ".join(LIVE.supported()) or "none"
            return await interaction.response.send_message(
                f"No live feed for {R.STATE_NAMES[st]} (the bot reads {have}).\n{links_text(rid)}",
                suppress_embeds=True)
        await interaction.response.defer()
        try:
            data = await LIVE.race_result(rid)
        except Exception as exc:
            return await interaction.followup.send(feed_error_text(st, exc))
        return await interaction.followup.send(embed=results_embed(rid, data), view=RefreshView(rid))

    await interaction.response.defer()
    tracked = set(CMP.RATING_OF) | {rid for rid, r in R.RACES.items() if r["type"] in ("senate", "governor")}
    e = discord.Embed(title="Live counts · tracked races", color=EMBED_COLOR[None])
    for st in LIVE.supported():
        try:
            data = await LIVE.fetch_state(st)
        except Exception as exc:
            e.add_field(name=R.STATE_NAMES[st], value=feed_error_text(st, exc), inline=False)
            continue
        rows = []
        for rid in sorted(data["races"], key=lambda x: (R.RACES[x]["type"] == "house", x)):
            if rid not in tracked:
                continue
            race = {**data["races"][rid], "fetched_at": data["fetched_at"]}
            if not LIVE.summary(race)["total"]:
                continue
            tag = f" ({CMP.RATING_OF[rid]})" if rid in CMP.RATING_OF else ""
            called = " ✅" if store.get(rid) else ""
            rows.append(f"`{rid}`{tag} {live_line(race)}{called}")
        old = " ⚠️ not refreshing" if data.get("stale") else ""
        e.add_field(name=f"{R.STATE_NAMES[st]} · fetched {clock(data['fetched_at'])}{old}", inline=False,
                    value="\n".join(rows)[:1024] if rows else "No votes in the file for tracked races yet.")
    e.set_footer(text="Unofficial counts from state results files · ✅ = desk has called it · /results FL-13 for one race")
    await interaction.followup.send(embed=e)


@tree.command(name="resultstest", description="Admin: check a state's results reader against its Nov 2024 file.")
@app_commands.guild_only()
@app_commands.choices(state=[app_commands.Choice(name=R.STATE_NAMES[s], value=s) for s in LIVE.supported()])
async def resultstest_cmd(interaction: discord.Interaction, state: app_commands.Choice[str]):
    if not is_admin(interaction.user):
        return await interaction.response.send_message("Admins only.", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    st = state.value
    try:
        data = await LIVE.fetch_state(st, test=True, force=True)
    except Exception as exc:
        return await interaction.followup.send(f"❌ {st}: {exc}", ephemeral=True)
    races = data["races"]
    if not races:
        return await interaction.followup.send(
            f"⚠️ {st}: downloaded {data['bytes'] / 1e6:.1f} MB but found no Senate, governor or House contests. "
            f"The file format may have changed; send me the first few lines of the file.", ephemeral=True)
    house = sorted(r for r in races if R.RACES[r]["type"] == "house")
    other = sorted(r for r in races if R.RACES[r]["type"] != "house")
    sample_id = house[0] if house else other[0]
    sample = races[sample_id]
    top = " vs ".join(f"{c['name']} ({c['party']}) {c['votes']:,}" for c in sample["cands"][:2])
    rep = sample.get("reporting")
    await interaction.followup.send(
        f"✅ **{st}** reader works on the 2024 file ({data['bytes'] / 1e6:.1f} MB).\n"
        f"Found {len(house)} House races" + (f" plus {', '.join(other)}" if other else "") + ".\n"
        f"Sample, {sample_id}: {top}" + (f" · precincts {rep[0]:,}/{rep[1]:,}" if rep else "") +
        "\n-# 2024 districts, so seat numbers line up with the old maps. The 2026 file appears on election night.",
        ephemeral=True)


@tree.command(name="board", description="Seat counts, path to majority, and recent official calls.")
@app_commands.guild_only()
async def board_cmd(interaction: discord.Interaction):
    calls = store.all()
    e = discord.Embed(title="Decision Desk tally", color=0x2B2D31)
    for kind, name in (("senate", "Senate"), ("governor", "Governors"), ("house", "House seats")):
        c = S.chamber_counts(kind, calls)
        e.add_field(name=name, value=f"D {c['D']} · R {c['R']}" + (f" · I {c['I']}" if c["I"] else "")
                    + f"\n{c['uncalled']} uncalled", inline=True)
    hc = calls.get("US-HOUSE")
    e.add_field(name="Path to majority", inline=False,
                value=f"{S.path_text('senate', calls)}\n{S.path_text('house', calls)}\n{S.house_tracker_text(results())}"
                      + (f"\nHouse control called: {S.EMOJI[hc]} {R.PARTY_NAMES[hc]}" if hc else ""))
    recent = store.recent(8)
    if recent:
        e.add_field(name="Recent", inline=False, value="\n".join(
            f"`{clock(ts)}` {rid} " + (f"called {S.EMOJI.get(p, '')}" if a == "call" else "retracted")
            for ts, a, rid, p in recent))
    await interaction.response.send_message(embed=e)


@tree.command(name="deskreset", description="Admin: wipe every call, vote and outlet call, and the points they gave (after testing).")
@app_commands.guild_only()
@app_commands.describe(confirm="Type RESET to confirm", include_predictions="Also wipe everyone's predictions")
async def deskreset_cmd(interaction: discord.Interaction, confirm: str, include_predictions: bool = False):
    refusal = wipe_refusal(interaction, confirm, "RESET")
    if refusal:
        return await interaction.response.send_message(refusal, ephemeral=True)
    await interaction.response.defer()
    gone = store.reset(include_predictions=include_predictions)
    reset_field()
    cw = await asyncio.to_thread(CWR.reset_night)
    _cw["dirty"] = True
    await interaction.followup.send(
        f"Cleared {cleared_text(gone)}, so nobody has points from them. Alerts, the final House seat count and the "
        "field's snapshots are cleared too" + (
            ", along with predictions." if include_predictions else ". Predictions kept.")
        + f" County Watch: {cw}. Numbers typed straight into the workbook's Manual columns stay; clear those by hand."
        + (" The field's automatic posts are still paused: `/fieldposts resume` turns them back on."
           if store.setting("field_paused", False) else ""))


@tree.command(name="cardtheme", description="Admin: change the look of official call cards.")
@app_commands.guild_only()
@app_commands.describe(theme="Replaces the saved art in assets/calls. Leave blank to bring that art back")
@app_commands.choices(theme=THEME_CHOICES)
async def cardtheme_cmd(interaction: discord.Interaction, theme: app_commands.Choice[str] | None = None):
    if not is_admin(interaction.user):
        return await interaction.response.send_message("Admins only.", ephemeral=True)
    store.set_setting("card_theme", theme.value if theme else None)
    store.set_setting("card_art", not theme)        # picking a theme means that look, not the saved art
    await interaction.response.defer(ephemeral=True)
    sample = await card_file("OH-SEN", "D", "Sample card")
    note = (f"Call cards now use **{card_theme()}**." if theme else
            f"Back to the saved art in assets/calls, and **{card_theme()}** for races without any.")
    await interaction.followup.send(f"{note} Here's a sample (OH-SEN, a flip):", file=sample, ephemeral=True)


@tree.command(name="cardpreview", description="See the call card for any race and winner, in any theme. Only you see it.")
@app_commands.guild_only()
@app_commands.describe(race="e.g. GA-SEN, PA-07, HOUSE", winner="Who wins on the card",
                       theme="Defaults to the current theme", retracted="Show the retraction card instead")
@app_commands.autocomplete(race=race_autocomplete)
@app_commands.choices(theme=THEME_CHOICES)
async def cardpreview_cmd(interaction: discord.Interaction, race: str, winner: Winner,
                          theme: app_commands.Choice[str] | None = None, retracted: bool = False):
    rid = resolve(race)
    if not rid:
        return await interaction.response.send_message(f"I don't recognize `{race}`.", ephemeral=True)
    if winner.value not in options_for(rid):
        return await interaction.response.send_message(no_such_winner(rid, winner.raw), ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    how = "Retracted by the desk (preview)" if retracted else "Called by the desk (preview)"
    card = await card_file(rid, winner.value, how, retracted=retracted, theme=theme.value if theme else None)
    await interaction.followup.send(f"Preview: {label(rid)} · {theme.value if theme else card_theme()} theme. "
                                    "Seat counts are the desk's current tally.", file=card, ephemeral=True)


# =========================================================================== the field
FIELD_CHOICES = [app_commands.Choice(name="Senate", value="senate"),
                 app_commands.Choice(name="House battleground", value="house")]
FIELD_TEST_CHOICES = [app_commands.Choice(name="Whole simulated night (timelapse)", value="night"),
                      app_commands.Choice(name="One simulated update (30 minutes of movement)", value="update"),
                      app_commands.Choice(name="Right now, real data", value="now")]


async def field_snapshot(kind: str) -> dict:
    """The field as it stands. House seats in FL, NC and VA use the state's live file once its polls close."""
    live = {}
    if kind == "house":
        for st in LIVE.supported():
            if now() >= N.first_close(st):
                try:
                    live.update((await LIVE.fetch_state(st))["races"])
                except Exception as exc:
                    log.info("field: no live file for %s yet (%s)", st, exc)
    return await asyncio.to_thread(F.snapshot, kind, dict(store.all()), live=live, when=now())


def field_file(buf, ext: str, kind: str) -> discord.File:
    return discord.File(buf, filename=f"{kind}_field.{ext}")


BOT_OUTLET = "The Bot"


async def bot_caller_pass(guild, snap: dict):
    """The desk's own bot: after each Senate field snapshot, log its call on any race whose projection has held
    long enough (autocaller.py). It goes on the board like an outlet's call and feeds /scoreboard, and it never
    makes anything official. Turn it off with "bot_caller": false in config.json."""
    if guild is None or not CFG.get("bot_caller", True):
        return
    try:
        snaps = await asyncio.to_thread(F.history, "senate")
        for rid, now_at in snap["races"].items():
            if now_at[2] != "proj" or any(p["outlet"] == BOT_OUTLET for p in store.procalls(rid)):
                continue
            call = AUTO.decide_now(TR.series(snaps, rid), rid)
            key = (now_at[4] if call.side == "D" else "R") if call else None
            if key in options_for(rid):
                await log_outlet_call(guild, rid, key, BOT_OUTLET, 0,
                                      source=f"The desk's own bot: the projection has held at {abs(call.margin):.1f} points "
                                             f"for {call.held:.0f} minutes. It's a voice to beat, never an official call.")
    except Exception:
        log.exception("The bot caller failed")


def desk_marks(rid: str, d_key: str) -> list[tuple[float, str, str | None]]:
    """Every time the desk called or retracted a race, for the chart: (time, "call" or "uncall", "D" or "R" side)."""
    rows = store.db.execute("SELECT ts, action, party FROM history WHERE race_id = ? ORDER BY id", (rid,)).fetchall()
    return [(ts, action, None if action == "uncall" else "D" if party == d_key else "R") for ts, action, party in rows]


@tree.command(name="trend", description="A Senate race through the night as a chart: projected margin, votes counted, and the desk's calls.")
@app_commands.guild_only()
@app_commands.autocomplete(race=race_autocomplete)
async def trend_cmd(interaction: discord.Interaction, race: str):
    rid = resolve(race)
    if not rid:
        return await interaction.response.send_message(f"I don't recognize `{race}`.", ephemeral=True)
    await interaction.response.defer()
    snaps = await asyncio.to_thread(F.history, "senate")
    points = TR.series(snaps, rid)
    d_key = next((s["races"][rid][4] for s in reversed(snaps) if rid in s["races"]), "D")
    names = (_surname(cand(rid, d_key)), _surname(cand(rid, "R")))
    bot = AUTO.decide(points, rid)
    buf = await asyncio.to_thread(TR.render, label(rid), points, names[0], names[1], desk_marks(rid, d_key),
                                  (bot.at, bot.side) if bot else None)
    if not buf:
        return await interaction.followup.send(
            f"There's no history for {label(rid)} yet. The chart is drawn from the Senate field's snapshots, which "
            "start when a state's votes do.", ephemeral=True)
    info = TR.summary(points)
    await interaction.followup.send(
        content=f"📈 **{label(rid)}** · projection ranged {TR._lean(info['low'], *names)} to {TR._lean(info['high'], *names)}"
                + (" and changed sides" if info["changed_sides"] else "") + f" · {info['votes']:,} votes counted",
        file=discord.File(buf, filename=f"{rid}_trend.png"))


REACTIONS = HERE / "assets" / "reactions"


def reaction_files() -> dict[str, Path]:
    """Every image in assets/reactions, by the name it's asked for: its file name without the ending."""
    kinds = (".png", ".jpg", ".jpeg", ".gif", ".webp")
    return {p.stem.lower().replace("_", " "): p for p in sorted(REACTIONS.glob("*")) if p.suffix.lower() in kinds}


async def reaction_autocomplete(_interaction: discord.Interaction, current: str):
    typed = current.strip().lower()
    return [app_commands.Choice(name=name, value=name) for name in reaction_files() if typed in name][:25]


@tree.command(name="react", description="Post a reaction image from the desk's collection.")
@app_commands.guild_only()
@app_commands.autocomplete(image=reaction_autocomplete)
async def react_cmd(interaction: discord.Interaction, image: str):
    files = reaction_files()
    path = files.get(image.strip().lower())
    if not path:
        have = ", ".join(files) or "none yet"
        return await interaction.response.send_message(
            f"There's no reaction called `{image}`. In the collection: {have[:1500]}.\n"
            "-# To add one, save an image into `assets/reactions/`. Its file name is what you type here.", ephemeral=True)
    await interaction.response.send_message(file=discord.File(str(path), filename=path.name))


@tree.command(name="drinkinggame", description="Admin: turn drinking-game lines on calls on or off for this server.")
@app_commands.guild_only()
@app_commands.choices(mode=[app_commands.Choice(name="on", value="on"), app_commands.Choice(name="off", value="off")])
async def drinkinggame_cmd(interaction: discord.Interaction, mode: app_commands.Choice[str] | None = None):
    if mode:
        if not is_admin(interaction.user):
            return await interaction.response.send_message("Admins only.", ephemeral=True)
        store.set_setting(DR.SETTING, mode.value == "on")
    state = "on" if DR.is_on(store) else "off"
    rules = "\n".join(f"**{event}:** {lines[0]}" for event, lines in DR.rules().items())
    await interaction.response.send_message(
        f"Drinking-game mode is **{state}** for this server." + (f"\n{rules.format(seat='the seat', outlet='an outlet', lead='a while', winner='the winner', place='the state')}"[:1700] if state == "on" else "")
        + "\n-# Water counts.", ephemeral=not mode)


@tree.command(name="field", description="Every race as a circle from safe D to safe R: margin and votes counted.")
@app_commands.describe(chamber="Senate (default) or the 82 rated House seats",
                       timelapse="Play the whole night so far instead of the latest movement")
@app_commands.choices(chamber=FIELD_CHOICES)
async def field_cmd(interaction: discord.Interaction, chamber: app_commands.Choice[str] | None = None,
                    timelapse: bool = False):
    kind = chamber.value if chamber else "senate"
    await interaction.response.defer(ephemeral=True)
    snap = await field_snapshot(kind)
    theme = F.palette(card_theme())
    last = store.setting(f"field_last_{kind}")
    if timelapse:
        buf, ext = await asyncio.to_thread(F.timelapse, F.history(kind) + [snap], theme)
        note = "The night so far."
    else:
        buf, ext = await asyncio.to_thread(F.render, snap, theme, last)
        note = f"Moving since the last post at {last['clock']}." if ext == "gif" else ""
    await interaction.followup.send(f"{F.TITLE[kind].title()} field · {snap['clock']}. {note}".strip(),
                                    file=field_file(buf, ext, kind), ephemeral=True)


@tree.command(name="fieldtest", description="Admin: try the field on a simulated night. Only you see it.")
@app_commands.guild_only()
@app_commands.describe(chamber="Senate (default) or the House battleground", view="What to show",
                       seed="Same number = same simulated night")
@app_commands.choices(chamber=FIELD_CHOICES, view=FIELD_TEST_CHOICES)
async def fieldtest_cmd(interaction: discord.Interaction, chamber: app_commands.Choice[str] | None = None,
                        view: app_commands.Choice[str] | None = None, seed: int | None = None):
    if not is_admin(interaction.user):
        return await interaction.response.send_message("Admins only.", ephemeral=True)
    kind, what = chamber.value if chamber else "senate", view.value if view else "night"
    await interaction.response.defer(ephemeral=True)
    theme = F.palette(card_theme())
    if what == "now":
        buf, ext = await asyncio.to_thread(F.render, await field_snapshot(kind), theme)
        note = "The real field right now (what `/field` shows)."
    else:
        seed = random.randint(1, 9999) if seed is None else seed
        night = await asyncio.to_thread(F.demo_night, kind, seed)
        if what == "night":
            buf, ext = await asyncio.to_thread(F.timelapse, night, theme)
            note = "A simulated night, 7 PM to 1 AM ET, as the end-of-night timelapse would play it."
        else:
            i = random.Random(seed).randint(2, len(night) - 2)
            buf, ext = await asyncio.to_thread(F.render, night[i], theme, night[i - 1])
            note = (f"One simulated update ({night[i - 1]['clock']} to {night[i]['clock']}), as the board gets it every "
                    f"{CFG.get('field_minutes', 30)} minutes on the night.")
        note += f" Real races and starting positions; counts, margins and calls are made up. Seed {seed}."
    await interaction.followup.send(note, file=field_file(buf, ext, kind), ephemeral=True)


@tree.command(name="fieldposts", description="Admin: pause or resume the field's automatic posts, or see which it is.")
@app_commands.guild_only()
@app_commands.choices(action=[app_commands.Choice(name="pause", value="pause"),
                              app_commands.Choice(name="resume", value="resume")])
async def fieldposts_cmd(interaction: discord.Interaction, action: app_commands.Choice[str] | None = None):
    if not is_admin(interaction.user):
        return await interaction.response.send_message("Admins only.", ephemeral=True)
    if action:
        store.set_setting("field_paused", action.value == "pause")
    if store.setting("field_paused", False):
        text = ("The field's automatic posts are **paused**. It still keeps track of the night, so the first post after "
                "you resume shows everything that moved, and the timelapse stays complete. `/field` still works.")
    else:
        text = (f"The field's automatic posts are **on**. On the night they go to {channel_note(interaction.guild, 'board_channel')} "
                f"every {CFG.get('field_minutes', 30)} minutes, or soon after a call.")
    await interaction.response.send_message(text, ephemeral=True)


@tasks.loop(seconds=60)
async def field_loop():
    """On the night: snapshot each field in config's field_auto every minute (kept when something moved, for the
    timelapse), post to the board every field_minutes, or within 5 minutes of a call. After 8 the morning after, post
    the night's timelapse once. /fieldposts pause holds the posts, and the snapshots carry on."""
    try:
        if not FEEDS.countywatch():
            return                      # the field is drawn from County Watch's projections
        kinds = [k for k in CFG.get("field_auto", ["senate"]) if k in F.KINDS]
        home = main_guild()
        if cw_night():
            await each_server(lambda guild: field_pass(guild, kinds, keep=guild == home))
        elif now() > N.morning_after():
            await each_server(lambda guild: field_timelapse(guild, kinds))
    except Exception:
        log.exception("field loop failed")


async def field_pass(guild, kinds: list[str], keep: bool):
    """One server's field: where every race stands with this server's own calls on it, posted when it's due. The
    night's history (the timelapse, /trend and the desk's bot read it) is kept from the home server's snapshots, since
    the projections in it are the same for everyone."""
    paused = store.setting("field_paused", False)
    for kind in kinds:
        snap = await field_snapshot(kind)
        if keep:
            await asyncio.to_thread(F.record, snap)
        if kind == "senate":
            await bot_caller_pass(guild, snap)
        if paused:
            continue
        last = store.setting(f"field_last_{kind}")
        since = time.time() - store.setting(f"field_at_{kind}", 0)
        called = lambda s: {rid for rid, v in s["races"].items() if v[3]}       # noqa: E731
        due = since >= 60 * CFG.get("field_minutes", 30) or (last and called(last) != called(snap) and since >= 300)
        if not due or not F.changed(last, snap):
            continue
        buf, ext = await asyncio.to_thread(F.render, snap, F.palette(card_theme()), last)
        head = f"📊 **{F.TITLE[kind].title()} field** · {snap['clock']}"
        await post(guild, "board_channel", content=head + (f" · moving since {last['clock']}" if last else ""),
                   file=field_file(buf, ext, kind))
        store.set_setting(f"field_last_{kind}", snap)
        store.set_setting(f"field_at_{kind}", time.time())


async def field_timelapse(guild, kinds: list[str]):
    """The morning after, once per server: the night on the field."""
    if store.setting("field_paused", False) or store.setting("field_timelapse_posted"):
        return
    store.set_setting("field_timelapse_posted", True)
    for kind in kinds:
        snaps = F.history(kind)
        if len(snaps) >= 2:
            buf, ext = await asyncio.to_thread(F.timelapse, snaps, F.palette(card_theme()))
            await post(guild, "board_channel", content=f"🎞️ **The night on the {F.TITLE[kind].lower()} field**",
                       file=field_file(buf, ext, kind))


def reset_field():
    """Forget the field's snapshots and posts (after a rehearsal)."""
    F.clear_history()
    for kind in F.KINDS:
        store.set_setting(f"field_last_{kind}", None)
        store.set_setting(f"field_at_{kind}", 0)
    store.set_setting("field_timelapse_posted", False)


# =========================================================================== County Watch
CW_STATE_CHOICES = [app_commands.Choice(name=CM.STATES[s], value=s) for s in CM.STATES]
_cw = {"early_at": 0.0, "live_at": 0.0, "status": "not run yet"}


def cw_night() -> bool:
    """Election-night window for live county pulls: from the first poll closings to 8 the morning after."""
    start, end = FEEDS.night_window()
    return start <= now() <= end


@tasks.loop(seconds=10)
async def county_loop():
    """Early vote every 6 hours before Election Day. On the night: every feed re-checked every
    refresh_seconds (30 by default), alerts posted the moment numbers move, workbook refreshed every
    workbook_seconds (120) so a slow rebuild never holds up the feeds."""
    try:
        if not FEEDS.countywatch():
            return                      # this pack's counties aren't modelled, so there's nothing to pull
        src = CF.load_sources()
        # stops when the first polls close: the NC pull takes ~20 s and would hold up live pulls on the night
        if (FEEDS.early_vote() and time.time() - _cw["early_at"] > 6 * 3600
                and now() < N.ELECTION_DAY.replace(hour=18)):
            _cw["early_at"] = time.time()
            await CWR.refresh_early()
            _cw["dirty"] = True
        if cw_night() and time.time() - _cw["live_at"] >= src.get("refresh_seconds", 30):
            _cw["live_at"] = time.time()
            cache = await CWR.refresh_live()
            for m in CWR.blocked_notices(cache):
                await post_everywhere("board_channel", content=m, allowed_mentions=NO_PINGS)
            _start_state_watch(cache)
            if cache.get("changed"):
                _cw["dirty"] = True
                if src.get("post_alerts", True):
                    for m in await asyncio.to_thread(CWR.alerts, cache, cache["changed"]):
                        await post_everywhere("board_channel", content=m, allowed_mentions=NO_PINGS)
        elif now() > FEEDS.night_window()[1]:
            # After the night, states with late ballots keep updating the workbook and the private /watchstate DMs. Nothing
            # goes to the board: the night's public alerts are over.
            extended = CWR.extended_states(src, now())
            if extended and time.time() - _cw["live_at"] >= min(extended.values()):
                _cw["live_at"] = time.time()
                cache = await CWR.refresh_live(only=set(extended))
                _start_state_watch(cache)
                if cache.get("changed"):
                    _cw["dirty"] = True
        loaded = await asyncio.to_thread(CWR.scan_inbox)
        for res in loaded:
            await _announce_file(main_guild(), res, "the grab button" if res.get("grab") else "the inbox folder")
        for m in CWR.workbook_notices():
            await post_everywhere("board_channel", content=m, allowed_mentions=NO_PINGS)
        busy = _cw.get("wb_task") and not _cw["wb_task"].done()
        # the shared workbook syncs even when no feed moved, so what people type into it reaches the bot
        if ((_cw.get("dirty") or CWR.CC.enabled()) and not busy
                and time.time() - _cw.get("wb_at", 0) >= src.get("workbook_seconds", 120)):
            _cw["dirty"], _cw["wb_at"] = False, time.time()

            async def _write():
                cache = await asyncio.to_thread(CWR.live_cache)
                _cw["status"] = await asyncio.to_thread(CWR.write_workbook, cache)
                log.info("County Watch: %s", _cw["status"])
                dms = _cw.get("dm_task")
                # one at a time: if sending is still going, the next update's message covers these numbers too
                if CWR.workbook_wrote(_cw["status"]) and (not dms or dms.done()):
                    _cw["dm_task"] = asyncio.create_task(each_server(lambda _guild: _send_results_dms(cache)))
            _cw["wb_task"] = asyncio.create_task(_write())   # runs alongside, never delays the next feed check
    except Exception:
        log.exception("County Watch loop failed")


async def _send_results_dms(cache: dict):
    """DM everyone who turned on /resultsdm each county whose numbers just reached the workbook. Runs on its own, so
    trouble here can't touch the workbook or the feeds. Only ever goes to people's DMs, never a channel."""
    try:
        if not store.setting("results_dm_users"):
            return
        new = await asyncio.to_thread(CWR.result_snapshot, cache)
        old = store.setting("results_dm_last")
        store.set_setting("results_dm_last", new)
        msgs = CWR.result_messages(old, new) if old is not None else []
        for uid in store.setting("results_dm_users", []) if msgs else []:
            try:
                user = client.get_user(uid) or await client.fetch_user(uid)
                for m in msgs:
                    await user.send(m, allowed_mentions=NO_PINGS)
                _cw.pop("dm_error", None)
            except discord.DiscordException as exc:
                _cw["dm_error"] = f"{datetime.now():%I:%M %p}: {exc}"[:200]
                log.warning("Results DM to %s failed: %s", uid, exc)
    except Exception:
        log.exception("Results DMs failed")


def _start_state_watch(cache: dict):
    watch = _cw.get("watch_task")
    if not watch or watch.done():       # each server's own watchers, from that server's records
        _cw["watch_task"] = asyncio.create_task(each_server(lambda _guild: _send_state_watch(cache)))


async def _send_state_watch(cache: dict):
    """DM whoever is watching a state: the full alert when its votes change, a county-by-county update when only % in moves. Runs on its
    own, so trouble here can't touch the feeds. Each DM compares against the last one sent. Only ever goes to DMs."""
    try:
        watch, last = store.setting("state_watch", {}), store.setting("state_watch_last", {})
        for st, users in watch.items():
            new = await asyncio.to_thread(CWR.state_snapshot, st, cache)
            old = last.get(st)
            if old is None:         # nothing to compare with yet: start from here, quietly
                last[st] = new
                store.set_setting("state_watch_last", last)
                continue
            if not users:
                continue
            if CWR.votes_moved(old, new):
                msgs = await asyncio.to_thread(CWR.state_watch_messages, st, old, new)
            else:
                moves = CWR.pct_moves(old, new)
                if not moves:
                    continue
                msgs = await asyncio.to_thread(CWR.pct_messages, st, old, new, moves)
            last[st] = new
            store.set_setting("state_watch_last", last)
            for uid in users:
                try:
                    user = client.get_user(uid) or await client.fetch_user(uid)
                    for m in msgs:
                        await user.send(m, allowed_mentions=NO_PINGS)
                    _cw.pop("watch_error", None)
                except discord.DiscordException as exc:
                    _cw["watch_error"] = f"{datetime.now():%I:%M %p}: {exc}"[:200]
                    log.warning("State watch DM to %s failed: %s", uid, exc)
    except Exception:
        log.exception("State watch DMs failed")


@tree.command(name="watchstate", description="Admin: DM you (and only you) when a state's Senate votes or % in change.")
@app_commands.guild_only()
@app_commands.choices(state=CW_STATE_CHOICES, turn=[app_commands.Choice(name="on", value="on"), app_commands.Choice(name="off", value="off")])
async def watchstate_cmd(interaction: discord.Interaction, state: app_commands.Choice[str], turn: app_commands.Choice[str] | None = None):
    if not is_admin(interaction.user):
        return await interaction.response.send_message("Admins only.", ephemeral=True)
    uid, st, watch = interaction.user.id, state.value, store.setting("state_watch", {})
    users = watch.get(st, [])
    if turn and turn.value == "off":
        watch[st] = [u for u in users if u != uid]
        store.set_setting("state_watch", {s: u for s, u in watch.items() if u})
        return await interaction.response.send_message(f"Alerts for **{state.name}** are **off** for you.", ephemeral=True)
    if turn:
        await interaction.response.defer(ephemeral=True)
        cache = await asyncio.to_thread(CWR.live_cache)
        now_in = await asyncio.to_thread(CWR.state_snapshot, st, cache)
        try:
            for m in await asyncio.to_thread(CWR.state_watch_messages, st, now_in, now_in):
                await interaction.user.send(m, allowed_mentions=NO_PINGS)
        except discord.DiscordException:
            return await interaction.followup.send(
                "I couldn't DM you, so these alerts are still off. Turn on direct messages from this server's members "
                "(server name → Privacy Settings), then run this again.", ephemeral=True)
        if uid not in users:
            watch[st] = users + [uid]
            store.set_setting("state_watch", watch)
        return await interaction.followup.send(f"Alerts for **{state.name}** are **on** for you. Nobody else sees them.", ephemeral=True)
    text = f"Alerts for **{state.name}** are **{'on' if uid in users else 'off'}** for you."
    if _cw.get("watch_error"):
        text += f"\n⚠️ Last send failed at {_cw['watch_error']}"
    await interaction.response.send_message(text, ephemeral=True)


@tree.command(name="resultsdm", description="Admin: DM you (and only you) each county's results as they reach the workbook.")
@app_commands.guild_only()
@app_commands.choices(turn=[app_commands.Choice(name="on", value="on"), app_commands.Choice(name="off", value="off")])
async def resultsdm_cmd(interaction: discord.Interaction, turn: app_commands.Choice[str] | None = None):
    if not is_admin(interaction.user):
        return await interaction.response.send_message("Admins only.", ephemeral=True)
    uid, users = interaction.user.id, store.setting("results_dm_users", [])
    if turn and turn.value == "off":
        users = [u for u in users if u != uid]
        store.set_setting("results_dm_users", users)
        if not users:
            store.set_setting("results_dm_last", None)
        return await interaction.response.send_message("Results DMs are **off** for you.", ephemeral=True)
    if turn:
        await interaction.response.defer(ephemeral=True)
        try:
            await interaction.user.send("🔔 Results DMs are on. Each time the workbook updates, you'll get the state, "
                                        "county, % in and results for every county that changed. `/resultsdm off` stops them.",
                                        allowed_mentions=NO_PINGS)
        except discord.DiscordException:
            return await interaction.followup.send(
                "I couldn't DM you, so results DMs are still off. Turn on direct messages from this server's members "
                "(server name → Privacy Settings), then run this again.", ephemeral=True)
        if not users:       # start from what's there now, rather than sending every county counted so far
            store.set_setting("results_dm_last", await asyncio.to_thread(CWR.result_snapshot))
        if uid not in users:
            store.set_setting("results_dm_users", users + [uid])
        return await interaction.followup.send(
            "Results DMs are **on** for you. Nobody else sees them, and counties already counted aren't sent again until "
            "their numbers change.", ephemeral=True)
    text = f"Results DMs are **{'on' if uid in users else 'off'}** for you."
    if _cw.get("dm_error"):
        text += f"\n⚠️ Last send failed at {_cw['dm_error']}"
    await interaction.response.send_message(text, ephemeral=True)


async def _announce_file(guild, res: dict, how: str):
    """Post what a loaded results file contained, then any alerts it triggers."""
    if res.get("error"):
        await post(guild, "board_channel", content=f"⚠️ Couldn't read `{res.get('file')}` from {how}: {res['error']}")
        return
    st = res["state"]
    if res.get("test_only"):
        await post(guild, "board_channel", content=(
            f"🧪 Grab button test for {CM.STATES[st]} worked: read {res['counties']} of {res['of']} counties "
            f"({res['votes']:,} votes). Polls haven't closed there yet, so these numbers weren't used."))
        return
    _cw["dirty"] = True
    grabbed = _cw.setdefault("grabbed", set())
    first_grab = res.get("grab") and st not in grabbed
    if not res.get("grab") or first_grab:      # repeat grab-button loads stay quiet; alerts still post
        if first_grab:
            grabbed.add(st)
        await post(guild, "board_channel", content=(
            f"📥 Loaded {CM.STATES[st]} results ({res['kind']}) from {how}: {res['counties']} of {res['of']} counties, "
            f"{res['votes']:,} votes, {res['complete']} fully counted."
            + (" Later grabs for this state load quietly." if first_grab else "")))
    if res.get("changed") and CF.load_sources().get("post_alerts", True):
        for m in await asyncio.to_thread(CWR.alerts, CWR.live_cache(), [st]):
            await post(guild, "board_channel", content=m)


@tree.command(name="countyfile", description="Load a results file (CSV, Excel, or a state's download) into County Watch.")
@app_commands.guild_only()
@app_commands.describe(file="The file you downloaded from the state's results site, or your own county table",
                       state="Only needed if the file name doesn't say the state")
@app_commands.choices(state=CW_STATE_CHOICES)
async def countyfile_cmd(interaction: discord.Interaction, file: discord.Attachment,
                         state: app_commands.Choice[str] | None = None):
    if not is_junkie(interaction.user):
        return await interaction.response.send_message("Only Election Junkies can load files.", ephemeral=True)
    if file.size > 60_000_000:
        return await interaction.response.send_message("That file is over 60 MB.", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    raw = await file.read()
    try:
        res = await asyncio.to_thread(CWR.load_file, raw, file.filename, state.value if state else None)
    except Exception as exc:
        return await interaction.followup.send(f"Couldn't read it: {exc}", ephemeral=True)
    res["file"] = file.filename
    await interaction.followup.send(
        f"Loaded {CM.STATES[res['state']]}: {res['counties']} counties, {res['votes']:,} votes.", ephemeral=True)
    await _announce_file(interaction.guild, res, f"{interaction.user.display_name}'s upload")


def _trim(text: str, n: int) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"


@tree.command(name="counties", description="County-by-county read of a Senate race: key counties, swing, projection.")
@app_commands.guild_only()
@app_commands.choices(state=CW_STATE_CHOICES)
async def counties_cmd(interaction: discord.Interaction, state: app_commands.Choice[str]):
    st = state.value
    await interaction.response.defer()
    cache = CWR.live_cache()
    p = await asyncio.to_thread(CWR.projection, st, cache)
    dn, rn = CWR.names()[st]
    lines = CM.read_lines(st, p, dn, rn)
    color = EMBED_COLOR["D" if (p["proj_m"] or 0) > 0 else "R"] if p["proj_m"] is not None else EMBED_COLOR[None]
    e = discord.Embed(title=f"{CM.STATES[st]} Senate · County Watch", color=color,
                      description=_trim("\n".join(f"• {l}" for l in lines), 4000))
    rows = CM.key_table(st, p)
    if p["counted"]:
        val = "\n".join(
            f"`{r['name'][:12]:<12}` {CM.fmt(r['live_m']) if r['live_m'] is not None else 'not in':>7} "
            f"vs {CM.fmt(r['breakeven'])} needed · "
            + (f"turnout {CM.idx_txt(r['t_index'])} → {CM.fmt(r['t_be'])} · done" if r["t_index"] is not None
               else 'done' if r['complete'] else format(r['est_in'], '.0%') + ' in')
            for r in rows)
    else:
        val = "\n".join(f"**{r['name']}** · 2024 {CM.fmt(r['m24'])} · break-even {CM.fmt(r['breakeven'])} · "
                        f"{r['share22']:.1%} of the vote\n-# {_trim(r['why'], 110)}" for r in rows)
    chunk, fields = "", []
    for line in val.split("\n" if p["counted"] else "\n**"):
        line = line if p["counted"] or line.startswith("**") else "**" + line
        if len(chunk) + len(line) + 1 > 1024:
            fields.append(chunk)
            chunk = ""
        chunk = (chunk + "\n" + line) if chunk else line
    fields.append(chunk)
    budget = 5800 - len(e.description or "") - len(e.title or "")
    for i, f in enumerate(fields[:4]):
        if budget - len(f) < 0:
            break
        budget -= len(f)
        e.add_field(name="Key counties" if i == 0 else "\u200b", value=_trim(f, 1024), inline=False)
    err = cache.get("errors", {}).get(st)
    src = (CF.load_sources().get("states", {}).get(st) or {}).get("source")
    foot = (f"Live: {src}" if src else "No live feed: enter counties by hand in the workbook") + \
           (f" · {err}" if err else "") + " · margins are two-party"
    e.set_footer(text=_trim(foot, 2000))
    await interaction.followup.send(embed=e)


@tree.command(name="needs", description="The vote still out in a Senate race, county by county, and what the trailing side needs of it.")
@app_commands.guild_only()
@app_commands.choices(state=CW_STATE_CHOICES)
@app_commands.describe(what_if="Optional: try a different break for the vote still out. Points toward the Democratic "
                               "side, e.g. 8, or -5 for the Republican side.")
async def needs_cmd(interaction: discord.Interaction, state: app_commands.Choice[str],
                    what_if: app_commands.Range[float, -100, 100] | None = None):
    st = state.value
    await interaction.response.defer()
    p = await asyncio.to_thread(CWR.projection, st, CWR.live_cache())
    dn, rn = CWR.names()[st]
    b = REM.breakdown(p)
    lead = b["final"] if b["left"] > 0.5 else b["lead"]
    e = discord.Embed(title=f"{CM.STATES[st]} Senate · the vote still out",
                      color=EMBED_COLOR["D" if lead > 0 else "R"] if b["counted"] else EMBED_COLOR[None],
                      description=_trim("\n".join(REM.lines(b, dn, rn, None if what_if is None else what_if / 100)), 4000))
    where = REM.county_lines(b, dn, rn)
    if where:
        e.add_field(name="Where it is", value=_trim("\n".join(where), 1024), inline=False)
    e.set_footer(text="The vote still out is County Watch's estimate, from turnout and swing in counties that have "
                      "finished. Unofficial, and never a call.")
    await interaction.followup.send(embed=e)


@tree.command(name="environment", description="The national read: swing by county type across every counted county, and early vote pace.")
@app_commands.guild_only()
async def environment_cmd(interaction: discord.Interaction):
    await interaction.response.defer()
    n = await asyncio.to_thread(CWR.national)
    e = discord.Embed(title="National read", color=EMBED_COLOR[None],
                      description=_trim("\n".join(f"• {l}" for l in CM.national_lines(n)), 4000))
    pace = CWR.early_pace()
    if pace:
        mine = [p for p in pace if p[0] in CM.STATES]
        e.add_field(name="Early vote vs 2022 total turnout", inline=False, value=_trim("\n".join(
            f"`{st}` {share:.0%} ({v:,})" + (f" · reg. {'D' if gap > 0 else 'R'}+{abs(gap) * 100:.0f}" if gap is not None else "")
            for st, v, share, gap in mine), 1024))
        e.set_footer(text="Early vote: UF Election Lab tracker. Registration gaps are not votes. Swing: fully counted counties only, two-party, vs 2024.")
    await interaction.followup.send(embed=e)


@tree.command(name="earlyvote", description="Early vote so far in a Senate state, compared with 2022.")
@app_commands.guild_only()
@app_commands.choices(state=CW_STATE_CHOICES)
async def earlyvote_cmd(interaction: discord.Interaction, state: app_commands.Choice[str]):
    st = state.value
    early = CWR.early_cache()
    row = (early.get("uf") or {}).get(st)
    lines = CE.uf_summary(row) if row else [early.get("uf_error") or "No early vote data loaded yet."]
    if st == "NH":
        lines = ["New Hampshire has no early voting, only absentee ballots, and doesn't publish running totals."]
    e = discord.Embed(title=f"{CM.STATES[st]} early vote", color=EMBED_COLOR[None],
                      description="\n".join(f"• {l}" for l in lines))
    if st == "NC" and early.get("nc"):
        e.add_field(name="North Carolina detail", inline=False,
                    value=_trim("\n".join(f"• {l}" for l in CWR.nc_summary(early["nc"])), 1024))
    e.set_footer(text=f"UF Election Lab early vote tracker (Michael McDonald) · updated {early.get('uf_fetched') or 'never'}"
                      + (" · NC: State Board of Elections absentee file" if st == "NC" else ""))
    await interaction.response.send_message(embed=e)


async def county_autocomplete(interaction: discord.Interaction, current: str):
    st = getattr(interaction.namespace, "state", None)
    rows = CM.load().get(st, []) if st in CM.STATES else []
    cur = current.lower()
    keys = [c for c in rows if c["key"]]
    rest = [c for c in rows if not c["key"]]
    hits = [c for c in keys + rest if cur in c["name"].lower()][:25]
    return [app_commands.Choice(name=("★ " if c["key"] else "") + c["name"], value=c["fips"]) for c in hits]


@tree.command(name="countyset", description="Enter a county's Senate vote count by hand (for states without a live feed).")
@app_commands.guild_only()
@app_commands.choices(state=CW_STATE_CHOICES)
@app_commands.autocomplete(county=county_autocomplete)
@app_commands.describe(county="Start typing; ★ = key county", dem="Votes for the Democratic side (Osborn in NE)",
                       rep="Votes for the Republican", other="Everyone else (optional)",
                       done="Fully counted? Only say yes when every precinct is in")
async def countyset_cmd(interaction: discord.Interaction, state: app_commands.Choice[str], county: str,
                        dem: app_commands.Range[int, 0], rep: app_commands.Range[int, 0],
                        other: app_commands.Range[int, 0] = 0, done: bool = False):
    if not is_junkie(interaction.user):
        return await interaction.response.send_message("Only Election Junkies can enter counts.", ephemeral=True)
    st = state.value
    row = next((c for c in CM.load()[st] if c["fips"] == county or c["name"].lower() == county.lower()), None)
    if not row:
        return await interaction.response.send_message(f"I can't find that county in {CM.STATES[st]}.", ephemeral=True)
    CWR.set_entry(row["fips"], dem, rep, other, done, interaction.user.display_name)
    _cw["dirty"] = True
    m = CM.m2(dem, rep)
    gap = (m - row["breakeven"]) if m is not None else None
    msg = (f"Saved {row['name']}, {st}: D {dem:,} · R {rep:,}" + (f" · other {other:,}" if other else "") +
           f" → {CM.fmt(m)}" + (f", {abs(gap) * 100:.1f} pts {'above' if gap >= 0 else 'below'} break-even" if gap is not None else "")
           + (" · marked fully counted" if done else " · partial (only fully counted counties feed the projection)"))
    await interaction.response.send_message(msg, ephemeral=True)
    if CF.load_sources().get("post_alerts", True):
        msgs = await asyncio.to_thread(CWR.alerts, CWR.live_cache(), [st])
        for t in msgs:
            await post(interaction.guild, "board_channel", content=t + f"\n-# entered by {interaction.user.display_name}",
                       allowed_mentions=NO_PINGS)


@tree.command(name="countyclear", description="Remove a county count entered with /countyset.")
@app_commands.guild_only()
@app_commands.choices(state=CW_STATE_CHOICES)
@app_commands.autocomplete(county=county_autocomplete)
async def countyclear_cmd(interaction: discord.Interaction, state: app_commands.Choice[str], county: str):
    if not is_junkie(interaction.user):
        return await interaction.response.send_message("Only Election Junkies can do that.", ephemeral=True)
    ok = CWR.clear_entry(county)
    _cw["dirty"] = True
    await interaction.response.send_message("Removed." if ok else "Nothing entered for that county.", ephemeral=True)


async def county_test(st: str) -> str:
    """One state's feed read from its test file, as a line that starts with ✅, ⚠️ or ❌."""
    try:
        res = await CF.fetch_state(st, test=True)
        base = {c["fips"]: c for c in CM.load()[st]}
        got = res["counties"]
        tcfg = (CF.load_sources().get("states", {}).get(st) or {}).get("test", {})
        # e.g. Virginia's 2025 governor race; Nebraska has no old file and its D side is Osborn, not Harris
        if not tcfg or tcfg.get("count_only") or "pres" not in tcfg.get("contest", "pres").lower():
            tot = sum(r["D"] + r["R"] + r["O"] for r in got.values())
            what = f"its {tcfg.get('election_id', 'test')} {tcfg.get('contest')} results" if tcfg.get("contest") else "the site"
            return (f"{'✅' if len(got) >= 0.95 * len(base) else '⚠️'} **{st}**: read {len(got)} of {len(base)} counties "
                    f"({tot:,} votes) from {what}.")
        off = [base[f]["name"] for f, r in got.items()
               if abs(r["D"] - base[f]["d24"]) > max(50, 0.01 * base[f]["d24"]) or abs(r["R"] - base[f]["r24"]) > max(50, 0.01 * base[f]["r24"])]
        return (f"{'✅' if got and not off else '⚠️'} **{st}**: read {len(got)} of {len(base)} counties from the 2024 file; "
                f"{len(off)} differ from the published 2024 results" + (f" ({', '.join(off[:5])})" if off else "") + ".")
    except Exception as exc:
        return f"❌ **{st}**: {str(exc)[:150] or type(exc).__name__}"


async def county_nov3(st: str) -> str:
    """Whether this state's Nov 3 source answers yet. Before polls close, most won't, and that's fine."""
    try:
        res = await CF.fetch_state(st)
        return f"Nov 3 source answers ({len(res['counties'])} counties)"
    except Exception as exc:
        return f"Nov 3 source: {str(exc)[:90] or type(exc).__name__}"


async def county_test_all() -> list[str]:
    """Every feed at once, as messages short enough to send."""
    states = CF.feed_states()
    tests, nov3 = await asyncio.gather(asyncio.gather(*map(county_test, states)), asyncio.gather(*map(county_nov3, states)))
    marks = [t.split(" ", 1)[0] for t in tests]
    lines = [f"**All {len(states)} feeds:** ✅ {marks.count('✅')} · ⚠️ {marks.count('⚠️')} · ❌ {marks.count('❌')}\n"
             "-# The first part reads old results, to prove the bot can read each format. The Nov 3 part shows what's "
             "up now; before polls close, a 404 or \"hasn't posted\" is normal."]
    lines += [f"{t}\n-# {n}" for t, n in zip(tests, nov3)]
    msgs = [""]
    for line in lines:
        if len(msgs[-1]) + len(line) > 1900:
            msgs.append("")
        msgs[-1] += line + "\n"
    return msgs


@tree.command(name="countytest", description="Admin: check a county feed against an old results file, or all of them at once.")
@app_commands.guild_only()
@app_commands.choices(state=[app_commands.Choice(name="All states", value="ALL")] +
                      [app_commands.Choice(name=CM.STATES[s], value=s) for s in CM.STATES])
async def countytest_cmd(interaction: discord.Interaction, state: app_commands.Choice[str]):
    if not is_admin(interaction.user):
        return await interaction.response.send_message("Admins only.", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    if state.value != "ALL":
        return await interaction.followup.send(await county_test(state.value), ephemeral=True)
    for msg in await county_test_all():
        await interaction.followup.send(msg, ephemeral=True)


@tree.command(name="countyfeed", description="Admin: point a state's county feed at its results link (Clarity sites), or clear it.")
@app_commands.guild_only()
@app_commands.choices(state=CW_STATE_CHOICES)
@app_commands.describe(link="The state's election results link, e.g. https://www.enr-scvotes.org/SC/127500/ (or 'clear')")
async def countyfeed_cmd(interaction: discord.Interaction, state: app_commands.Choice[str], link: str):
    if not is_admin(interaction.user):
        return await interaction.response.send_message("Admins only.", ephemeral=True)
    st = state.value
    if link.strip().lower() == "clear":
        CF.set_override(st, None)
        return await interaction.response.send_message(f"Cleared the {st} feed override.", ephemeral=True)
    cfg = CF.clarity_from_link(link)
    if not cfg:
        return await interaction.response.send_message(
            "That doesn't look like a Clarity results link (…/SC/123456/…). Other sites are set in countywatch/feeds.json.",
            ephemeral=True)
    CF.set_override(st, {**cfg, "contest_re": (CF.load_sources().get("states", {}).get(st) or {}).get("contest_re", CF.US_SENATE_RE)})
    await interaction.response.defer(ephemeral=True)
    try:
        res = await CF.fetch_state(st)
        msg = f"✅ {st} now reads {cfg['base']}/{cfg['eid']}: {len(res['counties'])} counties found."
    except Exception as exc:
        msg = f"Saved {st} → election {cfg['eid']}, but the first pull failed: {exc}"
    await interaction.followup.send(msg, ephemeral=True)


@tree.command(name="countyrefresh", description="Admin: pull county feeds and early vote now, and rebuild the workbook.")
@app_commands.guild_only()
async def countyrefresh_cmd(interaction: discord.Interaction):
    if not is_admin(interaction.user):
        return await interaction.response.send_message("Admins only.", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    early = await CWR.refresh_early()
    live = await CWR.refresh_live()
    _cw["early_at"] = _cw["live_at"] = time.time()
    _cw["status"] = await asyncio.to_thread(CWR.write_workbook)
    errs = "; ".join(f"{k}: {v}" for k, v in live.get("errors", {}).items())
    await interaction.followup.send(
        f"Early vote: {'ok' if early.get('uf') else early.get('uf_error')} · NC file: "
        f"{'ok' if early.get('nc') else early.get('nc_error')}\nLive feeds: {errs or 'all ok'}\nWorkbook: {_cw['status']}",
        ephemeral=True)


def county_card(link: str) -> dict:
    """send() arguments for the workbook card. A OneDrive link gets no preview in Discord, so this stands in for one.
    The picture is a snapshot from before results (assets/county_watch_card.png), and the footer says so."""
    e = discord.Embed(title=f"County Watch {N.ELECTION_DAY.year}: live workbook", url=link, color=0x1F2A44,
                      description="Live county-by-county numbers for every Senate race we're watching. It updates every couple of "
                                  "minutes once polls close. View only. If you're new to this, read the Start Here tab first.")
    e.set_footer(text="Picture is a preview from before results, based on final polls. Open the workbook for live numbers.")
    view = discord.ui.View()
    view.add_item(discord.ui.Button(label="Open the workbook", url=link))
    kwargs = {"embed": e, "view": view}
    if COUNTY_CARD.exists():
        e.set_image(url=f"attachment://{COUNTY_CARD.name}")
        kwargs["file"] = discord.File(COUNTY_CARD, filename=COUNTY_CARD.name)
    return kwargs


@tree.command(name="excellink", description="Admin: post a card here with the read-only link to the live workbook.")
@app_commands.guild_only()
async def excellink_cmd(interaction: discord.Interaction):
    if not is_admin(interaction.user):
        return await interaction.response.send_message("Admins only.", ephemeral=True)
    link = os.getenv("CLOUD_VIEW_LINK")
    if not link:
        return await interaction.response.send_message(
            "No link yet. Run `python cw_cloud.py share --view`, put the link in .env as CLOUD_VIEW_LINK, and restart "
            "the bot.", ephemeral=True)
    await interaction.response.send_message(allowed_mentions=NO_PINGS, **county_card(link))


# =========================================================================== help
def help_embed(interaction: discord.Interaction, key: str) -> discord.Embed:
    guild, user = interaction.guild, interaction.user
    title, body = H.section(key, role=CFG["junkie_role"], board=channel_note(guild, "board_channel"),
                            desk=channel_note(guild, "desk_channel"), junkie=is_junkie(user),
                            admin=is_admin(user), need=votes_needed(guild))
    e = discord.Embed(title=f"📖 Election night guide · {title}", description=_trim(body, 4096), color=0xFAD63E)
    e.set_footer(text="Only you can see this. Switch sections with the menu.")
    return e


class HelpView(ScopedView):
    def __init__(self, admin: bool, current: str):
        super().__init__(timeout=30 * 60)
        self.menu.options = [discord.SelectOption(label=label, value=key, description=desc, default=key == current)
                             for key, label, desc in H.sections_for(admin)]

    @discord.ui.select(placeholder="Pick a section")
    async def menu(self, interaction: discord.Interaction, select: discord.ui.Select):
        key = select.values[0]
        for opt in select.options:
            opt.default = opt.value == key
        await interaction.response.edit_message(embed=help_embed(interaction, key), view=self)


@tree.command(name="help", description="What each election night command does. Only you see it.")
@app_commands.guild_only()
@app_commands.describe(section="Jump straight to a section (default: Start here)")
@app_commands.choices(section=[app_commands.Choice(name=label, value=key) for key, label, _ in H.SECTIONS])
async def help_cmd(interaction: discord.Interaction, section: app_commands.Choice[str] | None = None):
    admin = is_admin(interaction.user)
    key = section.value if section else "start"
    if key == "admin" and not admin:
        key = "start"
    await interaction.response.send_message(embed=help_embed(interaction, key), view=HelpView(admin, key),
                                            ephemeral=True)


# =========================================================================== startup
@tree.error
async def on_app_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    log.exception("command failed", exc_info=error)
    cause = getattr(error, "original", error)
    capture.event("error", where=f"/{interaction.command.name}" if interaction.command else "a command",
                  user=interaction.user.id, error=f"{type(cause).__name__}: {cause}"[:300])
    msg = "Something went wrong running that command."
    try:
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
    except discord.HTTPException:       # the command took too long and Discord dropped it: there's nobody to tell
        log.warning("Couldn't tell the user their command failed")


@client.event
async def on_interaction(interaction: discord.Interaction):
    """Every command and button press goes in the capture log with what was typed, so a night can be replayed."""
    data = interaction.data or {}
    if interaction.type == discord.InteractionType.application_command:
        capture.event("command", user=interaction.user.id, name=data.get("name"),
                      options={o.get("name"): o.get("value") for o in data.get("options", [])})
    elif interaction.type == discord.InteractionType.component:
        capture.event("button", user=interaction.user.id, id=data.get("custom_id"))


@client.event
async def setup_hook():
    client.add_dynamic_items(AlsoCall)
    gid = os.getenv("GUILD_ID")
    if gid:
        await sync_commands(discord.Object(id=int(gid)))
    else:
        synced = await tree.sync()
        log.info("Synced %d commands everywhere (Discord can take up to an hour to show them)", len(synced))


_synced: set[int] = set()


async def sync_commands(guild) -> int:
    """Give one server the commands, which shows them there at once. With GUILD_ID set, every server the bot is in
    gets them this way: the home server at startup, the others as the bot finds itself in them."""
    if guild.id in _synced:
        return 0
    _synced.add(guild.id)
    try:
        tree.copy_global_to(guild=guild)
        synced = await tree.sync(guild=guild)
    except discord.HTTPException as exc:
        _synced.discard(guild.id)
        log.warning("Couldn't give the commands to server %s: %s", guild.id, exc)
        return 0
    log.info("Synced %d commands to server %s", len(synced), getattr(guild, "name", guild.id))
    return len(synced)


def server_report(guild) -> list[str]:
    """What this server still needs before the desk can work in it, in words. Empty when it's ready."""
    out = []
    me = guild.me
    for key in ("board_channel", "desk_channel"):
        ch = find_channel(guild, key)
        if not ch:
            out.append(f"no #{CFG.get(key)} channel (create it, or set {key} in config.json)")
            continue
        perms = ch.permissions_for(me) if me else None
        lacking = [name for name, ok in (("View Channel", perms.view_channel), ("Send Messages", perms.send_messages),
                                         ("Embed Links", perms.embed_links), ("Attach Files", perms.attach_files))
                   if not ok] if perms else []
        if lacking:
            out.append(f"in #{ch.name} the bot can't: {', '.join(lacking)}")
    role = discord.utils.get(guild.roles, name=CFG["junkie_role"])
    if not role:
        out.append(f'no "{CFG["junkie_role"]}" role (only admins can vote on calls until there is one)')
    elif not role.members:
        out.append(f'nobody has the "{CFG["junkie_role"]}" role yet')
    return out


@client.event
async def on_guild_join(guild):
    if os.getenv("GUILD_ID"):
        await sync_commands(guild)
    for line in server_report(guild):
        log.warning("%s: %s", guild.name, line)


@client.event
async def on_ready():
    log.info("Logged in as %s", client.user)
    g = main_guild()
    if os.getenv("DESK_FAKE_NOW"):
        # A rehearsal clock left in .env freezes the bot at that moment for the whole real night.
        msg = (f"⚠️ **The bot is running on a fake clock** (DESK_FAKE_NOW={os.getenv('DESK_FAKE_NOW')} in .env). "
               "Remove that line and restart before election night, or nothing will run on time.")
        log.warning(msg)
        real = datetime.now(N.ET)
        if N.ELECTION_DAY.replace(hour=0) <= real <= N.morning_after(12) and g:
            await post(g, "board_channel", content=msg)
    log.info("Running %s. Records: %s. Automatic calls start %s.", packs.active().name, packs.data_root(),
             "on" if AUTOCALLS_DEFAULT else "off (DESK_AUTOCALLS=off)")
    for guild in client.guilds:
        if os.getenv("GUILD_ID"):
            await sync_commands(guild)
        problems = server_report(guild)
        log.info("Server %s (%s): %s", guild.name, guild.id, "ready" if not problems else f"{len(problems)} thing(s) to fix")
        for line in problems:
            log.warning("%s: %s", guild.name, line)
    if not alert_loop.is_running():
        alert_loop.start()
    if not county_loop.is_running():
        county_loop.start()
    if not field_loop.is_running():
        field_loop.start()
    if not bluesky_loop.is_running():
        bluesky_loop.start()
    if not nyt_loop.is_running():
        nyt_loop.start()
    if not ddhq_loop.is_running():
        ddhq_loop.start()
    if not safe_batch_loop.is_running():
        safe_batch_loop.start()


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise SystemExit("DISCORD_TOKEN is missing. Copy .env.example to .env and fill it in.")
    client.run(token, log_handler=None)


if __name__ == "__main__":
    main()

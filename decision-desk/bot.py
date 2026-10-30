"""Decision Desk bot.

Before election night: /predict opens a clickable picker (Senate, House majority, optional
governors and battleground House seats) and /leaderboard scores everyone once results come in.

On election night: /call is one person's vote to call a race. Each vote is announced in
#decision-desk-board. When a majority of the Election Junkie role agrees, the bot posts the
official call (card + map) in #decision-desk. /realcall makes a call official without a
majority. /procall logs an outlet's call (AP, NYT, Wasserman...) for the scoreboard.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import tasks
from dotenv import load_dotenv

import competitive as CMP
import countywatch_run as CWR
import cw_early as CE
import cw_feeds as CF
import cw_model as CM
import graphics as G
import night as N
import races as R
import results as LIVE
import scoring as S
from store import Store

HERE = Path(__file__).parent
load_dotenv(HERE / ".env")
CFG = json.loads((HERE / "config.json").read_text(encoding="utf-8"))
TZ = ZoneInfo(CFG.get("timezone", "America/Chicago"))
log = logging.getLogger("desk")
NO_PINGS = discord.AllowedMentions.none()

intents = discord.Intents.default()
intents.members = True  # needed to know who holds the Election Junkie role
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)
store = Store(str(HERE / "desk.db"))

PARTY_CHOICES = [
    app_commands.Choice(name="Democrat", value="D"),
    app_commands.Choice(name="Republican", value="R"),
    app_commands.Choice(name="Independent", value="I"),
]
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
    p = HERE / "candidates.json"
    if not p.exists():
        return {}
    data = json.loads(p.read_text(encoding="utf-8"))
    return {k.upper(): {p: n for p, n in v.items() if n} for k, v in data.items() if not k.startswith("_")}


def cand(rid: str, party: str) -> str:
    return load_candidates().get(rid, {}).get(party) or DEFAULT_NAME[party]


def options_for(rid: str) -> list[str]:
    """Parties offered in the picker: D and R, plus I where candidates.json names one."""
    return ["D", "R"] + (["I"] if "I" in load_candidates().get(rid, {}) else [])


def flavor(kind: str, **fmt) -> str | None:
    try:
        lines = json.loads((HERE / "flavor.json").read_text(encoding="utf-8")).get(kind) or []
    except (OSError, ValueError):
        return None
    if not lines:
        return None
    line = random.choice(lines)
    try:
        return line.format(**fmt)
    except (KeyError, IndexError, ValueError):
        return line


def race_flavor(rid: str, party: str) -> str | None:
    """A race-specific line from flavor.json's "races" section, if one exists for this winner."""
    try:
        races = json.loads((HERE / "flavor.json").read_text(encoding="utf-8")).get("races") or {}
    except (OSError, ValueError):
        return None
    lines = (races.get(rid) or {}).get(party) or []
    return random.choice(lines) if lines else None


def resolve(race: str) -> str | None:
    return race if race in R.RACES else R.normalize(race)


def label(rid: str) -> str:
    return R.RACES[rid]["label"]


def race_phrase(rid: str) -> str:
    return "control of the House" if rid == "US-HOUSE" else f"the {label(rid)} race"


def name_of(guild, uid: int) -> str:
    m = guild.get_member(uid) if guild else None
    return m.display_name if m else f"user {uid}"


def results() -> dict[str, str]:
    return S.truth(store.all(), store.procalls(), CFG.get("score_against", "pros_then_desk"))


# =========================================================================== roles & channels
def _roles(member) -> set[str]:
    return {r.name for r in getattr(member, "roles", [])}


def is_junkie(member) -> bool:
    return CFG["junkie_role"] in _roles(member) or is_admin(member)


def is_admin(member) -> bool:
    perms = getattr(member, "guild_permissions", None)
    return bool(perms and perms.manage_guild) or CFG.get("admin_role", "") in _roles(member)


def junkie_ids(guild) -> set[int]:
    return {m.id for m in guild.members if not m.bot and CFG["junkie_role"] in _roles(m)}


def votes_needed(guild) -> int:
    n = max(len(junkie_ids(guild)), 1)
    thr = CFG.get("threshold", "majority")
    return n // 2 + 1 if thr == "majority" else max(1, min(int(thr), n))


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
    return await ch.send(**kwargs)


def channel_note(guild, key: str) -> str:
    ch = find_channel(guild, key)
    return ch.mention if ch else f"this channel (I couldn't find #{CFG.get(key)})"


# =========================================================================== official calls
def map_file(kind: str, calls: dict | None = None) -> discord.File:
    calls = store.all() if calls is None else calls
    buf = G.house_chart(calls) if kind in ("house", "control") else G.tile_map(kind, calls)
    return discord.File(buf, filename=f"{kind}_map.png")


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
    elif kind == "control":
        await post(guild, "desk_channel", fallback, content=S.path_text("house", calls), file=map_file("house"))
    elif CFG.get("post_map_after_call", True):
        await post(guild, "desk_channel", fallback, content=S.path_text(kind, calls), file=map_file(kind))


def prediction_check(guild, rid: str, winner: str) -> tuple[list[str], list[str]]:
    """Names of people whose saved prediction for this race was right / wrong."""
    right, wrong = [], []
    for uid in store.predictors():
        pick = store.user_predictions(uid)[0].get(rid)
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


def claim_official(rid: str, party: str, how: str) -> bool:
    """Record the official call unless the race is already called. There's no await in here, so when two
    /call or /realcall handlers reach it at the same moment, only the first one wins."""
    if store.get(rid):
        return False
    store.call(rid, party, {"how": how})
    return True


async def make_official(guild, rid: str, party: str, how: str, fallback=None):
    """Post the card and map for a call already recorded with claim_official."""
    who = cand(rid, party)
    card = G.call_card(rid, party, candidate=load_candidates().get(rid, {}).get(party), tally=how,
                       when=datetime.now(TZ))
    h = S.holder(rid)
    lines = ["🚨 **DECISION DESK PROJECTION**",
             f"**{who[0].upper() + who[1:]}** {S.EMOJI[party]} {'win' if rid == 'US-HOUSE' else 'wins'} "
             f"{race_phrase(rid)} **{S.tag(rid, party)}**",
             race_flavor(rid, party) or flavor("flip" if h and h != party else "call")]
    note = S.upset_note(rid, party)
    if note:
        lines.append(note)
    pros = [p for p in store.procalls(rid)]
    agree = [p for p in pros if p["party"] == party]
    if agree:
        first = agree[0]
        lines.append(flavor("desk_behind", outlet=first["outlet"],
                            lead=S.minutes_text((time.time() - first["ts"]) / 60)))
    elif pros:
        lines.append(f"⚠️ {pros[0]['outlet']} called this for {R.PARTY_NAMES[pros[0]['party']]}.")
    if CFG.get("post_prediction_check", True):
        check = prediction_check_text(guild, rid, party)
        if check:
            lines.append(f"\n**Prediction check**\n{check}")
    await post(guild, "desk_channel", fallback, content="\n".join(x for x in lines if x),
               file=discord.File(card, filename=f"{rid}_{party}.png"))
    await follow_up(guild, rid, fallback)


async def retract_official(guild, rid: str, how: str, fallback=None):
    prev = store.get(rid)
    if not prev:  # a simultaneous retraction already went through
        return
    store.uncall(rid, {"how": how})
    card = G.call_card(rid, prev["party"] if prev else None, tally=how, when=datetime.now(TZ), retracted=True)
    text = f"↩️ **CALL RETRACTED:** {label(rid)}\n{flavor('retract') or ''}".strip()
    await post(guild, "desk_channel", fallback, content=text,
               file=discord.File(card, filename=f"{rid}_retracted.png"))
    await follow_up(guild, rid, fallback)


# =========================================================================== autocomplete
async def race_autocomplete(_interaction: discord.Interaction, current: str):
    cur = current.strip().upper().replace(" ", "-")
    calls = store.all()
    out = []
    order = ["US-HOUSE"] + [r for r in R.RACES if r != "US-HOUSE"]
    for rid in order:
        r = R.RACES[rid]
        if cur and cur not in rid and cur.replace("-", " ") not in r["label"].upper():
            continue
        extra = f" [called {calls[rid]}]" if rid in calls else ""
        out.append(app_commands.Choice(name=f"{rid} — {r['label']}{extra}"[:100], value=rid))
        if len(out) == 25:
            break
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
@app_commands.choices(winner=PARTY_CHOICES)
async def call_cmd(interaction: discord.Interaction, race: str, winner: app_commands.Choice[str]):
    guild, user = interaction.guild, interaction.user
    rid = resolve(race)
    if not rid:
        return await interaction.response.send_message(f"I don't recognize `{race}`.", ephemeral=True)
    if not is_junkie(user):
        return await interaction.response.send_message(
            f"You need the **{CFG['junkie_role']}** role to vote on calls.", ephemeral=True)
    party = winner.value
    official = store.get(rid)
    if official:
        msg = (f"{label(rid)} is already called for {cand(rid, official['party'])}."
               if official["party"] == party else
               f"{label(rid)} is already called for {cand(rid, official['party'])}. "
               f"Use `/uncall` if you think the desk got it wrong.")
        return await interaction.response.send_message(msg, ephemeral=True)
    prev = store.active_vote(user.id, rid, "call")
    if prev and prev["party"] == party:
        return await interaction.response.send_message("You've already voted for that call.", ephemeral=True)

    store.cast(user.id, rid, "call", party)
    ids = junkie_ids(guild)
    support = [u for u, p in store.active_votes(rid, "call").items() if p == party and u in ids]
    need = votes_needed(guild)
    await interaction.response.send_message(
        f"Vote recorded: {label(rid)} → {cand(rid, party)}. Posted in {channel_note(guild, 'board_channel')}.",
        ephemeral=True)

    switched = f" (switched from {S.EMOJI[prev['party']]})" if prev else ""
    how = f"Called by {len(support)} of {len(ids)} Election Junkies"
    reached = len(support) >= need and claim_official(rid, party, how)
    text = (f"🗳️ **{user.display_name}** has voted to call {race_phrase(rid)} for "
            f"**{cand(rid, party)}** {S.EMOJI[party]} **{S.tag(rid, party)}**{switched}\n"
            f"-# {min(len(support), need)} of {need} votes needed for an official call")
    if reached:
        text += f"\n✅ That's a majority. Official call posted in {channel_note(guild, 'desk_channel')}."
    elif store.get(rid):  # another vote completed the majority while this one was being recorded
        text += "\n-# The desk had just called it."
    await post(guild, "board_channel", interaction.channel, content=text)
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
    official = store.get(rid) if rid else None
    if not official:
        return await interaction.response.send_message("That race hasn't been called.", ephemeral=True)
    if not is_junkie(user):
        return await interaction.response.send_message(f"You need the **{CFG['junkie_role']}** role.",
                                                       ephemeral=True)
    if store.active_vote(user.id, rid, "uncall"):
        return await interaction.response.send_message("You've already voted to retract it.", ephemeral=True)
    store.cast(user.id, rid, "uncall", official["party"])
    ids = junkie_ids(guild)
    support = [u for u in store.active_votes(rid, "uncall") if u in ids]
    need = votes_needed(guild)
    await interaction.response.send_message("Retraction vote recorded.", ephemeral=True)
    text = (f"🧯 **{user.display_name}** has voted to retract the call in {race_phrase(rid)} "
            f"({cand(rid, official['party'])} {S.EMOJI[official['party']]})\n"
            f"-# {len(support)} of {need} votes needed to retract")
    reached = len(support) >= need
    if reached:
        text += "\n↩️ Majority reached. The call is retracted."
    await post(guild, "board_channel", interaction.channel, content=text)
    if reached:
        await retract_official(guild, rid, f"Retracted by {len(support)} of {len(ids)} Election Junkies",
                               interaction.channel)


@tree.command(name="forceuncall", description="Admin only: retract an official call without a vote.")
@app_commands.guild_only()
@app_commands.autocomplete(race=race_autocomplete)
async def forceuncall_cmd(interaction: discord.Interaction, race: str):
    rid = resolve(race)
    if not is_admin(interaction.user):
        return await interaction.response.send_message("Admins only.", ephemeral=True)
    if not rid or not store.get(rid):
        return await interaction.response.send_message("That race hasn't been called.", ephemeral=True)
    await interaction.response.send_message("Retracted.", ephemeral=True)
    await post(interaction.guild, "board_channel", interaction.channel,
               content=f"↩️ **{interaction.user.display_name}** retracted the call in {label(rid)} (admin).")
    await retract_official(interaction.guild, rid, f"Retracted by {interaction.user.display_name}",
                           interaction.channel)


# =========================================================================== /realcall and /procall
@tree.command(name="realcall", description="Make a call official right now, without waiting for a majority.")
@app_commands.guild_only()
@app_commands.autocomplete(race=race_autocomplete)
@app_commands.choices(winner=PARTY_CHOICES)
async def realcall_cmd(interaction: discord.Interaction, race: str, winner: app_commands.Choice[str]):
    guild, user = interaction.guild, interaction.user
    rid = resolve(race)
    if not rid:
        return await interaction.response.send_message(f"I don't recognize `{race}`.", ephemeral=True)
    if not is_junkie(user):
        return await interaction.response.send_message(f"You need the **{CFG['junkie_role']}** role.",
                                                       ephemeral=True)
    party = winner.value
    how = f"Called by {user.display_name} (override)"
    if not claim_official(rid, party, how):
        return await interaction.response.send_message(
            f"{label(rid)} is already called. `/uncall` it first if the desk got it wrong.", ephemeral=True)
    # the override counts as this person's call vote too, so the scoreboard credits them
    if not store.active_vote(user.id, rid, "call") or store.active_vote(user.id, rid, "call")["party"] != party:
        store.cast(user.id, rid, "call", party)
    await interaction.response.send_message(
        f"Called. Posted in {channel_note(guild, 'desk_channel')}.", ephemeral=True)
    await post(guild, "board_channel", interaction.channel,
               content=f"⚡ **{user.display_name}** called {race_phrase(rid)} without waiting for a majority: "
                       f"**{cand(rid, party)}** {S.EMOJI[party]} **{S.tag(rid, party)}**")
    await make_official(guild, rid, party, how, interaction.channel)


@tree.command(name="procall", description="Log a call made by an outlet or analyst (AP, NYT, Wasserman...).")
@app_commands.guild_only()
@app_commands.describe(outlet="Who made the call")
@app_commands.autocomplete(race=race_autocomplete, outlet=outlet_autocomplete)
@app_commands.choices(winner=PARTY_CHOICES)
async def procall_cmd(interaction: discord.Interaction, race: str, winner: app_commands.Choice[str], outlet: str):
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
    if any(p["outlet"].lower() == outlet.lower() for p in store.procalls(rid)):
        await interaction.response.send_message(f"Updated {outlet}'s call.", ephemeral=True)
    else:
        await interaction.response.send_message(
            f"Logged. Posted in {channel_note(guild, 'board_channel')}.", ephemeral=True)
    store.procall(rid, outlet, party, user.id)
    official = store.get(rid)
    lines = [f"📡 **{outlet}** has called {race_phrase(rid)} for **{cand(rid, party)}** "
             f"{S.EMOJI[party]} **{S.tag(rid, party)}**"]
    if official and official["party"] == party:
        lead = (time.time() - official["called_at"]) / 60
        lines.append(flavor("desk_ahead", outlet=outlet, lead=S.minutes_text(lead)) or "The desk was first.")
    elif official:
        lines.append(flavor("desk_wrong") or "The desk called this the other way.")
    else:
        lines.append("-# The desk hasn't called this yet: `/call` to vote, or `/realcall` to make it official.")
    early = [(ts, uid) for uid, r, p, ts in store.all_call_votes() if r == rid and p == party]
    if early:
        ts, uid = min(early)
        lines.append(f"-# First desk vote for this: {name_of(guild, uid)}, "
                     f"{S.minutes_text((time.time() - ts) / 60)} before {outlet}")
    await post(guild, "board_channel", interaction.channel, content="\n".join(lines))


# =========================================================================== /predict picker
def chunks(seq, n):
    return [seq[i:i + n] for i in range(0, len(seq), n)]


def build_pages() -> list[dict]:
    pages = []
    sen = sorted(R.races_of("senate"))
    for i, part in enumerate(chunks(sen, 20)):
        pages.append({"title": f"Senate {i + 1}/{len(chunks(sen, 20))}", "races": part, "map": "senate",
                      "kind": "senate"})
    pages.append({"title": "House majority", "races": ["US-HOUSE"], "map": None, "kind": "house"})
    gov = sorted(R.races_of("governor"))
    for i, part in enumerate(chunks(gov, 20)):
        pages.append({"title": f"Governors {i + 1}/{len(chunks(gov, 20))} (bonus)", "races": part,
                      "map": "governor", "kind": "governor"})
    bg = [rid for rid, _, _ in CMP.BATTLEGROUND if rid in R.RACES]
    for i, part in enumerate(chunks(bg, 20)):
        pages.append({"title": f"House battlegrounds {i + 1}/{len(chunks(bg, 20))} (bonus)", "races": part,
                      "map": None, "kind": "battleground"})
    return pages


PAGES = build_pages()
STYLE = {None: discord.ButtonStyle.secondary, "D": discord.ButtonStyle.primary,
         "R": discord.ButtonStyle.danger, "I": discord.ButtonStyle.success}


def deadline() -> datetime:
    return datetime.fromisoformat(CFG.get("predictions_deadline", "2026-11-03T18:00:00-05:00"))


def predictions_open() -> bool:
    return now() < deadline()


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
        short = rid if kind == "battleground" else rid.split("-")[0]
        super().__init__(label=short, style=STYLE[pick], row=row)
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
    def __init__(self, party: str, picked: str | None):
        text = {"D": "Democrats win the House", "R": "Republicans keep the House"}[party]
        super().__init__(label=text, emoji=S.EMOJI[party], row=0,
                         style=STYLE[party] if picked == party else discord.ButtonStyle.secondary)
        self.party = party

    async def callback(self, interaction: discord.Interaction):
        view: PredictView = self.view
        if view.picks.get("US-HOUSE") == self.party:
            view.picks.pop("US-HOUSE")
        else:
            view.picks["US-HOUSE"] = self.party
        view.note = None
        await view.refresh(interaction)


class TiebreakModal(discord.ui.Modal, title="Tiebreaker"):
    seats = discord.ui.TextInput(label="How many House seats will Democrats win?",
                                 placeholder="0 to 435", max_length=3)

    def __init__(self, view: "PredictView"):
        super().__init__()
        self.pv = view
        if view.house_seats is not None:
            self.seats.default = str(view.house_seats)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            n = int(str(self.seats.value).strip())
            assert 0 <= n <= 435
        except (ValueError, AssertionError):
            return await interaction.response.send_message("Enter a whole number from 0 to 435.", ephemeral=True)
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


class PredictView(discord.ui.View):
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
            for p in ("D", "R"):
                self.add_item(HouseButton(p, self.picks.get("US-HOUSE")))
            self.add_item(NavButton("tiebreak", row=1, label="Tiebreaker: Dem seats", emoji="🔢",
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
            "senate": "Click a state to cycle it: grey → 🔵 → 🔴 (→ 🟢 independent where there's a serious one) → grey.",
            "house": "Who controls the House? The tiebreaker settles leaderboard ties.",
            "governor": "Optional. Each correct governor pick is bonus points.",
            "battleground": "Optional. Toss-up, Tilt and Lean seats (avg. of Cook, Inside Elections, Sabato); each correct pick is bonus points.",
        }[page["kind"]]
        required = page["kind"] in ("senate", "house")
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
        gov_n = sum(1 for r in self.picks if R.RACES[r]["type"] == "governor")
        bg_n = sum(1 for r in self.picks if r in CMP.RATING)
        house = self.picks.get("US-HOUSE")
        e.add_field(name="Your picks so far", inline=False, value="\n".join([
            f"**Senate:** {senate_summary(self.picks)}",
            f"**House:** " + (f"{S.EMOJI[house]} {R.PARTY_NAMES[house]}" if house else "not picked")
            + (f" · tiebreaker D {self.house_seats}" if self.house_seats is not None else ""),
            f"**Governors (bonus):** {gov_n}/{len(R.races_of('governor'))}",
            f"**House battlegrounds (bonus):** {bg_n}/{len(CMP.BATTLEGROUND)}",
        ]))
        e.set_footer(text="Nothing is saved until you press Save all · "
                          f"Picks close {deadline().astimezone(TZ).strftime('%b %d, %I:%M %p').replace(' 0', ' ')} "
                          f"{deadline().astimezone(TZ).tzname()}")
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
        if not predictions_open():
            return await interaction.response.send_message("Predictions are closed.", ephemeral=True)
        store.save_predictions(self.user.id, self.picks, self.house_seats)
        missing = []
        sen_missing = len(R.races_of("senate")) - sum(1 for r in self.picks if R.RACES[r]["type"] == "senate")
        if sen_missing:
            missing.append(f"{sen_missing} Senate race{'s' * (sen_missing != 1)}")
        if "US-HOUSE" not in self.picks:
            missing.append("House majority")
        self.note = f"✅ **Saved at {clock(time.time())}.** You can keep editing and save again until picks close."
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
                    (R.PARTY_NAMES[house] if house else "not picked")
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
    if not predictions_open():
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
    e.add_field(name="Senate", value=f"{senate_summary(picks)}\n{grade_text(g['cats']['senate'])}", inline=False)
    e.add_field(name="House", value=(f"{S.EMOJI[house]} {R.PARTY_NAMES[house]}{mark}" if house else "not picked")
                + (f"\nTiebreaker: D {meta['house_dem_seats']}" if meta.get("house_dem_seats") is not None else ""))
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
        if r["flips"]:
            bits.append(f"{r['flips']} flip{'s' * (r['flips'] != 1)}")
        if r["miss"] is not None:
            bits.append(f"tiebreak off by {r['miss']}")
        lines.append(f"**{i}. {name_of(guild, r['user_id'])}** — **{r['points']}** pts · " + " · ".join(bits))
    pts = {**S.DEFAULT_POINTS, **(CFG.get("points") or {})}
    e = discord.Embed(title="Prediction leaderboard", description="\n".join(lines)[:4000], color=0xFAD63E)
    e.set_footer(text=f"Senate {pts['senate']} · House control {pts['house_control']} · governor "
                      f"{pts['governor']} · battleground {pts['battleground']} · +{pts['flip_bonus']} per "
                      f"correct flip · scored on pro calls, else desk calls")
    await interaction.response.send_message(embed=e)


@tree.command(name="finalhouse", description="Admin: set the final number of Democratic House seats (tiebreaker).")
@app_commands.guild_only()
async def finalhouse_cmd(interaction: discord.Interaction, dem_seats: app_commands.Range[int, 0, 435]):
    if not is_admin(interaction.user):
        return await interaction.response.send_message("Admins only.", ephemeral=True)
    store.set_setting("final_house_dem", dem_seats)
    await interaction.response.send_message(f"Final Democratic House seats set to {dem_seats}.")


# =========================================================================== /scoreboard (calling)
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
    # an exception escaping here would stop the task loop for the rest of the night, so nothing may escape
    try:
        guild = main_guild()
        if guild is None or not find_channel(guild, "board_channel"):
            return
        t = now()
        if t >= deadline() and not store.alert_sent("predictions-locked"):
            n = len(store.predictors())
            await send_alert(guild, "predictions-locked",
                             content=f"🔒 **Predictions are locked.** {n} entr{'y' if n == 1 else 'ies'}. "
                                     f"Use `/predictions` and `/compare` to see everyone's picks.")
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
    await interaction.response.send_message(content=S.path_text(chamber.value, store.all()),
                                            file=map_file(chamber.value))


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
        data = json.loads((HERE / "seat_info.json").read_text(encoding="utf-8"))
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
    e.set_footer(text=f"{source_name(st)} · fetched {clock(race['fetched_at'])} · unofficial count, not a call")
    return e


class RefreshView(discord.ui.View):
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
        e.add_field(name=f"{R.STATE_NAMES[st]} · fetched {clock(data['fetched_at'])}", inline=False,
                    value="\n".join(rows)[:1024] if rows else "No votes in the file for tracked races yet.")
    e.set_footer(text="Unofficial counts from state results files · ✅ = desk has called it · /results FL-13 for one race")
    await interaction.followup.send(embed=e)


@tree.command(name="resultstest", description="Admin: check a state's results reader against its Nov 2024 file.")
@app_commands.guild_only()
@app_commands.choices(state=[app_commands.Choice(name=R.STATE_NAMES[s], value=s) for s in ("FL", "NC", "VA")])
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


@tree.command(name="deskreset", description="Admin: wipe calls, votes and outlet calls (after testing).")
@app_commands.guild_only()
@app_commands.describe(confirm="Type RESET to confirm", include_predictions="Also wipe everyone's predictions")
async def deskreset_cmd(interaction: discord.Interaction, confirm: str, include_predictions: bool = False):
    if not is_admin(interaction.user) or confirm != "RESET":
        return await interaction.response.send_message("Admins only, and type RESET to confirm.", ephemeral=True)
    store.reset(include_predictions=include_predictions)
    await interaction.response.send_message(
        "Calls, votes, outlet calls and alerts cleared" + (" along with predictions." if include_predictions
                                                            else ". Predictions kept."))


# =========================================================================== County Watch
CW_STATE_CHOICES = [app_commands.Choice(name=CM.STATES[s], value=s) for s in CM.STATES]
_cw = {"early_at": 0.0, "live_at": 0.0, "status": "not run yet"}


def cw_night() -> bool:
    """Election-night window for live county pulls: from the first poll closings to 8 AM ET Nov 4."""
    t = now()
    return N.ELECTION_DAY.replace(hour=18) <= t <= N.ELECTION_DAY.replace(day=4, hour=8)


@tasks.loop(seconds=10)
async def county_loop():
    """Early vote every 6 hours before Election Day. On the night: every feed re-checked every
    refresh_seconds (30 by default), alerts posted the moment numbers move, workbook refreshed every
    workbook_seconds (120) so a slow rebuild never holds up the feeds."""
    try:
        src = CF.load_sources()
        # stops when the first polls close: the NC pull takes ~20 s and would hold up live pulls on the night
        if time.time() - _cw["early_at"] > 6 * 3600 and now() < N.ELECTION_DAY.replace(hour=18):
            _cw["early_at"] = time.time()
            await CWR.refresh_early()
            _cw["dirty"] = True
        if cw_night() and time.time() - _cw["live_at"] >= src.get("refresh_seconds", 30):
            _cw["live_at"] = time.time()
            cache = await CWR.refresh_live()
            for m in CWR.blocked_notices(cache):
                await post(main_guild(), "board_channel", content=m, allowed_mentions=NO_PINGS)
            if cache.get("changed"):
                _cw["dirty"] = True
                if src.get("post_alerts", True):
                    msgs = await asyncio.to_thread(CWR.alerts, cache, cache["changed"])
                    g = main_guild()
                    for m in msgs:
                        await post(g, "board_channel", content=m, allowed_mentions=NO_PINGS)
        loaded = await asyncio.to_thread(CWR.scan_inbox)
        for res in loaded:
            await _announce_file(main_guild(), res, "the grab button" if res.get("grab") else "the inbox folder")
        busy = _cw.get("wb_task") and not _cw["wb_task"].done()
        if _cw.get("dirty") and not busy and time.time() - _cw.get("wb_at", 0) >= src.get("workbook_seconds", 120):
            _cw["dirty"], _cw["wb_at"] = False, time.time()

            async def _write():
                _cw["status"] = await asyncio.to_thread(CWR.write_workbook)
                log.info("County Watch: %s", _cw["status"])
            _cw["wb_task"] = asyncio.create_task(_write())   # runs alongside, never delays the next feed check
    except Exception:
        log.exception("County Watch loop failed")


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


@tree.command(name="countytest", description="Admin: check a state's live county feed against its Nov 2024 file.")
@app_commands.guild_only()
@app_commands.choices(state=[app_commands.Choice(name=CM.STATES[s], value=s)
                             for s in ("GA", "NC", "MN", "FL", "VA", "IA", "OH", "TX", "SC", "AK")])
async def countytest_cmd(interaction: discord.Interaction, state: app_commands.Choice[str]):
    if not is_admin(interaction.user):
        return await interaction.response.send_message("Admins only.", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    st = state.value
    try:
        res = await CF.fetch_state(st, test=True)
    except Exception as exc:
        return await interaction.followup.send(f"❌ {st}: {exc}", ephemeral=True)
    base = {c["fips"]: c for c in CM.load()[st]}
    got = res["counties"]
    tcfg = (CF.load_sources().get("states", {}).get(st) or {}).get("test", {})
    if tcfg.get("count_only") or "pres" not in tcfg.get("contest", "pres").lower():   # e.g. Virginia's 2025 governor race
        tot = sum(r["D"] + r["R"] + r["O"] for r in got.values())
        return await interaction.followup.send(
            f"{'✅' if len(got) >= 0.95 * len(base) else '⚠️'} **{st}**: read {len(got)} of {len(base)} counties "
            f"({tot:,} votes) from its {tcfg.get('election_id', 'test')} {tcfg.get('contest')} results.", ephemeral=True)
    off = [base[f]["name"] for f, r in got.items()
           if abs(r["D"] - base[f]["d24"]) > max(50, 0.01 * base[f]["d24"]) or abs(r["R"] - base[f]["r24"]) > max(50, 0.01 * base[f]["r24"])]
    await interaction.followup.send(
        f"{'✅' if got and not off else '⚠️'} **{st}**: read {len(got)} of {len(base)} counties from the 2024 file; "
        f"{len(off)} differ from the published 2024 results" + (f" ({', '.join(off[:5])})" if off else "") + ".",
        ephemeral=True)


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


# =========================================================================== startup
@tree.error
async def on_app_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    log.exception("command failed", exc_info=error)
    msg = "Something went wrong running that command."
    if interaction.response.is_done():
        await interaction.followup.send(msg, ephemeral=True)
    else:
        await interaction.response.send_message(msg, ephemeral=True)


@client.event
async def setup_hook():
    gid = os.getenv("GUILD_ID")
    if gid:
        guild = discord.Object(id=int(gid))
        tree.copy_global_to(guild=guild)
        synced = await tree.sync(guild=guild)
    else:
        synced = await tree.sync()
    log.info("Synced %d commands", len(synced))


@client.event
async def on_ready():
    log.info("Logged in as %s", client.user)
    g = main_guild()
    for key in ("board_channel", "desk_channel"):
        if g and not find_channel(g, key):
            log.warning("Channel #%s not found: create it or fix config.json", CFG.get(key))
    if not alert_loop.is_running():
        alert_loop.start()
    if not county_loop.is_running():
        county_loop.start()


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise SystemExit("DISCORD_TOKEN is missing. Copy .env.example to .env and fill it in.")
    client.run(token, log_handler=None)


if __name__ == "__main__":
    main()

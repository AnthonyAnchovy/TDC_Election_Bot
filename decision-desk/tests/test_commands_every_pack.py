"""Every slash command, run under each data pack against a stand-in server: none may fail, and the ones that show a
part of the election (/map, /board, /closings...) show the pack's parts, not 2026's.

The bot is started in its own process per pack (the pack is fixed when bot.py loads). Nothing reaches Discord or the
network: posts and replies are collected, and the results feeds answer "not up yet".
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

DESK = Path(__file__).parent.parent

RUN = r'''
import asyncio, json, time, traceback
from types import SimpleNamespace as NS
import discord
from discord import app_commands

said = []          # (command, what was sent: text or embed title, whether a file went with it)


def record(cmd, kw):
    content = kw.get("content") or ""
    embed = kw.get("embed")
    title = (embed.title or "") if embed else ""
    body = (embed.description or "") + " ".join(f"{f.name}: {f.value}" for f in embed.fields) if embed else ""
    said.append({"cmd": cmd, "text": content, "title": title, "body": body, "file": bool(kw.get("file") or kw.get("files"))})


class Channel:
    def __init__(self, name):
        self.name, self.id, self.mention = name, abs(hash(name)) % 10**8, "#" + name
    async def send(self, content=None, **kw):
        record(CURRENT[0], {"content": content, **kw})
        return NS(id=len(said), edit=self.edit)
    async def edit(self, **kw):
        pass
    def permissions_for(self, _me):
        return NS(view_channel=True, send_messages=True, embed_links=True, attach_files=True)


async def dm(content=None, **kw):
    record(CURRENT[0], {"content": content, **kw})


def person(i, name):
    return NS(id=i, display_name=name, name=name, bot=False, mention="@" + name, send=dm,
              roles=[NS(name=bot.CFG["junkie_role"])], guild_permissions=NS(manage_guild=True),
              resolved_permissions=NS(manage_guild=True))


ME, YOU = person(11, "Me"), person(12, "You")
GUILD = NS(id=1, name="stand-in", text_channels=[Channel("decision-desk-board"), Channel("decision-desk")],
           members=[ME, YOU], roles=[NS(name=bot.CFG["junkie_role"], members=[ME, YOU])], me=NS(),
           get_member=lambda uid: {11: ME, 12: YOU}.get(uid), get_channel=lambda n: None)
bot.client._connection._guilds = {1: GUILD}
CURRENT = [""]


class Response:
    def __init__(self):
        self.done = False
    async def send_message(self, content=None, **kw):
        self.done = True
        record(CURRENT[0], {"content": content, **kw})
    async def defer(self, **kw):
        self.done = True
    async def send_modal(self, modal):
        self.done = True
        record(CURRENT[0], {"content": "(a form: " + type(modal).__name__ + ")"})
    def is_done(self):
        return self.done


def interaction(name, **typed):
    async def followup(content=None, **kw):
        record(CURRENT[0], {"content": content, **kw})
    async def edit(**kw):
        record(CURRENT[0], kw)
    return NS(guild=GUILD, guild_id=1, user=ME, channel=GUILD.text_channels[0], message=None, response=Response(),
              followup=NS(send=followup), command=NS(name=name), namespace=NS(**typed),
              type=discord.InteractionType.application_command, edit_original_response=edit)


async def offline(*a, **k):
    raise LookupError("not up yet (a test: nothing is fetched)")
bot.LIVE.race_result = bot.LIVE.fetch_state = offline
bot.NYT.fetch_all = lambda: asyncio.sleep(0, ({}, []))

V = bot.V
views = V.views()
races = [rid for v in views for rid in v["races"]]
race, key = V.sample_card()
other = next(k for k in bot.options_for(race) if k != key)
control = next((v["control"] for v in views if v["control"]), None)
choice = lambda value, name=None: app_commands.Choice(name=name or str(value), value=value)
pick = lambda rid, k: bot.Pick(k, k)
with bot.TEN.scope(1):
    bot.store.save_predictions(12, {rid: bot.options_for(rid)[0] for rid in races}, 1)
    bot.store.save_predictions(11, {rid: bot.options_for(rid)[-1] for rid in races}, 2)

plan = [
    ("pack", {}), ("help", {}), ("closings", {}), ("closings", {"preview": True}), ("next", {}), ("earlywarning", {}),
    ("board", {}), ("scoreboard", {}), ("leaderboard", {}), ("predictions", {}), ("compare", {"member": YOU}),
    ("closeout", {}), ("seat", {"race": race}), ("race", {"race": race}), ("results", {"race": race}), ("results", {}),
    ("call", {"race": race, "winner": pick(race, key)}), ("procall", {"race": race, "winner": pick(race, other), "outlet": "AP"}),
    ("uncall", {"race": race}), ("realcall", {"race": race, "winner": pick(race, key)}),
    ("forceuncall", {"race": race}), ("forcecall", {"race": race, "winner": pick(race, other)}),
    ("unvote", {"race": race}), ("final", {"race": race, "winner": pick(race, key)}), ("finalhouse", {"dem_seats": 1}),
    ("cardpreview", {"race": race, "winner": pick(race, other)}), ("cardtheme", {}), ("autocalls", {}),
    ("callnyt", {}), ("globalboard", {}), ("globalme", {"share": choice("on")}), ("drinkinggame", {}), ("unlockpicks", {"member": YOU}),
    ("trend", {"race": race}), ("field", {}), ("fieldposts", {}), ("clearrace", {"race": race, "confirm": "CLEAR"}),
    ("predict", {}),
]
if control:
    plan += [("seat", {"race": control}), ("call", {"race": control, "winner": pick(control, "D")})]
plan += [("map", {"chamber": c}) for c in bot.MAP_CHOICES]
plan += [("deskreset", {"confirm": "RESET"})]

failed, maps = {}, {}
async def run():
    for name, kw in plan:
        cmd = bot.tree.get_command(name)
        typed = {k: (v.value if isinstance(v, app_commands.Choice) else v) for k, v in kw.items()}
        label = name + ("" if name != "map" else ":" + kw["chamber"].value)
        CURRENT[0] = label
        it = interaction(name, **{k: v for k, v in typed.items() if isinstance(v, str)})
        before = len(said)
        try:
            if await bot._enter_server(it):
                with bot.TEN.scope(1):
                    await cmd.callback(it, **kw)
        except Exception:
            failed[label] = traceback.format_exc()[-900:]
        if len(said) == before:
            failed.setdefault(label, "said nothing at all")
        if name == "map":
            maps[kw["chamber"].value] = said[before:]
asyncio.run(run())
print(json.dumps({"failed": failed, "choices": [c.name for c in bot.MAP_CHOICES], "maps": maps,
                  "said": said, "describe": bot.tree.get_command("call").parameters[0].description}))
'''


def run_pack(pack_id: str, tmp: Path) -> dict:
    env = {**os.environ, "DESK_PACK": pack_id, "DESK_DATA": str(tmp), "DESK_CAPTURE": "0", "GUILD_ID": "1",
           "DESK_AUTOCALLS": "off", "PYTHONIOENCODING": "utf-8"}
    env.pop("DESK_FAKE_NOW", None)
    env.pop("DESK_SIMULATE", None)
    out = subprocess.run([sys.executable, "-X", "utf8", "-c", "import bot\n" + RUN], capture_output=True, text=True,
                         encoding="utf-8", cwd=DESK, env=env, timeout=600)
    assert out.returncode == 0, out.stderr[-2000:]
    return json.loads(out.stdout.strip().splitlines()[-1])


@pytest.fixture(scope="module")
def packs_run(tmp_path_factory):
    return {p: run_pack(p, tmp_path_factory.mktemp(p)) for p in ("2026-general", "2028-general", "2027-chicago-mayor")}


@pytest.mark.parametrize("pack_id", ["2026-general", "2028-general", "2027-chicago-mayor"])
def test_every_command_answers_under_every_pack(packs_run, pack_id):
    got = packs_run[pack_id]
    assert got["failed"] == {}, "\n\n".join(f"/{k}\n{v}" for k, v in got["failed"].items())


def text_of(rows):
    return " ".join(r["text"] + " " + r["title"] + " " + r["body"] for r in rows)


def test_the_map_offers_the_packs_own_parts(packs_run):
    assert packs_run["2026-general"]["choices"] == ["Senate", "House", "Governors"]
    assert packs_run["2028-general"]["choices"] == ["President", "Senate", "House", "Governors"]
    assert packs_run["2027-chicago-mayor"]["choices"] == ["The race", "Side bets"]
    pres = packs_run["2028-general"]["maps"]["president"]
    assert pres[0]["file"] and "Electoral College: D need **270** of 538 uncalled" in pres[0]["text"]
    city = packs_run["2027-chicago-mayor"]["maps"]["city"]
    assert not city[0]["file"] and city[0]["title"] == "The race" and "Mayor of Chicago:" in city[0]["body"]
    senate = packs_run["2026-general"]["maps"]["senate"]
    assert senate[0]["file"] and senate[0]["text"].startswith("Senate:")


def test_nothing_says_2026_outside_2026(packs_run):
    for pack_id in ("2028-general", "2027-chicago-mayor"):
        words = text_of([r for r in packs_run[pack_id]["said"] if r["cmd"] not in ("pack", "help")])   # /pack lists every pack
        for stale in ("2026", "Nov 3", "Senate & governor", "Cook PVI, 2026", "House seat count"):
            assert stale not in words, (pack_id, stale)


def test_command_examples_come_from_the_pack(packs_run):
    assert packs_run["2026-general"]["describe"].startswith("e.g. ") and "SEN" in packs_run["2026-general"]["describe"]
    assert "PRES" in packs_run["2028-general"]["describe"]
    assert packs_run["2027-chicago-mayor"]["describe"] == "e.g. CHI-MAYOR, CHI-RUNOFF"


def test_a_call_in_a_city_race_posts_a_card_and_where_it_stands(packs_run):
    rows = [r for r in packs_run["2027-chicago-mayor"]["said"] if r["cmd"] in ("call", "realcall", "forcecall")]
    assert any(r["text"].startswith("🚨 **DECISION DESK PROJECTION**") and r["file"] for r in rows)
    assert any(r["text"].startswith("Mayor of Chicago:") for r in rows)       # the list, where a map would go
    closings = next(r for r in packs_run["2027-chicago-mayor"]["said"] if r["cmd"] == "closings")
    assert closings["title"].startswith("Poll closings, ") and "Nov 3" not in closings["title"]
    preview = [r for r in packs_run["2027-chicago-mayor"]["said"] if r["cmd"] == "closings"][1]
    assert "**CHI-MAYOR**" in preview["body"] and "Senate" not in preview["body"]
    pres = [r for r in packs_run["2028-general"]["said"] if r["cmd"] == "closings"][1]
    assert "**IN-PRES** (R-held)" in pres["body"]

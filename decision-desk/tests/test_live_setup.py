"""Getting ready for a real Discord server: the preflight check, a quiet start, and what a server still needs."""
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

import packs
import preflight

DESK = Path(__file__).parent.parent
TOKEN = "x" * 26 + "." + "y" * 6 + "." + "z" * 38          # the shape of a bot token, and nothing more


@pytest.fixture(autouse=True)
def back_to_2026():
    yield
    packs.activate(packs.DEFAULT)


def boot(code: str, **env) -> dict:
    env = {**__import__("os").environ, "DESK_CAPTURE": "0", "PYTHONIOENCODING": "utf-8", **env}
    out = subprocess.run([sys.executable, "-X", "utf8", "-c", "import bot, json\n" + code], capture_output=True,
                         text=True, encoding="utf-8", cwd=DESK, env=env)
    assert out.returncode == 0, out.stderr[-800:]
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_a_test_setup_passes(tmp_path):
    bad, note, ok = preflight.check({"DISCORD_TOKEN": TOKEN, "GUILD_ID": "123456789012345678", "DESK_DATA": str(tmp_path),
                                     "DESK_AUTOCALLS": "off", "DESK_PACK": "2027-chicago-mayor"})
    assert bad == [] and note == ["This pack is marked as a draft: candidates and ratings aren't filled in."]
    text = " ".join(ok)
    assert "Chicago" in text and str(tmp_path) in text and "names no outlets" in text and "#decision-desk-board" in text
    assert TOKEN not in text                                    # what's in .env is never printed


@pytest.mark.parametrize("env, said", [
    ({}, "DISCORD_TOKEN is empty"),
    ({"DISCORD_TOKEN": "paste-token-here"}, "doesn't look like a bot token"),
    ({"DISCORD_TOKEN": TOKEN, "GUILD_ID": "my server"}, "numeric ID"),
    ({"DISCORD_TOKEN": TOKEN, "DESK_PACK": "1999-nothing"}, 'no data pack called "1999-nothing"'),
])
def test_what_must_be_fixed_is_said(env, said):
    bad, _note, _ok = preflight.check(env)
    assert any(said in line for line in bad), bad


def test_the_2026_flood_is_warned_about(tmp_path):
    """2026's outlets have called everything, so starting with automatic calls on posts all of it."""
    base = {"DISCORD_TOKEN": TOKEN, "GUILD_ID": "1", "DESK_DATA": str(tmp_path), "DESK_PACK": "2026-general"}
    _bad, note, _ok = preflight.check(base)
    assert any("posts every House call" in line for line in note)
    bad, note, ok = preflight.check({**base, "DESK_AUTOCALLS": "off"})
    assert bad == [] and not any("posts every House call" in line for line in note)
    assert any("start off" in line for line in ok) and any("idle now" in line for line in ok)
    _bad, note, _ok = preflight.check({**base, "DESK_AUTOCALLS": "off", "DESK_FAKE_NOW": "2026-11-03T19:05-05:00"})
    assert any("clock is held" in line for line in note) and any("election night by the bot's clock" in line for line in note)
    _bad, note, _ok = preflight.check({**base, "DESK_DATA": ""}, now=datetime.fromisoformat("2027-01-01T12:00:00-05:00"))
    assert any("DESK_DATA=data-test" in line for line in note)       # records would be shared: say how to keep a test apart


def test_automatic_calls_can_start_off(tmp_path):
    code = """
with bot.TEN.scope(5):
    before = bot.store.setting("autocalls", bot.AUTOCALLS_DEFAULT)
    bot.store.set_setting("autocalls", True)
    after = bot.store.setting("autocalls", bot.AUTOCALLS_DEFAULT)
print(json.dumps([bot.AUTOCALLS_DEFAULT, before, after]))
"""
    assert boot(code, DESK_DATA=str(tmp_path / "a"), DESK_AUTOCALLS="off") == [False, False, True]     # /autocalls on still wins
    assert boot(code, DESK_DATA=str(tmp_path / "b"), DESK_AUTOCALLS="on") == [True, True, True]


def test_every_server_gets_the_commands_and_a_report(tmp_path):
    got = boot("""
import asyncio
from types import SimpleNamespace as NS
synced = []
async def sync(guild=None):
    synced.append(guild.id if guild else None)
    return [1, 2, 3]
bot.tree.sync, bot.tree.copy_global_to = sync, lambda guild: None
perms = lambda **no: NS(**{**dict(view_channel=True, send_messages=True, embed_links=True, attach_files=True), **no})
def channel(name, **no):
    return NS(name=name, permissions_for=lambda me: perms(**no))
def server(i, channels, roles):
    return NS(id=i, name=f"s{i}", me=NS(), text_channels=channels, roles=roles, get_channel=lambda n: None)
junkies = NS(name="Election Junkie", members=[1])
ready = server(1, [channel("decision-desk-board"), channel("decision-desk")], [junkies])
bare = server(2, [channel("decision-desk-board", attach_files=False, embed_links=False)], [NS(name="Election Junkie", members=[])])
async def run():
    await bot.setup_hook()
    await bot.sync_commands(ready)          # the home server again: not synced twice
    await bot.on_guild_join(bare)
asyncio.run(run())
print(json.dumps({"synced": synced, "ready": bot.server_report(ready), "bare": bot.server_report(bare)}))
""", DESK_DATA=str(tmp_path), GUILD_ID="1")
    assert got["synced"] == [1, 2]                              # each server once, so the second has commands too
    assert got["ready"] == []
    assert got["bare"] == ["in #decision-desk-board the bot can't: Embed Links, Attach Files",
                           "no #decision-desk channel (create it, or set desk_channel in config.json)",
                           'nobody has the "Election Junkie" role yet']

"""A recorded night played through the bot's own handlers into a stand-in server (simulate.py).

The stand-in only collects what would be posted. The first two hours of 2026 are played here; the whole night is the
same code and takes about a minute, mostly drawing cards.
"""
import json
import subprocess
import sys
from pathlib import Path

import simulate

DESK = Path(__file__).parent.parent

RUN = """
import asyncio, collections, simulate
from types import SimpleNamespace as NS
posts = []
def channel(name):
    async def send(**kw):
        files = [kw["file"]] if kw.get("file") else kw.get("files") or []
        posts.append([name, kw.get("content") or "", [f.filename for f in files], bool(kw.get("embed"))])
        return NS(id=len(posts))
    return NS(name=name, send=send, mention="#" + name, id=len(name))
guild = NS(id=1, name="stand-in", text_channels=[channel("decision-desk-board"), channel("decision-desk")],
           get_member=lambda uid: None, members=[], roles=[], me=None, get_channel=lambda n: None)
whole = simulate.load
def part(folder):
    night, snaps = whole(folder)
    night = {**night, "events": [e for e in night["events"] if e["t"] < night["events"][0]["t"] + 2 * 3600]}
    return night, [s for s in snaps if s["ts"] <= night["events"][-1]["t"]]
simulate.load = part
with bot.TEN.scope(1):
    bot.store.call("MS-SEN", "D", {"how": "left over from trying the bot by hand"})
bot.TEN.forget()
res = asyncio.run(simulate.start(bot, guild))
with bot.TEN.scope(1):
    official = bot.store.all()
    people = sorted(bot.store.predictors())
first = lambda mark: next((p for p in posts if p[1].startswith(mark)), None)
print(json.dumps({"res": {k: res[k] for k in ("events", "calls", "failed")}, "official": len(official), "ga": official.get("MS-SEN"),
                  "people": people, "kinds": collections.Counter(p[1][:1] for p in posts), "open": posts[0][1][:60],
                  "close": posts[-1][1][:40], "vote": first("\U0001f5f3")[1], "card": first("\U0001f6a8"),
                  "clock": bot.now().isoformat(), "roster": bot.roster is not None and bot.roster.__name__}))
"""


def test_the_first_hours_of_the_night_play_through_the_bot(tmp_path):
    env = {**__import__("os").environ, "DESK_DATA": str(tmp_path), "DESK_CAPTURE": "0", "GUILD_ID": "1",
           "DESK_AUTOCALLS": "off", "DESK_SIMULATE": "tests/fixtures/2026", "DESK_SIM_SPEED": "1000000",
           "DESK_SIM_MAX_GAP": "0", "PYTHONIOENCODING": "utf-8"}
    env.pop("DESK_FAKE_NOW", None)
    out = subprocess.run([sys.executable, "-X", "utf8", "-c", "import bot, json\n" + RUN], capture_output=True, text=True,
                         encoding="utf-8", cwd=DESK, env=env)
    assert out.returncode == 0, out.stderr[-1500:]
    got = json.loads(out.stdout.strip().splitlines()[-1])
    assert got["res"]["failed"] == 0 and got["res"]["events"] > 80 and got["res"]["calls"] == got["official"] > 20
    assert got["ga"] is None                                    # what the server held before was set aside, not built on
    assert (tmp_path / "2026-general" / "1" / "desk.before-sim.db").exists()
    assert got["people"] == [1, 2, 3, 4, 5, 6]                  # the night's predictions, for the prediction checks
    assert got["open"].startswith("🧪 **Simulation: 2026 midterm elections") and got["close"].startswith("🧪 **Simulation finished**")
    assert got["vote"].startswith("🗳️ **user1** has voted to call")        # people post under the fixture's numbers
    assert got["card"][0] == "decision-desk" and got["card"][2] and got["card"][2][0].endswith(".png")
    assert got["kinds"]["📡"] > 5 and got["kinds"]["✅"] >= 1    # outlet calls on the board, Safe seats in roll-ups
    assert got["clock"].startswith("2026-11-03T2")              # the clock stays on the night afterwards
    assert got["roster"] == "roster"                            # and the desk is the server's own again
    rows = [json.loads(line) for line in (tmp_path / "2026-general" / "1" / "simulation.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(rows) == got["res"]["events"] and all("seconds" in r for r in rows)


def test_a_simulation_only_runs_on_records_kept_apart(tmp_path):
    assert "DESK_DATA isn't" in simulate.refusal({"DESK_SIMULATE": "tests/fixtures/2026"})
    assert "no night.fixture.json" in simulate.refusal({"DESK_SIMULATE": str(tmp_path), "DESK_DATA": str(tmp_path)})
    assert simulate.refusal({"DESK_SIMULATE": str(DESK / "tests" / "fixtures" / "2026"), "DESK_DATA": str(tmp_path)}) is None

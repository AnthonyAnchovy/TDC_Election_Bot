"""The Discord side for any data pack: who can win a race as typed, the picker's pages, and the call cards.

bot.py is imported here (it connects to nothing until main() runs), so its helpers are tested directly. What needs
a live Discord server, the posts themselves, is still only checked by hand.
"""
import asyncio
import io
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

import graphics as G
import packs
import picks as P
import scoring as S

DESK = Path(__file__).parent.parent


@pytest.fixture(scope="module")
def bot(tmp_path_factory):
    import os
    os.environ["DESK_DATA"] = str(tmp_path_factory.mktemp("data"))
    import bot as B
    yield B
    os.environ.pop("DESK_DATA", None)


def said(race: str):
    """An interaction where `race` has been typed already, as Discord hands it over."""
    return SimpleNamespace(namespace=SimpleNamespace(race=race))


def boot(pack_id: str, code: str) -> str:
    """Start the bot's module under another pack in its own process, run a line, and return what it printed."""
    env = {**__import__("os").environ, "DESK_PACK": pack_id, "DESK_CAPTURE": "0", "PYTHONIOENCODING": "utf-8"}
    out = subprocess.run([sys.executable, "-X", "utf8", "-c", "import bot, json\n" + code], capture_output=True, text=True,
                         encoding="utf-8", cwd=DESK, env=env)
    assert out.returncode == 0, out.stderr[-600:]
    return out.stdout.strip().splitlines()[-1]


# ------------------------------------------------------------------ who someone means
@pytest.mark.parametrize("race, typed, want", [
    ("GA-SEN", "D", "D"), ("GA-SEN", "d", "D"), ("GA-SEN", "Democrat", "D"), ("GA-SEN", "republican", "R"),
    ("GA-SEN", "Ossoff", "D"), ("GA-SEN", "jon ossoff", "D"), ("GA-SEN", "Collins", "R"),
    ("NE-SEN", "Osborn", "I"), ("NE-SEN", "independent", "I"),
    ("GA-SEN", "I", None), ("GA-SEN", "independent", None),        # nobody independent is running there
    ("GA-SEN", "somebody else", None), ("GA-SEN", "", None),
    ("US-HOUSE", "Democrats", "D"), ("US-HOUSE", "republicans", "R"),
])
def test_a_winner_can_be_typed_as_a_letter_a_party_or_a_name(race, typed, want):
    assert P.resolve_option(race, typed) == want


def test_a_nonpartisan_race_is_called_by_candidate():
    packs.activate("2027-chicago-mayor")
    try:
        assert P.options_for("CHI-MAYOR") == ["A", "B", "C", "D"]
        assert P.resolve_option("CHI-MAYOR", "b") == "B" and P.resolve_option("CHI-MAYOR", "Candidate C") == "C"
        assert P.resolve_option("CHI-MAYOR", "candidate") is None          # four of them: say which
        assert P.resolve_option("CHI-RUNOFF", "yes") == "YES" and P.resolve_option("CHI-RUNOFF", "No, it's won outright") == "NO"
        assert P.name_of("CHI-MAYOR", "B") == "Candidate B"
        assert S.tag("CHI-MAYOR", "B") == "(WIN)" and S.EMOJI["B"] == "⚪"
    finally:
        packs.activate(packs.DEFAULT)


def test_tags_for_party_races_are_unchanged():
    assert S.tag("GA-SEN", "D") == "(D HOLD)" and S.tag("GA-SEN", "R") == "(R PICKUP)" and S.tag("VT-AL", "D") == "(D)"


def test_the_winner_box_offers_the_races_own_candidates(bot):
    w = bot.WinnerOf()
    offered = asyncio.run(w.autocomplete(said("ne sen"), ""))
    assert [(c.name, c.value) for c in offered] == [("the Democrat (D)", "D"), ("Pete Ricketts (R)", "R"), ("Dan Osborn (I)", "I")]
    assert [c.value for c in asyncio.run(w.autocomplete(said("NE-SEN"), "osb"))] == ["I"]
    assert [c.name for c in asyncio.run(w.autocomplete(said(""), ""))] == ["Pick the race first"]
    pick = asyncio.run(w.transform(said("georgia senate"), "ossoff"))
    assert (pick.value, pick.raw) == ("D", "ossoff")
    assert asyncio.run(w.transform(said("GA-SEN"), "the green party")).value is None
    assert asyncio.run(w.transform(said("not a race"), "D")).value is None


def test_a_winner_the_race_does_not_have_is_explained(bot):
    text = bot.no_such_winner("GA-SEN", "independent")
    assert text == "`independent` isn't someone who can win Georgia Senate. It's between Jon Ossoff 🔵 and Mike Collins 🔴."


def test_every_command_that_takes_a_winner_takes_any_option(bot):
    takes = {c.name: [p.name for p in c.parameters] for c in bot.tree.get_commands() if hasattr(c, "parameters")}
    for name in ("call", "realcall", "forcecall", "procall", "final", "cardpreview"):
        cmd = bot.tree.get_command(name)
        winner = next(p for p in cmd.parameters if p.name == "winner")
        assert winner.autocomplete and not winner.choices, name      # offered from the race, not a fixed D/R/I list
        assert "race" in takes[name]


# ------------------------------------------------------------------ the picker
def test_the_2026_picker_has_the_pages_it_always_had(bot):
    titles = [p["title"] for p in bot.PAGES]
    assert titles == ["Senate 1/2", "Senate 2/2", "House majority", "House battlegrounds 1/3 (bonus)",
                      "House battlegrounds 2/3 (bonus)", "House battlegrounds 3/3 (bonus)", "Governors 1/2 (bonus)",
                      "Governors 2/2 (bonus)"]
    assert sum(len(p["races"]) for p in bot.PAGES) == 35 + 1 + 50 + 36
    assert {rid for p in bot.PAGES for rid in p["races"]} == P.pickable()
    assert [p["map"] for p in bot.PAGES] == ["senate", "senate", None, None, None, None, "governor", "governor"]
    assert all(len(p["races"]) <= 20 for p in bot.PAGES)          # Discord fits 25 buttons; 5 are navigation


def test_picker_buttons_say_what_they_are(bot):
    assert bot.PickButton("MS-SEN", None, 0, "senate").label == "Miss."
    assert bot.PickButton("MS-SEN", "R", 0, "senate").emoji.name == "🔴"
    assert bot.PickButton("TX-15", "D", 0, "battleground").label == "TX-15"
    d, r = (bot.HouseButton("US-HOUSE", p, "D") for p in ("D", "R"))
    assert (d.label, r.label) == ("Democrats win the House", "Republicans keep the House")
    assert d.style == bot.STYLE["D"] and r.style == bot.discord.ButtonStyle.secondary


def test_the_picker_lays_out_any_pack():
    city = json.loads(boot("2027-chicago-mayor", "print(json.dumps([[p['title'], p['kind'], p['races']] for p in bot.PAGES]))"))
    assert city == [["The race", "mayor", ["CHI-MAYOR"]], ["Side bets (bonus)", "question", ["CHI-RUNOFF"]]]
    label = boot("2027-chicago-mayor", "print(bot.PickButton('CHI-MAYOR', 'B', 0, 'mayor').label, '|', bot.PickButton('CHI-MAYOR', None, 0, 'mayor').label)")
    assert label == "Mayor of Chicago: Candidate B | Mayor of Chicago"
    pres = json.loads(boot("2028-general", "print(json.dumps([p['title'] for p in bot.PAGES]))"))
    assert pres[:3] == ["President 1/3", "President 2/3", "President 3/3"] and "House majority" in pres
    labels = boot("2028-general", "print(bot.PickButton('ME1-PRES', None, 0, 'president').label, '|', bot.PickButton('TX-PRES', 'R', 0, 'president').label)")
    assert labels == "Maine 1st District | Texas"


# ------------------------------------------------------------------ call cards
@pytest.mark.parametrize("theme", sorted(G.CARD_THEMES))
def test_a_card_is_drawn_for_a_candidate_who_is_not_a_party(theme):
    packs.activate("2027-chicago-mayor")
    try:
        buf = G.call_card("CHI-MAYOR", "B", candidate="Candidate B", tally="Called by 3 of 5", theme=theme, use_custom=False)
        img = Image.open(io.BytesIO(buf.getvalue()))
        assert img.width >= 800 and img.height >= 400
        gone = G.call_card("CHI-MAYOR", "B", candidate="Candidate B", tally="Retracted", theme=theme, retracted=True, use_custom=False)
        assert Image.open(io.BytesIO(gone.getvalue())).width >= 800
    finally:
        packs.activate(packs.DEFAULT)


@pytest.mark.parametrize("theme", sorted(G.CARD_THEMES))
def test_party_cards_still_draw(theme):
    for rid, party in (("GA-SEN", "D"), ("TX-15", "R"), ("US-HOUSE", "D"), ("US-SENATE", "R"), ("NE-SEN", "I")):
        buf = G.call_card(rid, party, candidate=P.name_of(rid, party), tally="Called by 3 of 5", theme=theme,
                          counts=S.chamber_counts(__import__("races").chamber(rid), {}), use_custom=False)
        assert Image.open(io.BytesIO(buf.getvalue())).width >= 800, (rid, party)

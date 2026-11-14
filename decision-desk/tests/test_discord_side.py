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


def test_the_newer_commands_are_registered(bot):
    names = {c.name for c in bot.tree.get_commands()}
    assert {"trend", "react", "drinkinggame", "globalboard", "globalme", "pack", "needs", "closeout", "final", "forcecall"} <= names
    assert bot.BOT_OUTLET == "The Bot" and not bot.is_house_caller(bot.BOT_OUTLET)      # it can never make a seat official
    assert bot.card_theme() in ("editorial", "wire", "classic") and bot.CFG["card_theme"] == "editorial"
    assert bot.reaction_files() == {}                                                   # none shipped: people add their own


# ------------------------------------------------------------------ an option's own colour
def _dominant(buf) -> tuple:
    img = Image.open(io.BytesIO(buf.getvalue())).convert("RGB").resize((64, 64))
    return max(img.getcolors(64 * 64), key=lambda c: c[0])[1]


def test_a_candidate_is_drawn_in_their_own_colour():
    import cards as C
    import colors as COL
    packs.activate("2027-chicago-mayor")
    try:
        own = {o["key"]: o["color"] for o in __import__("races").RACES["CHI-MAYOR"]["options"]}
        assert len(set(own.values())) == 4 and COL.style("CHI-MAYOR", "B") == own["B"]
        # "Candidate D" is not the Democrat: neither the party's colour nor its words
        assert COL.style("CHI-MAYOR", "D") == own["D"] and not COL.is_party("CHI-MAYOR", "D")
        facts = C.race_facts("CHI-MAYOR", "D", "Candidate D", "", None, False, None)
        assert facts["key"] == "D" and facts["party"] == own["D"] and C.status_text(facts) == "WIN"
        assert C.ED_COL[own["D"]] == COL.rgb(own["D"]) != C.ED_COL["D"]
        assert C.WIRE_FIELD[own["B"]] == COL.dark(COL.rgb(own["B"]), 0.30)          # each table in its own tone
        assert C.WIRE_DOT[own["B"]] == COL.light(COL.rgb(own["B"]), 0.45)
        assert G.GLASS[own["B"]] == (COL.dark(COL.rgb(own["B"]), 0.50), COL.rgb(own["B"]))
        b, d = (_dominant(G.call_card("CHI-MAYOR", k, candidate=f"Candidate {k}", theme="wire", use_custom=False)) for k in "BD")
        assert b != d                                                               # two candidates, two cards
        gone = G.call_card("CHI-MAYOR", "B", candidate="Candidate B", theme="wire", retracted=True, use_custom=False)
        assert _dominant(gone) not in (b, d)                                        # a retraction is nobody's colour
    finally:
        packs.activate(packs.DEFAULT)


def test_party_colours_are_what_they_were():
    import cards as C
    import colors as COL
    assert COL.style("GA-SEN", "D") == "D" and COL.style("NE-SEN", "I") == "I" and COL.is_party("GA-SEN", "R")
    assert C.ED_COL["D"] == (33, 92, 189) and C.WIRE_FIELD["R"] == (186, 24, 32) and G.PARTY["I"] == (154, 92, 214)
    assert C.ED_COL["X"] == (110, 112, 118) == C.ED_COL["nobody"] == C.ED_COL["#nonsense"]      # unknown: the neutral
    assert G.PARTY["nobody"] == G.PARTY["I"]                                        # a table with no neutral entry
    facts = C.race_facts("GA-SEN", "R", "Mike Collins", "", None, False, None)
    assert facts["party"] == facts["key"] == "R" and C.status_text(facts) == "REPUBLICAN PICKUP"


def test_a_packs_fourth_party_gets_its_colour(monkeypatch):
    import cards as C
    monkeypatch.setitem(packs.active().options, "G", {"name": "Green", "color": "#2e7d32"})
    assert C.ED_COL["G"] == (46, 125, 50) and G.PANEL_FILL["G"] == (14, 38, 15)


# ------------------------------------------------------------------ the picker's map, for a president
def test_the_presidential_page_has_a_map():
    packs.activate("2028-general")
    try:
        import races as RR
        pres = {rid: r for rid, r in RR.RACES.items() if r["type"] == "president"}
        assert sum(r["weight"] for r in pres.values()) == 538 and "DC-PRES" in pres
        blank = Image.open(io.BytesIO(G.tile_map("president", {}).getvalue()))
        full = Image.open(io.BytesIO(G.tile_map("president", {rid: r["holder"] for rid, r in pres.items()},
                                                title="Someone's picks", pending_word="unpicked", width=760).getvalue()))
        assert blank.size == (1200, 1074) and full.width == 760                    # taller: a row for the districts
        assert blank.tobytes() != Image.open(io.BytesIO(G.tile_map("senate", {}).getvalue())).resize(blank.size).tobytes()
    finally:
        packs.activate(packs.DEFAULT)
    assert Image.open(io.BytesIO(G.tile_map("senate", {"GA-SEN": "D"}).getvalue())).size == (1200, 1030)     # 2026's is unchanged
    maps = json.loads(boot("2028-general", "print(json.dumps([[p['title'], p['map']] for p in bot.PAGES][:4]))"))
    assert maps[:3] == [["President 1/3", "president"], ["President 2/3", "president"], ["President 3/3", "president"]]


# ------------------------------------------------------------------ the tiebreaker, in the pack's words
def test_the_tiebreaker_asks_the_packs_question(bot):
    tb = bot.tiebreak()
    assert (tb["label"], tb["min"], tb["max"], tb["name"]) == ("How many seats do Democrats win?", 0, 435, "Democratic House seats")
    city = json.loads(boot("2027-chicago-mayor", "print(json.dumps(bot.tiebreak()))"))
    assert city["label"] == "Guess the leader's share" and (city["min"], city["max"]) == (0, 100)      # the question is too long for the box
    pres = json.loads(boot("2028-general", "print(json.dumps(bot.tiebreak()))"))
    assert pres["label"] == "How many electoral votes does the winner get?" and (pres["min"], pres["max"]) == (270, 538)
    assert all(len(t["label"]) <= 45 for t in (tb, city, pres))                     # Discord's limit for a box's label



ROLL_UP = """
import asyncio, time
from types import SimpleNamespace as NS
posted = []
async def slow(guild, safe):
    await asyncio.sleep(0.05)
    posted.append(sorted(r for rids in safe.values() for r in rids))
bot.post_safe_seats = slow
g = NS(id=1)
with bot.TEN.scope(1):
    for rid, p in (("KY-05", "R"), ("FL-10", "D")):
        bot.DESK.claim_official(bot.store, rid, p, "Projected by NYT", outlet="NYT")
        bot.queue_safe_seat(rid, p)
    bot.store.set_setting("safe_batch", [[r, p, time.time() - 900] for r, p, _t in bot.store.setting("safe_batch")])
    async def both():
        await asyncio.gather(bot.flush_safe_seats(g), bot.flush_safe_seats(g))
    asyncio.run(both())
    print(json.dumps([posted, bot.store.setting("safe_batch")]))
"""


def test_a_safe_seat_roll_up_is_posted_once_when_two_passes_overlap(tmp_path):
    """The roll-up is taken off the queue before its post goes out. In the simulation, the 30-second loop and the
    driver's own pass both posted the same seats, and 17 seats were listed twice."""
    env = {**__import__("os").environ, "DESK_PACK": "2026-general", "DESK_DATA": str(tmp_path), "DESK_CAPTURE": "0",
           "GUILD_ID": "1", "PYTHONIOENCODING": "utf-8"}
    out = subprocess.run([sys.executable, "-X", "utf8", "-c", "import bot, json\n" + ROLL_UP], capture_output=True,
                         text=True, encoding="utf-8", cwd=DESK, env=env)
    assert out.returncode == 0, out.stderr[-800:]
    posted, left = json.loads(out.stdout.strip().splitlines()[-1])
    assert posted == [["FL-10", "KY-05"]] and left == []


# ------------------------------------------------------------------ a city drawn by ward
def test_chicago_has_its_wards_and_its_outline():
    packs.activate("2027-chicago-mayor")
    try:
        chi = G.place("CHI")
        assert chi["state"] == "IL" and len(chi["parts"]) == 50 and sorted(map(int, chi["parts"])) == list(range(1, 51))
        assert len(G.holes(chi["outline"])) == 1            # Norridge and Harwood Heights, surrounded by Chicago
        lons = [x for r in chi["outline"] for x, _ in r]
        lats = [y for r in chi["outline"] for _, y in r]
        assert -87.95 < min(lons) < max(lons) < -87.52 and 41.64 < min(lats) < max(lats) < 42.03
        import views as V
        assert [(v["key"], v["draw"]) for v in V.views()] == [("city", "wards"), ("bets", "wards")]
        called = Image.open(G.ward_map("CHI", "CHI-MAYOR", "B"))
        blank = Image.open(G.ward_map("CHI", "CHI-MAYOR", None))
        assert called.width == 1100 and called.height > 1300 and called.tobytes() != blank.tobytes()
        import cards as C
        assert C.race_facts("CHI-MAYOR", "B", "Candidate B", "", None, False, None)["state"] == "CHI"
        mask = G._shape_mask("CHI", 300, 300)
        assert mask is not None and 0 < sum(mask.point(lambda v: 255 if v > 127 else 0).histogram()[255:]) < 300 * 300
    finally:
        packs.activate(packs.DEFAULT)
    assert G.place("CHI") is None and G.place("GA") is None               # other packs don't have it

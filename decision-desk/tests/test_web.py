"""The website's API: signing in, saving picks under the game's rules, the leaderboard and the night page."""
import pytest
from fastapi.testclient import TestClient

import clock
import picks as P
import races as R
import scoring as S
from store import Store
from web import app as WEB

BEFORE = P.deadline().timestamp() - 86400       # a day before picks close
AFTER = P.deadline().timestamp() + 3 * 86400    # well after every poll has closed
JSON = {"X-Desk": "1"}


@pytest.fixture
def site(monkeypatch, tmp_path):
    """A client on an empty database, a day before the deadline, with dev sign-in on."""
    monkeypatch.setenv("DESK_WEB_SECRET", "test-secret")
    monkeypatch.setenv("DESK_WEB_DEV", "1")
    monkeypatch.delenv("DESK_FAKE_NOW", raising=False)
    WEB.use_store(Store(str(tmp_path / "desk.db"), check_same_thread=False))
    clock.set(BEFORE)
    with TestClient(WEB.app, follow_redirects=False) as client:
        yield client
    WEB.use_store(None)


def sign_in(client, uid=1, name="Ann"):
    r = client.get(f"/login?as={uid}&name={name}")
    assert r.status_code == 303
    return client


def full_senate(d_seats=20):
    return {rid: ("D" if i < d_seats else "R") for i, rid in enumerate(sorted(R.races_of("senate")))}


# ------------------------------------------------------------------ signing in
def test_a_visitor_sees_the_game_but_has_no_entry(site):
    s = site.get("/api/state").json()
    assert s["me"] is None and "entry" not in s and s["open"] and s["dev"]
    secs = {x["key"]: x for x in s["races"]["sections"]}
    assert [(k, x["kind"], len(x["races"])) for k, x in secs.items()] == [("senate", "map", 35), ("house", "house", 50), ("governor", "map", 36)]
    assert secs["house"]["control"]["id"] == "US-HOUSE" and secs["senate"]["implied"] == {"name": "Senate control", "points": 5}
    assert s["pack"] == "2026-general" and "2028-general" in [p["id"] for p in s["packs"]]
    assert s["races"]["bodies"]["senate"]["majority"] == 51 and s["races"]["tiebreaker"]["max"] == 435
    assert set(s["map"]["states"]) == set(R.STATE_NAMES) and s["map"]["states"]["TX"]["d"].startswith("M")
    assert s["points"]["senate_control"] == 5
    assert site.put("/api/picks", json={"picks": {}}, headers=JSON).status_code == 401


def test_dev_sign_in_sets_a_signed_cookie_and_remembers_the_name(site):
    sign_in(site, 42, "Pillow")
    s = site.get("/api/state").json()
    assert s["me"] == {"id": "42", "name": "Pillow", "avatar": None}
    assert s["entry"]["picks"] == {} and s["entry"]["completion"]["share"] == 0
    assert WEB.store().setting("names") == {"42": "Pillow"}


def test_a_forged_or_expired_cookie_is_nobody(site, monkeypatch):
    site.cookies.set(WEB.COOKIE, WEB.sign({"uid": "1", "name": "x", "exp": clock.time() + 60})[:-2] + "00")
    assert site.get("/api/state").json()["me"] is None
    site.cookies.set(WEB.COOKIE, WEB.sign({"uid": "1", "name": "x", "exp": clock.time() - 1}))
    assert site.get("/api/state").json()["me"] is None
    token = WEB.sign({"uid": "1", "name": "x", "exp": clock.time() + 60})
    monkeypatch.setenv("DESK_WEB_SECRET", "a-different-secret")
    assert WEB.unsign(token) is None


def test_dev_sign_in_is_off_unless_asked_for(site, monkeypatch):
    monkeypatch.delenv("DESK_WEB_DEV")
    monkeypatch.delenv("DISCORD_CLIENT_ID", raising=False)
    assert site.get("/login?as=1").status_code == 500          # falls through to Discord, which isn't set up here
    assert site.get("/api/state").json()["dev"] is False


def test_discord_sign_in_sends_you_to_discord_and_checks_the_state_on_return(site, monkeypatch):
    monkeypatch.setenv("DISCORD_CLIENT_ID", "123")
    monkeypatch.setenv("DISCORD_CLIENT_SECRET", "shh")
    monkeypatch.setenv("DESK_WEB_URL", "https://desk.example.com")
    r = site.get("/login")
    assert r.status_code == 303 and r.headers["location"].startswith("https://discord.com/oauth2/authorize?")
    assert "redirect_uri=https%3A%2F%2Fdesk.example.com%2Fauth%2Fcallback" in r.headers["location"]
    assert "scope=identify+guilds.members.read" in r.headers["location"]
    state = r.headers["location"].split("state=")[1].split("&")[0]
    assert site.get("/auth/callback?code=abc&state=wrong").status_code == 400

    async def member(code, redirect_uri):
        assert (code, redirect_uri) == ("abc", "https://desk.example.com/auth/callback")
        return {"id": 77, "name": "Anthon", "avatar": "a1b2", "member": True}
    monkeypatch.setattr(WEB, "discord_identity", member)
    assert site.get(f"/auth/callback?code=abc&state={state}").status_code == 303
    assert site.get("/api/state").json()["me"] == {"id": "77", "name": "Anthon", "avatar": "a1b2"}


def test_someone_outside_the_server_is_turned_away(site, monkeypatch):
    monkeypatch.setenv("DISCORD_CLIENT_ID", "123")
    monkeypatch.setenv("DISCORD_CLIENT_SECRET", "shh")
    state = site.get("/login").headers["location"].split("state=")[1].split("&")[0]

    async def stranger(code, redirect_uri):
        return {"id": 5, "name": "Stranger", "avatar": None, "member": False}
    monkeypatch.setattr(WEB, "discord_identity", stranger)
    r = site.get(f"/auth/callback?code=abc&state={state}")
    assert r.status_code == 403 and "members of the server" in r.json()["error"]
    assert site.get("/api/state").json()["me"] is None


# ------------------------------------------------------------------ saving picks
def test_saving_stores_the_entry_where_the_bot_reads_it(site):
    sign_in(site)
    picks = {**full_senate(), "US-HOUSE": "D", "GA-GOV": "D", "TX-15": "D"}
    r = site.put("/api/picks", json={"picks": picks, "house_dem_seats": "240"}, headers=JSON)
    assert r.status_code == 200
    body = r.json()
    assert body["house_dem_seats"] == 240 and body["completion"]["parts"]["senate"] == [35, 35]
    assert body["completion"]["missing"] == ["governor", "battleground"]
    stored, meta = WEB.store().user_predictions(1)
    assert stored == picks and meta["house_dem_seats"] == 240 and meta["saved_at"] == BEFORE
    assert WEB.store().setting("web_saves") == [{"uid": 1, "first": True, "t": BEFORE}]
    site.put("/api/picks", json={"picks": picks, "house_dem_seats": None}, headers=JSON)
    assert WEB.store().setting("web_saves")[-1]["first"] is False
    assert site.get("/api/state").json()["entry"]["house_dem_seats"] is None


@pytest.mark.parametrize("picks, seats, why", [
    ({"GA-SEN": "I"}, None, "GA-SEN has no I candidate"),
    ({"NOPE-01": "D"}, None, "NOPE-01 isn't a race in the game"),
    ({"US-SENATE": "D"}, None, "US-SENATE isn't a race in the game"),      # it's worked out from the seats
    ({"AL-01": "R"}, None, "AL-01 isn't a race in the game"),               # a real seat, but not a battleground
    ({}, 436, "tiebreaker"), ({}, "many", "tiebreaker"),
])
def test_an_invalid_entry_is_refused_whole(site, picks, seats, why):
    sign_in(site)
    r = site.put("/api/picks", json={"picks": picks, "house_dem_seats": seats}, headers=JSON)
    assert r.status_code == 422 and why in r.json()["error"]
    assert WEB.store().predictors() == []


def test_an_independent_can_be_picked_where_one_is_running(site):
    sign_in(site)
    assert site.put("/api/picks", json={"picks": {"NE-SEN": "I"}}, headers=JSON).status_code == 200


def test_picks_lock_at_the_deadline_unless_an_admin_unlocked_you(site):
    sign_in(site)
    clock.set(P.deadline().timestamp() + 60)
    assert site.get("/api/state").json()["open"] is False
    r = site.put("/api/picks", json={"picks": {"US-HOUSE": "D"}}, headers=JSON)
    assert r.status_code == 403 and r.json()["error"] == "Predictions are locked."
    WEB.store().set_setting("picks_unlocked", {"1": clock.time() + 900})      # what /unlockpicks writes
    assert site.get("/api/state").json()["open"] is True
    assert site.put("/api/picks", json={"picks": {"US-HOUSE": "D"}}, headers=JSON).status_code == 200


def test_a_save_needs_the_header_a_form_on_another_site_cant_send(site):
    sign_in(site)
    assert site.put("/api/picks", json={"picks": {}}).status_code == 400


# ------------------------------------------------------------------ leaderboard, entries, the night
def two_entries(site):
    s = WEB.store()
    s.save_predictions(1, {**full_senate(20), "US-HOUSE": "D"}, 240)
    s.save_predictions(2, {"GA-SEN": "R", "US-HOUSE": "R"}, None)
    s.set_setting("names", {"1": "Ann", "2": "Bo"})
    return s


def test_before_the_deadline_the_board_can_hide_picks(site, monkeypatch):
    two_entries(site)
    monkeypatch.setattr(P, "config", lambda: {"hide_predictions_until_deadline": True})
    b = site.get("/api/leaderboard").json()
    assert b["hidden"] and [r["name"] for r in b["rows"]] == ["Ann", "Bo"] and "picks" not in b["rows"][0]
    assert b["rows"][1]["missing"] == ["Senate", "governors", "House seats", "tiebreaker"]
    assert site.get("/api/entry/1").status_code == 403
    sign_in(site, 1)
    assert site.get("/api/entry/1").status_code == 200          # your own is always yours to see


def test_the_board_scores_with_the_bots_rules(site):
    s = two_entries(site)
    clock.set(AFTER)
    s.call("GA-SEN", "D", {"how": "test"})
    s.call("US-HOUSE", "D", {"how": "test"})
    b = site.get("/api/leaderboard").json()
    assert not b["hidden"] and [(r["name"], r["points"]) for r in b["rows"]] == [("Ann", 7), ("Bo", 0)]
    ann = b["rows"][0]
    assert ann["cats"]["senate"][:2] == [1, 0] and ann["marks"]["US-HOUSE"] is True and ann["flips"] == 1
    assert ann["cats"]["senate_control"] == [0, 0, 1, 1] and b["open_races"] > 0 and not b["closed"]
    e = site.get("/api/entry/2").json()
    assert e["name"] == "Bo" and e["marks"] == {"GA-SEN": False, "US-HOUSE": False}
    assert site.get("/api/entry/999").status_code == 404


def test_a_call_from_before_polls_close_scores_nothing_on_the_site_either(site):
    s = two_entries(site)
    s.call("GA-SEN", "D", {"how": "rehearsal"})                # stamped a day before the deadline
    clock.set(AFTER)
    assert [r["points"] for r in site.get("/api/leaderboard").json()["rows"]] == [0, 0]


def test_the_night_page_follows_the_desk(site):
    s = WEB.store()
    clock.set(AFTER)
    assert site.get("/api/night").json()["recent"] == []
    s.call("GA-SEN", "D", {"how": "test"})
    s.call("TX-15", "D", {"how": "test"})
    s.uncall("TX-15", {"how": "test"})
    n = site.get("/api/night").json()
    assert n["senate"]["counts"]["D"] == R.SENATE_HOLDOVER["D"] + 1 and n["senate"]["majority"] == 51 and n["senate"]["clinched"] is None
    assert [b["key"] for b in n["bodies"]] == ["senate", "house", "governor"]
    assert [(x["action"], x["race"]) for x in n["recent"]] == [("uncall", "TX-15"), ("call", "TX-15"), ("call", "GA-SEN")]
    assert n["recent"][2]["name"] == "Jon Ossoff" and n["recent"][2]["flip"] is False and n["recent"][1]["rated"]
    assert n["house"]["uncalled"] == 435 and n["calls"] == {"GA-SEN": "D"}


def test_the_page_and_its_files_are_served(site):
    assert "Decision Desk" in site.get("/").text
    assert site.get("/static/app.js").status_code == 200 and site.get("/static/app.css").status_code == 200
    assert site.get("/fonts/DMSerifDisplay-Regular.ttf").status_code == 200


def test_pick_rules_agree_with_scoring():
    assert P.pickable() >= set(S.completion({})["parts"]) - {"senate", "house_control", "governor", "battleground"}
    cat = P.catalogue()
    secs = {x["key"]: x for x in cat["sections"]}
    assert {r["id"] for r in secs["senate"]["races"]} == set(R.races_of("senate"))
    assert all(len(r["options"]) >= 2 and all(o["color"] for o in r["options"]) for x in cat["sections"] for r in x["races"])
    assert set(P.safe_fill()) <= P.pickable() and set(P.ratings_fill()) <= P.pickable()


# ------------------------------------------------------------------ data packs and servers
@pytest.fixture
def desks(monkeypatch, tmp_path):
    """A client where every data pack and server has its own database, as it does for real."""
    import packs
    import tenancy as T
    monkeypatch.setenv("DESK_WEB_SECRET", "test-secret")
    monkeypatch.setenv("DESK_WEB_DEV", "1")
    monkeypatch.setenv("DESK_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("GUILD_ID", "111")
    monkeypatch.delenv("DESK_DB", raising=False)
    monkeypatch.delenv("DESK_FAKE_NOW", raising=False)
    T.forget()
    WEB.use_store(None)
    clock.set(BEFORE)
    with TestClient(WEB.app, follow_redirects=False) as client:
        yield client
    T.forget()
    packs.activate(packs.DEFAULT)


def choose(client, **what):
    assert client.post("/api/choose", json=what, headers=JSON).status_code == 200


def test_switching_data_packs_switches_the_whole_game(desks):
    sign_in(desks)
    assert desks.put("/api/picks", json={"picks": {"GA-SEN": "D"}}, headers=JSON).status_code == 200
    choose(desks, pack="2027-chicago-mayor")
    s = desks.get("/api/state").json()
    assert s["pack"] == "2027-chicago-mayor" and s["entry"]["picks"] == {}           # its own records
    assert [x["key"] for x in s["races"]["sections"]] == ["city", "bets"] and s["races"]["bodies"] == {}
    mayor = s["races"]["sections"][0]["races"][0]
    assert mayor["id"] == "CHI-MAYOR" and [o["party"] for o in mayor["options"]] == ["A", "B", "C", "D"]
    assert desks.put("/api/picks", json={"picks": {"GA-SEN": "D"}}, headers=JSON).status_code == 422     # not in this pack
    r = desks.put("/api/picks", json={"picks": {"CHI-MAYOR": "B", "CHI-RUNOFF": "YES"}, "house_dem_seats": 41}, headers=JSON)
    assert r.status_code == 200 and r.json()["completion"]["share"] == 1
    assert desks.put("/api/picks", json={"picks": {"CHI-MAYOR": "Z"}}, headers=JSON).status_code == 422
    choose(desks, pack="2026-general")
    assert desks.get("/api/state").json()["entry"]["picks"] == {"GA-SEN": "D"}       # still there
    choose(desks, pack="no-such-pack")
    assert desks.get("/api/state").json()["pack"] == "2026-general"                  # ignored


def test_a_presidential_pack_adds_states_up_to_the_electoral_college(desks):
    import packs
    sign_in(desks)
    choose(desks, pack="2028-general")
    s = desks.get("/api/state").json()
    pres = s["races"]["sections"][0]
    assert (pres["key"], pres["kind"], pres["body"], len(pres["races"])) == ("president", "map", "electoral_college", 56)
    assert s["races"]["bodies"]["electoral_college"]["majority"] == 270
    assert sum(r["weight"] for r in pres["races"]) == 538 and pres["implied"]["name"] == "The presidency"
    picks = {r["id"]: r["holder"] for r in pres["races"]}
    assert desks.put("/api/picks", json={"picks": picks, "house_dem_seats": 312}, headers=JSON).status_code == 200
    assert desks.put("/api/picks", json={"picks": {"US-PRES": "D"}}, headers=JSON).status_code == 422      # worked out, not picked
    with packs.using("2028-general"):
        assert S.grade(picks, {})["implied"] == {"US-PRES": "R"}


def test_a_server_is_only_shown_to_its_own_members(desks):
    import tenancy as T
    T.store_for(222).save_predictions(9, {"GA-SEN": "R"}, None)
    T.store_for(222).set_setting("names", {"9": "Bo"})
    T.store_for(111).save_predictions(1, {"GA-SEN": "D"}, None)
    sign_in(desks, 1, "Ann")
    s = desks.get("/api/state").json()
    assert [x["id"] for x in s["servers"]] == ["111"] and s["server"] == "111"
    choose(desks, server="222")
    assert desks.get("/api/state").json()["server"] == "111"                         # not a member: stays home
    assert [r["id"] for r in desks.get("/api/leaderboard").json()["rows"]] == ["1"]
    sign_in(desks, 9, "Bo")
    choose(desks, server="222")
    s = desks.get("/api/state").json()
    assert s["server"] == "222" and [x["id"] for x in s["servers"]] == ["111", "222"]
    assert [r["name"] for r in desks.get("/api/leaderboard").json()["rows"]] == ["Bo"]


def test_the_global_board_is_opt_in_for_servers_and_people(desks):
    import tenancy as T
    T.store_for(111).save_predictions(1, {"GA-SEN": "D"}, None)
    T.store_for(222).save_predictions(9, {"GA-SEN": "R"}, None)
    T.store_for(222).set_setting("names", {"9": "Bo"})
    T.store_for(222).set_setting("server_name", "The other place")
    sign_in(desks, 1, "Ann")
    desks.get("/api/state")
    clock.set(AFTER)
    g = desks.get("/api/global").json()
    assert g["rows"] == [] and g["servers"] == 0 and g["joined"] is False
    T.set_joined(111, True)
    T.set_joined(222, True)
    T.store_for(222).procall("GA-SEN", "AP", "D", 0)
    g = desks.get("/api/global").json()
    assert [(r["name"], r["server"], r["points"]) for r in g["rows"]] == [("Ann", "Server 111", 1), ("Bo", "The other place", 0)]
    assert g["servers"] == 2 and g["joined"] and not g["out"]
    assert desks.post("/api/global/me", json={"out": True}, headers=JSON).json() == {"out": True}
    g = desks.get("/api/global").json()
    assert [r["name"] for r in g["rows"]] == ["Bo"] and g["out"] is True

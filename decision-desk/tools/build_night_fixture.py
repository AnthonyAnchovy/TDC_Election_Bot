"""Build the replay fixture for one election night from the bot's own records.

    python tools/build_night_fixture.py --db desk.db --export DIR --field field_history.json --out tests/fixtures/2026

Reads three things that never go in the repo, because they hold Discord user IDs and names:
  desk.db             the bot's database from the night (votes, calls, outlet calls, predictions)
  a Discord export    DiscordChatExporter JSON for the board channel, all authors. It supplies what the database
                      doesn't keep: withdrawn votes, and who holds the Election Junkie role
  field_history.json  the Senate field's snapshots (no personal data; copied as is, gzipped)

Writes, into the fixture folder, files whose names no running bot would ever create (they all say ".fixture"):
  night.fixture.json         every input event in order, the roster, the predictions, and what the bot did with them
                             (the `expected` block). replay.py feeds the events back through desk.py and the tests
                             compare the outcome with `expected`.
  desk.fixture.sqlite        the night's database itself, rebuilt row by row with people replaced by numbers. It is
                             what a real desk.db looks like, so tests can open one with today's code.
  countywatch.fixture.json   the county counts County Watch was holding (2026: Mississippi, all 82 counties)
  field.fixture.json.gz      the Senate field's snapshots, as recorded
People are numbers in all of them, the same number everywhere. Tests copy the database before opening it, and
packs.db_path() refuses to run the bot on a file named like these.

Two things about the roster aren't recorded anywhere and are worked out here:
  votes needed       read off each vote's board post ("2 of 3 votes needed"); a change becomes a roster event
  whose vote counts  the role holders, always. People who voted without the role (admins) are tried as counting
                     from each moment one of them voted, and the moment that reproduces the night's calls is kept.
                     The tally on every vote's board post is stored with the vote, so the tests can check the
                     result against a second source.
"""
from __future__ import annotations

import argparse
import gzip
import json
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import desk as DESK  # noqa: E402
import races as R  # noqa: E402
import replay as RP  # noqa: E402
import scoring as S  # noqa: E402

JUNKIE_ROLE = "Election Junkie"
VOTED = re.compile(r"^🗳️ \*\*(.+?)\*\* has voted to call (?:the (.+?) race|(control of the House)) for .*?\n-# (\d+) of (\d+) votes", re.S)
WITHDREW = re.compile(r"^↩️ \*\*(.+?)\*\* withdrew their vote on (.+)\.$")
CARD = re.compile(r"^🚨 \*\*DECISION DESK PROJECTION\*\*\n\*\*.+?\*\* \S+ wins? (?:the (.+?) race|(control of the House))")
SAFE_ROLLUP = re.compile(r"Safe House seats projected \((\d+)\)")
BY_NAME = re.compile(r"^(Called|Retracted) by (.+?)( \(override\))?$")


def export_messages(folder: Path) -> list[dict]:
    out = []
    for f in sorted(folder.glob("*.json")):
        out += json.loads(f.read_text(encoding="utf-8")).get("messages", [])
    return out


def people(messages: list[dict]) -> dict[int, dict]:
    """user id -> {"names": every name they posted under, "junkie": holds the role}."""
    found: dict[int, dict] = {}
    for m in messages:
        for a in [m["author"]] + ([m["interaction"]["user"]] if m.get("interaction") else []):
            if a.get("isBot"):
                continue
            p = found.setdefault(int(a["id"]), {"names": set(), "junkie": False})
            p["names"] |= {a.get("nickname") or a["name"], a["name"]}
            p["junkie"] |= any(r["name"] == JUNKIE_ROLE for r in a.get("roles", []))
    return found


def build(db_path: Path, export_dir: Path) -> dict:
    db = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    msgs = export_messages(export_dir)
    who = people(msgs)
    by_name = {n: uid for uid, p in who.items() for n in p["names"]}
    by_label = {r["label"]: rid for rid, r in R.RACES.items()}
    by_label["control of the House"] = "US-HOUSE"

    votes = db.execute("SELECT user_id, race_id, kind, party, ts, active FROM desk_votes ORDER BY id").fetchall()
    history = [(a, rid, p, json.loads(d or "{}"), ts) for a, rid, p, d, ts in
               db.execute("SELECT action, race_id, party, detail, ts FROM history ORDER BY id")]
    procalls = db.execute("SELECT race_id, outlet, party, ts, logged_by FROM procalls ORDER BY ts").fetchall()

    # people become numbers, in the order they first appear
    order: list[int] = []
    for uid in [v[0] for v in votes] + [r[0] for r in db.execute("SELECT user_id FROM prediction_meta ORDER BY saved_at")]:
        if uid not in order:
            order.append(uid)
    num = {uid: i + 1 for i, uid in enumerate(order)}

    def anon_how(how: str) -> tuple[str, int | None]:
        """'Called by Anthon (override)' -> ('Called by user2 (override)', 2). Majority and outlet wording is left alone."""
        m = BY_NAME.match(how or "")
        if not m or m.group(2) not in by_name:
            return how, None
        n = num[by_name[m.group(2)]]
        return f"{m.group(1)} by user{n}{m.group(3) or ''}", n

    events = []
    overrides = []      # (race, ts) of each /realcall, whose own vote row isn't a separate event
    for action, rid, party, detail, ts in history:
        how, n = anon_how(detail.get("how", ""))
        if action == "call" and "(override)" in how:
            events.append({"t": ts, "do": "realcall", "user": n, "race": rid, "party": party})
            overrides.append((rid, ts))
        elif action == "uncall" and how.startswith("Retracted by user"):
            events.append({"t": ts, "do": "forceuncall", "user": n, "race": rid})
    for uid, rid, kind, party, ts, _active in votes:
        if kind == "call" and any(r == rid and abs(ts - t) < 1 for r, t in overrides):
            continue
        events.append({"t": ts, "do": "vote" if kind == "call" else "uncallvote", "user": num[uid], "race": rid,
                       **({"party": party} if kind == "call" else {})})
    for action, rid, _party, detail, ts in history:
        # a majority call with no vote just before it: standing votes were settled by hand after the roster changed
        if action == "call" and "Election Junkies" in detail.get("how", "") and not any(
                e["do"] == "vote" and e["race"] == rid and -1 < ts - e["t"] < 10 for e in events):
            events.append({"t": ts, "do": "settle", "race": rid})
    followed = {(rid, d.get("outlet")): ts for a, rid, _p, d, ts in history if a == "call" and d.get("outlet")}
    announced = [(datetime.fromisoformat(m["timestamp"]).timestamp(), m.get("content") or "") for m in msgs
                 if m["author"].get("isBot") and (m.get("content") or "").startswith("📡 **")]

    def was_posted(rid, outlet, ts):
        return any(abs(t - ts) < 30 and c.startswith(f"📡 **{outlet}**") and R.RACES[rid]["label"] in c
                   for t, c in announced)

    for rid, outlet, party, ts, logged_by in procalls:
        if not logged_by:
            via = "auto"                    # read from Bluesky or NYT's data
        elif R.RACES[rid]["type"] != "house" and abs(followed.get((rid, outlet), -1e9) - ts) < 2:
            via = "callnyt"                 # /callnyt made a Senate or governor race official
        elif outlet == "NYT" and not was_posted(rid, outlet, ts):
            via = "callnyt"                 # /callnyt logging, without a post, a call the desk had already made
        else:
            via = "manual"                  # /procall, which posts to the board
        events.append({"t": ts, "do": "outlet", "via": via, "race": rid, "party": party, "outlet": outlet,
                       "user": num.get(logged_by, 0)})
    for m in msgs:
        hit = WITHDREW.match(m.get("content") or "") if m["author"].get("isBot") else None
        if hit and hit.group(1) in by_name and hit.group(2) in by_label:
            events.append({"t": datetime.fromisoformat(m["timestamp"]).timestamp(), "do": "unvote",
                           "user": num[by_name[hit.group(1)]], "race": by_label[hit.group(2)]})
    # the tally each vote's board post showed, matched to the vote by person, race and time
    posts = []
    for m in msgs:
        hit = VOTED.match(m.get("content") or "") if m["author"].get("isBot") else None
        if hit and hit.group(1) in by_name:
            posts.append([datetime.fromisoformat(m["timestamp"]).timestamp(), num.get(by_name[hit.group(1)]),
                          by_label.get(hit.group(2) or hit.group(3)), int(hit.group(4)), int(hit.group(5))])
    for e in events:
        if e["do"] == "vote":
            near = [p for p in posts if p[1] == e["user"] and p[2] == e["race"] and abs(p[0] - e["t"]) < 3]
            if near:
                best = min(near, key=lambda p: abs(p[0] - e["t"]))
                posts.remove(best)
                e["posted"] = best[3:]
    events.sort(key=lambda e: e["t"])
    need = None
    for e in list(events):          # the votes needed, whenever a post shows it changed
        if e.get("posted") and e["posted"][1] != need:
            need = e["posted"][1]
            events.append({"t": e["t"] - 0.001, "do": "roster", "threshold": need})
    events.sort(key=lambda e: e["t"])

    predictions = {}
    for uid, saved, seats in db.execute("SELECT user_id, saved_at, house_dem_seats FROM prediction_meta"):
        picks = dict(db.execute("SELECT race_id, party FROM predictions WHERE user_id = ?", (uid,)))
        predictions[str(num[uid])] = {"picks": picks, "house_dem_seats": seats, "saved_at": saved}

    calls = {rid: party for rid, party in db.execute("SELECT race_id, party FROM calls")}
    pro = [{"race_id": r, "outlet": o, "party": p, "ts": ts} for r, o, p, ts, _ in procalls]
    entries = {int(n): (p["picks"], {"house_dem_seats": p["house_dem_seats"]}) for n, p in predictions.items()}
    board = S.leaderboard(entries, S.truth(calls, pro), None, None)
    first = events[0]["t"]          # posts before the night's first event are rehearsals
    tonight = [m.get("content") or "" for m in msgs if m["author"].get("isBot")
               and datetime.fromisoformat(m["timestamp"]).timestamp() >= first]
    rollups = [int(SAFE_ROLLUP.search(c).group(1)) for c in tonight if SAFE_ROLLUP.search(c)]
    cards = [by_label.get(m.group(1) or m.group(2)) for m in map(CARD.match, tonight) if m]
    carded = {rid: cards.count(rid) for rid in set(cards)}
    called = [rid for a, rid, _p, _d, _ts in history if a == "call" and not DESK.is_safe_seat(rid)]
    no_card = sorted(rid for rid in set(called) if called.count(rid) > carded.get(rid, 0))

    junkies = sorted(num[u] for u in order if who.get(u, {}).get("junkie"))
    return {
        "_private": {"num": num, "by_name": by_name},        # for the database copy; never written out
        "_note": "Election night, built by tools/build_night_fixture.py. People are numbers; times are Unix seconds.",
        "roster": {"junkies": junkies, "counted": junkies, "threshold": "majority"},
        "events": events,
        "predictions": predictions,
        "expected": {
            "history": [{"action": a, "race": rid, "party": p, "how": anon_how(d.get("how", ""))[0],
                         "outlet": d.get("outlet"), "t": ts} for a, rid, p, d, ts in history],
            "calls": calls,
            "standing_votes": sorted([num[u], rid, kind, party] for u, rid, kind, party, _ts, active in votes if active),
            "procalls": sorted([r, o, p] for r, o, p, _ts, _by in procalls),
            "scores": [{"user": r["user_id"], "points": r["points"], "flips": r["flips"]} for r in board],
            "discord": {"projection_cards": len(cards), "safe_seats_rolled_up": sum(rollups), "called_without_a_card": no_card},
        },
    }


def fit_counted(night: dict) -> str:
    """Find when votes from people without the role started counting, and add it as a roster event. Returns a line
    saying what was found."""
    junkies = set(night["roster"]["junkies"])
    others = sorted({e["user"] for e in night["events"] if e["do"] in ("vote", "uncallvote", "realcall")} - junkies)
    if not others:
        return "everyone who voted holds the role"
    everyone = sorted(junkies | set(others))
    times = sorted(e["t"] for e in night["events"] if e["do"] == "vote" and e["user"] in others)
    best = None
    for t in [None] + times:        # None: they never counted
        trial = dict(night)
        trial["events"] = sorted(night["events"] + ([{"t": t - 0.002, "do": "roster", "counted": everyone}] if t else []),
                                 key=lambda e: e["t"])
        miss = len(RP.differences(trial, RP.replay(trial), most=None))
        if best is None or miss < best[0]:
            best = (miss, t, trial["events"])
        if not miss:
            break
    miss, t, events = best
    night["events"] = events
    when = datetime.fromtimestamp(t).astimezone().isoformat(timespec="seconds") if t else "never"
    return (f"votes from {len(others)} people without the role count from {when}"
            + ("" if not miss else f" (closest fit: {miss} differences from the night remain)"))


def write_db(src: Path, out: Path, num: dict[int, int], by_name: dict[str, int]):
    """An anonymised copy of the night's database: a new file with the same tables and rows, user IDs replaced by
    the fixture's numbers and names in "Called by Anthon (override)" replaced the same way. Built fresh, row by row,
    so nothing of the original lingers in unused pages."""
    from store import Store
    for stale in (out, out.with_name(out.name + "-journal")):
        stale.unlink(missing_ok=True)
    old = sqlite3.connect(f"file:{src.as_posix()}?mode=ro", uri=True)
    new = Store(str(out)).db

    def who(uid):
        return num.get(uid, 0) if uid else 0

    def detail(text):
        d = json.loads(text or "{}")
        m = BY_NAME.match(d.get("how", ""))
        if m and m.group(2) in by_name:
            d["how"] = f"{m.group(1)} by user{num[by_name[m.group(2)]]}{m.group(3) or ''}"
        return json.dumps(d)

    new.executemany("INSERT INTO calls VALUES (?,?,?,?)",
                    [(r, p, t, detail(d)) for r, p, t, d in old.execute("SELECT race_id, party, called_at, detail FROM calls")])
    new.executemany("INSERT INTO history VALUES (?,?,?,?,?,?)",
                    [(i, t, a, r, p, detail(d)) for i, t, a, r, p, d in old.execute("SELECT id, ts, action, race_id, party, detail FROM history")])
    new.executemany("INSERT INTO desk_votes VALUES (?,?,?,?,?,?,?)",
                    [(i, who(u), r, k, p, t, a) for i, u, r, k, p, t, a in
                     old.execute("SELECT id, user_id, race_id, kind, party, ts, active FROM desk_votes")])
    new.executemany("INSERT INTO procalls VALUES (?,?,?,?,?)",
                    [(r, o, p, t, who(u)) for r, o, p, t, u in old.execute("SELECT race_id, outlet, party, ts, logged_by FROM procalls")])
    new.executemany("INSERT INTO predictions VALUES (?,?,?,?)",
                    [(who(u), r, p, t) for u, r, p, t in old.execute("SELECT user_id, race_id, party, ts FROM predictions")])
    new.executemany("INSERT INTO prediction_meta VALUES (?,?,?)",
                    [(who(u), t, s) for u, t, s in old.execute("SELECT user_id, saved_at, house_dem_seats FROM prediction_meta")])
    new.executemany("INSERT INTO sent_alerts VALUES (?,?)", old.execute("SELECT key, ts FROM sent_alerts").fetchall())
    for key, value in old.execute("SELECT key, value FROM settings"):
        v = json.loads(value)
        if key == "picks_unlocked":
            v = {str(who(int(u))): t for u, t in v.items()}
        elif key == "state_watch":
            v = {st: [who(u) for u in users] for st, users in v.items()}
        elif key == "results_dm_users":
            v = [who(u) for u in v]
        elif key == "names":
            continue
        new.execute("INSERT INTO settings VALUES (?,?)", (key, json.dumps(v)))
    new.execute("INSERT OR REPLACE INTO settings VALUES ('names', ?)",
                (json.dumps({str(n): f"user{n}" for n in sorted(set(num.values()))}),))
    new.commit()
    new.execute("VACUUM")
    new.close()
    raw = out.read_bytes()
    leaked = [str(u) for u in num if str(u).encode() in raw] + [n for n in by_name if len(n) > 3 and n.encode() in raw]
    if leaked:
        out.unlink()
        raise SystemExit(f"The database copy still held {len(leaked)} real IDs or names, so it wasn't kept.")


def countywatch(src: Path) -> dict:
    """The county counts County Watch was holding when the database was copied, in the shape cw_model.project takes:
    {state: {fips: {"D", "R", "O", "pct_in", "complete"}}}."""
    db = sqlite3.connect(f"file:{src.as_posix()}?mode=ro", uri=True)
    row = db.execute("SELECT value FROM settings WHERE key = 'state_watch_last'").fetchone()
    held = json.loads(row[0]) if row else {}
    return {st: {fips: {"D": d, "R": r, "O": o, "pct_in": pct, "complete": bool(done)}
                 for fips, (d, r, o, pct, done) in counties.items()} for st, counties in held.items()}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--db", type=Path, required=True)
    ap.add_argument("--export", type=Path, required=True, help="folder of DiscordChatExporter JSON files")
    ap.add_argument("--field", type=Path, help="field_history.json from the night")
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    night = build(a.db, a.export)
    private = night.pop("_private")
    print(fit_counted(night))
    (a.out / "night.fixture.json").write_text(json.dumps(night, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    write_db(a.db, a.out / "desk.fixture.sqlite", private["num"], private["by_name"])
    print(f"desk.fixture.sqlite: {(a.out / 'desk.fixture.sqlite').stat().st_size:,} bytes, people as numbers")
    held = countywatch(a.db)
    (a.out / "countywatch.fixture.json").write_text(json.dumps(
        {"_note": "County counts County Watch was holding when the night's database was copied (its state_watch_last "
                  "setting), shaped for cw_model.project.", **held}, separators=(",", ":")), encoding="utf-8")
    print("countywatch.fixture.json: " + (", ".join(f"{st} {len(c)} counties" for st, c in held.items()) or "nothing held"))
    left = RP.differences(night, RP.replay(night))
    print("replay reproduces the night" if not left else "replay differs from the night:\n  " + "\n  ".join(left[:10]))
    print(f"night.fixture.json: {len(night['events'])} events, {len(night['expected']['history'])} calls and retractions")
    if a.field:
        raw = json.dumps(json.loads(a.field.read_text(encoding="utf-8")), separators=(",", ":")).encode()
        with gzip.GzipFile(a.out / "field.fixture.json.gz", "wb", mtime=0) as f:
            f.write(raw)
        print(f"field.fixture.json.gz: {len(raw):,} bytes before gzip")


if __name__ == "__main__":
    main()

"""Check this folder is ready to run the bot, without connecting to Discord:  python preflight.py

It reads .env (and never prints what's in it), loads the data pack, and says what the bot would do if started
now: where it keeps records, which feeds it would read, and what would post by itself. Exit code 1 means something
has to be fixed first.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).parent
TEST_HINT = "For a test server, put DESK_DATA=data-test and DESK_AUTOCALLS=off in .env (docs/live-test.md)."


def check(env: dict | None = None, now: datetime | None = None) -> tuple[list[str], list[str], list[str]]:
    """(what must be fixed, what to know, what's fine) for the settings in `env` (this process's, unless given)."""
    env = os.environ if env is None else env
    bad, note, ok = [], [], []

    token = (env.get("DISCORD_TOKEN") or "").strip()
    if not token:
        bad.append("DISCORD_TOKEN is empty. Copy .env.example to .env and paste the bot's token in.")
    elif token.count(".") != 2 or len(token) < 50:
        bad.append("DISCORD_TOKEN doesn't look like a bot token (three parts joined by dots). Copy it again from the "
                   "developer portal: Bot > Reset Token.")
    else:
        ok.append("A bot token is set.")
    gid = (env.get("GUILD_ID") or "").strip()
    if not gid:
        note.append("GUILD_ID is empty, so commands sync everywhere and can take up to an hour to appear. Set it to "
                    "your server's ID to get them at once.")
    elif not gid.isdigit():
        bad.append("GUILD_ID has to be the server's numeric ID (right-click the server icon > Copy Server ID).")
    else:
        ok.append("Commands will appear at once in the home server.")

    import packs
    try:
        pack = packs.load(env.get("DESK_PACK") or packs.default_id(), fresh=True)
    except packs.PackError as exc:
        bad.append(str(exc))
        return bad, note, ok
    packs.activate(pack.id)
    import feeds as FEEDS
    import night as N
    ok.append(f"Data pack: {pack.name} ({len(pack.races)} races, election day {pack.data['election_day']}).")
    if pack.data.get("draft"):
        note.append("This pack is marked as a draft: candidates and ratings aren't filled in.")

    data = env.get("DESK_DATA")
    legacy = HERE / "desk.db"
    if data:
        ok.append(f"Records are kept in {Path(data) if Path(data).is_absolute() else HERE / data}.")
    elif pack.id == packs.DEFAULT and legacy.exists():
        note.append(f"Records are the real 2026 database, {legacy.name}. Anything done in Discord changes it. " + TEST_HINT)
    else:
        note.append(f"Records are kept in {packs.DATA}, shared with any other copy of the bot run from this folder. " + TEST_HINT)

    try:
        cfg = json.loads((HERE / "config.json").read_text(encoding="utf-8"))
        ok.append(f"The bot posts in #{cfg.get('board_channel')} and #{cfg.get('desk_channel')}, and people with the "
                  f"\"{cfg.get('junkie_role')}\" role vote on calls. All three have to exist in the server.")
    except (OSError, ValueError) as exc:
        bad.append(f"config.json can't be read: {exc}")

    now = now or (datetime.fromisoformat(env["DESK_FAKE_NOW"]) if env.get("DESK_FAKE_NOW") else datetime.now(N.ET))
    if env.get("DESK_FAKE_NOW"):
        note.append(f"The clock is held at {env['DESK_FAKE_NOW']} (DESK_FAKE_NOW). Remove it for a real night.")
    quiet = (env.get("DESK_AUTOCALLS") or "on").strip().lower() in ("off", "0", "false", "no")
    reading = bool(FEEDS.nyt().get("house") or FEEDS.bluesky_accounts())
    if not reading:
        ok.append("This pack names no outlets to read, so nothing is called by itself.")
    elif quiet:
        ok.append("Automatic calls start off (DESK_AUTOCALLS=off). /autocalls on turns them on in a server.")
    elif datetime.now(N.ET) >= FEEDS.reading_since():
        note.append("Automatic calls are on, and the time to start reading outlets has passed: within a minute of "
                    "starting, the bot posts every House call it finds in NYT's file and on Bluesky. For a test, "
                    "set DESK_AUTOCALLS=off.")
    else:
        ok.append(f"Automatic calls start reading outlets at {FEEDS.reading_since():%b %d, %I:%M %p %Z}.")
    if FEEDS.countywatch():
        start, end = FEEDS.night_window()
        if start <= now <= end:
            note.append("It is election night by the bot's clock: county feeds are pulled every 30 seconds and "
                        "alerts post to the board.")
        else:
            ok.append("County feeds are only pulled on election night, so they're idle now.")
    if env.get("MS_CLIENT_ID") and env.get("CLOUD_WORKBOOK"):
        note.append("The shared workbook is set up (MS_CLIENT_ID, CLOUD_WORKBOOK): the bot will write to the real one.")

    missing = [name for name in ("assets/fonts", "state_shapes.json", "config.json") if not (HERE / name).exists()]
    if missing:
        bad.append("Missing from this folder: " + ", ".join(missing))
    try:
        import discord  # noqa: F401
        import PIL  # noqa: F401
        import aiohttp  # noqa: F401
    except ImportError as exc:
        bad.append(f"A library isn't installed ({exc.name}). Run start_bot.bat once, or pip install -r requirements.txt.")
    return bad, note, ok


def main() -> int:
    if not (HERE / ".env").exists():
        print("There is no .env file here. Copy .env.example to .env and fill in DISCORD_TOKEN and GUILD_ID.")
        return 1
    from dotenv import load_dotenv
    load_dotenv(HERE / ".env")
    bad, note, ok = check()
    for mark, lines in (("OK   ", ok), ("NOTE ", note), ("FIX  ", bad)):
        for line in lines:
            print(mark + line)
    print()
    print("Not ready: fix the lines marked FIX." if bad else "Ready to start: start_bot.bat, or python bot.py.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

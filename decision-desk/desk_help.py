"""The /help guide: what each election-night command does, split into short sections.

Pure text, no Discord objects, so it can be checked without a bot. bot.py turns each section into a private
embed with a menu to switch sections.

Markers used in the text:
  🗳️ needs the Election Junkie role   🔧 admins only
  📢 posts in the channel for everyone   🔒 only you see the reply
"""
from __future__ import annotations

# (key, menu label, menu one-liner). "admin" is shown only to admins.
SECTIONS = [
    ("start", "Start here", "How the night works, in 60 seconds"),
    ("calls", "Calling races", "/call, /realcall, /procall, /uncall..."),
    ("watch", "Following the night", "/next, /board, /race, /results, /field..."),
    ("county", "County Watch", "/counties, /environment, /countyset..."),
    ("scores", "Scores & predictions", "/scoreboard, /leaderboard, /predictions..."),
    ("admin", "Admin tools", "Fixes, feeds and resets"),
]
LEGEND = "🗳️ Election Junkie only · 🔧 admin only · 📢 everyone in the channel sees it · 🔒 only you see it"


def sections_for(admin: bool) -> list[tuple[str, str, str]]:
    return [s for s in SECTIONS if admin or s[0] != "admin"]


def section(key: str, *, role: str, board: str, desk: str, junkie: bool, admin: bool, need: int) -> tuple[str, str]:
    """(title, body) for one section. `need` is how many votes an official call takes right now."""
    you = ("You have the **{role}** role, so you can vote on calls." if junkie else
           "You don't have the **{role}** role, so the 🗳️ commands won't work for you. "
           "Ask an admin if you should be voting.").format(role=role)
    if admin:
        you += " You're also an admin (🔧)."
    body = {
        "start": (
            "Start here",
            f"{you}\n\n"
            "**How a race gets called**\n"
            f"1. Polls close. The bot posts an alert in {board}. `/next` lists every closed race the desk hasn't called.\n"
            f"2. When you think a race is over, `/call race winner`. Your vote shows up in {board}.\n"
            f"3. Once **{need}** {role} {'vote agrees' if need == 1 else 'votes agree'}, the official call card "
            f"and map post in {desk}.\n"
            f"4. When AP, NYT, Wasserman or another outlet calls it, log it with `/procall`. "
            "That feeds `/scoreboard`, which shows who beat the pros.\n\n"
            "**Race names** look like `GA-SEN`, `AZ-GOV`, `PA-07`, `AK-AL`. `HOUSE` means control of the House. "
            "Start typing and autocomplete fills them in.\n\n"
            "**Good first commands:** `/next`, `/board`, `/closings`.\n\n"
            "Pick a section from the menu below for every command.\n"
            f"-# {LEGEND}"),
        "calls": (
            "Calling races",
            "**`/call race winner`** 🗳️\n"
            f"Your vote that a race is over. It posts in {board}. Once enough votes agree "
            f"(**{need}** right now), the official call posts in {desk}. Run it again with the other party to "
            "switch your vote.\n\n"
            "**`/unvote race`** 🗳️\n"
            "Takes back your vote, if the race hasn't been called yet.\n\n"
            "**`/realcall race winner`** 🗳️\n"
            "Makes the call official **right away**, with no majority. Use it when the race is clearly over. "
            "It also counts as your vote. It won't overwrite an existing call: `/uncall` first.\n\n"
            "**`/uncall race`** 🗳️\n"
            "A vote to retract an official call that looks wrong. It takes the same majority.\n\n"
            "**`/procall race winner outlet`** 🗳️\n"
            f"Logs a call by AP, Decision Desk HQ, NYT, CNN, Wasserman and others. It posts in {board} and "
            "feeds `/scoreboard`, but **never** changes the desk's own map. Logging the same outlet again updates it.\n\n"
            "-# The bot replies to you privately (🔒) and makes the public posts itself."),
        "watch": (
            "Following the night",
            "**`/next`** 📢 Closed races the desk hasn't called yet, with live vote tallies, outlet calls and the "
            "next poll closing. **Your go-to command.**\n\n"
            "**`/closings`** 📢 Tonight's poll-closing schedule. Alerts post by themselves as each wave closes.\n\n"
            "**`/board`** 📢 Seat counts for Senate, governors and House, the path to a majority, and recent calls.\n\n"
            "**`/map chamber`** 📢 The current official map: Senate, Governors or House.\n\n"
            "**`/race race`** 📢 Everything about one race: who holds it, its rating, call votes, the official call, "
            "outlet calls, and who predicted what.\n\n"
            "**`/seat race`** 📢 Background card: rating, incumbent, nominees, PVI, poll close and results links.\n\n"
            "**`/results [race]`** 📢 Live counts from the official state files (**FL, NC, VA only**), with a "
            "🔄 Refresh button. Leave `race` blank to see every tracked race. These counts never call a race.\n\n"
            "**`/earlywarning`** 📢 Early-closing House seats (by 7:30 PM ET) that tip off a good or bad night.\n\n"
            "**`/field [chamber] [timelapse]`** 🔒 Every race as a circle from safe D to safe R, sized by votes "
            f"counted. It also posts in {board} every 30 minutes by itself. `timelapse` replays the whole night."),
        "county": (
            "County Watch",
            "County-by-county analysis for 15 Senate races (AK, FL, GA, IA, KS, ME, MI, MN, MS, NC, NE, NH, OH, SC, TX). "
            "Most states pull results by themselves. Alerts post when projections move.\n\n"
            "**`/counties state`** 📢 The state's read: projection and its range, swing by county type, which key "
            "counties are ahead of or behind pace, turnout, and the biggest movers.\n\n"
            "**`/environment`** 📢 The national read: the swing from 2024 by county type, pooled across every "
            "fully counted county, and which races that swing would flip.\n\n"
            "**`/earlyvote state`** 📢 Early vote compared with 2022.\n\n"
            "**`/countyset state county dem rep [other] [done]`** 🗳️🔒\n"
            "Type in a county's count from NYT, AP or a county site, for states with **no live feed: MI, ME, NH, "
            "MS, KY**. Key counties (★) come first in autocomplete. Set `done` **only when every precinct is in**: "
            "only finished counties feed the projection.\n\n"
            "**`/countyclear state county`** 🗳️🔒 Removes a count you typed in by mistake.\n\n"
            "**`/countyfile file [state]`** 🗳️🔒 Upload a results file (a state's CSV, Excel or XML download, or your "
            "own county table) and the bot loads it.\n\n"
            "-# **OH, TX, KS:** use the County Watch grab button on the state's results site. The bot picks it up "
            "within 10 seconds."),
        "scores": (
            "Scores & predictions",
            "**`/scoreboard`** 📢 Each person's right and wrong calls, how often they called a race first, and how "
            "many minutes ahead of AP, NYT and Wasserman they were. It also scores the desk against the pros.\n\n"
            "**`/leaderboard`** 📢 Prediction game standings. Points come in as races are called.\n\n"
            "**`/predictions [member]`** 📢 Someone's picks on a map (yours by default), marked right or wrong.\n\n"
            "**`/compare member [other]`** 📢 Every race where two people picked differently.\n\n"
            "**`/cardpreview race winner`** 🔒 Preview the call card for any race. Only you see it.\n\n"
            "-# Predictions locked when the first polls closed (6 PM ET). Every official call ends with a "
            "list of who predicted it right."),
        "admin": (
            "Admin tools",
            "**Fixing calls**\n"
            "**`/forceuncall race`** 🔧 Retracts an official call immediately, with no vote.\n\n"
            "**Feeds (if numbers stop arriving)**\n"
            "**`/countyrefresh`** 🔧 Pulls every county feed and early vote now, rebuilds the workbook, and lists "
            "any feed errors.\n"
            "**`/countyfeed state link`** 🔧 Points SC or IA (Clarity sites) at tonight's results link. Use `clear` "
            "to undo it.\n"
            "**`/countytest state`** 🔧 / **`/resultstest state`** 🔧 Checks that a feed can be read, using its "
            "Nov 2024 file. For before the night.\n"
            "**`/results` says a file isn't up?** If FL, NC or VA posts under an unexpected name, paste the new "
            "download link as `url` in `results_sources.json` on the bot's PC. No restart needed.\n\n"
            "**Look & wrap-up**\n"
            "**`/cardtheme [theme]`** 🔧 Changes the look of call cards.\n"
            "**`/fieldtest`** 🔧 Tries the field on a simulated night. Only you see it.\n"
            "**`/finalhouse seats`** 🔧 Enter the final Democratic House seat count. It settles leaderboard ties.\n"
            "**`/deskreset RESET`** 🔧 Wipes **all** calls, votes and outlet calls. **Not during the night.**"),
    }[key]
    return body

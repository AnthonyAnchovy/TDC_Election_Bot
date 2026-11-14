# A first run on a test server

The bot has 281 tests and replays the whole of election night 2026, but none of that touches Discord. This is the
checklist for the first real run: a private server, a bot of its own, and records kept apart from anything real.
About 30 minutes.

## 1. Make the test bot (Discord's website, about 5 minutes)

1. Make a private Discord server just for this. In it, create two text channels, `decision-desk-board` and
   `decision-desk`, and a role called `Election Junkie`. Give yourself the role.
2. At <https://discord.com/developers/applications>, **New Application**. Give it a name that says it's the test
   one, so it's never mistaken for the real bot.
3. **Bot** page: turn on **Server Members Intent** (the bot needs it to know who holds the role). Press
   **Reset Token** and keep the token for step 2.
4. **OAuth2 > URL Generator**: tick `bot` and `applications.commands`, then the permissions View Channels, Send
   Messages, Embed Links, Attach Files and Read Message History. Open the link it makes and add the bot to the test
   server.
5. In Discord, turn on Developer Mode (Settings > Advanced), right-click the test server's icon, **Copy Server ID**.

## 2. Settings for a test

In `decision-desk/`, copy `.env.example` to `.env` and set:

```
DISCORD_TOKEN=<the test bot's token>
GUILD_ID=<the test server's ID>
DESK_DATA=data-test
DESK_AUTOCALLS=off
DESK_PACK=2027-chicago-mayor
```

- `DESK_DATA=data-test` keeps the test's records in their own folder (ignored by git). Delete the folder to start
  over.
- `DESK_AUTOCALLS=off` matters for the 2026 pack. Its outlets have already called every House seat, so with
  automatic calls on the bot would post all of them within a minute of starting.
- Leave `MS_CLIENT_ID` and `CLOUD_WORKBOOK` blank, so nothing is written to a real shared workbook.

Then check it without connecting:

```
python preflight.py
```

It lists what's set, what the bot would do if started now, and anything to fix. It never prints the token.

If it doesn't start, the window says why. The two usual causes are both on the developer portal: **Server Members
Intent** isn't turned on (Bot page), or the bot was invited without the `applications.commands` scope, or to a
different server than `GUILD_ID` names, in which case Discord refuses to add its commands.

## 3. Round one: the small pack (10 minutes)

`DESK_PACK=2027-chicago-mayor` is two races and no feeds, so nothing happens unless you do it. Start the bot
(`start_bot.bat`, or `python bot.py`). The window should say it synced the commands and that the server is ready;
if a channel or the role is missing, it says which.

| Try | Should happen |
|---|---|
| `/pack` | Names the Chicago pack, and lists the others. |
| `/predict` | Two pages. Pick a candidate and the runoff question, enter the tiebreaker (it asks for the leader's share, 0 to 100), Save. |
| `/predictions` | Your picks, as saved. |
| `/call race:CHI-MAYOR winner:` | The winner box offers Candidates A to D. Vote; with one junkie the call is official at once. A card posts in the board channel, in that candidate's colour, with a line of flavor text. |
| `/uncall race:CHI-MAYOR` | The retraction card, in grey. |
| `/final race:CHI-MAYOR winner:` then `/leaderboard` | Your picks are scored. |
| `/counties state:` | Says the pack has no County Watch. |
| `/help` | The command list. |

Write down anything that looks wrong, with the time. `data-test/` and `captures/` hold a record of every command
and post, which is what a fix gets tested against.

## 4. Round two: 2026, held at 7:05 PM on the night (15 minutes)

Stop the bot. In `.env` change the pack and hold the clock inside election night:

```
DESK_PACK=2026-general
DESK_FAKE_NOW=2026-11-03T19:05-05:00
```

Run `python preflight.py` again, then start the bot. The clock stays at 7:05 PM, so county feeds are pulled (they
return the final 2026 counts, where the states still serve them) and the Senate field posts to the board.

| Try | Should happen |
|---|---|
| `/predict` | Eight pages with the Senate and governor maps. Predictions are closed at 7:05 PM; `/unlockpicks` lets you in. |
| `/call race:GA-SEN winner:Ossoff` | A hold card, with the count beside the vote if Georgia's feed answers. |
| `/call race:OH-SEN winner:` the Democrat | A flip: a different pool of flavor lines, and a flip tag on the card. |
| `/procall`, then `/scoreboard` | The outlet's call is logged and the desk is timed against it. |
| `/map chamber:Senate` | The map with your calls on it. |
| `/counties state:Georgia`, `/needs state:Georgia` | The county read and the vote still out. |
| `/trend race:GA-SEN` | A chart once the field has two snapshots (a couple of minutes). |
| `/forcecall`, `/closeout`, `/drinkinggame on`, `/react` | Each answers. `/react` has no images until you add some. |
| `/autocalls on` | Only if you want the flood: every House seat in a state that had closed by 7:05 PM is called from NYT's file. It tests rate limits well, and it is a lot of posts. |

## 4b. The whole night, played back (15 minutes)

Instead of calling races by hand, the bot can play election night 2026 from its record into the test server:
every vote, `/realcall`, retraction and outlet call, at about 240 times the night's speed. Add to `.env`:

```
DESK_SIMULATE=tests/fixtures/2026
```

and start the bot. It sets aside the server's earlier test records, posts a 🧪 marker, plays the night, and
finishes with a 🧪 summary that says whether the desk's record matched the night. People appear as user1 to user7;
votes have no live count beside them (today's feeds hold final counts); the field graphics are the night's own.
Remove the line afterwards, or the next start plays the night again. `simulate.py` has the details.

## 5. A second server (optional)

Add the same test bot to a second private server with the same two channels and role. It gets the commands as soon
as it joins. Make a call in each: neither should show in the other's `/board`. `/globalboard on` in both, and
`/globalboard` shows people from both, scored on outlet calls and finals only.

## 6. Clean up

Stop the bot, remove `DESK_FAKE_NOW` from `.env`, and delete `data-test/`. If the token was ever pasted anywhere
it shouldn't be, press **Reset Token** on the developer portal.

## What this run can't show

- **A real night's feeds.** State files and NYT's data are final now. The first run against moving numbers is the
  next election; until then the 2026 replay is the check.
- **Load.** One or two people can't press buttons the way twenty do. The retry and rate-limit handling is tested
  against simulated failures only.
- **The website's real sign-in.** That needs the Discord OAuth settings and somewhere to host it.

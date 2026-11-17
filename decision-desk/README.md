# Decision Desk bot

**Before election night:** everyone runs `/predict`, clicks states blue or red, and saves. Once results come in, the bot scores the picks on a leaderboard.

**On election night:**
- `/call` is one person's vote that a race is over. Every vote is announced in **#decision-desk-board**.
- Once a majority of the **Election Junkie** role agrees, the bot posts the official call card and updated map in **#decision-desk**.
- `/realcall` makes a call official without waiting for a majority.
- `/procall` logs a call by AP, NYT, Wasserman and the like.
- `/scoreboard` tracks who beat the pros, and by how many minutes.

Commands work in any channel. The bot replies to you privately and posts publicly only in the two desk channels.

**New to the bot?** Run `/help`. It explains every election-night command, and only you can see it. Pick a section from its menu: calling races, following the night, County Watch, scores, and (for admins only) admin tools.

## One-time setup

### 1. Create the bot in Discord
1. Go to https://discord.com/developers/applications and click **New Application**.
2. Under **Bot**:
   - Click **Reset Token** and copy the token. Keep it private.
   - Turn on **Server Members Intent**.
3. Under **OAuth2 → URL Generator**:
   - Scopes: `bot` and `applications.commands`
   - Permissions: Send Messages, Embed Links, Attach Files, Read Message History
   - Open the generated link and add the bot to your server.

### 2. Set up the server
- Create a role called **Election Junkie** and give it to everyone who votes on calls.
- Create two text channels: **#decision-desk-board** for the vote feed, outlet calls and poll-closing alerts, and **#decision-desk** for official calls with the graphics and maps.
- Admin commands work for anyone with the **Manage Server** permission.

### 3. Run it on your PC (Windows)
1. Install Python 3.12+ from python.org and check **"Add python.exe to PATH"**.
2. In this folder, open a terminal (click the address bar, type `cmd`, press Enter) and run:
   ```
   py -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   ```
3. Copy `.env.example` to `.env`. Paste in the token, plus your server ID as `GUILD_ID`. To get the ID, turn on Developer Mode, then right-click the server icon → Copy Server ID.
4. Run `python bot.py`. The bot is online while that window stays open.

**If you ran an older version**, delete `desk.db` before starting this one.

## Predictions (before Nov 3)

`/predict` opens a private picker:
- **Senate** (2 pages, required): click a state to cycle grey → blue → red → grey. NE, ID, SD and MT also offer a green independent option. A live map shows your picks, plus the Senate majority they add up to.
- **House majority** (required): Democrats or Republicans. The **tiebreaker** asks how many seats Democrats win.
- **Governors** (2 pages, bonus).
- **House battlegrounds** (3 pages, bonus): Cook's 46 Toss-up and Lean seats.
- **Safe races are pre-filled.** Cook's Solid-rated races (20 Senate, leaving out Florida, plus 20 governor) are already colored the first time you open the picker. Click any of them to change it. **Fill safe races** puts back any you've cleared.
- **Fill Tilt & Lean by ratings** (on the House battleground pages) colors every Tilt, Lean and Likely seat for the party the ratings favor, without touching seats you've already picked. Toss-ups stay grey for you to decide.
- **Save** stores your picks. You can reopen `/predict` and change them until **6 PM ET / 5 PM CT on Nov 3**, when the first polls close. Nothing is saved until you press Save.

Everyone's picks are visible as soon as they save, so you can compare maps before the election. (Set `hide_predictions_until_deadline` to `true` in `config.json` if you'd rather keep them secret until polls close.)

| Command | What it does |
|---|---|
| `/predictions [member]` | Someone's picks on a map, marked right/wrong as races are called. |
| `/compare member [other]` | Every race where two people picked differently. |
| `/leaderboard` | Standings. Points start counting as races are called. |
| `/finalhouse seats` | Admin. Enter the final Democratic House seat count to settle tiebreakers. |
| `/closeout` | Anyone. What still has no result before the leaderboard is final: uncalled races, the House seat count, missing tiebreakers. |
| `/final race [winner]` | Admin. Enter a race's final result for scoring. It outranks every call and leaves the map alone. Leave the winner blank to clear it. |
| `/unlockpicks member [minutes]` | Admin. Lets one person use `/predict` after the deadline, for 15 minutes unless you say otherwise. Posted on the board. `0` locks them again. They can change any pick, including races already called. |

**Who got it right:** every official call in #decision-desk ends with a prediction check listing who called it and who missed. `/race` shows the same for called races, and who picked what for uncalled ones. Turn it off with `"post_prediction_check": false` in `config.json`.

**Points:**
- Senate seat: 1
- Senate control: 5. Nobody picks this separately: it's what your 35 Senate picks add up to.
- House control: 5
- Governor: 1 (bonus)
- Battleground seat: 1 (bonus)
- +1 for every correctly predicted flip

Change the values under `points` in `config.json`. Picks are scored against outlet calls when one is logged, otherwise against the desk's official call.

## Election night

| Command | Who | What it does |
|---|---|---|
| `/call race winner` | Election Junkie | Your vote that the race is over. It's posted to the board, e.g. "Ethan has voted to call the Georgia Senate race for Jon Ossoff 🔵 (D HOLD) · 2 of 3 votes needed". When a majority agrees, the official call goes to #decision-desk. Calling again with the other party switches your vote. |
| **Also call** button | Election Junkie | On each vote's post in the board channel. One click casts the same vote as `/call`, and it keeps working after a restart. If you'd voted the other way, it switches your vote. The button comes off once the race is called and someone clicks it. |
| `/unvote race` | Election Junkie | Withdraws your vote. |
| `/uncall race` | Election Junkie | A vote to retract an official call, using the same majority rule. |
| `/realcall race winner` | Election Junkie | Makes the call official right away, without waiting for a majority. It also counts as your vote. |
| `/forcecall race winner` | Election Junkie | The same as `/realcall`, under the name people looked for on the night. |
| `/procall race winner outlet` | Election Junkie | Logs a call by AP, NYT, Wasserman and the like. It posts to the board and feeds `/scoreboard`. **House seats:** a call from AP, Decision Desk HQ, NYT or Wasserman makes the seat official, unless another of those four called it the other way. Everything else never changes the desk's map. |
| `/autocalls [on\|off]` | Admin | House calls read from Bluesky and NYT: turn them on or off, or see how they're doing. |
| `/forceuncall race` | Admin | Retracts without a vote. Outlet calls stay logged and keep scoring the race. |
| `/clearrace race CLEAR` | Admin | Erases one race's official call, votes and outlet calls, so it scores nothing. For a test or a call logged by mistake. |
| `/scoreboard` | Anyone | Per person: right and wrong calls, how often they were first, and the average minutes ahead of each outlet (e.g. "AP +14m · NYT +22m · best: NC-SEN, 41 min before Wasserman"). Also shows the official desk against the pros. |
| `/next` | Anyone | Races where polls have closed but the desk hasn't called, with vote counts and outlet calls. |
| `/board`, `/map`, `/race` | Anyone | Seat counts and path to majority, the current maps, or everything about one race. `/map` offers the data pack's own sections: Senate, House and Governors in 2026, President as well in 2028, and a list for a race with no map. |
| `/closings` | Anyone | Poll-closing schedule. Alerts post to the board automatically as each wave closes. |
| `/field [chamber] [timelapse]` | Anyone | The field (below): every race as a circle between safe D and safe R. It animates what moved since the last board post, or plays the whole night with `timelapse`. |
| `/fieldposts [pause\|resume]` | Admin | Pauses or resumes the field's automatic posts. Snapshots keep saving while paused, and a timelapse that comes due waits until you resume. Blank shows which it is. |
| `/fieldtest [chamber] [view] [seed]` | Admin | Tries the field on a simulated night, visible only to you: the whole night, one 30-minute update, or the real field right now. |
| `/deskreset RESET` | Admin | Clears every call, vote and outlet call, the points they gave, the final House seat count and the field's snapshots. Predictions are kept unless you tick `include_predictions`. |

### The field

An animated GIF of every race on one line from safe Democratic to safe Republican, built from the desk's own numbers:
- **Position** is the margin. In the Senate, that's County Watch's projection once counties finish counting; until then a race with votes counted uses those votes, with the rest at its 2024 margin plus the swing of similar finished counties in other states, and a race with no votes sits at its 2024 margin (at midterm turnout), and Cook Solid races sit in the Safe columns. House battleground seats start at their ratings average, and in FL, NC and VA the live count pulls them toward the counted margin as precincts report.
- **Size** is the votes counted so far. A hollow ring means nothing has been counted, a light fill means counting has started, and a solid fill means projected.
- **Called races** slide out of the field into their party's end zone. The top bar shows the seats each side has in hand (Senate seats not up this year included) and where it would land if every uncalled race went the way it leans now.

On the night (6 PM ET Nov 3 to 8 AM ET Nov 4), the Senate field posts to #decision-desk-board every 30 minutes, or within 5 minutes of a new call. Each post animates the movement since the last one. At 8 AM ET Nov 4, the whole night posts as a timelapse. `field_auto` in `config.json` picks which fields post (`["senate"]`, or `["senate", "house"]`), and `field_minutes` sets how often. The field follows the card theme: dark for wire and classic, paper for editorial.

Limits: Nebraska's left-hand side is Dan Osborn; his starting spot is his own 2024 Senate result (R+7 at midterm turnout), not Harris's. Governors have no field, because the bot has no ratings or live counts for most governor races.

## Race info and live counts

| Command | Who | What it does |
|---|---|---|
| `/seat race` | Anyone | Background card for any race: the ratings average, who holds it and whether the incumbent is running or it's open, nominees, Cook PVI, poll close, what a win does to the ratings math, the desk's status, the group's picks, and Wikipedia/Ballotpedia/official results links. Works before election night. |
| `/results race` | Anyone | Live count from the state's official results file: each candidate's votes and share, the margin, precincts reporting, and a **Refresh** button. Leave `race` blank for every tracked race in the feed states. Counts are unofficial and never count as a call. |
| `/resultstest state` | Admin | Downloads that state's Nov 2024 file and checks the bot can read it. **Run this once for FL, NC and VA before election night.** |

Live counts come from **Florida, North Carolina and Virginia** (Virginia now reads the state's new results system), all early-closing states with 15 of the rated House seats (FL-07, 09, 13, 14, 16, 22, 25, 27; NC-01, 03, 11; VA-01, 02, 05, 07) plus the FL and NC Senate races. Other states get a link to their official results page in `/seat` and `/results`.

The feed links are in `results_sources.json`. The 2026 file names follow each state's usual pattern, but they can't be confirmed until the files go up on the night. If `/results` says a file isn't up after polls have closed, open the state's results page, find the download link, and paste it in as `url`. No restart needed.

Race IDs look like `GA-SEN`, `AZ-GOV`, `PA-07` and `AK-AL`, and `HOUSE` means control of the House. Autocomplete suggests them as you type.

**Majority** means more than half of the people who currently hold the Election Junkie role. Server admins can vote without the role and their votes count, but they don't raise the number needed. To require a fixed number of votes instead, set `"threshold": 3` in `config.json`.

**DDHQ grab button:** Decision Desk HQ doesn't post every call on Bluesky, and its results site turns the bot away with a security check. So make a bookmark from `countywatch/ddhq_grab_bookmark.html` (rebuild its link from `countywatch/ddhq_grab.js` if that changes) and click it on DDHQ's House page in your regular browser (bookmarks like this do nothing on a blank new tab). It reads DDHQ's 2026 House balance of power page and saves `ddhq-calls.json` to Downloads, and the bot loads it within 10 seconds. It isn't automatic: each click is one read. New seats follow the same rule as other automatic calls. A file is refused if it's over 15 minutes old, or if the seats it read don't match DDHQ's own count. A winner that changes or disappears goes to a person. `/autocalls off` makes the bot ignore grabs.

**House calls from Bluesky:** starting at 5 PM ET on Nov 3, the bot reads two accounts every 30 seconds: Decision Desk HQ (`decisiondeskhq.bsky.social`) and an unofficial mirror of Dave Wasserman's posts (`redistrict.bluesky.bot`). A clear call on a House seat in the regular general election goes through the same rule as `/procall`, so it becomes official unless another trusted outlet called it the other way. Rated seats get the full projection; Safe seats post as a short roll-up. Anything unclear, like a winner whose party it can't find, a special election or a possible correction, goes to the board for a person. Other races are left alone. `/autocalls off` stops it, and the NYT reader below. Parties come from `candidates.json`, then `house_parties.json`, built from the FEC's candidate file by `python tools/make_house_parties.py`. The bot can't see a post being deleted, so a bad call stays up until someone runs `/uncall`.

**House calls from NYT:** from 5 PM ET on Nov 3, the bot also reads the New York Times' House results file every 30 seconds. It's one file with all 435 seats and, once called, each winner. Those calls are the AP's, but they're logged as NYT and go through the same rule as `/procall`. Nothing counts until a state's polls have closed. A race headed to a runoff stays uncalled. Louisiana's winners (a nonpartisan election), a winner from a minor party, or a winner NYT changes or takes back go to the board for a person; the bot never undoes a call on its own. The file isn't published for outside use, so if it can't be read for about 5 minutes, the board gets a warning.

**Safe House seats** get no card and no map. A call on one, from any source, goes in a text roll-up in #decision-desk, posted once the oldest waiting seat has waited `safe_batch_minutes` (5 by default, in `config.json`). Only Likely, Lean, Tilt and Toss-up seats get the full card.

**Early warning seats:** `/earlywarning` lists the 19 rated House seats in states closing by 7:30 PM ET (Likely D through Lean R), grouped by closing time, with what's been called. Poll-closing alerts list every rated seat in the closing states, with early-warning seats flagged. After each rated House call, the bot posts the ratings math: Dems start at 223 if every seat they're favored in goes their way, and every upset moves that number. Ratings are Adam Carlson's Oct 23 average of Cook, Inside Elections and Sabato, in `competitive.py`.

## County Watch (county-level analysis)

A county-by-county read of 15 Senate races: the 14 that Cook doesn't rate Solid (AK, GA, IA, KS, ME, MI, MN, MS, NC, NE, NH, OH, SC, TX) plus Florida, which the group is treating as competitive. It lives in two places:

- **`County Watch 2026.xlsx`** (in this folder). This is the full workbook: dashboard, key counties with why they matter, every county with 2024/2020/2022 results and demographics, swing by county type, and early vote. Every number is a formula, so it works without the bot. Type county results into the yellow **Manual** columns on the Counties sheet from any source (NYT, AP, county sites), put `Y` in *Manual complete* when a county is done, and the projections update. To have everyone work in one live copy instead of passing files around, set up the **shared live workbook** (below).
  - **Start Here** sheet: a plain-language guide for anyone new to election nights (how votes get counted, why early numbers mislead, how this workbook builds its projection, and how to read every tab). Point newcomers here first.
  - **At a Glance** sheet (opens first): the one-screen view for the night. Tiles for the Senate tally, results vs polls, vote counted and the national swing; **a needle per race** (needle = projection, or the final poll before any county finishes; grey arc = 90% range; underneath, the two-party votes still expected, what the projected loser needs of them to tie, and what they're on pace for) plus a Senate-control needle; a bar per race in poll-closing order (light bars = final poll, solid = live projection) with the colored call; a table of what the Senate looks like if the polls are right, miss like 2020, use Times/Siena's old weighting, or if other states' results hold; and two charts. All formulas, nothing to type.
  - **Dashboard** sheet: one row per race in poll-closing order. Left to right: who's ahead (with a strip showing the projection and 90% range), what sets the confidence, whether the trailer can catch up, the margin each county type and big key county needs, turnout by county type, second opinions, then the raw numbers. The bot's written take on each race sits under the table, and each state's name links to it.
  - **Projection range and "other states imply"**: the Dashboard shows each projection's 90% range (wide early, on purpose) and what the state's margin would be if its counties moved like the same kinds of counties in other states tonight. That's pooled like with like (Florida's Hispanic counties alone, rural counties by region) and never feeds the projection.
  - **Locked cells**: every sheet but Early Vote is protected against accidental edits (no password). The Manual columns stay open; filtering still works. To change a turnout factor or poll on purpose, Review > Unprotect Sheet.
  - **Polls** sheet: the final poll in each race in yellow cells, seeded from `countywatch/polls.json`: the Nov 1 NYT/Siena polls for MI, TX, ME, OH, KS, AK and IA, and a published polling average (or, for SC, the only recent nonpartisan poll) for the other eight, as of Nov 2. The Source column says which. Type over any of them. It shows sampling error, how much the new weighting moved each poll, and on the night how far each medium/high-confidence projection is running from its poll. The average of those misses is applied to every poll still waiting. Polls never feed the projection. Key Counties and County Benchmarks also gain a *poll pace* column: the county's final margin if the state lands on its poll.
  - **Key Counties** sheet: 6 to 11 counties per state on both sides (Democratic vote banks and Republican bases such as Cherokee, Montgomery or Butler), plus an "All rural" row per state. Each county shows margin against break-even and, once it's fully counted, its turnout against what it needed.
  - **County Benchmarks** sheet: for each key county, the final margin and net votes that line up with each statewide result (R+5 through D+5), its par turnout, and a plain-English line, e.g. "Ossoff at D+51.5 or better = great night... At 95% of par the break-even moves to D+48.9".
  - **Types** sheet: each kind of county as a block, e.g. Georgia's metro cores are 33.5% of the expected vote and need D+37.5 for a tie (D+39.4 if they turn out at 95% of par). On the night it shows each type's swing, turnout index and whether it's on pace.
  - **First Counties** sheet: the first counties to report in each state, in poll-closing order. It shows what reports first, how much bluer early and mail votes were than the total in 2022, and the first-batch number that only means break-even. North Carolina is also adjusted for the 2026 early electorate.
- **Discord commands:**

| Command | Who | What it does |
|---|---|---|
| `/needs state [what_if]` | Anyone | The vote still out in a Senate race, county by county: how much the model expects, where it is, and the share of it the trailing side needs to tie. `what_if` tries a different break for what's left, in points toward the Democratic side. |
| `/counties state` | Anyone | Before results: key counties, why they matter, the break-even margin and share of the vote in each. On the night: projection, swing by county type, turnout in Democratic-leaning vs Republican-leaning counties, which key counties are ahead of or behind pace on margin and turnout together, and the biggest movers. |
| `/earlyvote state` | Anyone | Early vote so far against 2022 turnout, with party, race and age where the state reports them. North Carolina adds a county-by-county comparison with 2022 at the same point, plus a modeled lean. |
| `/countyset state county dem rep [other] [done]` | Election Junkie | Enter a county's count by hand, from NYT or AP, for states without a feed. Autocomplete puts key counties (★) first. Set `done` only when every precinct is in; fully counted counties feed the projection. |
| `/countyclear state county` | Election Junkie | Removes a hand-entered count. |
| `/countyfile file [state]` | Election Junkie | Upload a results file: a state's download (CSV, Excel, Clarity XML, or the GA/NC/MN/NE formats) or your own county table. The bot loads it, posts what it found and any alerts, and updates the workbook. |
| `/environment` | Anyone | The national read. It pools every fully counted county in every state to show the swing from 2024 by county type, by state, and turnout against 2022, and lists which races that swing would flip. Before results, it shows early vote pace in every state. |
| `/countytest state` | Admin | Checks a county feed against an old results file (or, for some states, that the site answers). **All states** tests every feed at once and shows whether each Nov 3 source is up yet. **Run it before the night.** |
| `/countyfeed state link` | Admin | Points a Clarity state (South Carolina, Iowa) at its election link, e.g. `https://www.enr-scvotes.org/SC/127500/`, if the site doesn't list Nov 3 by date. `clear` undoes it. |
| `/countyrefresh` | Admin | Pulls everything now and rebuilds the workbook. |
| The **Mississippi** tab | Workbook | Sizes the votes still out by kind (missing precincts, other uncounted votes, affidavits, late mail), with three yellow questions at the top for the affidavit guess, live formulas for every step, a simulation, a late-vote grid, 2024's late-vote record and every county. The bot refreshes its grey cells with each workbook update. Mississippi's feed keeps running after election night, through Dec. 5, for its late ballots (`extended_feeds` in `feeds.json`): it updates the workbook and `/watchstate` DMs, and posts nothing to the board. Code: `cw_mississippi.py`. |
| `/watchstate state [on\|off]` | Admin | DMs you at the feed check, without waiting for the workbook. New votes get the full alert in short lines: the count, each county's new votes, how the projection moved and why, how to read it, and what to watch. If only a county's whole-number % in moves, you get the votes still expected in those counties by candidate, on the projection's numbers, on what % in implies and (Mississippi) on NYT's model, and what the move did to the projection. Restarts send nothing. Nobody else sees them. |
| `/resultsdm [on\|off]` | Admin | DMs you the name, state, U.S. House district, % in and results for each county that changed, each time the workbook updates (every 2 minutes at most). In the bellwether states, Virginia and Kentucky, it only sends counties on Key Counties or First Counties. Nobody else sees them. Your Discord has to allow DMs from this server's members. It starts from the current numbers, so counties already counted aren't sent until they change. |
| `/callnyt` | Admin | Makes official every House, Senate and governor race NYT lists a winner for (the AP's calls), all at once. House seats also load by themselves; Senate and governor races only load with this. A race called the other way by the desk or a trusted outlet is left alone and listed for you. Each new Senate or governor race gets its full projection post. |
| `/excellink` | Admin | Posts a card in the current channel with the read-only workbook link (`CLOUD_VIEW_LINK` in `.env`) and a preview picture from before results. |

**What the bot pulls by itself:**
- **Live county results:** Georgia, Florida, North Carolina, Minnesota, Nebraska, Iowa, South Carolina and Alaska (plus Virginia as a bellwether), all checked at once every 30 seconds from 6 PM ET on Nov 3 to 8 AM ET Nov 4. Ohio and Texas are tried too, but their sites sit behind Cloudflare's bot check; if they answer the bot with a challenge page, it stops trying for 15 minutes and posts once that you should use the grab button. The bot only downloads a file again when the state has changed it. Georgia uses its single-race endpoint, about 1.6 MB instead of the 33 MB full export. Each state is ignored until its polls close, because states load test numbers into their live files beforehand.
- **Early vote:** every 6 hours until Election Day. This covers the UF Election Lab statewide tracker, plus North Carolina's full absentee file (about 85 MB a pull).
- **Alerts:** posted to #decision-desk-board as soon as numbers move: key counties that finish counting, and the national read. Projection changes (first projection, confidence going up, a change of leader, or a shift of a point or more) are turned off; set `post_projection_alerts` to `true` in `countywatch/feeds.json` to get them back, with no restart. `/counties` and the workbook still show every projection.
- **The workbook:** refreshed every 2 minutes in the background, so it never slows the feeds. Your manual entries, `/countyset` entries and turnout factors are kept.

**Bellwether states:** Virginia and Kentucky are tracked for their early county results, not their Senate races, which are safe. They show the national swing before the battlegrounds fill in:
- Virginia is automatic. It moved to the same results system as Georgia, and its Nov 3 election is already set up there.
- Kentucky's eastern counties close at 6 PM ET, the first in the country.
- Virginia closes at 7 PM ET and has 2022 early-vote splits for first-batch benchmarks.

Kentucky comes from New York Times data (see below). Once 20 or more counties are fully counted across all states, the bot posts a 🌡️ National read to the board, and updates it as it moves.

**The County Watch grab button (Ohio, Texas, Kansas):** these three states have statewide results sites behind a bot check, so the bot can't read them itself. Make the bookmark once from the County Watch Grab page (or paste `countywatch/grab.js` into a bookmark as `javascript:` code). On the night, open the state's results site in Chrome or Edge and click it: it saves `countywatch-OH.json` (or TX, KS) to Downloads and the bot loads it within 10 seconds. The first load posts a line; later ones load quietly and only alerts post. Before a state's polls close the bot treats a grab as a test and doesn't use the numbers.

**State by state:**
- Automatic: GA, NC, FL, MN, NE, VA (bellwether), IA, SC (Clarity: the bot finds the Nov 3 election by date, or use `/countyfeed`), AK (district results arrive after 1 AM ET).
- Alaska is projected by vote type, not by finished districts (`cw_alaska.py`): no district finishes until its absentees are counted, up to 15 days later. The votes still expected come from each vote type's 2024 numbers (`countywatch/ak_modes24.json`) and the swing seen so far. Its first-choice margin puts Heikes (REP) on the Republican side and leaves out Daniel J. Sullivan; ranked-choice rounds aren't modeled. If a shared workbook predates this, run `python cw_cloud.py patch-alaska` once.
- Ohio, Texas and South Carolina try their own sites first and fall back on New York Times data when a site blocks the bot, is down or has no votes yet. A state's `fallback` in `feeds.json` can be one source or a list, tried in order: the first source stays in use while it has votes, and the bot switches when it has been stale for 10 minutes while a later one is moving with more votes, and goes back when the first moves again. A blocked source is left alone for a while. Kansas reads New York Times data directly. The grab button still works for Ohio, Texas and Kansas as a backup: a grabbed file is used until the feed's numbers next change.
- New York Times data: MI, ME, MS, NH, KY and KS (checked every 30 seconds with the others). This isn't a state feed or a published one, so NYT can change or block it, and its Nov 3 addresses can't be confirmed until they go up: `HTTP 404` in `/countyrefresh` after polls close means the file isn't there. If NYT uses a different address, change `url` for that state in `countywatch/feeds.json`; no restart needed. Keep someone ready to enter these states by hand with `/countyset` (Michigan: county clerk sites; Mississippi: counties post PDFs; Kentucky: behind Akamai's bot check) until the feed has proved itself. Numbers typed in always win over the feed. A county counts as in once its precincts are in and at least 95% of its expected vote is counted, or at 98.5% of its expected vote with at least 80% of its precincts in (a tiny precinct or an empty Maine township can stay unreported forever).

**Dropping files in:** save any results file into `countywatch/inbox` (the bot creates the folder) and it's loaded within 10 seconds, then moved to `inbox/done`. The file name should include the state, like `TX results.csv` or `Texas.xlsx`; otherwise the bot works it out from the county names. A plain table works if it has a County column plus either one column per candidate (headed with names or Dem/Rep) or Candidate/Party/Votes rows. Optional columns: % reporting, precincts reporting and total precincts, and a Final/Complete column marked Y. A newer feed pull for the same state replaces a loaded file.

**Colored calls:** the Dashboard's Call column and the reading columns on Key Counties, County Benchmarks and First Counties use five shades. Dark blue means great for D, light blue D on track, grey toss-up, light red R on track, dark red great for R. The tiers are D+5 and above, D+1 to D+5, within 1 point, R+1 to R+5, and R+5 and beyond, measured against break-even.

For the manual states, enter key counties with `/countyset` (fastest, and anyone can help) or in the workbook. Split the states among the group. `countywatch/feeds.json` holds the feed settings.

**How the projection works:**
1. Fully counted counties give a swing from 2024. A county is fully counted when every precinct is in and it has 85% of its 2022-pattern vote (a feed can show every precinct in with only early votes counted), when someone marks it `Y`, or (with no precinct count) when it has 97% of its expected vote. Expected votes follow the night's real turnout: the Dashboard turnout factor until 3 counties are known to be complete, then their actual pace. In North Carolina a precinct counts as in only once it has Election Day votes, because early votes are filed under every precinct.
2. Each county type (metro cores, college suburbs, Black Belt, Hispanic, rural and so on) gets its own average swing, pulled toward the state's overall swing until that type has a real share of the vote counted. One small county finishing first can't set the swing for its whole type.
3. Each county still counting keeps the votes already counted. The votes it still expects are projected from its 2024 margin plus its type's swing, not from the partial count's margin, because early returns are usually one vote method and lean one way.
4. Its vote total is its 2022 turnout times its type's turnout pace from finished counties (or the state's).

**Turnout:** benchmarks use 2022's midterm turnout pattern with 2024's county margins. That moves some needed swings slightly (Georgia: R+2.2 in 2024, R+1.1 at midterm turnout). Each county's *par* is its 2022 share of the state's two-party vote, scaled to the night's turnout. Its **turnout index** is actual votes against par, where 100 means in line with the state. A Democratic county at index 95 needs about a 5% bigger margin than its break-even; a Republican county at 105 has to be held about 5% closer. "Margin + turnout vs tie pace" combines the two in margin points. Times the county's votes it's net votes against a tie, and those add up across counties to the statewide margin. Alerts and `/counties` also compare turnout in finished Democratic-leaning and Republican-leaning counties. A turnout gap between county types only reaches the projection once counties of that type finish, so a weak metro night shows up late. Watch those indexes.

The Read Me sheet has the details and limits.

**Excel open during updates:** with `xlwings` installed (`start_bot.bat` installs it on Windows), the bot writes into the open workbook. Without it, the bot writes `County Watch live.csv` until you close the file.

### Shared live workbook (OneDrive or SharePoint)

Put the workbook on OneDrive once and the bot keeps that copy current. Everyone opens the same link in Excel (browser or desktop) and watches the numbers arrive. There are no copies to send around after each update.

- The bot writes only its own cells: the grey Feed columns, each state's take and the update stamp on the Dashboard, the Early Vote numbers, and `/countyset` entries in the Manual columns. It never overwrites what people type in yellow cells (Manual columns, turnout factors, the early-vote shares).
- Whatever people type reaches the bot within 2 minutes, so `/counties`, `/environment` and the alerts use it too.
- `/countyclear` blanks a county's Manual cells only while they still hold the bot's numbers. If someone has typed over them, their numbers stay.
- Filter the Counties sheet, but don't sort it or delete rows. The other sheets point at fixed rows.
- If Microsoft can't be reached, the bot updates the file in this folder instead and says so in its log and in `/countyrefresh`.

**Setup (once, about 10 minutes):**
1. Register an app for the bot to sign in with. With a work or school account, go to https://entra.microsoft.com → **App registrations** → **New registration**. With a personal Microsoft account, use https://portal.azure.com → **Microsoft Entra ID** → **App registrations**.
   - Name it anything, e.g. `Decision Desk`. Under supported account types, pick **Accounts in any organizational directory and personal Microsoft accounts**. Leave the redirect URI blank and click Register.
   - **Authentication** → *Allow public client flows* → **Yes** → Save.
   - **API permissions** → Add a permission → Microsoft Graph → Delegated permissions → `Files.ReadWrite.All` → Add.
   - Copy the **Application (client) ID** from the Overview page.
2. In `.env`, set `MS_CLIENT_ID` to that ID. Set `CLOUD_WORKBOOK` to where the shared copy should live, as a path in your OneDrive, e.g. `County Watch/County Watch 2026.xlsx`. If the workbook is already on OneDrive or SharePoint, paste its share link instead.
3. In this folder, with the venv active, run:
   ```
   python cw_cloud.py login
   python cw_cloud.py upload
   python cw_cloud.py share
   python cw_cloud.py check
   ```
   - `login` shows a code to enter at microsoft.com/devicelogin. Sign in with the account that will own the file.
   - `upload` copies the workbook in this folder, with anything you've typed into it, to OneDrive.
   - `share` prints an edit link to post in Discord. Anyone with the link can edit; a work account that blocks that gets a link for people in your organization. You can also share the file from OneDrive yourself.
   - `share --view` prints a **read-only** link instead. Give that one to friends who are just watching: they see the same live workbook but can't type into it, so nothing gets changed by accident. Keep the edit link for the people entering county numbers. Both links can be live at once, and the bot's own sign-in isn't affected. A read-only viewer may need to refresh the page to see the newest numbers.
   - `check` confirms the bot can read and write it.
4. Restart the bot. `/countyrefresh` should say *Workbook: updated the shared workbook*.

The sign-in is saved in `countywatch/ms_token.json` and renews itself while the bot runs. Keep that file private (it's in `.gitignore`). If it ever lapses (after about 90 days unused, or a password change), the bot falls back to the local file and its log says to run `python cw_cloud.py login` again.

If a new version of the bot changes the workbook's layout, run `python countywatch_run.py build --local`, then `python cw_cloud.py upload --replace`. That replaces the shared copy, including anything people typed into it, so do it before the night.

## Making it yours

- **`flavor.json`**: the lines posted with calls, flips and retractions. These reload instantly, so this is where the in-jokes go. A race's own lines are used first; otherwise the bot picks one for the kind of result (hold, flip, upset, control) and fills in the winner, loser and place. No line repeats until the rest of its pool has been used. Only 16 of 2026's races have lines of their own, so that is the place to add more.
- **`candidates.json`**: nominee names for all Senate and governor races and the 46 battleground House seats, verified Oct 22. An `I` entry adds the independent option to the picker.
- **`seat_info.json`**: incumbents, open-seat reasons and Cook PVI for `/seat`. Changes apply live.
- **`results_sources.json`**: live results file links and official results pages. Changes apply live.
- **`competitive.py`**: the bonus House seats, plus the `SAFE` list of pre-filled races. Edit them before people predict.
- **`config.json`**: channel names, role name, deadline, points, `hide_predictions_until_deadline`, and `card_theme`.
- **Call card themes** (`card_theme` in `config.json`, or `/cardtheme` to switch live):
  - `wire` (default): party-color poster with the state in halftone, a yellow PICKUP tag on flips, and a seat strip showing where the chamber stands, with the race just called ringed in yellow.
  - `editorial`: a results-page card with a serif name, a locator map and the state cut-out. Senate and governor cards end in a seat strip; House seats and House control get a taller card with a hemicycle.
  - `classic`: the original glass card.
  - The map posted after each Senate or governor call, and by `/map`, is the dark tile map in every theme, and the field stays dark too. (`cards.py` still has the older themed maps, unused.) Classic keeps the original tile map, as do the `/predict` picker and prediction cards. The House chart looks the same in every theme. With wire or editorial, it isn't posted after the House control call, since that card already shows the chamber.
  - `/cardtheme` (admin) sets the theme, uses it in place of the saved art in `assets/calls/`, and shows a sample; leave it blank to bring the saved art back. `/cardpreview race winner [theme]` shows any card privately, so you can check how a race will look before the night.
  - The fonts are in `assets/fonts` (SIL Open Font License), so cards look the same on any PC.
- **`district_shapes.json`**: the 2026 boundaries of all 435 House districts, used by the editorial card to highlight the district inside the state. 41 states come from Census 119th Congress files. The nine states with new 2026 lines (TX, CA, FL, NC, OH, UT, TN, LA, AL) come from each state's official plan file, or from Census's 120th Congress block file for OH and AL. A Census block-by-block comparison confirms those nine are the only states that changed. To rebuild it after a map change, see `tools/build_districts.py`.
- **Projection graphics** (`assets/calls/`): one PNG per race and winner, posted with the 🚨 projection. They're named `<RACE>_<PARTY>.png` (`GA-SEN_D.png`, `NE-SEN_I.png`, `US-HOUSE_R.png`, `PA-07_D.png`) and cover every Senate and governor race, House control and the 82 rated House seats. To use your own graphic, save it over the file with the same name. A `.jpg`, `.gif` or `.webp` with that name works too, and GIFs stay animated. The bot posts your file exactly as it is.
  - `python tools/make_projection_art.py` draws the finished art: a glass card with the state and the winner's photo, and for the races in `projection_memes.json`, a meme worked in. Those files count as yours, so the bot posts them as they are and neither tool overwrites one you've edited. [docs/projection-art.md](../docs/projection-art.md) covers the layouts and how to fit a new meme.
  - Files you haven't changed are stand-ins. When the race is called, the bot draws that card fresh in the current theme, with the seat counts and time as they stand. Only the races you've made art for look different.
  - `python tools/make_call_pngs.py` redraws the stand-ins (after a theme or candidate name change, say) and never touches files you've changed. `--all-house` adds every other House seat. For a race that isn't in the folder, you can still drop in a file with the right name.
  - Retraction cards are always drawn by the bot. `/cardpreview` shows your graphic when there is one.
- **`assets/`** (other art):
  - `candidates/GA-SEN_D.png` is a headshot placed on the card.
  - `memes/GA-SEN_D.png` is a transparent cutout placed bottom-left.

## Night-before checklist

Things the code can't check for you. Do them on the PC that will run the bot:

1. **Feeds:** run `/countytest` for every automatic state (GA, NC, FL, MN, NE, VA, IA, SC, AK) and `/resultstest` for FL, NC and VA. Anything that fails tonight will fail tomorrow.
2. **Grab button:** make the bookmark and try it once on the Ohio, Texas and Kansas sites (a grab before polls close is treated as a test).
3. **Shared workbook:** `python cw_cloud.py check`, then `/countyrefresh`. It should say *updated the shared workbook*. Open the edit link and the `--view` link once each.
4. **Clean slate:** after any rehearsal, `/deskreset RESET` (it now also clears County Watch's practice numbers, `/countyset` entries and alert memory), remove `DESK_FAKE_NOW` from `.env`, and restart. Clear any test numbers typed straight into the workbook's Manual columns by hand.
5. **The PC:** plugged in, sleep and automatic updates off until Nov 4, and a second person who knows how to restart `python bot.py` if the window closes.

## Rehearse
For a first run, or after big changes, use a private test server with a bot of its own: `docs/live-test.md` is the
checklist, and `python preflight.py` checks the setup without connecting.

Put a fake time in `.env`, e.g. `DESK_FAKE_NOW=2026-11-03T19:05-05:00`, and restart. Run through the night: votes, `/realcall`, alerts, `/scoreboard`. The field posts to the board every 30 minutes while the fake time is inside election night. Then remove the line, restart, and run `/deskreset RESET`, which also clears the field's rehearsal snapshots. To see the field without a rehearsal, use `/fieldtest`. Don't rehearse past the deadline with real predictions in place, because the "predictions locked" post only goes out once.

## The desk's own bot, charts and extras

| Command | Who | What it does |
|---|---|---|
| `/trend race` | Anyone | A Senate race through the night as a chart: the projected margin, the votes counted, when the desk called or retracted it, and when the desk's own bot would have called it. |
| `/react image` | Anyone | Posts a reaction image from `assets/reactions/`. Save an image there and its file name is what you type. None are shipped. |
| `/drinkinggame [on\|off]` | Admin to change | Drinking-game lines on calls: a flip, an upset, a retraction, an outlet beating the desk. Off by default, per server. Rules are in `drinking.py`, or a pack's `drinking.json`. |

**The Bot.** On the night the bot makes its own calls on Senate races from the forecast alone, and posts them to the
board as an outlet called "The Bot". They feed `/scoreboard`, so the desk is timed against it, and they never make
anything official. It calls when the forecast gives one side a 99.5% chance or better, results (not polls or other
states) carry at least half of that forecast, and it has stayed there for 8 minutes. In 960 simulated races across
five replayed elections that rule made no wrong call (`python backtest.py`). Set `"bot_caller": false` in
`config.json` to switch it off. The thresholds are in `autocaller.py`. With `"model": "classic"` the bot goes back to
2026's rule: a 15-point projected lead held for 20 minutes, 8 points for 40, 4 points for 75.

## The forecast
On election night 2026 each state's number came from `cw_model.project()`: finished counties' swing, applied to the
counties still counting. It was right by morning and rough before that. Georgia sat at its 2024 margin (R+1) until
its first county finished, then read D+23.5; it ended D+18. New Hampshire and Maine, which report by town, had no
projection until morning. `cw_forecast.py` now sits on top of every state's projection at once:

- **A starting point before votes.** Each state starts from its final poll and its baseline, moved by the swing other
  states are showing and by how far the polls are missing tonight.
- **Counties compared by what they're like.** A county's swing is its state's swing plus a tilt for race, education,
  size and past vote, fitted across all states together.
- **Early counts discounted.** A half-counted county is evidence only as far as it has earned, and a state's early
  votes leaning one way is estimated and taken out, not mixed into the final margin.
- **Finished means finished.** A county is done when every precinct is in with most of its expected vote, or when its
  count has stopped moving for an hour. On the night Ohio ended with 1 of 88 counties marked complete.
- **A fitted range and a chance of winning**, which is what The Bot calls on.

`python backtest.py` replays five elections county by county (2018, 2020, 2022, 2024 and 2026) and prints the forecast
against `project()` alone. Across the five, the average miss falls from 7.9 points to 5.2 before a state's first vote,
from 4.7 to 2.1 with 15-35% counted, and from 1.9 to 0.9 with 65-90% counted; the wrong side is shown ahead less often
(22% to 17% before any vote, 14% to 8% with 15-35% counted, 6% to 3% with 65-90% counted). In 960 simulated races
(12 nights each) the bot's calling rule called 916 and made no wrong call; half of the calls came with under 47% of
the vote counted. Unless a night's county counts were recorded (below), the reporting order is simulated, so the
replay tests the arithmetic, not the feeds. The field, `/trend`, The Bot and the website use the forecast.
`/counties`, `/needs`, the alerts and the workbook still show `project()`'s numbers. `"model": "classic"` in
`config.json` puts the field back on `project()` too.

**There is no untouched test left.** 2018 was used to find and fix the last wrong call (below), so all five elections
have now shaped the model in some way. The next honest test is a real recorded night: `snapshots/` logs from a live
night, replayed with `backtest.py`. County results for 2016 and 2018 come from the MIT Election Data and Science Lab.

One check was kept clean: the width of the range (`CAL`, `OUT_CAL`). Fitted on 2018-2024 alone, the best widths are
narrower (0.8 and 0.6 against 1.0 and 0.75). On 2026, which that fit never saw, the narrower range held the result 90%
of the time, as a 90% range should, but missed by more than 2.6 of its own widths 1.9% of the time (1% is right) and
made one wrong call in 20 replays of the night. The wider widths in use made no wrong call there, and in the other four
years cost one call in about 500 (506 against 507). They stay.

**The 2026 polls' weight (tried, not kept).** A pack's `polls.json` can say how many polls each state's number rests on
(`n_eff`), and the pre-election model widens a poll's range when it rests on few. 2026's doesn't say, so every state
gets 3. Giving the eight states that rested on a single Times/Siena poll 1, and the aggregators' averages 2 to 8, made
the 2026 replay slightly worse (average miss before any vote 6.9 to 7.2 points, one fewer call, none wrong): those
single polls were good ones. So 2026 keeps the flat 3. `backtest.py` uses a pack's own `n_eff` for its own election,
as the live bot does.

**House seats.** `cw_forecast.from_totals` forecasts a race known only by its running total and the share of precincts
in, starting from what its rating has meant historically. `python backtest.py --totals` tests it on 56 past statewide
races replayed as totals only. It did no better than the House board's simple rule (rating times the share not in,
plus the count times the share in) until 90% was counted, so the board still uses the simple rule.

**Sizing a county that is still counting.** The forecast no longer takes a county's final vote from one number. It
weighs what the county usually casts at tonight's pace against votes counted divided by the share of precincts in,
trusting the second less the fewer precincts are in (a county can post its whole early vote before its first
precinct). A county never ends below the votes already counted, and a county with no precinct count that is past its
expected vote still expects a little more, with real doubt about how much.

**When a county is finished.** A county whose feed has no precinct count, but whose count is being timed, is finished
only once its count has stopped moving (an hour, with most of the expected vote in). Reaching its expected vote doesn't
finish it: in a year when turnout runs ahead, a county can pass that with a tenth still to come. Replays had shown
forecasts nine of their own ranges away from the result for this reason.

**An outlier can't steer the fit.** A place that sits more than three expected misses from the pattern has its weight
cut by the square of how far out it is, so one wild county barely moves the result. This came from 2018, when sixteen
North Carolina counties had no Democratic votes at all (an uncontested House seat) and read as 70-point swings; the
plain fit explained them with a huge tilt and a huge early-vote lean and called the state for the wrong side. The
early-vote lean is also held to 0.40 at most, and everything else is refitted around it.

**The intake check and the snapshot log.** Every county pull goes through `intake.py` before the bot believes it. A
whole pull is refused if it is empty, lists only a handful of counties from a feed that had listed them all, has
many counties suddenly at zero, or looks like test data (finished numbers in the first minutes after polls close). A
short first pull from a feed that has never listed every county is let through with a note, so the first counties of the
night aren't lost. A single county that falls a long way, or jumps far past its expected vote, is held at its last good
number and noted; if the new number is still there on the third pull running, it is believed. If the whole state's total
falls by over 10% it is refused, then believed after 6 pulls running. Held numbers show on the Analyst desk's Feeds
tile. Each accepted change is appended to `countywatch/snapshots/<state>.jsonl` (only the counties that changed, with
the time and the source), and a new night's `reset_night` moves the old logs into a `rehearsal-` folder. The folder is
in `.gitignore`.

**Recorded-night replay.** If `snapshots/` holds a state's log, `backtest.py` replays that state in the order its
counties really reported, instead of simulating an order. Use `--snapshots <folder>` to point at another folder,
`--simulate` or `--no-extras` to leave the recorded night or the sections below out.

**Early vote.** Three uses of early vote were tested, on 2026 only: no early vote from 2022 or 2024 is in the repo, so
those years can't say. The backtest prints each one with and without it (figures from `python backtest.py`).
- County turnout from North Carolina's early vote by county helps: the average miss in each county's starting turnout
  falls from 11.1% to 4.3%, and the forecast is slightly better. It is on (`EV_COUNTY_TURNOUT`).
- State early vote totals as a turnout start do not help: the miss goes from 11.2% to 11.5%. It is off
  (`EV_STATE_TURNOUT`).
- The party registration of early voters in the starting point helps a little before the first vote (average miss 5.66
  to 5.32 points in the latest run) but hurts once counting begins (3.51 to 3.76 up to 15% counted). It is off
  (`EV_REG`).

`countywatch/early_before.csv` is UF Election Lab's file of the 2024 early vote at this point before the election,
unmodified, used to compare this year's early vote with last cycle's. Credit: UF Election Lab, licence CC BY-NC-ND.

**Past polls.** `countywatch/polls_history.json` holds the final polling average for 2022 and 2024 (18 averages, taken
from Wikipedia's polling aggregate tables); 14 races are marked as gaps because no average could be found, and those
states are left out of the check. With them, the average miss before any vote falls from 5.1 to 4.0 points in 2022
and from 3.5 to 3.1 in 2024 (10 states each). Once counting starts the difference is small.

**Features a pack chooses.** What tells places apart (the "tilts") comes from the pack's `features` list: names of
`baseline.csv` columns, of past contests (a contest's own margin), or `{"name": "logpop", "scale": 0.6}` to fix a
feature's size by hand. Left alone, each feature is divided by how far places sit from their state's average, so
any column works without choosing a scale. A name that matches nothing counts as zero everywhere and does nothing;
the bot doesn't warn about it. With no list a pack gets 2026's five (race, education, size, past vote).

**Learning the night's noise.** The forecast assumes places stray from what their make-up predicts by a set amount
(`SE`). As places finish it measures how far they really stray, as a multiple of that (shown as `noise`, 1 is as
expected), and widens the range for the vote still out to match. It also widens, never narrows, how far one state's
swing may sit from the national swing, when states with results show more. This is on by default (`LEARN`). On the
five replayed elections it reads about 0.65 to 1.1 at the end of the night, and about 2 when counties are made 6
points noisier. `python backtest.py --stress` replays elections unlike the ones the settings were chosen on (places
noisier, a wrong starting guess for `SE`, no features) with learning off and on, and prints how often the 90% range
held the result. In the readings so far learning keeps it close to 90% where fixed settings fall short.

**Races with more than two candidates.** `cw_multi.py` forecasts a first round with many names: each candidate's
share with a range, and the chance of finishing first, making the top two, winning outright, or going to a runoff.
It forecasts each candidate against everyone else with the same model, makes the shares add to 1, then draws
possible finishes where nobody ends below the votes already counted. Before any place reports it is the poll (or an
even split) with a wide range, and says so. It does not count ranked-choice rounds. `python backtest.py --multi`
tests it on made-up four-candidate races over the pack's real places: no real many-candidate election is in the
data, so it shows the arithmetic and the honesty of its ranges, not how a real city behaves. It is not yet wired
into the bot or the website: no feed reader produces its rows, and the workbook, alerts, auto-caller and website
still assume two sides.

**For another election.** What belongs to one election is in the pack's `feeds.countywatch` block, not in the code.
The block's `contests` lists the past contests in the pack's `baseline.csv` (which columns are each side's votes and
everyone's), `baseline` says which contest the night is measured from and at which one's turnout, and `backtests` lists
the elections `backtest.py` replays (one of them can be the pack's own election, marked `"result": "live"`). A pack
that says nothing gets 2026's meaning. `python backtest.py --pack <id>` runs another pack's elections.
`"not_comparable": ["NE"]` lists places whose baseline is a different kind of race (Nebraska's is its own 2024 race),
so their swing isn't used for other states. `"forecast": {"SE": 0.05}` overrides any of the model's settings (`HP` in
`cw_forecast.py`). The settings were fitted on statewide partisan generals, so a city race by ward or a primary has
no party baseline to hold its places together: widen its range by raising `SE`, `SS`, `SG0` and `SP` until a backtest
of that kind of election says otherwise. `feeds.problems` rejects a `forecast` that isn't named numbers and a
`not_comparable` place that isn't one of the pack's states.

## Feed check
`python feedcheck.py` runs the bot's own readers against every source it uses on the night: each state's county feed
and every backup in order (it also lists the states that have only one source) against the state's last real election, the results files, the NYT files, the early vote tracker and
the Bluesky accounts. It prints `ok`, `warn` (can't be checked from here: a site that blocks automated visitors, or a
file that only exists around election day) or `FAIL` for each, and exits 1 if anything failed. On GitHub
(`.github/workflows/feeds.yml`) a failure turns the run red and opens an issue titled "Feed check: a results source is
broken", which closes itself when everything reads again. The weekly Monday run is off between elections: run it by
hand from the Actions tab, and put the schedule back (the workflow file says how) in the months before one. A push
that changes a feed reader or the feed settings (`cw_feeds.py`, `results.py`, `feeds.json`, `results_sources.json`,
a pack's `pack.json`) runs it on that branch as well; only runs on the main branch open or close the issue.

Florida's results server sends a certificate that leads to a GoDaddy root Python doesn't know yet, so every request
failed. `trust.py` gives that one host GoDaddy's published cross-certificate (`certs/`), which links the new root to an
older one Python already trusts. No new root is trusted, and no other site is affected.

On the night, the Analyst desk's Feeds tile shows each state's backups and any held numbers, and the table *Where each
state's numbers are coming from* lists every source in order with the one in use, when it last answered, when its
numbers last changed and its last error.

## Data packs, servers and the global board
The bot isn't tied to one election. Everything about an election (its races, who can win them, what they add up to,
when polls close, how predictions score) is a **data pack**: a folder in `packs/` with a `pack.json`. A pack can be a
whole general election or one contest, such as a mayoral race.

| Command | Who | What it does |
|---|---|---|
| `/pack` | Anyone | Which election the bot is running, and the packs it could run. |
| `/pack switch_to:<id>` | Admin | Sets the pack the bot starts on. Restart the bot to switch. |
| `/globalboard` | Anyone | The scoreboard across every server that has joined it. |
| `/globalboard on` / `off` | Admin | Puts this server on the global board, or takes it off. Off by default. |
| `/globalme on` / `off` | Anyone | Keeps your own entry off the global board, or puts it back. |

- Each pack keeps its own records, and each Discord server its own within a pack, so one bot can run in several
  servers without their calls, picks or leaderboards mixing.
- The global board is scored on outlet calls and final results, never on any one server's own calls.
- A pack also names where its numbers come from (its `feeds` block): the state results files, the outlets read
  for calls, early vote and County Watch. A pack that names none fetches nothing and is run by hand. 2026's files
  are where they always were (`results_sources.json`, `countywatch/`).
- A feed is read once however many servers the bot is in. Each server acts on it for itself: its own automatic
  calls, field posts and County Watch alerts.
- `docs/data-packs.md` explains how to write a pack, and what isn't general yet.

## The website
One place for the whole night:

- **Results** is for anyone. Each race in a sentence, a bar for each side's chance of winning, the Senate's odds and
  likeliest seat count, with a short guide to reading it. No margins, no jargon.
- **Analyst desk** is for people who read margins. Every race's forecast, 90% range, chance, votes counted and left,
  what the trailing side needs against what it's on pace for, swing and poll miss; a "What to watch" list worked out
  from the numbers (a count and a forecast that disagree, races ready to call, feeds in trouble); and per race, the
  night's chart, where the outstanding vote is, key counties against their tie lines and the forecast's workings.
- **The model** explains the forecast and shows how it did on five replayed elections.
- **Predictions**, **Leaderboard** and **Calls** are the game and the desk's calls, as before. Before picks lock the
  site opens on Predictions; after, on Results.

It shares the bot's database, so a pick saved on the site is the pick the bot scores, and the bot posts each website
save to the board.

**Staying up.** The results pages never show an error because the numbers hiccuped: if a forecast can't be worked
out, the last good one is served with a note saying how old it is, and a browser that loses the site keeps showing
the last numbers it had. `/healthz` is there for an uptime checker. And `python -m web.export --every 60` writes
the whole site as plain files to `site/` every minute: point any static host at that folder and the pages stay up
when this PC doesn't. The copy is read-only (no sign-in or saving picks) and includes the leaderboard's names.

```
pip install -r requirements.txt
python -m uvicorn web.app:app --port 8026
```

Run it from this folder, next to the bot. Then:

1. In the [Discord developer portal](https://discord.com/developers/applications), open the bot's application,
   go to **OAuth2**, copy the **Client ID** and **Client Secret**, and add `https://your-site/auth/callback` under
   **Redirects**.
2. Put those in `.env` with `DESK_WEB_URL` (where people reach the site) and `DESK_WEB_SECRET` (any long random
   string). `.env.example` lists them.
3. Put the site behind HTTPS (a reverse proxy to port 8026). Sign-in cookies are only sent over HTTPS, except on
   localhost.

Signing in asks Discord for two things: who you are, and whether you're in the server (`GUILD_ID`). Nobody outside the
server can sign in. To try it on your own machine without Discord, set `DESK_WEB_DEV=1` and open
`http://localhost:8026/login?as=1&name=Me`.

The site obeys the same rules as `/predict`: the deadline, `/unlockpicks`, `hide_predictions_until_deadline`, and the
points in `config.json`. It can't make calls; those stay in Discord.

## Tests and replay
The rules for votes, overrides, retractions and outlet calls live in `desk.py`, with no Discord in them, so they can be tested. `bot.py` checks who is allowed, calls them, and words the posts.

```
pip install -r requirements-dev.txt
python -m pytest
python replay.py
```

- `python -m pytest` runs the rule tests and replays election night 2026: the same 877 recorded events have to give the same 482 calls and retractions in the same order, the same tally on all 348 vote posts, and the same leaderboard.
- `python replay.py` prints where the replayed night ends and whether it matches. `--until 2026-11-03T22:55-06:00` stops partway.
- `tests/fixtures/2026/` holds the night's real records with people replaced by numbers: the recorded events, the bot's database, County Watch's county counts and the Senate field's snapshots. Every file's name says `.fixture`, the bot refuses to run on one, and tests only ever open copies. `tests/fixtures/README.md` lists them.
- To rebuild them, or make a set for another night: `python tools/build_night_fixture.py --db desk.db --export <Discord export folder> --field field_history.json --out tests/fixtures/<year>`. The raw database and export hold Discord IDs and names, so they stay out of the repo.

GitHub runs the tests on every pull request.

## Known limits
- `/results` live counts cover FL, NC and VA only (County Watch adds county-level feeds for GA, NC, MN and NE). Only Decision Desk HQ's and Wasserman's House calls are read automatically (from Bluesky). Everything else is logged by hand with `/procall`.
- North Carolina's file has no "precincts reporting" column, so the bot shows how many precincts have any votes.
- `/seat` has the Cook PVI for only 18 of the 82 rated seats, and no nominees yet for 17 Likely-rated ones (AK-AL, AR-02, AZ-02, CA-06, CA-45, FL-07, FL-16, IN-05, KS-02, MI-08, MN-02, MO-02, NC-03, NE-01, NE-02, NJ-09, NY-19). Fill them in as you like in `seat_info.json` and `candidates.json`.
- For House seats without a known incumbent (open or redrawn), the tag shows `(D)` instead of HOLD or PICKUP.
- The bot is only online while your PC is on and the window is open. Set Windows to never sleep on election night.

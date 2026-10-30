# TDC Decision Desk bot

**Before election night:** everyone runs `/predict`, clicks states blue or red, and saves. Once results come in, the bot scores the picks on a leaderboard.

**On election night:**
- `/call` is one person's vote that a race is over. Every vote is announced in **#decision-desk-board**.
- Once a majority of the **Election Junkie** role agrees, the bot posts the official call card and updated map in **#decision-desk**.
- `/realcall` makes a call official without waiting for a majority.
- `/procall` logs a call by AP, NYT, Wasserman and the like.
- `/scoreboard` tracks who beat the pros, and by how many minutes.

Commands work in any channel. The bot replies to you privately and posts publicly only in the two desk channels.

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

**Who got it right:** every official call in #decision-desk ends with a prediction check listing who called it and who missed. `/race` shows the same for called races, and who picked what for uncalled ones. Turn it off with `"post_prediction_check": false` in `config.json`.

**Points:**
- Senate seat: 1
- House control: 5
- Governor: 1 (bonus)
- Battleground seat: 1 (bonus)
- +1 for every correctly predicted flip

Change the values under `points` in `config.json`. Picks are scored against outlet calls when one is logged, otherwise against the desk's official call.

## Election night

| Command | Who | What it does |
|---|---|---|
| `/call race winner` | Election Junkie | Your vote that the race is over. It's posted to the board, e.g. "Ethan has voted to call the Georgia Senate race for Jon Ossoff 🔵 (D HOLD) · 2 of 3 votes needed". When a majority agrees, the official call goes to #decision-desk. Calling again with the other party switches your vote. |
| `/unvote race` | Election Junkie | Withdraws your vote. |
| `/uncall race` | Election Junkie | A vote to retract an official call, using the same majority rule. |
| `/realcall race winner` | Election Junkie | Makes the call official right away, without waiting for a majority. It also counts as your vote. |
| `/procall race winner outlet` | Election Junkie | Logs a call by AP, NYT, Wasserman and the like. It posts to the board and feeds `/scoreboard`, but never changes the desk's map. |
| `/forceuncall race` | Admin | Retracts without a vote. |
| `/scoreboard` | Anyone | Per person: right and wrong calls, how often they were first, and the average minutes ahead of each outlet (e.g. "AP +14m · NYT +22m · best: NC-SEN, 41 min before Wasserman"). Also shows the official desk against the pros. |
| `/next` | Anyone | Races where polls have closed but the desk hasn't called, with vote counts and outlet calls. |
| `/board`, `/map`, `/race` | Anyone | Seat counts and path to majority, the current maps, or everything about one race. |
| `/closings` | Anyone | Poll-closing schedule. Alerts post to the board automatically as each wave closes. |
| `/deskreset RESET` | Admin | Clears test calls and votes. Predictions are kept unless you tick `include_predictions`. |

## Race info and live counts

| Command | Who | What it does |
|---|---|---|
| `/seat race` | Anyone | Background card for any race: the ratings average, who holds it and whether the incumbent is running or it's open, nominees, Cook PVI, poll close, what a win does to the ratings math, the desk's status, the group's picks, and Wikipedia/Ballotpedia/official results links. Works before election night. |
| `/results race` | Anyone | Live count from the state's official results file: each candidate's votes and share, the margin, precincts reporting, and a **Refresh** button. Leave `race` blank for every tracked race in the feed states. Counts are unofficial and never count as a call. |
| `/resultstest state` | Admin | Downloads that state's Nov 2024 file and checks the bot can read it. **Run this once for FL, NC and VA before election night.** |

Live counts come from **Florida, North Carolina and Virginia** (Virginia now reads the state's new results system), all early-closing states with 15 of the rated House seats (FL-07, 09, 13, 14, 16, 22, 25, 27; NC-01, 03, 11; VA-01, 02, 05, 07) plus the FL and NC Senate races. Other states get a link to their official results page in `/seat` and `/results`.

The feed links are in `results_sources.json`. The 2026 file names follow each state's usual pattern, but they can't be confirmed until the files go up on the night. If `/results` says a file isn't up after polls have closed, open the state's results page, find the download link, and paste it in as `url`. No restart needed.

Race IDs look like `GA-SEN`, `AZ-GOV`, `PA-07` and `AK-AL`, and `HOUSE` means control of the House. Autocomplete suggests them as you type.

**Majority** means more than half of the people who currently hold the Election Junkie role. To require a fixed number of votes instead, set `"threshold": 3` in `config.json`.

**Early warning seats:** `/earlywarning` lists the 19 rated House seats in states closing by 7:30 PM ET (Likely D through Lean R), grouped by closing time, with what's been called. Poll-closing alerts list every rated seat in the closing states, with early-warning seats flagged. After each rated House call, the bot posts the ratings math: Dems start at 223 if every seat they're favored in goes their way, and every upset moves that number. Ratings are Adam Carlson's Oct 23 average of Cook, Inside Elections and Sabato, in `competitive.py`.

## County Watch (county-level analysis)

A county-by-county read of 15 Senate races: the 14 that Cook doesn't rate Solid (AK, GA, IA, KS, ME, MI, MN, MS, NC, NE, NH, OH, SC, TX) plus Florida, which the group is treating as competitive. It lives in two places:

- **`County Watch 2026.xlsx`** (in this folder). This is the full workbook: dashboard, key counties with why they matter, every county with 2024/2020/2022 results and demographics, swing by county type, and early vote. Every number is a formula, so it works without the bot. Type county results into the yellow **Manual** columns on the Counties sheet from any source (NYT, AP, county sites), put `Y` in *Manual complete* when a county is done, and the projections update.
  - **Key Counties** sheet: 6 to 11 counties per state on both sides (Democratic vote banks and Republican bases such as Cherokee, Montgomery or Butler), plus an "All rural" row per state. Each county shows margin against break-even and, once it's fully counted, its turnout against what it needed.
  - **County Benchmarks** sheet: for each key county, the final margin and net votes that line up with each statewide result (R+5 through D+5), its par turnout, and a plain-English line, e.g. "Ossoff at D+51.5 or better = great night... At 95% of par the break-even moves to D+48.9".
  - **Types** sheet: each kind of county as a block, e.g. Georgia's metro cores are 33.5% of the expected vote and need D+37.5 for a tie (D+39.4 if they turn out at 95% of par). On the night it shows each type's swing, turnout index and whether it's on pace.
  - **First Counties** sheet: the first counties to report in each state, in poll-closing order. It shows what reports first, how much bluer early and mail votes were than the total in 2022, and the first-batch number that only means break-even. North Carolina is also adjusted for the 2026 early electorate.
- **Discord commands:**

| Command | Who | What it does |
|---|---|---|
| `/counties state` | Anyone | Before results: key counties, why they matter, the break-even margin and share of the vote in each. On the night: projection, swing by county type, turnout in Democratic-leaning vs Republican-leaning counties, which key counties are ahead of or behind pace on margin and turnout together, and the biggest movers. |
| `/earlyvote state` | Anyone | Early vote so far against 2022 turnout, with party, race and age where the state reports them. North Carolina adds a county-by-county comparison with 2022 at the same point, plus a modeled lean. |
| `/countyset state county dem rep [other] [done]` | Election Junkie | Enter a county's count by hand, from NYT or AP, for states without a feed. Autocomplete puts key counties (★) first. Set `done` only when every precinct is in; fully counted counties feed the projection. |
| `/countyclear state county` | Election Junkie | Removes a hand-entered count. |
| `/countyfile file [state]` | Election Junkie | Upload a results file: a state's download (CSV, Excel, Clarity XML, or the GA/NC/MN/NE formats) or your own county table. The bot loads it, posts what it found and any alerts, and updates the workbook. |
| `/environment` | Anyone | The national read. It pools every fully counted county in every state to show the swing from 2024 by county type, by state, and turnout against 2022, and lists which races that swing would flip. Before results, it shows early vote pace in every state. |
| `/countytest state` | Admin | Checks a live county feed against its Nov 2024 file (or, for Ohio, South Carolina and Alaska, that the site answers). **Run it once for each state before the night.** |
| `/countyfeed state link` | Admin | Points a Clarity state (South Carolina, Iowa) at its election link, e.g. `https://www.enr-scvotes.org/SC/127500/`, if the site doesn't list Nov 3 by date. `clear` undoes it. |
| `/countyrefresh` | Admin | Pulls everything now and rebuilds the workbook. |

**What the bot pulls by itself:**
- **Live county results:** Georgia, Florida, North Carolina, Minnesota, Nebraska, Iowa, South Carolina and Alaska (plus Virginia as a bellwether), all checked at once every 30 seconds from 6 PM ET on Nov 3 to 8 AM ET Nov 4. Ohio and Texas are tried too, but their sites sit behind Cloudflare's bot check; if they answer the bot with a challenge page, it stops trying for 15 minutes and posts once that you should use the grab button. The bot only downloads a file again when the state has changed it. Georgia uses its single-race endpoint, about 1.6 MB instead of the 33 MB full export. Each state is ignored until its polls close, because states load test numbers into their live files beforehand.
- **Early vote:** every 6 hours until Election Day. This covers the UF Election Lab statewide tracker, plus North Carolina's full absentee file (about 85 MB a pull).
- **Alerts:** posted to #decision-desk-board as soon as numbers move: a new or changed projection (first projection, confidence going up, a change of leader, or a shift of a point or more), and key counties that finish counting.
- **The workbook:** refreshed every 2 minutes in the background, so it never slows the feeds. Your manual entries, `/countyset` entries and turnout factors are kept.

**Bellwether states:** Virginia and Kentucky are tracked for their early county results, not their Senate races, which are safe. They show the national swing before the battlegrounds fill in:
- Virginia is automatic. It moved to the same results system as Georgia, and its Nov 3 election is already set up there.
- Kentucky's eastern counties close at 6 PM ET, the first in the country.
- Virginia closes at 7 PM ET and has 2022 early-vote splits for first-batch benchmarks.

Kentucky is manual until tonight's check. Once 20 or more counties are fully counted across all states, the bot posts a 🌡️ National read to the board, and updates it as it moves.

**The County Watch grab button (Ohio, Texas, Kansas):** these three states have statewide results sites behind a bot check, so the bot can't read them itself. Make the bookmark once from the County Watch Grab page (or paste `countywatch/grab.js` into a bookmark as `javascript:` code). On the night, open the state's results site in Chrome or Edge and click it: it saves `countywatch-OH.json` (or TX, KS) to Downloads and the bot loads it within 10 seconds. The first load posts a line; later ones load quietly and only alerts post. Before a state's polls close the bot treats a grab as a test and doesn't use the numbers.

**State by state:**
- Automatic: GA, NC, FL, MN, NE, VA (bellwether), IA, SC (Clarity: the bot finds the Nov 3 election by date, or use `/countyfeed`), AK (district results arrive after 1 AM ET).
- Grab button: OH, TX (once its site switches to the 2026 general), KS (reads all 105 county pages).
- Manual: MI (the state posts results only after every county reports, so use the county clerk sites), ME, NH, MS (counties post PDFs) and KY (behind Akamai's bot check, one page per county). Enter key counties with `/countyset`.

**Dropping files in:** save any results file into `countywatch/inbox` (the bot creates the folder) and it's loaded within 10 seconds, then moved to `inbox/done`. The file name should include the state, like `TX results.csv` or `Texas.xlsx`; otherwise the bot works it out from the county names. A plain table works if it has a County column plus either one column per candidate (headed with names or Dem/Rep) or Candidate/Party/Votes rows. Optional columns: % reporting, precincts reporting and total precincts, and a Final/Complete column marked Y. A newer feed pull for the same state replaces a loaded file.

**Colored calls:** the Dashboard's Call column and the reading columns on Key Counties, County Benchmarks and First Counties use five shades. Dark blue means great for D, light blue D on track, grey toss-up, light red R on track, dark red great for R. The tiers are D+5 and above, D+1 to D+5, within 1 point, R+1 to R+5, and R+5 and beyond, measured against break-even.

For the manual states, enter key counties with `/countyset` (fastest, and anyone can help) or in the workbook. Split the states among the group. `countywatch/feeds.json` holds the feed settings.

**How the projection works:**
1. Fully counted counties give a swing from 2024.
2. Each county type (metro cores, college suburbs, Black Belt, Hispanic, rural and so on) gets its own average swing.
3. Each county still counting is projected from its 2024 margin plus its type's swing. Its partial count is ignored, because early returns are usually one vote method and lean one way.
4. Its vote total is its 2022 turnout times its type's turnout pace from finished counties (or the state's).

**Turnout:** benchmarks use 2022's midterm turnout pattern with 2024's county margins. That moves some needed swings slightly (Georgia: R+2.2 in 2024, R+1.1 at midterm turnout). Each county's *par* is its 2022 share of the state's two-party vote, scaled to the night's turnout. Its **turnout index** is actual votes against par, where 100 means in line with the state. A Democratic county at index 95 needs about a 5% bigger margin than its break-even; a Republican county at 105 has to be held about 5% closer. "Margin + turnout vs tie pace" combines the two in margin points. Times the county's votes it's net votes against a tie, and those add up across counties to the statewide margin. Alerts and `/counties` also compare turnout in finished Democratic-leaning and Republican-leaning counties. A turnout gap between county types only reaches the projection once counties of that type finish, so a weak metro night shows up late. Watch those indexes.

The Read Me sheet has the details and limits.

**Excel open during updates:** with `xlwings` installed (`start_bot.bat` installs it on Windows), the bot writes into the open workbook. Without it, the bot writes `County Watch live.csv` until you close the file.

## Making it yours

- **`flavor.json`**: the lines posted with calls, flips and retractions. These reload instantly, so this is where the in-jokes go.
- **`candidates.json`**: nominee names for all Senate and governor races and the 46 battleground House seats, verified Oct 22. An `I` entry adds the independent option to the picker.
- **`seat_info.json`**: incumbents, open-seat reasons and Cook PVI for `/seat`. Changes apply live.
- **`results_sources.json`**: live results file links and official results pages. Changes apply live.
- **`competitive.py`**: the bonus House seats, plus the `SAFE` list of pre-filled races. Edit them before people predict.
- **`config.json`**: channel names, role name, deadline, points, and `hide_predictions_until_deadline`.
- **`assets/`**:
  - `calls/GA-SEN_D.png` replaces the generated card with your own graphic.
  - `candidates/GA-SEN_D.png` is a headshot placed on the card.
  - `memes/GA-SEN_D.png` is a transparent cutout placed bottom-left.

## Rehearse
Put a fake time in `.env`, e.g. `DESK_FAKE_NOW=2026-11-03T19:05-05:00`, and restart. Run through the night: votes, `/realcall`, alerts, `/scoreboard`. Then remove the line, restart, and run `/deskreset RESET`. Don't rehearse past the deadline with real predictions in place, because the "predictions locked" post only goes out once.

## Known limits
- `/results` live counts cover FL, NC and VA only (County Watch adds county-level feeds for GA, NC, MN and NE). There's no tweet feed yet, so outlet calls are logged by hand with `/procall`.
- North Carolina's file has no "precincts reporting" column, so the bot shows how many precincts have any votes.
- `/seat` has the Cook PVI for only 18 of the 82 rated seats, and no nominees yet for 17 Likely-rated ones (AK-AL, AR-02, AZ-02, CA-06, CA-45, FL-07, FL-16, IN-05, KS-02, MI-08, MN-02, MO-02, NC-03, NE-01, NE-02, NJ-09, NY-19). Fill them in as you like in `seat_info.json` and `candidates.json`.
- For House seats without a known incumbent (open or redrawn), the tag shows `(D)` instead of HOLD or PICKUP.
- The bot is only online while your PC is on and the window is open. Set Windows to never sleep on election night.

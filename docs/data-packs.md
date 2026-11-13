# Data packs

A data pack is everything that makes the desk about one election, written as data. It can be a whole general
election or a single contest. The code reads the active pack and knows nothing else about any year.

A pack is a folder in `decision-desk/packs/` holding a `pack.json`. The folder's name is the pack's `id`.

| Pack | What it is |
|---|---|
| `2026-general` | The 2026 midterms as they were run: 35 Senate seats, 36 governors, 435 House seats, both chambers. |
| `2028-general` | A skeleton for 2028: president by state with electoral votes, Senate, House, governors. Nominees, holders and ratings still to fill in. |
| `2027-chicago-mayor` | A single-contest template: one nonpartisan race with several candidates, plus a side bet. Placeholder names. |

`python tools/make_starter_packs.py` rewrites the last two from scratch.

## Choosing a pack

- **The bot** runs one pack at a time: `DESK_PACK=2028-general` in `.env`, or `/pack switch_to:2028-general` and a
  restart. `/pack` lists what's available.
- **The website** has a menu at the top right and switches at once.
- **Records never mix.** Each pack keeps its own database, and each Discord server its own within the pack:
  `decision-desk/data/<pack>/<server id>/desk.db`. The first server's 2026 records stay in `decision-desk/desk.db`,
  where they have always been. Set `DESK_DATA` to keep records somewhere else.

## Servers and the global board

One bot can serve several Discord servers. Each has its own desk: its own calls, votes, outlet log, predictions and
leaderboard. Nothing is shared unless a server asks for it.

- `/globalboard on` (admin) puts a server on the global board for the pack it's running. `/globalboard off` takes
  it off again.
- `/globalme off` keeps one person's entry off the global board even when their server has joined.
- `/globalboard` shows the board.

The global board scores every entry against the same results: final results an admin of any joined server entered
with `/final`, then the earliest outlet call any of them logged. A server's own desk calls are its opinions and are
never shared.

## `pack.json`

Only `id`, `name` and `election_day` are required. Everything else is there when the election needs it.

```json
{
  "id": "2027-chicago-mayor",
  "name": "Chicago mayoral election, 2027",
  "summary": "One line for the pack list.",
  "election_day": "2027-02-23",
  "timezone": "America/Chicago",
  "predictions_deadline": "2027-02-23T18:00:00-06:00"
}
```

### `races`

One entry per contest.

| Field | Meaning |
|---|---|
| `id` | Short and unique: `GA-SEN`, `TX-15`, `CHI-MAYOR`. Letters, digits and hyphens. |
| `type` | What kind of race: `senate`, `governor`, `house`, `president`, `mayor`, `question`, or any word of your own. `control` is special: a race for who wins a body. |
| `jurisdiction` | A state code, or a code from the pack's `jurisdictions` (`{"CHI": "Chicago"}`). Sets poll closing times. |
| `label` | What to call it. Optional when there's a jurisdiction: `GA` + `senate` reads "Georgia Senate". |
| `holder` | Who holds it now. A pick that beats the holder is a flip. |
| `options` | Who can win. See below. |
| `body`, `weight` | What the race counts toward, and for how much. A state's presidential race counts its electoral votes toward `electoral_college`. |
| `special`, `level`, `group` | A special election; `federal`, `state` or `local`; a heading to list it under. |

**Options.** Leave `options` out for an ordinary partisan race: it's Democrat and Republican, plus an independent
where `candidates.json` names one. Otherwise list them:

```json
"options": [
  {"key": "A", "name": "Candidate A"},
  {"key": "B", "name": "Candidate B", "party": "D"},
  {"key": "YES", "name": "Yes", "color": "#0f766e"}
]
```

`party` borrows a colour and a label from the pack's shared `options`; `color` sets one directly; with neither, each
option gets its own colour from a fixed palette.

**`house_seats`** is shorthand: `{"TX": 38, "AK": 1}` expands to every U.S. House district.

### `options`, `jurisdictions`

`options` are the answers races share, usually parties: name, noun, plural, colour and emoji for each key. Leave it
out to get D, R and I. Set it to `{}` for a pack with no parties. `jurisdictions` names places that aren't states.

### `bodies`

Things races add up to.

```json
"bodies": {
  "senate": {"name": "Senate", "seats": 100, "majority": 51, "holdover": {"D": 34, "R": 31},
             "tie_goes_to": "R", "control": "US-SENATE"},
  "electoral_college": {"name": "Electoral College", "seats": 538, "majority": 270, "control": "US-PRES"}
}
```

`holdover` is what each side holds that isn't up. `tie_goes_to` needs one seat fewer. `control` names the race of
type `control` that says who won the body.

### `ratings`, `closings`

- `ratings.rated`: `[race, rating, holder]` for every race a rater has as anything but safe. A rating ends in the
  option it favors (`"Lean D"`).
- `ratings.battleground`: the same shape, for the bonus races in the prediction game.
- `ratings.safe`: `{race: option}`, pre-filled in the picker.
- `closings`: `{"GA": ["19:00", "19:00"]}`, most polls closed and last polls closed, in the pack's timezone. `"*"` is
  the default; with no closings at all, everything closes at 8 PM.

### `game`

How predictions score.

```json
"game": {
  "categories": [
    {"key": "president", "name": "President, state by state", "points": 1, "required": true, "races": {"type": "president"}},
    {"key": "presidency", "name": "The presidency", "points": 10, "implied_by": "president", "races": {"ids": ["US-PRES"]}}
  ],
  "flip_bonus": 1,
  "tiebreaker": {"label": "How many electoral votes does the winner get?", "min": 270, "max": 538}
}
```

A category's `races` rule is `{"type": ...}`, `{"ids": [...]}`, `{"battleground": true}` or `{"rated": true}`. A race
belongs to the first category it fits. `implied_by` marks a category nobody picks directly: it is whatever their
picks in that body add up to (Senate control from 35 seat picks, the presidency from 56 state picks).

### `sections`

How the website lays out the picker. Leave it out for one plain list per category.

| `kind` | What it draws |
|---|---|
| `map` | A U.S. map of statewide races whose IDs end in `suffix`, with a running tally of `body`. |
| `house` | A control pick (`control`), the tiebreaker, and a list of rated seats. |
| `list` | The races of `category`, one per row. |

### `feeds`

Where the pack's numbers come from on the night. Every part is optional, and a pack with no `feeds` at all fetches
nothing: calls, outlet calls and finals are entered by hand, which is all a one-off race needs.

```json
"feeds": {
  "results": "results_sources.json",
  "nyt": {"house": "https://...", "senate": "https://...", "governor": "https://..."},
  "bluesky": {"since": "2028-11-07T17:00:00-05:00", "accounts": {"did:plc:...": "Decision Desk HQ"}},
  "early_vote": {"uf": "https://...", "nc_absentee": "https://..."},
  "countywatch": {"folder": "countywatch", "race": "{state}-SEN", "workbook": "County Watch 2026.xlsx",
                  "states": ["GA", "NC"], "bellwether": ["VA"], "night_ends": "08:00"}
}
```

| Part | What it turns on |
|---|---|
| `results` | The file, in the pack's folder, of official state results files that `/results` reads. Same layout as 2026's `results_sources.json`. |
| `nyt` | The NYT results files read for calls. `house` feeds automatic House calls; all three feed `/callnyt`. A file is trusted only if it holds nine in ten of the pack's races of that kind. |
| `bluesky` | The accounts read for House calls, by permanent ID, and the time from which their posts count (5 PM on election day if left out). Only Decision Desk HQ and Dave Wasserman posts can be read. |
| `early_vote` | The UF Election Lab tracker and North Carolina's absentee file. |
| `countywatch` | County Watch: the states it models, the race it follows in each (`{state}-SEN`, or `{state}-PRES` in a presidential year), and the folder with its baselines. Without it, the County Watch commands, the field graphic, `/trend` and the desk's bot are off. |

A County Watch folder holds what 2026's `decision-desk/countywatch` does: `baseline.csv` (each county's last
presidential result, its last comparable turnout and its demographics), `key_counties.json`, `first_counties.json`,
`county_districts.json` and `feeds.json` (each state's live county feed). The column names in `baseline.csv` still
say 22 and 24; in another year they mean "the turnout pattern to expect" and "the margins to swing from".

What the pack ships is only read. What the night writes (pulled results, what alerts already said, the field's
history, files dropped in the inbox, the workbook) goes under `data/<pack>/countywatch`, beside the servers'
records. 2026 is the exception: its files have always shared `decision-desk/countywatch`, and still do.

**Feeds and servers.** Everything a feed pulls is public, so it is fetched once however many servers the bot is
in. Each server then acts on it for itself: its own automatic calls (and its own `/autocalls` switch), its own
field posts with its own calls on them, County Watch alerts in its own board channel, and its own `/watchstate`
and `/resultsdm` messages. Counties typed in with `/countyset`, files loaded with `/countyfile` and a feed
re-pointed with `/countyfeed` change the shared numbers, so they show for every server.

### Files beside `pack.json`

`candidates.json`, `seat_info.json`, `flavor.json`: the same files the bot has always read, now per pack. A pack with
`"shared_files": true` (2026) uses the copies in `decision-desk/` instead.

## Checking a pack

A pack that's wrong is refused when the bot starts, with every problem listed in plain words. `python -m pytest`
loads every pack in `packs/`.

## What is not general yet

- **Call cards use a neutral grey for a candidate who isn't a party's.** They draw in every theme, but don't yet
  pick up the colour the pack gives each option.
- **The `/predict` map image** is only drawn for Senate and governor pages. A presidential page has its buttons but
  no map in Discord; the website has the map.
- **The tiebreaker box in Discord** still describes itself as House seats. The website uses the pack's wording.
- **County Watch's model is still built around a two-party state race.** The states, the race it follows and every
  file it reads now come from the pack, but only 2026 ships baselines. A 2028 pack needs its own `baseline.csv`
  and `feeds.json`, and three pieces are still written for 2026's races: the Mississippi and Alaska special
  handling, the Nebraska baseline (Osborn's 2024 race), and the Excel workbook's wording.
- **The field graphic and `/trend`** follow Senate races, whatever race County Watch is pointed at.
- **State parsers are per state, not per year.** A state that changes its results site needs its parser updated;
  the pack only says where the file is.
- **No national feed.** Nothing reads a presidential or all-states results source yet.

## Flavor lines

`flavor.json` holds the lines posted with calls. `flavor.py` picks them:

1. A line written for the race and its winner (`"races": {"GA-SEN": {"D": [...]}}`).
2. Otherwise a line for the kind of result: `upset`, `flip`, `hold`, `control`, else `call`.

A line isn't used again until every other line in its pool has had a turn, and that memory survives a restart. Lines
can name `{winner}`, `{loser}`, `{place}`, `{seat}`, `{rating}` and `{party}`. A race can also have its own
`"retract"` lines. A pack with no `flavor.json` gets built-in lines, so a call always has one.

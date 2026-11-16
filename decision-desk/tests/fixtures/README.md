# Test fixtures

Real records from an election night, kept so the tests can check today's code against what actually happened.

## What makes them safe to keep here

- **They can't be mistaken for live files.** Every fixture's name says `.fixture`. The bot's own files are
  `desk.db`, `countywatch/field_history.json`, `countywatch/live.json` and so on, and it never looks in `tests/`.
- **The bot refuses to run on one.** Pointing `DESK_DB` at a `.fixture` file stops with a message to copy it first.
- **Tests work on copies.** `replay.fixture_db(tmp_path)` hands out a copy of the database; the fixture is never
  opened for writing. A test checks that it is byte-for-byte unchanged afterwards.
- **Nobody real is in them.** People are numbers, the same number in every file. The builder scans the database copy
  for any real Discord ID or name and throws the copy away if it finds one.

## `2026/`

| File | What it is | Used by |
|---|---|---|
| `night.fixture.json` | Every vote, override, retraction and outlet call of the night in order, the roster and its changes, the predictions, and what the bot did with them. | `test_night_2026.py`, `replay.py` |
| `desk.fixture.sqlite` | The bot's database from the night, rebuilt row by row with people as numbers. What a real `desk.db` looks like. | `test_records_2026.py` |
| `countywatch.fixture.json` | The county counts County Watch was holding: all 82 Mississippi counties. | `test_records_2026.py` |
| `field.fixture.json.gz` | The Senate field's 580 snapshots through the night: projected margin and votes counted. | `test_field_2026.py`, `test_records_2026.py` |
| `modules_snapshot.json` | What `races.py`, `competitive.py` and `night.py` held while 2026 was still written into them. The 2026 data pack has to load to exactly this. | `test_packs.py` |

## Rebuilding, or adding another night

```
python tools/build_night_fixture.py --db desk.db --export <Discord export folder> --field field_history.json --out tests/fixtures/<year>
```

The inputs are the raw records: the bot's `desk.db`, a DiscordChatExporter export of the board and desk channels
(all authors), and `countywatch/field_history.json`. They hold Discord IDs and names, so they stay out of the repo
(`.gitignore` covers them at the repository root). Only what the builder writes is committed.

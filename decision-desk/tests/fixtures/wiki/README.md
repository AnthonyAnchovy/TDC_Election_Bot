# Saved Wikipedia wikitext for test_polls_wiki.py

Wikipedia text is CC BY-SA 4.0; authors are in each article's page history. These are excerpts (the polling
sections and nothing else), saved so the tests never touch the network.

| File | From the article | Layout it covers |
|---|---|---|
| `senate_2026_georgia.wiki` | 2026 United States Senate election in Georgia (general election polling) | `Poll source`, party tags `(R)`, `{{sdash}}`, rowspan'd poll variants, an aggregator table, many head-to-head tables |
| `president_2028_national.wiki` | Nationwide opinion polling for the 2028 United States presidential election | headings with only last names and party templates, two-row header, three-way hypotheticals (footnotes removed) |
| `chicago_2023_mayor.wiki` | 2023 Chicago mayoral election (first round and runoff polling) | ten nonpartisan candidates, rating and sponsor columns, runoff table (footnotes removed) |
| `senate_2008_minnesota.wiki` | 2008 United States Senate election in Minnesota | old plain table: no sample size, one-day dates, `(DFL)` tags (footnotes removed) |
| `no_polls_senate_2026.wiki` | 2026 United States Senate elections | a real page section with a table but no polls |
| `year_headings_made_up.wiki` | invented | dates with no year under year headings, a year row in a table, a footnote naming a campaign |

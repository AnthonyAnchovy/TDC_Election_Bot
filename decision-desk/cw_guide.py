"""County Watch: the 'Start Here' tab. A plain-language guide for people who are only watching the workbook and have
never followed results county by county, and an explanation of how it turns those counties into a projection."""
from __future__ import annotations

import math

from openpyxl.cell.rich_text import CellRichText, TextBlock
from openpyxl.cell.text import InlineFont
from openpyxl.styles import Alignment, PatternFill

import cw_workbook as W

F = W.F
WIDTH = 118                 # column B width, in characters
PER_LINE = 112              # roughly how many characters of 11pt Arial fit on one line of it
NAVY = "1F2A44"
SOFT = PatternFill("solid", fgColor="F3F4F7")
NOTE = PatternFill("solid", fgColor="FFF8E1")

# (kind, text). kinds: title, lede, h (section), p (paragraph), b (bullet: "Lead-in|rest"), ex (example box),
# term ("Word|meaning"), gap
GUIDE = [
    ("title", "Start here: what this workbook is and how to read it"),
    ("lede", "If you've never followed an election night county by county, start with this page. It takes about ten minutes "
             "to read."),
    ("gap", ""),

    ("h", "The short version"),
    ("p", "We're watching the 15 Senate races that could go either way, plus Virginia and Kentucky as early hints about "
          "the national mood. As each county finishes counting, we compare its result with how it voted in 2024. If "
          "the counties that are done have moved toward one party, we assume the counties still counting will move "
          "the same way, and add it all up. That's our projection."),
    ("p", "Open the At a Glance tab on the night. It has one needle per race, a bar showing "
          "who's ahead, and how sure we are. The other tabs are the workings behind it."),
    ("p", "You don't need to do anything but watch. The workbook updates itself about every 2 minutes on the night, "
          "and the top right of At a Glance shows when it last updated. If that time looks old, reload the page."),
    ("gap", ""),

    ("h", "How election night works"),
    ("p", "Polls close at different times in different states, from 6 PM Eastern in parts of Kentucky to 1 AM "
          "Eastern in Alaska. Nothing gets counted publicly until a state's polls close."),
    ("p", "After that, each county counts its own ballots and posts the numbers as it goes. There are usually three "
          "kinds of ballots, and they don't arrive at the same time:"),
    ("b", "Early in-person votes|Cast in the weeks before Election Day. Many states count these first, so they're "
          "often the very first numbers you see."),
    ("b", "Mail ballots|Sometimes counted early, sometimes last. It depends entirely on the state's rules."),
    ("b", "Election Day votes|Cast today at the precinct. These come in through the evening as each polling place "
          "reports."),
    ("p", "In recent years Democrats have voted early and by mail more than Republicans, and "
          "Republicans have voted on Election Day more than Democrats. So the first batch from a county can look "
          "lopsided, then swing the other way as the rest comes in. This is normal."),
    ("ex", "Example: a county posts its early votes at 7:31 PM and the Democrat is up by 30. By 10 PM, with Election "
           "Day votes added, she's up by 8."),
    ("p", "That's why this workbook mostly ignores half-counted counties. It waits until a county is finished, "
          "because a finished county's result is close to final."),
    ("gap", ""),

    ("h", "Who \"calls\" a race, and what our projection is"),
    ("p", "News organizations like the AP and the New York Times \"call\" a race when they're confident who won, "
          "usually long before every vote is counted. They use their own models and data we don't have."),
    ("p", "This workbook doesn't call races. It shows where each race is heading based on the counties that are "
          "completely counted, and how uncertain that is, often before a race is called."),
    ("gap", ""),

    ("h", "How the projection is built, step by step"),
    ("b", "1. Wait for finished counties.|A county counts as finished when the state says every precinct has "
          "reported and most of the votes we expected are in, when one of the people helping on the night marks it complete, or when its count reaches 97% of "
          "the votes we expected from it. Until a state has at least one finished county, there's no projection, only the poll."),
    ("b", "2. Measure the swing.|For each finished county, we compare tonight's margin with its 2024 presidential "
          "margin. If Cobb County went for Harris by 15 and tonight goes for the Democrat by 19, that's a 4-point "
          "swing toward the Democrats."),
    ("b", "3. Group similar counties.|Counties are sorted into types: big-city cores, college-educated suburbs, "
          "working-class metros, the majority-Black counties of the Deep South (the Black Belt), majority-Hispanic "
          "counties, Native American counties, and small towns and rural areas. Similar places tend to move together, "
          "so each type gets its own average swing."),
    ("b", "4. Don't let one small county speak for everyone.|If the first rural county in is a tiny one with a "
          "strange result, we don't apply that to every rural county in the state. A type's swing gets pulled toward "
          "the state's overall swing until enough of that type has actually been counted."),
    ("b", "5. Fill in the rest.|Every county still counting gets its 2024 margin, moved by its type's swing. "
          "How many votes we expect from it is based on 2022 turnout, adjusted to how turnout is running tonight."),
    ("b", "6. Add it up.|Finished counties plus filled-in counties gives the statewide projection."),
    ("ex", "Why 2022 turnout? 2026 is a midterm, and midterm voters aren't the same crowd as presidential voters. "
           "Fewer people vote, and the mix of places they come from shifts. 2022 is the closest guide we have to "
           "how big each county's share of tonight's vote will be."),
    ("p", "The Nebraska race is different. The candidate against the Republican there is Dan Osborn, an independent, "
          "so Nebraska is measured against Osborn's own 2024 Senate race, not the presidential race."),
    ("gap", ""),

    ("h", "How to read the At a Glance tab"),
    ("b", "The tiles across the top|The Senate tally if every race went the way it leans right now, who that gives "
          "the majority, how the results compare with the final polls so far, how much of the vote is counted, and "
          "the national swing across every finished county."),
    ("b", "The needles|One per race, in the order polls close. The needle points at the projection (or at the final "
          "poll, before any county there has finished). The colored band underneath goes from deep red (a big "
          "Republican win) through grey (a toss-up) to deep blue (a big Democratic win). The grey arc around the "
          "needle is the range we'd expect the final result to land in, 9 times out of 10."),
    ("b", "Under each needle|How many votes are still left to count, how much of that the trailing candidate "
          "needs to tie, and how much they're on pace to get. If someone needs 54% and is on pace for 47%, they're "
          "in trouble. If they need 50.5% and are on pace for 50.1%, it's a coin flip."),
    ("b", "The race board|The same races as a table. Light, dotted bars are the final poll. Solid bars are the "
          "live projection. One block is one point of margin. The call column turns blue or red as a race leans "
          "one way."),
    ("b", "\"If the polls are off\"|What the Senate would look like if the polls were right, if they missed the "
          "way they did in 2020, and so on. Most useful before results come in."),
    ("ex", "A needle sitting near the middle with a wide grey arc means \"we really don't know yet.\" A needle well "
           "into the blue with a narrow arc that stays blue means the Democrat is very likely to win."),
    ("gap", ""),

    ("h", "What the colors and words mean"),
    ("b", "Great for (name)|The projection, or the county, is at least 5 points better for that candidate than "
          "they need to win. Dark blue or dark red."),
    ("b", "(Name) on track|Between 1 and 5 points better than they need. Light blue or light red."),
    ("b", "Toss-up|Within a point either way. Grey."),
    ("b", "Confidence low, medium, high|How much of the state's vote comes from finished counties. Low means under "
          "10% (or fewer than 5 counties), and should be read like an early weather forecast. Medium is 10 to 40%. "
          "High is above 40%."),
    ("b", "Final poll vs projected|Before a state has finished counties, its needle and margin show the final "
          "poll, labeled as such and shown in grey italics. Once results take over, the label says projected."),
    ("gap", ""),

    ("h", "Polls, and why we don't mix them in"),
    ("p", "The Polls tab has the last poll (or polling average) for each race. The projection never uses them. "
          "Polls are only there as a yardstick: once a state has real results, you can see whether it's running "
          "ahead of or behind its poll."),
    ("p", "That matters because polls tend to miss in the same direction everywhere at once. In 2020 they "
          "underestimated Republicans almost across the board. So if the first few states come in 3 points more "
          "Republican than their polls, the later states may be too."),
    ("b", "Other states imply|On the race board, this column estimates a race from how similar counties are "
          "voting in other states tonight. It's how you get a feel for Iowa before Iowa has counted much. It only "
          "compares like with like (rural counties with rural counties in the same region, for example), and it "
          "never feeds the projection itself."),
    ("gap", ""),

    ("h", "The other tabs, in plain English"),
    ("b", "Read Me|The technical notes: formulas, sources and limits. Skip it unless you're curious."),
    ("b", "Dashboard|One row per race, in poll-closing order. Graphics on the left, every number behind At a Glance on the right, and a written take on each race under the table."),
    ("b", "Polls|The final polls, and how far results are running from them."),
    ("b", "Key Counties|6 to 11 counties per state that matter most, with a note on why and what to watch for."),
    ("b", "County Benchmarks|For each key county: the margin it needs to post for its candidate to be on track to "
          "win, lose narrowly, or win big."),
    ("b", "First Counties|Which counties tend to report first in each state, and what their first numbers really "
          "mean once you account for early votes leaning Democratic."),
    ("b", "Counties|Every county, with its history and tonight's numbers."),
    ("b", "Types|The county groups from step 3 above, with how each is doing."),
    ("b", "Early Vote|How many people voted before today, compared with 2022."),
    ("gap", ""),

    ("h", "Where the numbers come from"),
    ("p", "Many results come in automatically from state election websites. Some states don't post them in "
          "a form that can be read automatically, so a few people enter key counties by hand from the New York Times, "
          "the AP or the county's own website. That's why you may see a county's numbers jump all at once, or a state "
          "fill in more slowly than the news."),
    ("gap", ""),

    ("h", "Things that fool people on election night"),
    ("b", "Early leads that shrink|Early and mail votes lean Democratic and Election Day votes lean Republican, "
          "so a big early lead often gets smaller."),
    ("b", "Rural counties finish first|Small counties are quick to count, big cities are slow. So the first "
          "projection in a state often leans on a handful of small towns. That's why the range starts wide."),
    ("b", "\"Only 20% reporting\"|That number counts precincts, not votes, and not every precinct is the same "
          "size. Twenty percent of precincts can be 5% of the vote or 40% of it."),
    ("b", "A state counting overnight|Alaska won't be meaningful until well after midnight, and states with lots "
          "of mail ballots can take days."),
    ("b", "One county isn't the state|A big swing in one county doesn't mean much alone. Look for the same "
          "swing across many counties of different types."),
    ("gap", ""),

    ("h", "A few words you'll hear"),
    ("term", "Margin|The gap between the two candidates. D+5 means the Democrat leads by 5 points. Here it's "
             "always measured between the top two, ignoring third parties."),
    ("term", "Swing|How much a place moved compared with a past election. A 3-point swing toward Republicans in a "
             "county that was D+10 leaves it at D+7."),
    ("term", "Break-even|The margin a county needs to post for its candidate to tie statewide, if every county "
             "moves the same amount. Beating break-even is good news for that candidate."),
    ("term", "Turnout index|How hard a county is turning out compared with the state as a whole. 100 is average. "
             "A Democratic city at 92 is underperforming even if its margin looks fine."),
    ("term", "Two-party vote|Votes for the two main candidates only. It makes margins comparable from year to year."),
    ("term", "Toss-up, Lean, Likely, Safe|The ratings forecasters give a race before the election, from "
             "coin flip to sure thing."),
    ("term", "Holdover seats|Senate seats that aren't up this year. They count toward each party's total no matter "
             "what happens tonight."),
    ("gap", ""),

    ("h", "When to trust it, and when not to"),
    ("p", "Trust it more as the night goes on and the grey arcs shrink. Trust it less early on, in states with few "
          "finished counties, and anywhere a big city hasn't reported yet. It also can't see things like a "
          "candidate's home-town advantage, a ballot problem in one county, or a late batch of mail votes that "
          "breaks differently from everything before it."),
    ("p", "When it disagrees with the AP, the AP is usually right."),
]


def _rows_needed(text: str, per_line: int = PER_LINE) -> int:
    return max(1, sum(math.ceil(max(1, len(part)) / per_line) for part in text.split("\n")))


def guide_sheet(ws):
    ws.sheet_view.showGridLines = False
    ws.column_dimensions["A"].width = 3
    ws.column_dimensions["B"].width = WIDTH
    ws.column_dimensions["C"].width = 3
    bold = InlineFont(rFont=W.FONT, b=True, sz=11, color="1F1F1F")
    plain = InlineFont(rFont=W.FONT, sz=11, color="1F1F1F")
    r = 1
    for kind, text in GUIDE:
        c = ws.cell(row=r, column=2)
        if kind == "title":
            c.value = text
            c.font = F(bold=True, size=18, color="FFFFFF")
            c.fill = PatternFill("solid", fgColor=NAVY)
            c.alignment = Alignment(vertical="center", indent=1)
            ws.row_dimensions[r].height = 40
        elif kind == "lede":
            c.value = text
            c.font = F(italic=True, size=12, color="404040")
            c.alignment = Alignment(wrap_text=True, vertical="top")
            ws.row_dimensions[r].height = 17 * _rows_needed(text, 100) + 6
        elif kind == "h":
            c.value = text.upper()
            c.font = F(bold=True, size=12, color=NAVY)
            c.fill = SOFT
            c.alignment = Alignment(vertical="center", indent=1)
            ws.row_dimensions[r].height = 26
        elif kind in ("b", "term"):
            lead, rest = text.split("|", 1)
            prefix = "•  " if kind == "b" and not lead[:2].rstrip(".").isdigit() else ""
            sep = " " if lead.endswith((".", "?", ":", "!")) else (". " if kind == "term" else ": ")
            c.value = CellRichText([TextBlock(bold, f"{prefix}{lead}{sep}"),
                                    TextBlock(plain, rest)])
            c.alignment = Alignment(wrap_text=True, vertical="top", indent=1 if kind == "b" else 0)
            ws.row_dimensions[r].height = 15 * _rows_needed(lead + rest, PER_LINE - 4) + 5
        elif kind in ("ex", "note"):
            c.value = text
            c.font = F(italic=(kind == "ex"), size=11, color="5A4500")
            c.fill = NOTE
            c.alignment = Alignment(wrap_text=True, vertical="center", indent=1)
            ws.row_dimensions[r].height = 15 * _rows_needed(text, PER_LINE - 4) + 12
        elif kind == "p":
            c.value = text
            c.font = F(size=11, color="1F1F1F")
            c.alignment = Alignment(wrap_text=True, vertical="top")
            ws.row_dimensions[r].height = 15 * _rows_needed(text) + 5
        else:
            ws.row_dimensions[r].height = 8
        r += 1
    ws.sheet_properties.tabColor = "B7950B"
    ws.page_setup.orientation = "portrait"
    ws.page_setup.fitToWidth, ws.page_setup.fitToHeight = 1, 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.print_area = f"A1:C{r}"

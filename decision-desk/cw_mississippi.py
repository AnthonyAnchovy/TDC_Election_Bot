"""The Mississippi tab of the County Watch workbook: a bucket-by-bucket look at the Senate vote still out.

The bot's projection and NYT's both treat what's left as ordinary votes from counties they can see. Mississippi's late
votes aren't ordinary: affidavit ballots are counted days later, late mail can arrive for five business days, and in 2024
the votes added after election night leaned further toward the Democrat the later they came. So this tab sizes each kind of
late vote separately. Three yellow questions at the top set the affidavit guess; everything else is a fixed assumption the
sums show. The sums are live formulas, so every step can be checked. The bot fills the grey cells from NYT's file and runs
the simulation.

Net votes in a bucket = votes counted x (county mix's margin + 2 x lean x county mix's two-party share)
where lean is how much better Colom does in that bucket's two-party vote than in the same counties' count so far."""
import json
import logging
import random
import statistics
from datetime import datetime

import requests
from openpyxl.utils import get_column_letter as L

import cw_model as M
import night as N

log = logging.getLogger(__name__)

SHEET = "Mississippi"
NYT_URL = "https://static01.nyt.com/elections-assets/pages/data/2026-11-03/results-mississippi-us-senate.json"
D_ID, R_ID, O_ID = "colom-s", "hydesmith-c", "pinkins-t"
HINDS, COAST = {"28049"}, {"28047", "28059"}      # Hinds; Harrison and Jackson, where voter rolls were cut hardest
TAB_POSITION = 4
NUM_WIDTH = 112
DRAWS = 20_000

# ------------------------------------------------------------------ where everything sits
R_STATUS, R_ANSWER = 2, 3
R_BE = 5            # what Colom needs from the affidavits: heading, starting margin, header, six rows, note
BE_COUNTS = [2000, 4000, 6000, 8000, 10000, 15000]
Q_HOW, Q_SHARE, Q_LEAN = R_BE + 3, R_BE + 4, R_BE + 5    # the three yellow questions, in column K
R_LEFT = 17         # the table of what's left: four kinds of vote, then the total
R_CHARTS = 24       # first row of the charts
OFFSET = 80         # rows the charts and those tables take up
R_STAND = 6 + OFFSET    # Colom, Hyde-Smith, other, total, lead, two-party share, precincts out, NYT expected
R_MODELS = 17 + OFFSET  # bot, NYT, this tab's three cases
R_BUCKET = 28 + OFFSET  # four buckets, then total, then final margin
R_TOTAL, R_FINAL = R_BUCKET + 4, R_BUCKET + 5
R_MIX = 38 + OFFSET     # Hinds, Harrison + Jackson, everyone else
R_SIM = 43 + OFFSET
R_GRID = 53 + OFFSET
GRID_VOTES = [5_000, 10_000, 15_000, 20_000, 30_000, 40_000, 60_000, 80_000, 100_000, 150_000]
GRID_SHARE = [0.44, 0.46, 0.48, 0.49, 0.50, 0.51, 0.52, 0.54, 0.56, 0.60]
R_2024 = 66 + OFFSET
R_TYPES = 75 + OFFSET
R_BATCH = 86 + OFFSET
N_BATCH = 14
R_SOURCES = 102 + OFFSET
R_COUNTY = 117 + OFFSET     # first county row; the header is the row above
# chart data, off to the right of the county table
C_PATH, C_VOTES, C_TOP, C_SIM, C_NIGHT = 32, 37, 42, 46, 50
R_DATA = 15
N_TOP, N_NIGHT = 12, 40
SIM_BINS = list(range(-8000, 14000, 1000))

COLS = ["name", "type", "dist", "m24", "m22", "D", "R", "O", "T", "share", "marg", "swing", "turn", "t24", "pin", "ptot",
        "nexp", "nprov", "nadv", "nleft", "nnet", "bleft", "bnet", "pout", "other", "f2", "group", "rk", "rf", "midnet"]
HEADS = ["County", "Type", "U.S. House", "Margin 2024 (two-party)", "Margin 2022 (two-party)", "Colom", "Hyde-Smith", "Other", "Total",
         "Colom share of two-party", "Margin of all votes", "Swing since 2024 (two-party)", "Turnout vs 2024", "Votes 2024",
         "Precincts in", "Precincts total", "NYT expected votes", "NYT expected provisional", "NYT expected absentee",
         "NYT votes left", "NYT net for Colom", "Bot votes left", "Bot net for Colom", "Votes in missing precincts",
         "Other uncounted votes", "Two-party share", "Affidavit group", "(working)", "(working)", "Middle case: net for Colom"]
C = {k: L(i + 1) for i, k in enumerate(COLS)}

BUCKETS = ["Precincts still out", "Other uncounted votes", "Affidavit ballots", "Late mail ballots"]
# (size x 3, share counted x 3, lean x 3), each as Hyde-Smith's best case, the middle, Colom's best case
DEFAULTS = [
    (4.0, 1.5, 0.5, 1, 1, 1, -0.02, 0.0, 0.02),
    (0.5, 0.5, 1.5, 1, 1, 1, -0.06, 0.01, 0.04),
    (5783, 12000, 18000, 0.5, 0.7, 0.85, -0.02, 0.04, 0.09),
    (3000, 1200, 400, 1, 1, 1, -0.04, 0.0, 0.03),
]
MIX_DEFAULTS = [(0.045, 0.20, 0.35), (0.25, 0.12, 0.08)]

EVIDENCE_2024 = [
    ("Morning after election night", 1_062_132, 0.370, 153_269, 0.383),
    ("Day after, 7 PM ET", 1_088_664, 0.367, 126_737, 0.410),
    ("Six days after", 1_136_387, 0.366, 79_014, 0.453),
    ("The last load, a month later", 1_139_972, 0.366, 75_429, 0.461),
]
SOURCES = [
    "Live numbers: NYT's results file for this race, refreshed by the bot. NYT marks them unofficial.",
    "Affidavit count, 10,000 to 15,000 or more: the Colom campaign's own estimate, via Mississippi Today and The Dispatch (cdispatch.com). No official count exists yet. Hyde-Smith's campaign says its numbers show her winning.",
    "NYT expects only 5,783 provisional ballots statewide, and just 262 in Hinds.",
    "Hinds had about 12,000 affidavit ballots in 2024 (WLBT) and about 5,000 in 2016 (Mississippi Free Press). I found no statewide count or acceptance rate for Mississippi.",
    "Acceptance: 73.8% of provisional ballots were counted nationally in 2024 (MIT Election Lab, from EAC data). That's a national figure, used only as a guide.",
    "Rolls: 28,000 Harrison voters were moved to inactive this year, and Jackson's clerk saw more inactive voters than usual (WLOX). That could mean more affidavits on the Coast, which leans Hyde-Smith.",
    "Timing: affidavits usually take a few days to verify. Voters without ID have until Nov. 10 (WLOX). Counties must certify within 10 days of the election (WLBT).",
    "Late mail: ballots postmarked by Election Day count if they arrive within five business days. The Supreme Court upheld this on June 29, 2026 in Watson v. RNC (CBS News, Justia).",
    "Mail volume: 64,537 absentee voters and 56,049 ballots back as of Oct. 26 (Secretary of State's report, via DeSoto County News). In-person absentee is included, so few of the rest are likely mail ballots still on the way.",
    "No runoff: the winner only needs the most votes. Mississippi has no automatic recount (NBC News, Mississippi Today).",
    "My judgment, not data: every assumption in section 2, the three starting answers at the top, and the simulation's shape.",
    "Limits of the 2024 table: one election, a Republican blowout, and the last load may include more than affidavits. It shows direction more than size. The batches the day after that election ran well below the count, which is why Hyde-Smith's best case has a -6% lean on other uncounted votes.",
]


def _rng(k: str) -> str:
    n = len(M.load()["MS"])
    return f"${C[k]}${R_COUNTY}:${C[k]}${R_COUNTY + n - 1}"


def _order() -> list[dict]:
    return sorted(M.load()["MS"], key=lambda c: c["name"])


def _group(fips: str) -> str:
    return "Hinds" if fips in HINDS else "Coast" if fips in COAST else "Rest"


# ------------------------------------------------------------------ the sheet, as a grid of cells
def layout() -> dict:
    """{'cells': {(row, col): value or formula}, 'fmt': {(row, col): number format}, and styling lists}. Yellow inputs and
    grey bot cells are listed so a later update never touches the yellow ones."""
    cells, fmt = {}, {}
    fills, bold, wrap, inputs, bot, merge = [], [], [], [], [], []

    def put(r, c, v, f=None):
        cells[(r, c)] = v
        if f:
            fmt[(r, c)] = f

    def head(r, text, cols=12):
        put(r, 1, text)
        fills.append((f"A{r}:{L(cols)}{r}", "HEAD"))
        bold.append((f"A{r}:{L(cols)}{r}", "FFFFFF"))

    NET = '[Blue]"Colom +"#,##0;[Red]"Hyde-Smith +"#,##0;"even"'
    NUM, PCT1, PCT0, PTS = "#,##0", "0.0%", "0%", '+0.0%;-0.0%;0.0%'

    put(1, 1, "Mississippi Senate: what the votes still out could do")
    bold.append(("A1:A1", "000000"))
    bot.append(f"A{R_STATUS}:A{R_STATUS}")
    merge.append(f"A{R_ANSWER}:L{R_ANSWER}")
    wrap.append(f"A{R_ANSWER}:L{R_ANSWER}")
    bold.append((f"A{R_ANSWER}:L{R_ANSWER}", "000000"))
    lead, f_all = f"$B${R_STAND + 4}", f"$B${R_STAND + 5}"
    final = lambda col: f"{col}{R_FINAL}"
    who = lambda a: f'IF({a}>=0,"Colom","Hyde-Smith")&" by "&TEXT(ABS({a}),"#,##0")'
    total_row = R_LEFT + 4
    be_start = R_BE + 1
    be_row = lambda n: R_BE + 3 + BE_COUNTS.index(n)
    need = lambda n: f'IF(B{be_row(n)}>=0,"needs to win them by "&TEXT(B{be_row(n)}*100,"0.0")&" points","can lose them by up to "&TEXT(-B{be_row(n)}*100,"0.0")&" points")'
    put(R_ANSWER, 1, f'="Too close to call. Nobody outside the county offices knows how many affidavit ballots there are, how many will count, or how they\'ll break. '
                     f'Before them, we estimate "&{who(f"B{be_start}")}&", which also rests on assumptions. From there, Colom "&{need(8000)}&" if 8,000 count, and "&{need(4000)}&" if 4,000 do. '
                     f'Our guess at the affidavits, which is not data, would make it "&{who(final("P"))}&"."')

    # ---- what Colom needs from the affidavits
    head(R_BE, "What Colom needs from the affidavit ballots, whatever their number turns out to be", 12)
    put(be_start, 1, "Margin before affidavits (our estimate)")
    b_be = lambda n: R_BUCKET + n
    put(be_start, 2, f"={lead}+P{b_be(0)}+P{b_be(1)}+P{b_be(3)}", '[Blue]"Colom +"#,##0;[Red]"Hyde-Smith +"#,##0;"even"')
    put(be_start, 3, "Today\'s lead plus the middle case for missing precincts, other uncounted votes and late mail. Those are assumptions too, so this number can move.")
    merge.append(f"C{be_start}:L{be_start}")
    wrap.append(f"C{be_start}:L{be_start}")
    bold.append((f"A{be_start}:B{be_start}", "000000"))
    for j, h in enumerate(["If this many affidavits count", "Colom must win them by (points)", "So his share of their two-party vote", "Or, with no lean, Hinds\' share of them that would do it"]):
        put(R_BE + 2, j + 1, h)
    bold.append((f"A{R_BE + 2}:D{R_BE + 2}", "000000"))
    wrap.append(f"A{R_BE + 2}:D{R_BE + 2}")
    others = (f'((G{R_MIX + 1}*SUMIFS({_rng("nprov")},{_rng("group")},"Coast")+G{R_MIX + 2}*SUMIFS({_rng("nprov")},{_rng("group")},"Rest"))'
              f'/(SUMIFS({_rng("nprov")},{_rng("group")},"Coast")+SUMIFS({_rng("nprov")},{_rng("group")},"Rest")))')
    for n in BE_COUNTS:
        r = be_row(n)
        put(r, 1, n, NUM)
        put(r, 2, f"=-$B${be_start}/A{r}", '+0.0%;-0.0%;0.0%')
        put(r, 3, f'=IF(ABS(B{r})>=V{b_be(2)},"out of reach",0.5+B{r}/(2*V{b_be(2)}))', PCT1)
        put(r, 4, f'=IFERROR(IF((B{r}-{others})/(G{R_MIX}-{others})>1,"not enough",MAX(0,(B{r}-{others})/(G{R_MIX}-{others}))),"")', PCT0)
    put(R_BE + 2, 6, "Have better information? Type it in the yellow cells")
    bold.append((f"F{R_BE + 2}:K{R_BE + 2}", "000000"))
    merge.append(f"F{R_BE + 2}:K{R_BE + 2}")
    wrap.append(f"F{R_BE + 2}:K{R_BE + 2}")
    questions = [(Q_HOW, "How many affidavit ballots were cast?", DEFAULTS[2][1], NUM), (Q_SHARE, "What share of them gets accepted?", DEFAULTS[2][4], PCT0),
                 (Q_LEAN, "How many points better does Colom do in them than in the same counties' count?", DEFAULTS[2][7], "+0%;-0%;0%")]
    for r, text, value, f in questions:
        put(r, 6, text)
        put(r, 11, value, f)
        merge.append(f"F{r}:J{r}")
        inputs.append(f"K{r}:K{r}")
    put(Q_LEAN + 1, 6, "With those numbers, the race ends")
    put(Q_LEAN + 1, 11, f"={final('P')}", '[Blue]"Colom +"#,##0;[Red]"Hyde-Smith +"#,##0;"even"')
    put(Q_LEAN + 2, 6, "Colom must win the accepted ones by (points)")
    put(Q_LEAN + 2, 11, f"=IFERROR(-$B${be_start}/(K{Q_HOW}*K{Q_SHARE}),\"\")", '+0.0%;-0.0%;0.0%')
    put(Q_LEAN + 3, 6, f"My starting guesses, which aren't data: {DEFAULTS[2][1]:,} cast, {DEFAULTS[2][4]:.0%} accepted, {DEFAULTS[2][7]:+.0%} better for Colom.")
    merge += [f"F{Q_LEAN + 1}:J{Q_LEAN + 1}", f"F{Q_LEAN + 2}:J{Q_LEAN + 2}", f"F{Q_LEAN + 3}:K{Q_LEAN + 3}"]
    bold.append((f"F{Q_LEAN + 1}:K{Q_LEAN + 1}", "000000"))
    put(R_BE + 9, 1, "Minus means Colom can lose them by that much and still win. The last column assumes affidavits split like each county\'s count so far, with Harrison, Jackson and the rest in NYT\'s proportions.")
    merge.append(f"A{R_BE + 9}:L{R_BE + 9}")
    wrap.append(f"A{R_BE + 9}:L{R_BE + 9}")

    # ---- what's left, what we expect, why
    head(R_LEFT - 2, "What might be left, our guess at how it looks, and why. Only the count is known: everything else here is an assumption", 18)
    tops = ["Kind of vote", "Votes we assume count", "Fewest", "Most", "Colom", "Hyde-Smith", "Other", "Net for Colom", "Colom's share of two-party", "Where"]
    for j, h in enumerate(tops):
        put(R_LEFT - 1, j + 1, h)
    put(R_LEFT - 1, 13, "Why we assume this")
    bold.append((f"A{R_LEFT - 1}:R{R_LEFT - 1}", "000000"))
    wrap.append(f"A{R_LEFT - 1}:I{R_LEFT - 1}")
    merge += [f"J{R_LEFT - 1}:L{R_LEFT - 1}", f"M{R_LEFT - 1}:R{R_LEFT - 1}"]
    NETV = '[Blue]"Colom +"#,##0;[Red]"Hyde-Smith +"#,##0;"even"'
    b_ = lambda n: R_BUCKET + n
    pct = lambda a: f'TEXT({a},"0%")'
    lean = lambda a: f'TEXT({a},"+0%;-0%;0%")'
    whys = [
        f'="Only "&B{R_STAND + 7}&" precincts are out, in counties that lean Hyde-Smith. A normal precinct there holds about "&TEXT(B{b_(0)}/MAX(1,B{R_STAND + 7}),"#,##0")&" votes. We assume "&TEXT(D{b_(0)},"0.0")&" times that, because NYT expects Jackson\'s to be large."',
        f'="NYT expects about "&TEXT(B{b_(1)},"#,##0")&" more votes in counties beyond the count and provisionals. We count "&{pct(f"D{b_(1)}")}&", about what NYT\'s own model implies. In 2024, votes from the morning after onward ran 1.3 points better for the Democrat than the count, so the lean here is "&{lean(f"J{b_(1)}")}&"."',
        f'="No official count exists. As a guess, we assume "&TEXT(D{b_(2)},"#,##0")&" cast and "&{pct(f"G{b_(2)}")}&" accepted. NYT expects only "&TEXT(SUM({_rng("nprov")}),"#,##0")&" statewide, but Hinds alone had about 12,000 in 2024, and Colom\'s campaign claims 10,000 to 15,000 or more. Lean "&{lean(f"J{b_(2)}")}&": about half of what 2024\'s last votes showed. Counted over the next several days."',
        f'="Mail postmarked by Election Day counts if it arrives by Nov. 10. As of Oct. 26, about 8,500 absentee ballots weren\'t back. We assume "&TEXT(D{b_(3)},"#,##0")&" more arrive. I found no evidence on how they lean, so "&{lean(f"J{b_(3)}")}&"."',
    ]
    for i, name in enumerate(BUCKETS):
        r, b = R_LEFT + i, b_(i)
        put(r, 1, name + (" (a guess, not data)" if i == 2 else ""))
        put(r, 2, f"=M{b}", NUM)
        put(r, 3, f"=MIN(L{b}:N{b})", NUM)
        put(r, 4, f"=MAX(L{b}:N{b})", NUM)
        put(r, 5, f"=(M{b}*V{b}+P{b})/2", NUM)
        put(r, 6, f"=(M{b}*V{b}-P{b})/2", NUM)
        put(r, 7, f"=M{b}*(1-V{b})", NUM)
        put(r, 8, f"=E{r}-F{r}", NETV)
        put(r, 9, f"=IFERROR(E{r}/(E{r}+F{r}),\"\")", PCT1)
        put(r, 13, whys[i])
        merge += [f"J{r}:L{r}", f"M{r}:R{r}"]
        wrap += [f"J{r}:L{r}", f"M{r}:R{r}", f"A{r}:A{r}"]
    bot += [f"J{R_LEFT}:J{R_LEFT + 1}", f"J{R_LEFT + 3}:J{R_LEFT + 3}"]
    put(R_LEFT + 2, 10, f'="Unknown until counties report. We assume Hinds "&{pct(f"D{R_MIX}")}&", Harrison and Jackson "&{pct(f"D{R_MIX + 1}")}&", everyone else "&{pct(f"D{R_MIX + 2}")}&"."')
    put(total_row, 1, "All of it")
    for c, f in ((2, NUM), (5, NUM), (6, NUM), (7, NUM)):
        put(total_row, c, f"=SUM({L(c)}{R_LEFT}:{L(c)}{R_LEFT + 3})", f)
    put(total_row, 8, f"=E{total_row}-F{total_row}", NETV)
    put(total_row, 9, f"=IFERROR(E{total_row}/(E{total_row}+F{total_row}),\"\")", PCT1)
    bold.append((f"A{total_row}:I{total_row}", "000000"))
    put(total_row + 1, 1, f'="Why this differs from the other models: the bot and NYT treat what\'s left as ordinary votes. They give little room to affidavits, or to late votes leaning. Bot: "&IF(B{R_MODELS + 1}>=0,"Colom","Hyde-Smith")&" by "&TEXT(ABS(B{R_MODELS + 1}),"#,##0")&". NYT: "&IF(B{R_MODELS + 2}>=0,"Colom","Hyde-Smith")&" by "&TEXT(ABS(B{R_MODELS + 2}),"#,##0")&"."')
    merge.append(f"A{total_row + 1}:R{total_row + 1}")
    wrap.append(f"A{total_row + 1}:R{total_row + 1}")
    head(R_STAND - 3, "The detail behind the charts", 18)

    # ---- chart data, off to the right
    SIDE = '"Colom +"#,##0;"Hyde-Smith +"#,##0;"even"'
    put(R_DATA - 1, C_PATH, "Chart data (the charts read these)")
    for j, h in enumerate(["Step", "Hyde-Smith\'s best case", "Middle case", "Colom\'s best case"]):
        put(R_DATA, C_PATH + j, h)
    steps = ["\nNow", "\nAfter missing precincts", "\nAfter other uncounted", "\nAfter affidavits", "\nAfter late mail"]
    for i, s in enumerate(steps):
        r = R_DATA + 1 + i
        put(r, C_PATH, s)
        for k in range(3):
            col = L(C_PATH + 1 + k)
            put(r, C_PATH + 1 + k, f"={lead}" if i == 0 else f"={col}{r - 1}+{L(15 + k)}{R_BUCKET + i - 1}", SIDE)
    for j, h in enumerate(["Kind of vote", "Colom", "Hyde-Smith", "Other"]):
        put(R_DATA, C_VOTES + j, h)
    for i, name in enumerate(BUCKETS):
        r = R_DATA + 1 + i
        put(r, C_VOTES, name)
        for j, src in enumerate("EFG"):
            put(r, C_VOTES + 1 + j, f"={src}{R_LEFT + i}", NUM)
    for j, h in enumerate(["County", "Helps Colom", "Helps Hyde-Smith"]):
        put(R_DATA, C_TOP + j, h)
    for j, h in enumerate(["Final margin", "Hyde-Smith wins", "Colom wins"]):
        put(R_DATA, C_SIM + j, h)
    for i in range(len(SIM_BINS)):
        for j in (1, 2):
            fmt[(R_DATA + 1 + i, C_SIM + j)] = "0.0%"
    for j, h in enumerate(["Time (ET)", "Colom\'s lead"]):
        put(R_DATA, C_NIGHT + j, h)
    for i in range(N_NIGHT):
        fmt[(R_DATA + 1 + i, C_NIGHT + 1)] = SIDE
    for i in range(N_TOP):
        fmt[(R_DATA + 1 + i, C_TOP + 1)] = NUM
        fmt[(R_DATA + 1 + i, C_TOP + 2)] = NUM
    bot += [f"{L(C_TOP)}{R_DATA + 1}:{L(C_TOP + 2)}{R_DATA + N_TOP}", f"{L(C_SIM)}{R_DATA + 1}:{L(C_SIM + 2)}{R_DATA + len(SIM_BINS)}",
            f"{L(C_NIGHT)}{R_DATA + 1}:{L(C_NIGHT + 1)}{R_DATA + N_NIGHT}"]

    # ---- where it stands
    head(R_STAND - 1, "1. Where it stands")
    stand = [("Colom", f"=SUM({_rng('D')})", NUM), ("Hyde-Smith", f"=SUM({_rng('R')})", NUM), ("Other", f"=SUM({_rng('O')})", NUM),
             ("Total counted", f"=SUM({_rng('T')})", NUM), ("Lead", f"=B{R_STAND}-B{R_STAND + 1}", NET),
             ("Two-party share of the count", f"=1-B{R_STAND + 2}/B{R_STAND + 3}", PCT1),
             ("Colom's share of the two-party count", f"=B{R_STAND}/(B{R_STAND}+B{R_STAND + 1})", '0.00%'),
             ("Precincts out", f"=SUM({_rng('ptot')})-SUM({_rng('pin')})", NUM),
             ("NYT's expected total", f"=SUM({_rng('nexp')})", NUM)]
    for i, (a, b, f) in enumerate(stand):
        put(R_STAND + i, 1, a)
        put(R_STAND + i, 2, b, f)

    # ---- three answers
    head(R_MODELS - 1, "How the answers compare (final margin)")
    for j, h in enumerate(["", "Final margin", "Colom's chance", "Votes left", "What it assumes"]):
        put(R_MODELS, j + 1, h)
    bold.append((f"A{R_MODELS}:E{R_MODELS}", "000000"))
    notes = ["2022 turnout pattern x tonight's pace, and each county's 2024 margin plus its type's swing. Nothing special about late votes.",
             "NYT's own model. It expects few provisional ballots.",
             "My assumptions in section 2, Hyde-Smith's best case.",
             "My assumptions in section 2, middle.",
             "My assumptions in section 2, Colom's best case.",
             "20,000 draws across those assumptions."]
    rows = [("Bot's projection", None, None, None), ("NYT", None, None, None),
            ("This tab: Hyde-Smith's best", f"=O{R_FINAL}", None, f"=L{R_TOTAL}"),
            ("This tab: middle", f"=P{R_FINAL}", None, f"=M{R_TOTAL}"),
            ("This tab: Colom's best", f"=Q{R_FINAL}", None, f"=N{R_TOTAL}"),
            ("This tab: simulation", f"=B{R_SIM + 3}", f"=B{R_SIM + 1}", None)]
    for i, (a, b, c, d) in enumerate(rows):
        r = R_MODELS + 1 + i
        put(r, 1, a)
        put(r, 2, b, NET)
        put(r, 3, c, PCT0)
        put(r, 4, d, NUM)
        put(r, 5, notes[i])
    bot.append(f"B{R_MODELS + 1}:D{R_MODELS + 2}")

    # ---- buckets
    head(R_BUCKET - 3, "2. The math behind it. These are my assumptions. The three yellow questions at the top set the affidavit row", 23)
    groups = [(3, "Size"), (6, "Share that counts"), (9, "Colom's lean vs the same counties' count"), (12, "Votes that count"), (15, "Net for Colom"),
              (18, "Working: county mix's margin"), (21, "Working: county mix's two-party share")]
    for c, text in groups:
        put(R_BUCKET - 2, c, text)
    bold.append((f"A{R_BUCKET - 2}:W{R_BUCKET - 2}", "000000"))
    top = ["Kind of vote", "Base votes"] + ["Hyde-Smith's best", "Middle", "Colom's best"] * 7
    for j, h in enumerate(top):
        put(R_BUCKET - 1, j + 1, h)
    bold.append((f"A{R_BUCKET - 1}:W{R_BUCKET - 1}", "000000"))
    wrap.append(f"A{R_BUCKET - 1}:W{R_BUCKET - 1}")
    margin = lambda w: f"IFERROR(SUMPRODUCT({_rng(w)},{_rng('marg')})/SUM({_rng(w)}),0)"
    twoway = lambda w: f"IFERROR(SUMPRODUCT({_rng(w)},{_rng('f2')})/SUM({_rng(w)}),1)"
    for b, name in enumerate(BUCKETS):
        r = R_BUCKET + b
        put(r, 1, name)
        put(r, 2, {0: f"=SUM({_rng('pout')})", 1: f"=SUM({_rng('other')})"}.get(b, 1), NUM)
        sizes, shares, leans = DEFAULTS[b][:3], DEFAULTS[b][3:6], DEFAULTS[b][6:]
        for k in range(3):
            put(r, 3 + k, sizes[k], "0.00" if b < 2 else NUM)
            put(r, 6 + k, shares[k], PCT0)
            put(r, 9 + k, leans[k], PTS)
            size, share, lean = f"{L(3 + k)}{r}", f"{L(6 + k)}{r}", f"{L(9 + k)}{r}"
            mix, fac = f"{L(18 + k)}{r}", f"{L(21 + k)}{r}"
            put(r, 12 + k, f"=$B{r}*{size}*{share}", NUM)
            put(r, 15 + k, f"={L(12 + k)}{r}*({mix}+2*{lean}*{fac})", NET)
            if b == 2:
                sc = L(3 + k)
                put(r, 18 + k, f"={sc}{R_MIX}*$G{R_MIX}+{sc}{R_MIX + 1}*$G{R_MIX + 1}+{sc}{R_MIX + 2}*$G{R_MIX + 2}", PTS)
                put(r, 21 + k, f"={sc}{R_MIX}*$H{R_MIX}+{sc}{R_MIX + 1}*$H{R_MIX + 1}+{sc}{R_MIX + 2}*$H{R_MIX + 2}", PCT1)
            else:
                w = {0: "pout", 1: "other", 3: "nadv"}[b]
                put(r, 18 + k, f"={margin(w)}", PTS)
                put(r, 21 + k, f"={twoway(w)}", PCT1)
    # the affidavit row follows the three questions, and its best cases always stay at least that far out
    r3 = R_BUCKET + 2
    put(r3, 3, f"=MIN($K${Q_HOW},{DEFAULTS[2][0]})", NUM)
    put(r3, 4, f"=$K${Q_HOW}", NUM)
    put(r3, 5, f"=MAX($K${Q_HOW},{DEFAULTS[2][2]})", NUM)
    put(r3, 6, f"=MIN($K${Q_SHARE},{DEFAULTS[2][3]})", PCT0)
    put(r3, 7, f"=$K${Q_SHARE}", PCT0)
    put(r3, 8, f"=MAX($K${Q_SHARE},{DEFAULTS[2][5]})", PCT0)
    put(r3, 9, f"=MIN($K${Q_LEAN},{DEFAULTS[2][6]})", PTS)
    put(r3, 10, f"=$K${Q_LEAN}", PTS)
    put(r3, 11, f"=MAX($K${Q_LEAN},{DEFAULTS[2][8]})", PTS)
    put(R_TOTAL, 1, "All four")
    put(R_FINAL, 1, "Final margin (lead + net)")
    bold.append((f"A{R_TOTAL}:Q{R_FINAL}", "000000"))
    for k in range(3):
        put(R_TOTAL, 12 + k, f"=SUM({L(12 + k)}{R_BUCKET}:{L(12 + k)}{R_BUCKET + 3})", NUM)
        put(R_TOTAL, 15 + k, f"=SUM({L(15 + k)}{R_BUCKET}:{L(15 + k)}{R_BUCKET + 3})", NET)
        put(R_FINAL, 15 + k, f"={lead}+{L(15 + k)}{R_TOTAL}", NET)
    put(R_FINAL + 1, 1, "Size: for the first two rows, a multiple of the base votes; for the last two, ballots cast. Lean: +4% means Colom's two-party share in that kind of vote runs 4 points above the same counties' count so far.")

    # ---- affidavit mix
    head(R_MIX - 2, "Where the affidavit ballots are")
    for j, h in enumerate(["", "NYT's share", "Hyde-Smith's best", "Middle", "Colom's best", "", "Margin of its count", "Two-party share"]):
        put(R_MIX - 1, j + 1, h)
    bold.append((f"A{R_MIX - 1}:H{R_MIX - 1}", "000000"))
    wrap.append(f"A{R_MIX - 1}:H{R_MIX - 1}")
    groups_ = [("Hinds", "Hinds"), ("Harrison and Jackson", "Coast"), ("Everyone else", "Rest")]
    for i, (name, g) in enumerate(groups_):
        r = R_MIX + i
        put(r, 1, name)
        put(r, 2, f'=IFERROR(SUMIFS({_rng("nprov")},{_rng("group")},"{g}")/SUM({_rng("nprov")}),0)', PCT1)
        if i < 2:
            for k in range(3):
                put(r, 3 + k, MIX_DEFAULTS[i][k], PCT1)
        else:
            for k in range(3):
                sc = L(3 + k)
                put(r, 3 + k, f"=1-{sc}{R_MIX}-{sc}{R_MIX + 1}", PCT1)
        put(r, 7, f'=IFERROR(SUMIFS({_rng("rk")},{_rng("group")},"{g}")/SUMIFS({_rng("nprov")},{_rng("group")},"{g}"),0)', PTS)
        put(r, 8, f'=IFERROR(SUMIFS({_rng("rf")},{_rng("group")},"{g}")/SUMIFS({_rng("nprov")},{_rng("group")},"{g}"),1)', PCT1)

    # ---- simulation
    head(R_SIM - 1, "3. Simulation: 20,000 draws across those assumptions (the bot reruns this each update)")
    sim = [("Colom wins", PCT0), ("Hyde-Smith wins", PCT0), ("Median final margin", NET), ("10th percentile", NET), ("90th percentile", NET), ("Margin under 1,000 either way", PCT0)]
    for i, (a, f) in enumerate(sim):
        put(R_SIM + 1 + i, 1, a)
        put(R_SIM + 1 + i, 2, None, f)
    put(R_SIM, 1, "Each draw picks every assumption from a triangle: the two best cases as the ends, the middle as the peak. Most values move together, because late votes that favor Colom in one bucket probably do in the rest. It's only as good as those assumptions: treat it as a way to sum them up, not as odds from data.")
    bot.append(f"B{R_SIM + 1}:B{R_SIM + 6}")

    # ---- grid
    head(R_GRID - 2, "4. Any late vote, any lean: final margin if this many more votes count and Colom gets this share of their two-party vote", 12)
    put(R_GRID - 1, 1, "More votes that count")
    for j, s in enumerate(GRID_SHARE):
        put(R_GRID - 1, 2 + j, s, PCT0)
    put(R_GRID - 1, 12, "Colom needs")
    bold.append((f"A{R_GRID - 1}:L{R_GRID - 1}", "000000"))
    for i, v in enumerate(GRID_VOTES):
        r = R_GRID + i
        put(r, 1, v, NUM)
        for j in range(len(GRID_SHARE)):
            put(r, 2 + j, f"={lead}+$A{r}*{f_all}*(2*{L(2 + j)}${R_GRID - 1}-1)", NET)
        put(r, 12, f"=0.5-{lead}/(2*$A{r}*{f_all})", PCT1)

    # ---- 2024
    head(R_2024 - 2, "5. How late votes broke in Mississippi's 2024 Senate race (Pinkins, D, v Wicker, R). NYT's update history")
    for j, h in enumerate(["From this point", "Votes counted", "D share then (two-party)", "Votes added after", "Added vs the count", "D share of those (two-party)", "Lean"]):
        put(R_2024 - 1, j + 1, h)
    bold.append((f"A{R_2024 - 1}:G{R_2024 - 1}", "000000"))
    wrap.append(f"A{R_2024 - 1}:G{R_2024 - 1}")
    for i, (a, n, s0, add, s1) in enumerate(EVIDENCE_2024):
        r = R_2024 + i
        put(r, 1, a)
        put(r, 2, n, NUM)
        put(r, 3, s0, PCT1)
        put(r, 4, add, NUM)
        put(r, 5, f"=D{r}/B{r}", PCT1)
        put(r, 6, s1, PCT1)
        put(r, 7, f"=F{r}-C{r}", PTS)
    put(R_2024 + 4, 1, "If tonight's count grew by the same 14.4%, that would be about this many more votes:")
    put(R_2024 + 4, 5, f"=B{R_STAND + 3}*E{R_2024}", NUM)
    put(R_2024 + 5, 1, "NYT's expected total also ran short then: at 99% of expected it had 1,088,664, and the final was 1,215,401.")

    # ---- the bot's math
    head(R_TYPES - 2, "6. Inside the bot's projection: votes left = 2022 turnout pattern x pace, minus the count. Their margin = county's 2024 margin + its type's swing")
    for j, h in enumerate(["County type", "Finished counties", "Votes counted", "Swing used", "Turnout pace", "Votes left", "Net for Colom"]):
        put(R_TYPES - 1, j + 1, h)
    bold.append((f"A{R_TYPES - 1}:G{R_TYPES - 1}", "000000"))
    wrap.append(f"A{R_TYPES - 1}:G{R_TYPES - 1}")
    for i in range(len(M.TYPES)):
        r = R_TYPES + i
        for c, f in ((3, NUM), (4, PTS), (5, "0.00"), (6, NUM), (7, NET)):
            fmt[(r, c)] = f
    bot.append(f"A{R_TYPES}:G{R_TYPES + len(M.TYPES) - 1}")

    # ---- batches
    head(R_BATCH - 2, "7. The last updates in NYT's file, newest first")
    for j, h in enumerate(["Time (ET)", "Votes added", "Colom", "Hyde-Smith", "Colom share (two-party)", "Lead after"]):
        put(R_BATCH - 1, j + 1, h)
    bold.append((f"A{R_BATCH - 1}:F{R_BATCH - 1}", "000000"))
    wrap.append(f"A{R_BATCH - 1}:F{R_BATCH - 1}")
    for i in range(N_BATCH):
        r = R_BATCH + i
        for c, f in ((2, "+#,##0;-#,##0;0"), (3, "+#,##0;-#,##0;0"), (4, "+#,##0;-#,##0;0"), (5, PCT1), (6, NET)):
            fmt[(r, c)] = f
    bot.append(f"A{R_BATCH}:F{R_BATCH + N_BATCH - 1}")

    # ---- sources
    head(R_SOURCES - 1, "8. Where the numbers come from, and how far to trust them")
    for i, s in enumerate(SOURCES):
        put(R_SOURCES + i, 1, s)

    # ---- counties
    head(R_COUNTY - 2, "9. Every county", len(COLS))
    for j, h in enumerate(HEADS):
        put(R_COUNTY - 1, j + 1, h)
    bold.append((f"A{R_COUNTY - 1}:{L(len(COLS))}{R_COUNTY - 1}", "000000"))
    wrap.append(f"A{R_COUNTY - 1}:{L(len(COLS))}{R_COUNTY - 1}")
    formats = {"m24": PTS, "m22": PTS, "D": NUM, "R": NUM, "O": NUM, "T": NUM, "share": PCT1, "marg": PTS, "swing": PTS, "turn": PCT0, "t24": NUM,
               "nexp": NUM, "nprov": NUM, "nadv": NUM, "nleft": NUM, "nnet": NET, "bleft": NUM, "bnet": NET, "pout": NUM, "other": NUM,
               "f2": PCT1, "midnet": NET, "rk": "0.0", "rf": "0.0"}
    import countywatch_run as CWR
    districts = CWR._county_districts()
    for i, c in enumerate(_order()):
        r = R_COUNTY + i
        x = {k: f"{C[k]}{r}" for k in COLS}
        row = {"name": c["name"], "type": c["type"], "dist": ", ".join(districts.get(c["fips"], [])), "m24": c["m24"], "m22": c["m22"], "t24": c["t24"],
               "group": _group(c["fips"]),
               "T": f"={x['D']}+{x['R']}+{x['O']}",
               "share": f'=IF({x["D"]}+{x["R"]}>0,{x["D"]}/({x["D"]}+{x["R"]}),"")',
               "marg": f'=IF({x["T"]}>0,({x["D"]}-{x["R"]})/{x["T"]},{x["m24"]})',
               "swing": f'=IF({x["D"]}+{x["R"]}>0,({x["D"]}-{x["R"]})/({x["D"]}+{x["R"]})-{x["m24"]},"")',
               "turn": f'=IF({x["t24"]}>0,{x["T"]}/{x["t24"]},"")',
               "pout": f'=IF({x["pin"]}>0,{x["T"]}/{x["pin"]}*({x["ptot"]}-{x["pin"]}),{x["nexp"]})',
               "other": f'=MAX(0,{x["nexp"]}-{x["T"]}-{x["nprov"]}-{x["pout"]})',
               "f2": f'=IF({x["T"]}>0,1-{x["O"]}/{x["T"]},1)',
               "rk": f'={x["nprov"]}*{x["marg"]}', "rf": f'={x["nprov"]}*{x["f2"]}'}
        b = lambda n: R_BUCKET + n
        each = lambda votes, n: f'({votes})*({x["marg"]}+2*$J${b(n)}*{x["f2"]})'
        aff = (f'IF({x["group"]}="Hinds",$D${R_MIX},IF({x["group"]}="Coast",$D${R_MIX + 1}*{x["nprov"]}/SUMIFS({_rng("nprov")},{_rng("group")},"Coast"),'
               f'$D${R_MIX + 2}*{x["nprov"]}/SUMIFS({_rng("nprov")},{_rng("group")},"Rest")))')
        row["midnet"] = ("=" + "+".join([each(f'{x["pout"]}*$D${b(0)}*$G${b(0)}', 0), each(f'{x["other"]}*$D${b(1)}*$G${b(1)}', 1),
                                        each(f'$D${b(2)}*$G${b(2)}*{aff}', 2),
                                        each(f'$D${b(3)}*$G${b(3)}*{x["nadv"]}/SUM({_rng("nadv")})', 3)]))
        for j, k in enumerate(COLS):
            if k in row:
                put(r, j + 1, row[k], formats.get(k))
            elif k in formats:
                fmt[(r, j + 1)] = formats[k]
    n = len(_order())
    last = R_COUNTY + n - 1
    bot_cols = [("D", "O"), ("pin", "ptot"), ("nexp", "bnet")]
    for a, z in bot_cols:
        bot.append(f"{C[a]}{R_COUNTY}:{C[z]}{last}")
    put(last + 1, 1, "Total")
    bold.append((f"A{last + 1}:{L(len(COLS))}{last + 1}", "000000"))
    for k in ("D", "R", "O", "T", "t24", "pin", "ptot", "nexp", "nprov", "nadv", "nleft", "nnet", "bleft", "bnet", "pout", "other", "midnet"):
        j = COLS.index(k) + 1
        put(last + 1, j, f"=SUM({_rng(k)})", formats.get(k, NUM))
    fills += [(a, "EDEDED") for a in bot if not a.startswith("A2")] + [(a, "FFF2CC") for a in inputs]
    widths = {"A": 200, **{L(i): NUM_WIDTH for i in range(2, len(COLS) + 1)}}      # wide enough for "Hyde-Smith +17,592"
    # long notes get their own wrapped block across the page; header rows get room for their wrapped text
    tall = {R_ANSWER: 66, R_BE + 2: 62, R_BE + 1: 20, R_BE + 9: 20, R_LEFT - 1: 46, **{R_LEFT + i: 80 for i in range(4)}, R_LEFT + 5: 32, R_BUCKET - 1: 32, R_MIX - 1: 32, R_2024 - 1: 45, R_TYPES - 1: 32, R_BATCH - 1: 32, R_COUNTY - 1: 62}
    for r, h in [(R_FINAL + 1, 32), (R_SIM, 46), (R_2024 + 4, 18), (R_2024 + 5, 18)] + [(R_SOURCES + i, 32 if len(s) > 150 else 18) for i, s in enumerate(SOURCES)]:
        tall[r] = h
        if r != R_2024 + 4:     # that row has a number in column E
            merge.append(f"A{r}:L{r}")
            wrap.append(f"A{r}:L{r}")
    return {"cells": cells, "fmt": fmt, "fills": fills, "bold": bold, "wrap": wrap, "inputs": inputs, "bot": bot, "merge": merge, "tall": tall,
            "widths": widths, "rows": last + 1, "cols": len(COLS), "hidden": [C["rk"], C["rf"]]}


# ------------------------------------------------------------------ the numbers the bot fills in
def fetch_nyt() -> dict | None:
    """County numbers, batches and NYT's model from its file. None if it can't be read, so old numbers stay."""
    try:
        r = requests.get(NYT_URL, timeout=30, headers={"User-Agent": "DecisionDeskDiscordBot/1.0 (friend-group election night bot)"})
        r.raise_for_status()
        return parse_nyt(r.json())
    except Exception as exc:
        log.warning("Mississippi tab: couldn't read NYT's file: %s", exc)
        return None


def parse_nyt(data: dict) -> dict:
    race = next(x for x in data["races"] if (x.get("office") or "") == "U.S. Senate")
    top = race["reporting_units"][0]
    est = {c["nyt_id"]: (c.get("nyt_model_estimates") or {}) for c in top["candidates"]}
    counties = {}
    for u in race["reporting_units"]:
        if u.get("level") != "county":
            continue
        mod = {c["nyt_id"]: (c.get("nyt_model_estimates") or {}) for c in u["candidates"]}
        left = {k: mod.get(k, {}).get("votes_remaining") or 0 for k in (D_ID, R_ID, O_ID)}
        kinds = u.get("expected_vote_by_vote_type") or {}
        counties[f"{u['fips_state']}{u['fips_county']}"] = {
            "pin": u.get("precincts_reporting") or 0, "ptot": u.get("precincts_total") or 0, "exp": u.get("total_expected_vote") or 0,
            "prov": kinds.get("provisional") or 0, "adv": kinds.get("advance") or 0, "left": sum(left.values()), "net": left[D_ID] - left[R_ID], "d": left[D_ID], "r": left[R_ID]}
    rows = []
    for t in (data.get("timeseries") or {}).get(race["nyt_id"]) or []:
        v = {c["nyt_id"]: (c.get("votes") or {}).get("total") or 0 for c in t["candidates"]}
        rows.append((t.get("greatest_updated_at") or "", t.get("total_votes") or 0, v.get(D_ID, 0), v.get(R_ID, 0)))
    rows.sort()
    rows = [x for i, x in enumerate(rows) if x[1] and (i == 0 or x[1] != rows[i - 1][1])]
    batches = []
    for a, b in zip(rows, rows[1:]):
        batches.append((b[0], b[1] - a[1], b[2] - a[2], b[3] - a[3], b[2] - b[3]))
    night = [(_clock(x[0]).split(" ", 2)[-1], x[2] - x[3]) for x in rows if rows and x[1] >= 0.25 * rows[-1][1]]
    return {"counties": counties, "batches": batches[-N_BATCH:][::-1], "night": night, "updated": race.get("updated_at"), "call": race.get("race_call_status"),
            "model_at": (top.get("nyt_model_estimates") or {}).get("nyt_updated_at"),     # NYT's model can stop updating while the counts go on
            "win": est.get(D_ID, {}).get("win_probability"), "final": (est.get(D_ID, {}).get("votes_estimated") or 0) - (est.get(R_ID, {}).get("votes_estimated") or 0),
            "left": sum(e.get("votes_remaining") or 0 for e in est.values()), "net": (est.get(D_ID, {}).get("votes_remaining") or 0) - (est.get(R_ID, {}).get("votes_remaining") or 0)}


def county_table(live: dict, nyt: dict | None, proj: dict) -> list[dict]:
    """What the sums use, one row per county, in the sheet's order."""
    rows, by = [], {r["fips"]: r for r in proj["rows"]}
    for c in _order():
        lv, ny, b = live.get(c["fips"]) or {}, ((nyt or {}).get("counties") or {}).get(c["fips"]) or {}, by[c["fips"]]
        d, r, o = lv.get("D") or 0, lv.get("R") or 0, lv.get("O") or 0
        t = d + r + o
        pin, ptot, exp, prov, adv = (ny.get(k, 0) for k in ("pin", "ptot", "exp", "prov", "adv"))
        pout = t / pin * (ptot - pin) if pin else exp
        rows.append({"fips": c["fips"], "D": d, "R": r, "O": o, "T": t, "pin": pin, "ptot": ptot, "exp": exp, "prov": prov, "adv": adv,
                     "nleft": ny.get("left", 0), "nnet": ny.get("net", 0), "bleft": max(0.0, b["proj_D"] + b["proj_R"] - b["D"] - b["R"]),
                     "bnet": (b["proj_D"] - b["proj_R"]) - (b["D"] - b["R"]), "pout": pout, "other": max(0, exp - t - prov - pout),
                     "marg": (d - r) / t if t else c["m24"], "f2": 1 - o / t if t else 1.0, "group": _group(c["fips"])})
    return rows


def buckets_math(rows: list[dict]) -> dict:
    """The county mix behind each bucket, as the sheet's working columns work it out."""
    def mix(key, keep=lambda x: True):
        w = sum(x[key] for x in rows if keep(x))
        if not w:
            return 0.0, 1.0
        return sum(x[key] * x["marg"] for x in rows if keep(x)) / w, sum(x[key] * x["f2"] for x in rows if keep(x)) / w
    out = {"lead": sum(x["D"] - x["R"] for x in rows), "base": [sum(x["pout"] for x in rows), sum(x["other"] for x in rows), 1, 1],
           "mix": [mix("pout"), mix("other"), None, mix("adv")]}
    out["aff"] = {g: mix("prov", lambda x, g=g: x["group"] == g) for g in ("Hinds", "Coast", "Rest")}
    return out


def case_net(b: dict, inputs: list[list[float]], mix_inputs: list[list[float]], k: int) -> tuple[float, float]:
    """(votes that count, net for Colom) in case k: 0 Hyde-Smith's best, 1 middle, 2 Colom's best. The same sum as the sheet."""
    votes = net = 0.0
    for i, row in enumerate(inputs):
        size, share, lean = row[k], row[3 + k], row[6 + k]
        m, f = _bucket_mix(b, i, mix_inputs[0][k], mix_inputs[1][k])
        v = b["base"][i] * size * share
        votes += v
        net += v * (m + 2 * lean * f)
    return votes, net


def _bucket_mix(b: dict, i: int, h: float, g: float) -> tuple[float, float]:
    if i != 2:
        return b["mix"][i]
    a = b["aff"]
    rest = max(0.0, 1 - h - g)
    return (h * a["Hinds"][0] + g * a["Coast"][0] + rest * a["Rest"][0], h * a["Hinds"][1] + g * a["Coast"][1] + rest * a["Rest"][1])


SHARED = 0.6     # how often a value in a draw follows that draw's shared "how the late vote goes" rather than its own


def simulate(b: dict, inputs: list[list[float]], mix_inputs: list[list[float]], draws: int = DRAWS, seed: int = 20261103) -> dict:
    """Draws every assumption from a triangle (two best cases as the ends, middle as the peak). Treating them as
    independent would cancel the highs against the lows and sound far too sure, so each draw has one shared "how the late
    vote goes" that most values follow, from Hyde-Smith's best case toward Colom's. The seed is fixed so the answer only
    moves when the numbers or the cells do."""
    rng = random.Random(seed)

    def tri(row, common):
        lo, mid, hi = min(row), row[1], max(row)
        if lo == hi:
            return lo
        mid = max(lo, min(hi, mid))
        u = common if rng.random() < SHARED else rng.random()
        if row[0] > row[2]:         # Colom's best case is the smaller number here (a bigger bucket that helps Hyde-Smith)
            u = 1 - u
        cut = (mid - lo) / (hi - lo)
        return lo + ((u * (hi - lo) * (mid - lo)) ** 0.5 if u < cut else (hi - lo) - ((1 - u) * (hi - lo) * (hi - mid)) ** 0.5)
    finals = []
    for _ in range(draws):
        common = rng.random()
        h, g = tri(mix_inputs[0], common), tri(mix_inputs[1], common)
        total = b["lead"]
        for i, row in enumerate(inputs):
            size, share = tri(row[:3], common), tri(row[3:6], common)
            lean = tri(row[6:9], common)
            m, f = _bucket_mix(b, i, h, g)
            total += b["base"][i] * size * share * (m + 2 * lean * f)
        finals.append(total)
    finals.sort()
    hist = [0] * len(SIM_BINS)
    for x in finals:
        i = max(0, min(len(SIM_BINS) - 1, int((x - SIM_BINS[0]) // 1000)))
        hist[i] += 1
    return {"hist": [h / draws for h in hist], "colom": sum(1 for x in finals if x > 0) / draws, "hs": sum(1 for x in finals if x < 0) / draws, "median": statistics.median(finals),
            "p10": finals[draws // 10], "p90": finals[draws * 9 // 10], "close": sum(1 for x in finals if abs(x) < 1000) / draws}


def county_middle(rows: list[dict], inputs: list[list[float]], mix: list[list[float]]) -> dict:
    """{county: middle-case net for Colom}, the same sum as the county table's last column."""
    prov = {g: sum(x["prov"] for x in rows if x["group"] == g) for g in ("Hinds", "Coast", "Rest")}
    adv = sum(x["adv"] for x in rows)
    h, g = mix[0][1], mix[1][1]
    share = {"Hinds": h, "Coast": g, "Rest": max(0.0, 1 - h - g)}
    out = {}
    for x, c in zip(rows, _order()):
        per = lambda lean: x["marg"] + 2 * lean * x["f2"]
        votes = [x["pout"] * inputs[0][1] * inputs[0][4], x["other"] * inputs[1][1] * inputs[1][4],
                 inputs[2][1] * inputs[2][4] * share[x["group"]] * (x["prov"] / prov[x["group"]] if prov[x["group"]] else 0),
                 inputs[3][1] * inputs[3][4] * (x["adv"] / adv if adv else 0)]
        out[c["name"]] = sum(v * per(inputs[i][7]) for i, v in enumerate(votes))
    return out


def chart_rows(rows, inputs, mix, sim, nyt) -> dict:
    """The grey cells the charts read: biggest counties, the simulation's spread, and the lead through the night."""
    top = sorted(county_middle(rows, inputs, mix).items(), key=lambda kv: -abs(kv[1]))[:N_TOP][::-1]    # a bar chart draws its first row at the bottom
    counties = [[n, round(v) if v > 0 else 0, round(-v) if v < 0 else 0] for n, v in top]
    label = lambda v: f"{'Colom' if v >= 0 else 'H-S'} {abs(v if v >= 0 else v + 1000) // 1000}-{abs(v if v >= 0 else v + 1000) // 1000 + 1}k"
    spread = [[label(b), sim["hist"][i] if b < 0 else 0, sim["hist"][i] if b >= 0 else 0] for i, b in enumerate(SIM_BINS)]
    night = (nyt or {}).get("night") or []
    if len(night) > N_NIGHT:
        step = (len(night) - 1) / (N_NIGHT - 1)
        night = [night[round(i * step)] for i in range(N_NIGHT)]
    night = [["'" + a, b] for a, b in night] + [["", ""]] * (N_NIGHT - len(night))
    return {"top": counties, "sim": spread, "night": night}


def _where(rows: list[dict]) -> list[str]:
    named = lambda key, n=5: "; ".join(f"{c['name']} {x[key]:,.0f}" for x, c in sorted(zip(rows, _order()), key=lambda p: -p[0][key])[:n] if x[key] >= 1)
    out = [f"{c['name']}: {x['ptot'] - x['pin']} of {x['ptot']} precincts" for x, c in zip(rows, _order()) if x["ptot"] > x["pin"]]
    return ["; ".join(out) or "None: every precinct is in", f"Most of it: {named('other') or 'nowhere yet'}", f"Follows absentee voting. Most in {named('adv', 3)}"]


# ------------------------------------------------------------------ the shared workbook
def _sheet_url(CC) -> str:
    return f"{CC.item()['base']}/workbook/worksheets/{SHEET}"


def sheet_exists(CC) -> bool:
    names = CC._call("GET", f"{CC.item()['base']}/workbook/worksheets?$select=name").json()["value"]
    return any(w["name"] == SHEET for w in names)


def setup(CC):
    """Adds the tab. Only ever adds it: if the tab is already there, it and its yellow cells are left alone."""
    if sheet_exists(CC):
        return False
    import cw_workbook as W
    base = CC.item()["base"]
    CC._call("POST", f"{base}/workbook/worksheets/add", json={"name": SHEET})
    CC._call("PATCH", _sheet_url(CC), json={"position": TAB_POSITION})
    lay = layout()
    rows, cols = lay["rows"], lay["cols"]
    def block(r0, r1, c0, c1):
        forms = [[lay["cells"].get((r, c)) if lay["cells"].get((r, c)) is not None else "" for c in range(c0, c1 + 1)] for r in range(r0, r1 + 1)]
        fmts = [[lay["fmt"].get((r, c), "General") for c in range(c0, c1 + 1)] for r in range(r0, r1 + 1)]
        address = f"{L(c0)}{r0}:{L(c1)}{r1}"
        CC.write(SHEET, address, numberFormat=fmts)
        CC.write(SHEET, address, formulas=forms)
    block(1, rows, 1, cols)
    block(R_DATA - 1, R_DATA + N_NIGHT, C_PATH, C_NIGHT + 1)
    head = W.HEAD.fgColor.rgb[-6:]
    area = lambda a: CC._range(SHEET, a)
    for a, color in lay["fills"]:
        CC._call("PATCH", area(a) + "/format/fill", json={"color": head if color == "HEAD" else color})
    for a, color in lay["bold"]:
        CC._call("PATCH", area(a) + "/format/font", json={"bold": True, "color": color})
    polish(CC, lay)
    for col, width in lay["widths"].items():
        CC._call("PATCH", area(f"{col}:{col}") + "/format", json={"columnWidth": width})
    for col in lay["hidden"]:
        CC._call("PATCH", area(f"{col}:{col}"), json={"columnHidden": True})
    for a in lay["inputs"] + lay["bot"]:
        CC._call("PATCH", area(a) + "/format/protection", json={"locked": False})
    return True


BLUE, RED, INK, GREYBAR = "#2F6DB5", "#C8352B", "#1F2A44", "#A6A6A6"
CHARTS = [
    ("path", "Line", "Where the lead could go as each kind of vote comes in (assumptions, above zero is Colom)", (C_PATH, 3, 5), "A24", "E43", [("line", RED), ("line", INK), ("line", BLUE)]),
    ("votes", "BarStacked", "What might be left, and how it might split (middle case; affidavits are a guess)", (C_VOTES, 3, 4), "F24", "K43", [("fill", BLUE), ("fill", RED), ("fill", GREYBAR)]),
    ("top", "BarStacked", "Which counties move it most, and who they help (middle case, net votes)", (C_TOP, 2, N_TOP), "A45", "E64", [("fill", BLUE), ("fill", RED)]),
    ("sim", "ColumnStacked", "How the race could end: share of 20,000 simulated finishes (judgment-based)", (C_SIM, 2, len(SIM_BINS)), "F45", "K64", [("fill", RED), ("fill", BLUE)]),
    ("night", "Line", "The lead through the night (above zero is Colom)", (C_NIGHT, 1, N_NIGHT), "A66", "K81", [("line", INK)]),
]


def add_charts(CC) -> list[str]:
    """Adds the charts. Styling is best effort: a chart that keeps Excel's default colors is still right. Returns what failed."""
    problems = []
    base = f"{_sheet_url(CC)}/charts"
    have = {c["name"] for c in CC._call("GET", f"{base}?$select=name").json()["value"]}
    for name, kind, title, (col, extra, n), start, end, series in CHARTS:
        if name in have:
            continue
        source = f"{L(col)}{R_DATA}:{L(col + extra)}{R_DATA + n}"
        try:
            made = CC._call("POST", f"{base}/add", json={"type": kind, "sourceData": source, "seriesBy": "Columns"}).json()
            chart = f"{base}('{made['id']}')"
            CC._call("PATCH", chart, json={"name": name})
            chart = f"{base}('{name}')"
            CC._call("POST", f"{chart}/setPosition", json={"startCell": start, "endCell": end})
            CC._call("PATCH", f"{chart}/title", json={"text": title, "visible": True})
            CC._call("PATCH", f"{chart}/legend", json={"visible": len(series) > 1, "position": "Bottom"})
            for i, (how, color) in enumerate(series):
                item = f"{chart}/series/itemAt(index={i})"
                if how == "line":
                    CC._call("PATCH", f"{item}/format/line", json={"color": color, "weight": 2.5})
                else:
                    CC._call("POST", f"{item}/format/fill/setSolidColor", json={"color": color})
                    CC._call("PATCH", item, json={"gapWidth": 40})
        except Exception as exc:
            problems.append(f"{name}: {str(exc)[:120]}")
    return problems


def polish(CC, lay: dict | None = None):
    """Merges the long notes, wraps text and sets row heights. Safe to run again."""
    lay = lay or layout()
    area = lambda a: CC._range(SHEET, a)
    for a in lay["merge"]:
        CC._call("POST", area(a) + "/merge", json={"across": False})
    for a in lay["wrap"]:
        CC._call("PATCH", area(a) + "/format", json={"wrapText": True, "verticalAlignment": "Top"})
    for r, h in lay["tall"].items():
        CC._call("PATCH", area(f"A{r}:A{r}") + "/format", json={"rowHeight": h})


def protect(CC):
    """The same guard as the other tabs: yellow and grey cells stay open, the formulas are locked."""
    options = CC._call("GET", f"{CC.item()['base']}/workbook/worksheets/Dashboard/protection").json().get("options")
    CC._call("POST", _sheet_url(CC) + "/protection/protect", json={"options": options})


def read_inputs(CC) -> tuple[list[list[float]], list[list[float]]]:
    inputs = CC.read(SHEET, f"C{R_BUCKET}:K{R_BUCKET + 3}")
    mix = CC.read(SHEET, f"C{R_MIX}:E{R_MIX + 1}")
    number = lambda v, d: v if isinstance(v, (int, float)) and not isinstance(v, bool) else d
    return ([[number(v, DEFAULTS[i][j]) for j, v in enumerate(row)] for i, row in enumerate(inputs)],
            [[number(v, MIX_DEFAULTS[i][j]) for j, v in enumerate(row)] for i, row in enumerate(mix)])


def refresh(CC, cache: dict | None = None) -> str:
    """Fills the grey cells and reruns the simulation. Never touches the yellow cells. Called after each workbook update."""
    import countywatch_run as CWR
    cache = cache or CWR.live_cache()
    if not sheet_exists(CC):
        setup(CC)
        for problem in add_charts(CC):
            log.warning("Mississippi tab chart: %s", problem)
        protect(CC)
    live = CWR.merged_live("MS", cache, CWR.manual())
    proj = CWR.projection("MS", cache)
    nyt = fetch_nyt()
    rows = county_table(live, nyt, proj)
    first, last = R_COUNTY, R_COUNTY + len(rows) - 1
    CC.write(SHEET, f"{C['D']}{first}:{C['O']}{last}", "ms-counts", values=[[x["D"], x["R"], x["O"]] for x in rows])
    CC.write(SHEET, f"{C['bleft']}{first}:{C['bnet']}{last}", "ms-bot", values=[[round(x["bleft"]), round(x["bnet"])] for x in rows])
    pd, pr = sum(r["proj_D"] for r in proj["rows"]), sum(r["proj_R"] for r in proj["rows"])
    CC.write(SHEET, f"B{R_MODELS + 1}:D{R_MODELS + 1}", "ms-bot-final", values=[[round(pd - pr), "", round(proj.get("remaining") or 0)]])
    if nyt:
        CC.write(SHEET, f"{C['pin']}{first}:{C['ptot']}{last}", "ms-prec", values=[[x["pin"], x["ptot"]] for x in rows])
        CC.write(SHEET, f"{C['nexp']}{first}:{C['nnet']}{last}", "ms-nyt", values=[[x["exp"], x["prov"], x["adv"], x["nleft"], x["nnet"]] for x in rows])
        CC.write(SHEET, f"B{R_MODELS + 2}:D{R_MODELS + 2}", "ms-nyt-final", values=[[nyt["final"], nyt["win"] if nyt["win"] is not None else "", nyt["left"]]])
        batch = [["'" + _clock(b[0]), b[1], b[2], b[3], b[2] / (b[2] + b[3]) if b[2] + b[3] else "", b[4]] for b in nyt["batches"]]
        batch += [[""] * 6] * (N_BATCH - len(batch))
        CC.write(SHEET, f"A{R_BATCH}:F{R_BATCH + N_BATCH - 1}", "ms-batch", values=batch)
    types = _type_rows(proj)
    CC.write(SHEET, f"A{R_TYPES}:G{R_TYPES + len(M.TYPES) - 1}", "ms-types", values=types)
    inputs, mix = read_inputs(CC)
    sim = simulate(buckets_math(rows), inputs, mix)
    CC.write(SHEET, f"B{R_SIM + 1}:B{R_SIM + 6}", "ms-sim", values=[[sim[k]] for k in ("colom", "hs", "median", "p10", "p90", "close")])
    charts = chart_rows(rows, inputs, mix, sim, nyt)
    CC.write(SHEET, f"{L(C_TOP)}{R_DATA + 1}:{L(C_TOP + 2)}{R_DATA + N_TOP}", "ms-c-top", values=charts["top"])
    CC.write(SHEET, f"{L(C_SIM)}{R_DATA + 1}:{L(C_SIM + 2)}{R_DATA + len(SIM_BINS)}", "ms-c-sim", values=charts["sim"])
    if nyt:
        CC.write(SHEET, f"{L(C_NIGHT)}{R_DATA + 1}:{L(C_NIGHT + 1)}{R_DATA + N_NIGHT}", "ms-c-night", values=charts["night"])
        where = _where(rows)
        CC.write(SHEET, f"J{R_LEFT}:J{R_LEFT + 1}", "ms-where", values=[[where[0]], [where[1]]])
        CC.write(SHEET, f"J{R_LEFT + 3}", "ms-where2", values=[[where[2]]])
    stamp = datetime.now(N.ET).strftime("%b %d %I:%M %p ET")
    note = f"Last bot update: {stamp}." + (f" NYT's file: {_clock(nyt['updated'])}, race status {nyt['call']}." if nyt else " NYT's file couldn't be read, so its columns are from the last good read.")
    CC.write(SHEET, f"A{R_STATUS}", values=[[note]])
    return "updated the Mississippi tab"


def _clock(iso: str | None) -> str:
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(N.ET).strftime("%b %d %I:%M %p")
    except (AttributeError, ValueError):
        return ""


def _type_rows(proj: dict) -> list[list]:
    out = []
    for t in M.TYPES:
        rows = [r for r in proj["rows"] if r["type"] == t]
        if not rows:
            continue
        left = sum(max(0.0, r["proj_D"] + r["proj_R"] - r["D"] - r["R"]) for r in rows)
        net = sum((r["proj_D"] - r["proj_R"]) - (r["D"] - r["R"]) for r in rows)
        out.append([t, f"{sum(1 for r in rows if r['complete'])} of {len(rows)}", sum(r["D"] + r["R"] for r in rows),
                    proj["type_swing_used"].get(t, proj["state_swing"] or 0), proj["type_pace"].get(t, proj["pace"]), round(left), round(net)])
    return out + [[""] * 7] * (len(M.TYPES) - len(out))     # Mississippi only has some of the types: no gaps between them


# ------------------------------------------------------------------ a workbook rebuilt on this PC
def local_sheet(wb):
    """The same tab in a workbook built from scratch, with the formulas and yellow cells ready and no numbers yet."""
    from openpyxl.styles import Alignment, Font, PatternFill, Protection
    import cw_workbook as W
    ws = wb.create_sheet(SHEET, TAB_POSITION)
    lay = layout()
    for (r, c), v in lay["cells"].items():
        ws.cell(row=r, column=c, value=v)
    ws.cell(row=R_LEFT + 5, column=1)
    for (r, c), f in lay["fmt"].items():
        ws.cell(row=r, column=c).number_format = f
    each = lambda a: (cell for row in ws[a] for cell in row)
    for a, color in lay["fills"]:
        for cell in each(a):
            cell.fill = W.HEAD if color == "HEAD" else PatternFill("solid", fgColor=color)
    for a, color in lay["bold"]:
        for cell in each(a):
            cell.font = Font(bold=True, color=color)
    for a in lay["merge"]:
        ws.merge_cells(a)
    for a in lay["wrap"]:
        for cell in each(a):
            cell.alignment = Alignment(wrap_text=True, vertical="top")
    for r, h in lay["tall"].items():
        ws.row_dimensions[r].height = h
    for col, px in lay["widths"].items():
        ws.column_dimensions[col].width = px / 5.5
    for col in lay["hidden"]:
        ws.column_dimensions[col].hidden = True
    for a in lay["inputs"] + lay["bot"]:
        for cell in each(a):
            cell.protection = Protection(locked=False)
    return ws

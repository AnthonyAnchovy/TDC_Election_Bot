"""County Watch: the 'At a Glance' and 'Polls' sheets.

Polls: the final pre-election poll in each race (seeded from countywatch/polls.json, yellow cells editable), what it
would take for the polls to be wrong, and once results come in, how far the live projection is running from each
poll. Polls never feed the projection. They're only something to measure the results against, because a polling
miss tends to run the same way across states: what the early states show about the polls is a hint about the
late ones.

At a Glance: one screen for the night. Seat tally, how the results compare with the polls, a bar per race (light =
final poll, solid = live projection), and two charts. Every cell is a formula over the Dashboard and Polls sheets, so
it works in the shared workbook without the bot writing anything new.
"""
from __future__ import annotations

import json
from zoneinfo import ZoneInfo

from openpyxl.chart import BarChart, Reference
from openpyxl.chart.label import DataLabelList
from openpyxl.chart.shapes import GraphicalProperties
from openpyxl.chart.text import RichText
from openpyxl.drawing.text import CharacterProperties, Paragraph, ParagraphProperties
from openpyxl.drawing.line import LineProperties
from openpyxl.formatting.rule import FormulaRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

import cw_model as M
import cw_workbook as W
import night as N
import races as R

F, YELLOW, HEAD, MARGIN, SWING = W.F, W.YELLOW, W.HEAD, W.MARGIN, W.SWING
ET = ZoneInfo("America/New_York")
NAVY, BLUE, RED, GREEN, GREY_MID = "1F2A44", "1F4E9A", "B4231B", "2E7D4F", "BFBFBF"
TILE = PatternFill("solid", fgColor="F3F4F7")
SOFT = "8C8C8C"
PLAIN = '"D+"0.0%;"R+"0.0%;"even"'

# ------------------------------------------------------------------ Polls sheet
POLL_ROW0 = 7
POLL_AVG = "Polls!$E$3"            # average results-minus-poll, in two-party margin
POLL_N = "Polls!$H$3"
PCOLS = [  # header, key, width, kind
    ("State", "st", 6, "data"), ("Senate race", "race", 24, "data"),
    ("Pollster or average", "source", 14, "input"), ("Field dates", "dates", 13, "input"),
    ("D side %", "d", 7, "input"), ("R %", "r", 7, "input"), ("Sample", "n", 8, "input"),
    ("Poll margin (two-party)", "pm", 10, "formula"), ("Sampling error on margin (±)", "moe", 10, "formula"),
    ("Published margin, raw pts", "pub", 10, "input"), ("Same poll on old weighting, raw pts", "old", 10, "input"),
    ("Weighting effect (pts toward R)", "weff", 10, "formula"), ("Old-weighting margin (two-party)", "oldm", 10, "formula"),
    ("If polls miss like 2020 (5 pts toward R)", "m20", 11, "formula"),
    ("Live projection (medium/high confidence)", "proj", 11, "formula"), ("Results minus poll", "err", 10, "formula"),
    ("Poll moved by tonight's average miss", "adj", 11, "formula"), ("What the poll said to check tonight", "check", 90, "input"),
]
PL = {k: get_column_letter(i + 1) for i, (_, k, _, _) in enumerate(PCOLS)}
POLL_INPUTS = [k for _, k, _, kind in PCOLS if kind == "input"]


def poll_seed() -> dict:
    try:
        return json.loads((M.CW_DIR / "polls.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"states": {}}


def poll_row(st: str) -> int:
    return POLL_ROW0 + list(M.STATES).index(st)


def poll_ref(st: str, key: str) -> str:
    return f"Polls!${PL[key]}${poll_row(st)}"


def dash_ref(st: str, col: str) -> str:
    return f"Dashboard!${col}${W.DASH_ROW0 + list(M.STATES).index(st)}"


def polls_sheet(ws, names: dict, kept: dict | None):
    """kept: {st: {key: value}} typed into the yellow cells of an earlier build (wins over polls.json)."""
    seed = poll_seed()
    kept = kept or {}
    ws["A1"] = "Final polls, and how the results compare"
    ws["A1"].font = F(bold=True, size=14)
    ws["A2"] = ("Polls never feed the projection. They're a yardstick: once a state has a medium- or high-confidence "
                "projection, 'Results minus poll' shows how far off its poll was, and the average of those is applied to "
                "every other poll. Polling misses tend to run the same way across states, so the early states hint at "
                "the late ones, but early-finishing counties are small and rural, so give it time.")
    ws["A2"].font = F(italic=True, color="595959")
    ws["A2"].alignment = W.WRAP
    ws.merge_cells("A2:R2")
    ws.row_dimensions[2].height = 44
    ws["A3"] = "Tonight's average miss:"
    ws["A3"].font = F(bold=True)
    ws.merge_cells("A3:D3")
    ws["E3"] = f'=IFERROR(AVERAGE({PL["err"]}{POLL_ROW0}:{PL["err"]}{POLL_ROW0 + len(M.STATES) - 1}),"")'
    ws["E3"].number_format = SWING
    ws["E3"].font = F(bold=True, size=12)
    ws.merge_cells("E3:F3")
    ws["G3"] = "states:"
    ws["G3"].alignment = Alignment(horizontal="right")
    ws["H3"] = f'=COUNT({PL["err"]}{POLL_ROW0}:{PL["err"]}{POLL_ROW0 + len(M.STATES) - 1})'
    ws["I3"] = "Negative = results running more Republican than the polls."
    ws["I3"].font = F(italic=True, color="595959")
    art = seed.get("article") or {}
    if art.get("takeaways"):
        ws["A4"] = "From " + art.get("title", "the article") + ": " + " ".join(art["takeaways"])
        ws["A4"].font = F(color="404040", size=9)
        ws["A4"].alignment = W.WRAP
        ws.merge_cells("A4:R4")
        ws.row_dimensions[4].height = 58
    hr = POLL_ROW0 - 1
    for i, (h, _, w, kind) in enumerate(PCOLS):
        ws.cell(row=hr, column=i + 1, value=h)
        ws.column_dimensions[get_column_letter(i + 1)].width = w
    W._style_header(ws, hr, len(PCOLS), 54)
    for i, (_, _, _, kind) in enumerate(PCOLS):
        if kind == "input":
            ws.cell(row=hr, column=i + 1).fill = PatternFill("solid", fgColor="B7950B")
    for si, st in enumerate(M.STATES):
        r = POLL_ROW0 + si
        P = {k: f"{PL[k]}{r}" for k in PL}
        s = {**(seed.get("states", {}).get(st) or {}), **(kept.get(st) or {})}
        dn, rn = names[st]
        race = f"{M.STATES[st]}: {W._surname(dn)} v {W._surname(rn)}" + (" (bellwether)" if st in M.BELLWETHER else "")
        conf = dash_ref(st, "T")
        vals = {
            "st": st, "race": race, **{k: s.get(k) for k in POLL_INPUTS},
            "pm": f'=IF(AND(ISNUMBER({P["d"]}),ISNUMBER({P["r"]}),{P["d"]}+{P["r"]}>0),({P["d"]}-{P["r"]})/({P["d"]}+{P["r"]}),"")',
            "moe": (f'=IF(AND({P["pm"]}<>"",ISNUMBER({P["n"]})),1.96*SQRT(({P["d"]}+{P["r"]})/100-(({P["d"]}-{P["r"]})/100)^2)'
                    f'/SQRT({P["n"]})/(({P["d"]}+{P["r"]})/100),"")'),
            "weff": f'=IF(AND(ISNUMBER({P["pub"]}),ISNUMBER({P["old"]})),{P["old"]}-{P["pub"]},"")',
            "oldm": f'=IF(OR({P["pm"]}="",{P["weff"]}=""),"",{P["pm"]}+{P["weff"]}/({P["d"]}+{P["r"]}))',
            "m20": f'=IF({P["pm"]}="","",{P["pm"]}-0.05)',
            "proj": f'=IF(OR({conf}="medium",{conf}="high"),{dash_ref(st, "Q")},"")',
            "err": f'=IF(AND({P["proj"]}<>"",{P["pm"]}<>""),{P["proj"]}-{P["pm"]},"")',
            "adj": f'=IF({P["pm"]}="","",IF({POLL_AVG}="",{P["pm"]},{P["pm"]}+{POLL_AVG}))',
        }
        for i, (_, k, _, kind) in enumerate(PCOLS):
            cell = ws.cell(row=r, column=i + 1, value=vals.get(k))
            cell.font = F(color="0000FF") if kind == "input" else F(bold=(k == "race"))
            cell.border = W.THIN
            if kind == "input":
                cell.fill = YELLOW
        for k in ("pm", "oldm", "m20", "proj", "adj"):
            ws[f"{PL[k]}{r}"].number_format = MARGIN
        ws[f"{PL['moe']}{r}"].number_format = '"±"0.0%'
        ws[f"{PL['err']}{r}"].number_format = SWING
        ws[f"{PL['weff']}{r}"].number_format = '0;-0;0'
        ws[f"{PL['n']}{r}"].number_format = W.NUM
        ws[f"{PL['check']}{r}"].alignment = W.WRAP
        ws.row_dimensions[r].height = 58 if s.get("check") else 18
    ws.freeze_panes = f"C{POLL_ROW0}"
    last = POLL_ROW0 + len(M.STATES) - 1
    for k in ("pm", "adj", "proj"):
        c = f"${PL[k]}{POLL_ROW0}"
        W._tier_colors(ws, f"{PL[k]}{POLL_ROW0}:{PL[k]}{last}", c, W.TIER_CONDS(c))
    note = last + 2
    for i, line in enumerate([
        "Notes",
        "• Poll margin is two-party, like the rest of the workbook: (D − R) / (D + R). Replace any poll with an average if you have one; just change the yellow cells.",
        "• Sampling error is only the part from sample size. Real poll error (who answers, who votes, weighting) is bigger and runs the same way across states.",
        "• Weighting effect = how much less Democratic the published poll is than the same interviews on Times/Siena's old weighting. Big numbers mean heavy correction for Republicans not answering: the correction might be right (2022) or not enough (2020).",
        "• 'Results minus poll' only counts a state once its projection is medium or high confidence. Early projections lean on small rural counties.",
    ]):
        c = ws.cell(row=note + i, column=1, value=line)
        c.font = F(bold=(i == 0), color="404040")


def read_poll_inputs(wb) -> dict:
    if "Polls" not in wb.sheetnames:
        return {}
    ws = wb["Polls"]
    idx = {k: i for i, (_, k, _, _) in enumerate(PCOLS)}
    out = {}
    for row in ws.iter_rows(min_row=POLL_ROW0, max_row=POLL_ROW0 + len(M.STATES) - 1, values_only=True):
        if row and row[0] in M.STATES:
            out[row[0]] = {k: row[idx[k]] for k in POLL_INPUTS}
    return out


# ------------------------------------------------------------------ At a Glance
def _close(st: str) -> str:
    t = N.first_close(st).astimezone(ET)
    return t.strftime("%I:%M").lstrip("0") + (" AM" if t.hour < 12 else "") + " ET"


def _bar(val: str, sign: str, ch: str) -> str:
    """Text bar: one block per point of margin, capped at 15."""
    cmp = ">0" if sign == "D" else "<0"
    return f'IF(AND({val}<>"",{val}{cmp}),REPT("{ch}",MIN(15,ROUND(ABS({val})*100,0))),"")'


def glance_sheet(ws, names: dict, national_swing: str):
    races = [st for st in M.STATES if st not in M.BELLWETHER]
    races.sort(key=lambda s: (N.first_close(s), M.STATES[s]))
    bells = sorted((st for st in M.STATES if st in M.BELLWETHER), key=N.first_close)
    ws.sheet_view.showGridLines = False
    widths = {"A": 2, "B": 17, "C": 12, "D": 9, "E": 17, "F": 10, "G": 17, "H": 22, "I": 19, "J": 11, "K": 10, "L": 12, "M": 2}
    for c, w in widths.items():
        ws.column_dimensions[c].width = w

    # ---- banner
    ws.merge_cells("B1:L1")
    ws["B1"] = "SENATE AT A GLANCE  ·  County Watch 2026"
    ws["B1"].font = F(bold=True, size=18, color="FFFFFF")
    ws["B1"].alignment = Alignment(vertical="center", indent=1)
    for c in range(2, 13):
        ws.cell(row=1, column=c).fill = HEAD
    ws.row_dimensions[1].height = 36
    ws.merge_cells("B2:H2")
    ws["B2"] = ("Solid bars = live projection from finished counties. Light bars = final poll, until a projection exists. "
                "Blue = Democratic side, red = Republican. One block = 1 point.")
    ws["B2"].font = F(italic=True, color="595959", size=9)
    ws.merge_cells("I2:L2")
    ws["I2"] = "=Dashboard!A3"
    ws["I2"].font = F(color="595959", size=9)
    ws["I2"].alignment = Alignment(horizontal="right")

    # ---- helper block for tallies and charts (hidden columns O onward)
    H0, hr0 = 15, 41                       # column O, first data row
    hcols = ["State", "Poll", "Projection", "Lean now", "Lean if 2020 miss", "Lean if old weighting",
             "Lean if tonight's miss holds", "Results minus poll"]
    HL = {h: get_column_letter(H0 + i) for i, h in enumerate(hcols)}
    ws.cell(row=hr0 - 1, column=H0, value="Chart data (formulas; hidden)")
    for i, h in enumerate(hcols):
        ws.cell(row=hr0, column=H0 + i, value=h).font = F(bold=True)
    for j, st in enumerate(races):
        r = hr0 + 1 + j
        poll, conf = poll_ref(st, "pm"), dash_ref(st, "T")
        proj = f'IF(OR({conf}="medium",{conf}="high"),{dash_ref(st, "Q")},"")'
        live_or = lambda alt: f'=IF({proj}<>"",{proj},{alt})'
        vals = [M.STATES[st],
                f'=IF({poll}="",NA(),{poll}*100)',
                f'=IF({proj}="",NA(),{proj}*100)',
                live_or(f'IF({poll}="","",{poll})'),
                live_or(f'IF({poll}="","",{poll}-0.05)'),
                live_or(f'IF({poll_ref(st, "oldm")}<>"",{poll_ref(st, "oldm")},IF({poll}="","",{poll}))'),
                live_or(f'IF({poll_ref(st, "adj")}="","",{poll_ref(st, "adj")})'),
                f'=IF({poll_ref(st, "err")}="",NA(),{poll_ref(st, "err")}*100)']
        for i, v in enumerate(vals):
            ws.cell(row=r, column=H0 + i, value=v)
    hlast = hr0 + len(races)
    rngh = lambda h: f"${HL[h]}${hr0 + 1}:${HL[h]}${hlast}"
    st_rng = rngh("State")
    ne = M.STATES["NE"]
    d_base = R.SENATE_HOLDOVER["D"] + sum(1 for k, p in W_SAFE().items() if p == "D")
    r_base = R.SENATE_HOLDOVER["R"] + sum(1 for k, p in W_SAFE().items() if p == "R")

    def tally(col):
        d = f'({d_base}+COUNTIFS({rngh(col)},">0",{st_rng},"<>{ne}"))'
        i = f'COUNTIFS({rngh(col)},">0",{st_rng},"{ne}")'
        r = f'({r_base}+COUNTIF({rngh(col)},"<0"))'
        return d, i, r

    # seat strip chart data: scenarios x (D, I, no read yet, R)
    s0 = hlast + 3
    ws.cell(row=s0, column=H0, value="Scenario")
    for i, h in enumerate(["Democrats", "Osborn (I)", "No poll or result", "Republicans"]):
        ws.cell(row=s0, column=H0 + 1 + i, value=h)
    scen = [("If every poll is right", "Lean now"), ("If polls miss like 2020", "Lean if 2020 miss"),
            ("Old Times/Siena weighting", "Lean if old weighting"), ("If tonight's miss holds", "Lean if tonight's miss holds")]
    for j, (lab, col) in enumerate(scen):
        r = s0 + 1 + j
        d, i, rr = tally(col)
        ws.cell(row=r, column=H0, value=lab)
        ws.cell(row=r, column=H0 + 1, value=f"={d}")
        ws.cell(row=r, column=H0 + 2, value=f"={i}")
        ws.cell(row=r, column=H0 + 4, value=f"={rr}")
        L_ = lambda k: f"{get_column_letter(H0 + k)}{r}"
        ws.cell(row=r, column=H0 + 3, value=f"=100-{L_(1)}-{L_(2)}-{L_(4)}")
    for c in range(H0, H0 + len(hcols)):
        ws.column_dimensions[get_column_letter(c)].hidden = True

    # ---- tiles (rows 4-6)
    sc = lambda j, k: f"{get_column_letter(H0 + k)}{s0 + 1 + j}"      # scenario table cell
    now_d, now_i, now_r = sc(0, 1), sc(0, 2), sc(0, 4)
    tiles = [
        ("B", "C", "SENATE IF EVERY RACE GOES THE WAY IT LEANS NOW",
         f'="D "&{now_d}&"  ·  R "&{now_r}&IF({now_i}>0,"  ·  I "&{now_i},"")', None),
        ("D", "E", "MAJORITY",
         f'=IF({now_d}>=51,"Democrats",IF({now_r}>=50,"Republicans",IF({now_d}+{now_i}>=51,"Osborn decides","Not yet clear")))', None),
        ("F", "G", "RESULTS VS POLLS SO FAR",
         f'=IF({POLL_AVG}="","waiting",IF({POLL_AVG}>=0,"D +","R +")&TEXT(ABS({POLL_AVG})*100,"0.0")&" vs polls")', POLL_AVG),
        ("H", "H", "EXPECTED VOTE COUNTED",
         f"=IFERROR(SUM(Dashboard!$I${W.DASH_ROW0}:$I${W.DASH_ROW0 + len(M.STATES) - 1})/"
         f"SUM(Dashboard!$H${W.DASH_ROW0}:$H${W.DASH_ROW0 + len(M.STATES) - 1}),0)", None),
        ("I", "J", "RACES WITH A SOLID PROJECTION",
         f'=COUNTIF({rngh("Projection")},">-1000")&" of {len(races)}"', None),
        ("K", "L", "NATIONAL SWING VS 2024",
         f'=IF({national_swing}="","no county finished",IF({national_swing}>=0,"D +","R +")&TEXT(ABS({national_swing})*100,"0.0"))',
         national_swing),
    ]
    for a, b, label, formula, colour_ref in tiles:
        ws.merge_cells(f"{a}4:{b}4")
        ws.merge_cells(f"{a}5:{b}6")
        ws[f"{a}4"] = label
        ws[f"{a}4"].font = F(bold=True, size=8, color=SOFT)
        ws[f"{a}4"].alignment = Alignment(horizontal="center", vertical="bottom", wrap_text=True)
        ws[f"{a}5"] = formula
        ws[f"{a}5"].font = F(bold=True, size=16, color=NAVY)
        ws[f"{a}5"].alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        for rr in (4, 5, 6):
            for c in range(ws[f"{a}4"].column, ws[f"{b}4"].column + 1):
                ws.cell(row=rr, column=c).fill = TILE
        if colour_ref:
            for cond, col in ((f"{colour_ref}>=0.01", BLUE), (f"{colour_ref}<=-0.01", RED)):
                ws.conditional_formatting.add(f"{a}5", FormulaRule(
                    formula=[f'AND(ISNUMBER({colour_ref}),{cond})'], font=Font(name=W.FONT, bold=True, size=16, color=col)))
    ws["H5"].number_format = "0%"
    ws.row_dimensions[4].height = 24
    ws.row_dimensions[5].height = 22
    ws.row_dimensions[6].height = 22
    ws.conditional_formatting.add("D5", FormulaRule(formula=['D5="Democrats"'], font=Font(name=W.FONT, bold=True, size=16, color=BLUE)))
    ws.conditional_formatting.add("D5", FormulaRule(formula=['D5="Republicans"'], font=Font(name=W.FONT, bold=True, size=16, color=RED)))

    # ---- race board
    b0 = 8
    hdr = ["Race", "Polls close", "Final poll", "◀ Republican", "Margin", "Democratic side ▶", "Call", "Vote counted",
           "Confidence", "Results vs poll", "Poll + tonight's miss"]
    for i, h in enumerate(hdr):
        c = ws.cell(row=b0, column=2 + i, value=h)
        c.font = F(bold=True, color="FFFFFF", size=9)
        c.fill = HEAD
        c.alignment = Alignment(horizontal="right" if h.startswith("◀") else "left" if h.endswith("▶") else "center",
                                vertical="center", wrap_text=True)
    ws.row_dimensions[b0].height = 28
    r = b0 + 1
    first = r
    for st in races:
        dr = W.DASH_ROW0 + list(M.STATES).index(st)
        dn, rn = names[st]
        poll, conf, proj_raw = poll_ref(st, "pm"), f"Dashboard!$T${dr}", f"Dashboard!$Q${dr}"
        proj = f'IF(OR({conf}="medium",{conf}="high",{conf}="low"),{proj_raw},"")'
        show = f'IF({proj}<>"",{proj},{poll})'
        is_proj = f'{proj}<>""'
        bar = lambda side: (f'=IF({is_proj},{_bar(proj, side, "█")},{_bar(poll, side, "░")})')
        counted = f"Dashboard!$J${dr}"
        cells = {
            "B": f"{M.STATES[st]}",
            "C": _close(st),
            "D": f'={poll}',
            "E": bar("R"),
            "F": f'={show}',
            "G": bar("D"),
            "H": f'=IF({is_proj},Dashboard!$S${dr},IF({poll}="","no poll entered","poll: "&IF({poll}>=0,"{W._surname(dn)}","{W._surname(rn)}")&" ahead"))',
            "I": f'=REPT("▰",ROUND(MIN(1,{counted})*10,0))&REPT("▱",10-ROUND(MIN(1,{counted})*10,0))&"  "&TEXT({counted},"0%")',
            "J": f'=IF({conf}="none","—",{conf})',
            "K": f'={poll_ref(st, "err")}',
            "L": f'=IF({proj}<>"","",{poll_ref(st, "adj")})',
        }
        for col, v in cells.items():
            c = ws[f"{col}{r}"]
            c.value = v
            c.font = F(size=10)
            c.border = Border(bottom=Side(style="thin", color="E3E3E3"))
            c.alignment = Alignment(vertical="center", horizontal="center")
        ws[f"B{r}"].font = F(bold=True, size=11)
        ws[f"B{r}"].alignment = Alignment(vertical="center", indent=1)
        ws[f"E{r}"].font = Font(name="Consolas", size=10, color=RED)
        ws[f"E{r}"].alignment = Alignment(horizontal="right", vertical="center")
        ws[f"G{r}"].font = Font(name="Consolas", size=10, color=BLUE)
        ws[f"G{r}"].alignment = Alignment(horizontal="left", vertical="center")
        ws[f"I{r}"].font = Font(name="Consolas", size=9, color="595959")
        ws[f"F{r}"].font = F(bold=True, size=11)
        for col in ("D", "K"):
            ws[f"{col}{r}"].number_format = MARGIN
        for col in ("F", "L"):          # plain: the tier fill sets the text color
            ws[f"{col}{r}"].number_format = PLAIN
        ws[f"H{r}"].font = F(bold=True, size=10)
        # before a projection the margin cell shows the poll in grey italics
        ws.conditional_formatting.add(f"F{r}", FormulaRule(
            formula=[f'NOT({is_proj})'], stopIfTrue=True, font=Font(name=W.FONT, italic=True, color=SOFT)))
        ws.conditional_formatting.add(f"H{r}", FormulaRule(
            formula=[f'NOT({is_proj})'], stopIfTrue=True, font=Font(name=W.FONT, italic=True, color=SOFT)))
        W._tier_colors(ws, f"F{r}", f"$F{r}", W.TIER_CONDS(f"$F{r}"))
        W._tier_colors(ws, f"H{r}", f"$F{r}", W.TIER_CONDS(f"$F{r}"))
        W._tier_colors(ws, f"L{r}", f"$L{r}", W.TIER_CONDS(f"$L{r}"))
        ws.row_dimensions[r].height = 22
        r += 1
    last = r - 1
    # bellwethers: the national swing, not a race
    r += 1
    ws.merge_cells(f"B{r}:L{r}")
    ws[f"B{r}"] = "BELLWETHERS · safe seats watched for the national swing (swing vs 2024 in finished counties)"
    ws[f"B{r}"].font = F(bold=True, size=8, color=SOFT)
    r += 1
    for st in bells:
        dr = W.DASH_ROW0 + list(M.STATES).index(st)
        ws[f"B{r}"] = M.STATES[st]
        ws[f"B{r}"].font = F(bold=True, size=10)
        ws[f"B{r}"].alignment = Alignment(indent=1)
        ws[f"C{r}"] = _close(st)
        ws[f"C{r}"].alignment = Alignment(horizontal="center")
        sw = f"Dashboard!$N${dr}"
        ws[f"E{r}"] = f'={_bar(sw, "R", "█")}'
        ws[f"E{r}"].font = Font(name="Consolas", size=10, color=RED)
        ws[f"E{r}"].alignment = Alignment(horizontal="right")
        ws[f"F{r}"] = f'=IF({sw}="","",{sw})'
        ws[f"F{r}"].number_format = SWING
        ws[f"F{r}"].alignment = Alignment(horizontal="center")
        ws[f"F{r}"].font = F(bold=True)
        ws[f"G{r}"] = f'={_bar(sw, "D", "█")}'
        ws[f"G{r}"].font = Font(name="Consolas", size=10, color=BLUE)
        ws[f"H{r}"] = f'=IF({sw}="","no county finished yet",TEXT(Dashboard!$K${dr},"0")&" counties finished")'
        ws[f"H{r}"].font = F(size=9, color="595959")
        r += 1

    # scenario table (the article's "choose your own adventure")
    r += 1
    ws.merge_cells(f"B{r}:L{r}")
    ws[f"B{r}"] = ("IF THE POLLS ARE OFF · Senate seats under each polling story. Races with a solid projection use it; "
                   "the rest use their poll, moved as described. Majority = 51 (the vice president breaks a 50-50 tie for R).")
    ws[f"B{r}"].font = F(bold=True, size=8, color=SOFT)
    ws[f"B{r}"].alignment = Alignment(wrap_text=True)
    ws.row_dimensions[r].height = 26
    r += 1
    for i, h in enumerate(["Polling story", "", "Democrats", "Osborn (I)", "Republicans", "No read yet", "Majority"]):
        c = ws.cell(row=r, column=2 + i, value=h or None)
        c.font = F(bold=True, size=9, color="FFFFFF")
        c.fill = HEAD
        c.alignment = Alignment(horizontal="center")
    ws.merge_cells(f"B{r}:C{r}")
    r += 1
    scen_desc = ["Every poll is right", "2020-size miss (5 pts to R)", "Times/Siena's old weighting",
                 "Off by tonight's average miss"]
    for j, lab in enumerate(scen_desc):
        ws.merge_cells(f"B{r}:C{r}")
        ws[f"B{r}"] = lab
        ws[f"B{r}"].font = F(size=10)
        ws[f"B{r}"].alignment = Alignment(indent=1)
        d, i, rr, n = sc(j, 1), sc(j, 2), sc(j, 4), sc(j, 3)
        ws[f"D{r}"] = f"={d}"
        ws[f"E{r}"] = f"={i}"
        ws[f"F{r}"] = f"={rr}"
        ws[f"G{r}"] = f"={n}"
        ws[f"H{r}"] = f'=IF({d}>=51,"Democrats",IF({rr}>=50,"Republicans",IF({d}+{i}>=51,"Osborn decides","unclear")))'
        for col in "DEFGH":
            ws[f"{col}{r}"].alignment = Alignment(horizontal="center")
            ws[f"{col}{r}"].font = F(bold=(col in "DFH"), size=10, color=BLUE if col == "D" else RED if col == "F" else "1F1F1F")
            ws[f"{col}{r}"].border = Border(bottom=Side(style="thin", color="E3E3E3"))
        ws.conditional_formatting.add(f"H{r}", FormulaRule(formula=[f'H{r}="Democrats"'], fill=PatternFill("solid", fgColor="DCE6F1", bgColor="DCE6F1")))
        ws.conditional_formatting.add(f"H{r}", FormulaRule(formula=[f'H{r}="Republicans"'], fill=PatternFill("solid", fgColor="F8D7D3", bgColor="F8D7D3")))
        r += 1
    ws.merge_cells(f"B{r}:L{r}")
    ws[f"B{r}"] = ("Seats outside the board: D " + str(d_base) + " (not up, or Solid D), R " + str(r_base)
                   + ". 'No read yet' = races with no poll typed into the Polls sheet and no projection.")
    ws[f"B{r}"].font = F(italic=True, size=8, color=SOFT)
    charts_row = r + 2

    # ---- charts
    cats = Reference(ws, min_col=H0, min_row=hr0 + 1, max_row=hlast)
    ch = BarChart()
    ch.type = "bar"
    ch.grouping = "clustered"
    ch.title = "Final poll vs live projection (two-party margin)"
    ch.add_data(Reference(ws, min_col=H0 + 1, max_col=H0 + 2, min_row=hr0, max_row=hlast), titles_from_data=True)
    ch.set_categories(cats)
    for s, colr in zip(ch.series, ("A6B4CC", NAVY)):
        s.graphicalProperties = GraphicalProperties(solidFill=colr, ln=LineProperties(noFill=True))
    ch.x_axis.scaling.orientation = "maxMin"          # first-closing state at top, like the board
    ch.y_axis.number_format = '"D+"0;"R+"0;0'
    ch.y_axis.scaling.min, ch.y_axis.scaling.max, ch.y_axis.majorUnit = -20, 20, 5   # fixed, so 0 stays centered
    ch.y_axis.majorGridlines.spPr = GraphicalProperties(ln=LineProperties(solidFill="E3E3E3"))
    ch.x_axis.delete = False
    ch.y_axis.delete = False
    ch.x_axis.tickLblPos = "low"
    ch.gapWidth = 60
    ch.legend.position = "b"
    ch.visible_cells_only = False
    ch.height, ch.width = 10, 16
    ws.add_chart(ch, f"B{charts_row}")

    seat = BarChart()
    seat.type = "bar"
    seat.grouping = "stacked"
    seat.overlap = 100
    seat.title = "Senate seats under each polling story (51 for a majority)"
    seat.add_data(Reference(ws, min_col=H0 + 1, max_col=H0 + 4, min_row=s0, max_row=s0 + len(scen)), titles_from_data=True)
    seat.set_categories(Reference(ws, min_col=H0, min_row=s0 + 1, max_row=s0 + len(scen)))
    for s, colr in zip(seat.series, (BLUE, GREEN, GREY_MID, RED)):
        s.graphicalProperties = GraphicalProperties(solidFill=colr, ln=LineProperties(solidFill="FFFFFF", w=19050))
        s.dLbls = DataLabelList()
        s.dLbls.showVal = True
        s.dLbls.numFmt = '0;-0;;'                     # hide zero-seat labels
        s.dLbls.txPr = RichText(p=[Paragraph(pPr=ParagraphProperties(defRPr=CharacterProperties(
            b=True, sz=1000, solidFill="FFFFFF" if colr != GREY_MID else "1F1F1F")), endParaRPr=CharacterProperties())])
        for a in ("showSerName", "showCatName", "showLegendKey", "showPercent"):
            setattr(s.dLbls, a, False)
    seat.x_axis.scaling.orientation = "maxMin"
    seat.y_axis.scaling.min = 0
    seat.y_axis.scaling.max = 100
    seat.y_axis.majorUnit = 10
    seat.y_axis.majorGridlines.spPr = GraphicalProperties(ln=LineProperties(solidFill="E3E3E3"))
    seat.x_axis.delete = False
    seat.y_axis.delete = False
    seat.gapWidth = 50
    seat.legend.position = "b"
    seat.visible_cells_only = False
    seat.height, seat.width = 10, 15.5
    ws.add_chart(seat, f"H{charts_row}")

    foot = charts_row + 21
    for i, line in enumerate([
        "How to read it",
        "• Before results, everything here comes from the polls (Polls sheet). Once a state's finished counties give a projection, its bar goes solid and the call is colored.",
        "• 'Results vs poll' and 'Poll + tonight's miss' only use projections at medium or high confidence. A miss that shows up in Georgia and North Carolina at 8 PM is a hint about Iowa and Alaska later, not proof: misses also differ by region and electorate.",
        "• The board's projection includes low-confidence ones (marked 'low'); treat those as a weather report. Details per state are on the Dashboard.",
    ]):
        c = ws.cell(row=foot + i, column=2, value=line)
        c.font = F(bold=(i == 0), size=9, color="404040")
    ws.freeze_panes = "A4"
    ws.sheet_properties.tabColor = NAVY


def W_SAFE() -> dict:
    """Solid-rated Senate seats up this year that aren't on the board (from competitive.SAFE)."""
    import competitive as C
    board = {f"{st}-SEN" for st in M.STATES if st not in M.BELLWETHER}
    return {k: p for k, p in C.SAFE.items() if k.endswith("-SEN") and k not in board}

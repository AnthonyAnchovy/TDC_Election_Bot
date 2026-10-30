"""County Watch workbook: builds 'County Watch 2026.xlsx' and keeps its live columns current.

Every calculated cell is an Excel formula, so the workbook works on its own: type county numbers into the
yellow Manual columns and the projections update. The bot fills the grey Feed columns for states with a
live feed, plus the early vote tables and the written read.

Updating while the file is open in Excel needs the optional xlwings package (Windows). Without it, the bot
updates the file only while it's closed and otherwise writes 'County Watch live.csv' next to it.
"""
from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from openpyxl import Workbook, load_workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

import cw_early as E
import cw_model as M

HERE = Path(__file__).parent
FONT = "Arial"
F = lambda **k: Font(name=FONT, size=k.pop("size", 10), **k)
YELLOW = PatternFill("solid", fgColor="FFF2CC")
GREY = PatternFill("solid", fgColor="EDEDED")
HEAD = PatternFill("solid", fgColor="1F2A44")
BAND = PatternFill("solid", fgColor="DCE6F1")
THIN = Border(bottom=Side(style="thin", color="BFBFBF"))
MARGIN = '[Blue]"D+"0.0%;[Red]"R+"0.0%;"even"'
SWING = '[Blue]"+"0.0%" D";[Red]"+"0.0%" R";"0.0%"'
PCT = "0%"
NUM = "#,##0"
WRAP = Alignment(wrap_text=True, vertical="top")

# Counties sheet layout: (header, key, width, kind)  kind: data | input | feed | formula
CCOLS = [
    ("State", "state", 6, "data"), ("FIPS", "fips", 7, "data"), ("County", "name", 18, "data"),
    ("Type", "type", 20, "data"), ("Key", "key", 5, "data"),
    ("D votes 2024", "d24", 11, "data"), ("R votes 2024", "r24", 11, "data"), ("Total 2024", "t24", 11, "data"),
    ("D votes 2020", "d20", 11, "data"), ("R votes 2020", "r20", 11, "data"), ("2022 race", "race22", 10, "data"),
    ("D votes 2022", "d22", 11, "data"), ("R votes 2022", "r22", 11, "data"), ("Total 2022", "t22", 11, "data"),
    ("Margin 2024", "m24", 10, "formula"), ("Margin 2020", "m20", 10, "formula"), ("Swing 20→24", "sw2024", 10, "formula"),
    ("Margin 2022", "m22", 10, "formula"), ("Share of state 2024", "share", 9, "formula"),
    ("D break-even", "be", 10, "formula"), ("Expected 2-party votes", "exp", 12, "formula"),
    ("Feed D", "fd", 10, "feed"), ("Feed R", "fr", 10, "feed"), ("Feed other", "fo", 9, "feed"),
    ("Feed % precincts", "fp", 9, "feed"),
    ("Manual D", "md", 10, "input"), ("Manual R", "mr", 10, "input"), ("Manual other", "mo", 9, "input"),
    ("Manual complete (Y)", "mc", 9, "input"),
    ("D counted", "dc", 10, "formula"), ("R counted", "rc", 10, "formula"), ("2-party counted", "tc", 11, "formula"),
    ("Complete (1/0)", "done", 8, "formula"), ("Live margin", "lm", 10, "formula"), ("Swing vs 2024", "sw", 10, "formula"),
    ("Projected margin", "pm", 10, "formula"), ("Projected 2-party votes", "pv", 12, "formula"),
    ("Projected D", "pd", 11, "formula"), ("Projected R", "pr", 11, "formula"),
    ("Live vs break-even", "vbe", 10, "formula"), ("Est. % in", "ein", 8, "formula"),
    ("Swing x votes (helper)", "swv", 10, "formula"),
    ("2-party votes at 2022 turnout", "w", 12, "formula"), ("Margin x votes (helper)", "mw", 10, "formula"),
    ("Turnout pace used (vs 2022)", "pace", 9, "formula"), ("Par votes (tie pace)", "par", 11, "formula"),
    ("Turnout index (100 = state pace)", "ti", 8, "formula"), ("Break-even at actual turnout", "tbe", 10, "formula"),
    ("Turnout index needed at live margin", "tneed", 9, "formula"), ("Turnout effect (pts)", "teff", 9, "formula"),
    ("Margin + turnout vs tie pace (pts)", "ov", 10, "formula"), ("Net votes vs tie pace", "nvt", 11, "formula"),
    ("Margin net votes (helper)", "mv", 10, "formula"), ("Turnout net votes (helper)", "tv", 10, "formula"),
    ("Black %", "black", 8, "data"), ("Hispanic %", "hisp", 8, "data"), ("College %", "ba", 8, "data"),
    ("Population 2023", "pop", 11, "data"),
]
CL = {k: get_column_letter(i + 1) for i, (_, k, _, _) in enumerate(CCOLS)}
DASH_ROW0 = 6          # first state row on Dashboard
TYPE_ROW0 = 4
# Dashboard columns that other sheets read
D_NEED, D_FACTOR, D_DONE, D_PACE_USED, D_PACE, D_READ = "F", "G", "K", "X", "Y", "AB"


def _dash(col: str, state_ref: str) -> str:
    """INDEX/MATCH into a Dashboard column for the state in state_ref."""
    n = DASH_ROW0 + len(M.STATES) - 1
    return f"INDEX(Dashboard!${col}${DASH_ROW0}:${col}${n},MATCH({state_ref},Dashboard!$A${DASH_ROW0}:$A${n},0))"


def _style_header(ws, row, ncols, height=42):
    for c in range(1, ncols + 1):
        cell = ws.cell(row=row, column=c)
        cell.font = F(bold=True, color="FFFFFF")
        cell.fill = HEAD
        cell.alignment = Alignment(wrap_text=True, vertical="center", horizontal="center")
    ws.row_dimensions[row].height = height


def _names() -> dict:
    try:
        c = json.loads((HERE / "candidates.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        c = {}
    out = {}
    for st in M.STATES:
        e = c.get(f"{st}-SEN", {})
        out[st] = (e.get("I") if st == "NE" else e.get("D")) or "Democrat", e.get("R") or "Republican"
    return out


# ------------------------------------------------------------------ build
def build(path: Path, *, keep: dict | None = None, live: dict | None = None, uf: dict | None = None,
          nc: dict | None = None, reads: dict | None = None):
    """keep: manual inputs carried over {fips: {md, mr, mo, mc}} and {'factor': {st: x}}.
    live: {st: {fips: row}} feed values. uf: parsed UF rows. nc: NC early vote tables. reads: {st: text}."""
    keep = keep or {}
    live = live or {}
    data = M.load()
    names = _names()
    wb = Workbook()
    readme = wb.active
    readme.title = "Read Me"
    dash = wb.create_sheet("Dashboard")
    keys = wb.create_sheet("Key Counties")
    bench = wb.create_sheet("County Benchmarks")
    first = wb.create_sheet("First Counties")
    cty = wb.create_sheet("Counties")
    types = wb.create_sheet("Types")
    ev = wb.create_sheet("Early Vote")

    # ---------------- Counties
    ws = cty
    ws.append([h for h, _, _, _ in CCOLS])
    _style_header(ws, 1, len(CCOLS))
    for i, (_, k, w, kind) in enumerate(CCOLS):
        ws.column_dimensions[get_column_letter(i + 1)].width = w
        if kind == "input":
            ws.cell(row=1, column=i + 1).fill = PatternFill("solid", fgColor="B7950B")
        if kind == "feed":
            ws.cell(row=1, column=i + 1).fill = PatternFill("solid", fgColor="595959")
    row = 2
    rowmap = {}
    order = [c for st in M.STATES for c in sorted(data[st], key=lambda c: c["name"])]
    n_last = len(order) + 1
    rng = lambda k: f"Counties!${CL[k]}$2:${CL[k]}${n_last}"
    for c in order:
        r = row
        rowmap[c["fips"]] = r
        L = {k: f"{CL[k]}{r}" for k in CL}
        vals = {"state": c["state"], "fips": c["fips"], "name": c["name"], "type": c["type"], "key": "★" if c["key"] else "",
                "d24": c["d24"], "r24": c["r24"], "t24": c["t24"], "d20": c["d20"] or None, "r20": c["r20"] or None,
                "race22": c["race22"], "d22": c["d22"] or None, "r22": c["r22"] or None, "t22": c["t22"],
                "black": c["black"] or None, "hisp": c["hisp"] or None, "ba": c["ba"] or None, "pop": c["pop"] or None}
        forms = {
            "m24": f"=IFERROR(({L['d24']}-{L['r24']})/({L['d24']}+{L['r24']}),\"\")",
            "m20": f"=IFERROR(({L['d20']}-{L['r20']})/({L['d20']}+{L['r20']}),\"\")",
            "sw2024": f"=IFERROR({L['m24']}-{L['m20']},\"\")",
            "m22": f"=IFERROR(({L['d22']}-{L['r22']})/({L['d22']}+{L['r22']}),\"\")",
            "share": f"={L['t24']}/SUMIFS({rng('t24')},{rng('state')},$A{r})",
            "be": f"={L['m24']}+{_dash(D_NEED, f'$A{r}')}",
            "exp": f"={L['w']}*{_dash(D_FACTOR, f'$A{r}')}",
            "dc": f"=IF({L['md']}<>\"\",{L['md']},N({L['fd']}))",
            "rc": f"=IF({L['mr']}<>\"\",{L['mr']},N({L['fr']}))",
            "tc": f"={L['dc']}+{L['rc']}",
            "done": (f"=IF(UPPER({L['mc']})=\"Y\",1,IF({L['tc']}=0,0,IF(AND({L['md']}=\"\",{L['fp']}<>\"\"),"
                     f"IF({L['fp']}>=0.999,1,0),IF({L['tc']}>={L['exp']},1,0))))"),
            "lm": f"=IF({L['tc']}>0,({L['dc']}-{L['rc']})/{L['tc']},\"\")",
            "sw": f"=IF({L['done']}=1,{L['lm']}-{L['m24']},\"\")",
            "pm": (f"=IF({L['done']}=1,{L['lm']},MAX(-1,MIN(1,{L['m24']}+INDEX(Types!$H:$H,"
                   f"MATCH($A{r}&\"|\"&$D{r},Types!$A:$A,0)))))"),
            "pv": f"=IF({L['done']}=1,{L['tc']},MAX({L['w']}*{L['pace']},{L['tc']}))",
            "pd": f"=IF({L['done']}=1,{L['dc']},{L['pv']}*(1+{L['pm']})/2)",
            "pr": f"=IF({L['done']}=1,{L['rc']},{L['pv']}*(1-{L['pm']})/2)",
            "vbe": f"=IF({L['lm']}=\"\",\"\",{L['lm']}-{L['be']})",
            "ein": f"=IF({L['done']}=1,1,IF({L['exp']}>0,MIN({L['tc']}/{L['exp']},0.99),0))",
            "swv": f"=IF({L['done']}=1,{L['sw']}*{L['tc']},0)",
            "w": f"={L['t22']}*({L['d24']}+{L['r24']})/{L['t24']}",
            "mw": f"={L['m24']}*{L['w']}",
            "pace": (f"=IF({L['done']}=1,IF({L['w']}>0,{L['tc']}/{L['w']},1),IFERROR(INDEX(Types!$K:$K,"
                     f"MATCH($A{r}&\"|\"&$D{r},Types!$A:$A,0)),{_dash(D_PACE_USED, f'$A{r}')}))"),
            "par": f"={L['w']}*{_dash(D_PACE, f'$A{r}')}",
            "ti": f"=IF(AND({L['done']}=1,{L['par']}>0,{L['tc']}>0),100*{L['tc']}/{L['par']},\"\")",
            "tbe": f"=IF({L['ti']}=\"\",\"\",{L['be']}/({L['ti']}/100))",
            "tneed": (f"=IF({L['ti']}=\"\",\"\",IF(AND(ABS({L['lm']})>=0.1,ABS({L['be']})>=0.05,"
                      f"SIGN({L['lm']})=SIGN({L['be']})),100*{L['be']}/{L['lm']},\"\"))"),
            "teff": f"=IF({L['ti']}=\"\",\"\",{L['be']}-{L['tbe']})",
            "ov": f"=IF({L['ti']}=\"\",\"\",{L['lm']}-{L['tbe']})",
            "nvt": f"=({L['pd']}-{L['pr']})-{L['be']}*{L['par']}",
            "mv": f"=IF({L['ti']}=\"\",0,({L['lm']}-{L['be']})*{L['tc']})",
            "tv": f"=IF({L['ti']}=\"\",0,{L['be']}*({L['tc']}-{L['par']}))",
        }
        lv = (live.get(c["state"]) or {}).get(c["fips"]) or {}
        feed = {"fd": lv.get("D"), "fr": lv.get("R"), "fo": lv.get("O"), "fp": lv.get("pct_in")}
        man = keep.get(c["fips"], {})
        for i, (_, k, _, kind) in enumerate(CCOLS):
            cell = ws.cell(row=r, column=i + 1)
            if kind == "data":
                cell.value = vals.get(k)
            elif kind == "formula":
                cell.value = forms[k]
            elif kind == "feed":
                cell.value = feed.get(k)
                cell.fill = GREY
            else:
                cell.value = man.get(k)
                cell.fill = YELLOW
                cell.font = F(color="0000FF")
            if kind != "input":
                cell.font = F()
            if k in ("m24", "m20", "m22", "be", "lm", "pm", "tbe"):
                cell.number_format = MARGIN
            elif k in ("ti", "tneed"):
                cell.number_format = "0"
            elif k == "pace":
                cell.number_format = "0.00"
            elif k in ("sw2024", "sw", "vbe", "teff", "ov"):
                cell.number_format = SWING
            elif k in ("share", "fp", "ein", "black", "hisp", "ba"):
                cell.number_format = PCT if k != "share" else "0.0%"
            elif k in ("d24", "r24", "t24", "d20", "r20", "d22", "r22", "t22", "exp", "fd", "fr", "fo", "md", "mr", "mo",
                       "dc", "rc", "tc", "pv", "pd", "pr", "pop", "swv", "w", "mw", "par", "mv", "tv"):
                cell.number_format = NUM
            if k in ("nvt",):
                cell.number_format = NET
        row += 1
    ws.freeze_panes = "D2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(CCOLS))}{n_last}"
    dv = DataValidation(type="list", formula1='"Y"', allow_blank=True)
    ws.add_data_validation(dv)
    dv.add(f"{CL['mc']}2:{CL['mc']}{n_last}")
    for k in ("swv", "mw", "mv", "tv"):
        ws.column_dimensions[CL[k]].hidden = True
    ws.cell(row=1, column=CCOLS.index(next(x for x in CCOLS if x[1] == "be")) + 1).comment = Comment(
        "The D margin this county needs if every county swings by the same amount from 2024 and the state ends tied, "
        "with 2022's turnout pattern.", "County Watch")
    ws.cell(row=1, column=CCOLS.index(next(x for x in CCOLS if x[1] == "exp")) + 1).comment = Comment(
        "2022 midterm turnout x the state's turnout factor on the Dashboard x the 2024 two-party share. Used to judge "
        "when a county without a precinct count is finished.", "County Watch")
    for k, note in (("ti", "This county's two-party votes / its par votes. Par = its 2022-pattern votes x the state's projected "
                           "turnout pace. 100 = in line with the state; 110 = 10% bigger share of the state vote than in 2022."),
                    ("tbe", "Break-even / (turnout index / 100). A Democratic county with weak turnout needs a bigger margin; "
                            "a Republican county with strong turnout must be held closer."),
                    ("tneed", "The turnout index at which the live margin exactly hits the tie pace (blank when margin is too close "
                              "to even for turnout to matter much)."),
                    ("ov", "Live margin minus break-even at actual turnout: margin and turnout together, in margin points. "
                           "Positive helps D."),
                    ("nvt", "Projected D net votes minus what a tie needs from this county. Summed over every county it equals the "
                            "statewide projected net margin.")):
        ws.cell(row=1, column=CCOLS.index(next(x for x in CCOLS if x[1] == k)) + 1).comment = Comment(note, "County Watch")

    # ---------------- Types
    trow = _types_sheet(types, data, names, rng)

    # ---------------- Dashboard
    ws = dash
    ws["A1"] = "County Watch 2026: competitive Senate races"
    ws["A1"].font = F(bold=True, size=16)
    ws["A2"] = ("Projection = counted margin in fully reported counties + every other county's 2024 margin moved by the swing "
                "seen in counties of its type. Margins are two-party. Yellow cells are yours to change.")
    ws["A2"].font = F(italic=True, color="595959")
    ws["A3"] = f"Last bot update: {keep.get('updated') or 'not yet'}"
    ws["A3"].font = F(color="595959")
    hdr = ["State", "Senate race", "Democratic side", "Republican", "2024 margin", "Swing D needs vs 2024",
           "Turnout factor (vs 2022)", "Expected 2-party votes", "Counted 2-party votes", "% counted",
           "Counties complete", "of", "% of expected vote complete", "Swing in complete counties",
           "Ahead / behind needed swing", "Raw margin", "Projected margin", "Leader (projected)", "Call", "Confidence",
           "Early votes so far", "Early vote as % of 2022 turnout",
           "Turnout pace in complete counties (vs 2022)", "Turnout pace used for uncounted", "Projected turnout (vs 2022)",
           "Turnout index: D-leaning complete counties", "Turnout index: R-leaning complete counties", "Latest read (bot)"]
    for i, h in enumerate(hdr):
        ws.cell(row=DASH_ROW0 - 1, column=i + 1, value=h)
    _style_header(ws, DASH_ROW0 - 1, len(hdr), 48)
    widths = [6, 21, 18, 16, 10, 10, 10, 13, 13, 9, 9, 6, 11, 11, 11, 10, 11, 18, 24, 11, 12, 11, 10, 10, 10, 11, 11, 80]
    for i, w in enumerate(widths):
        ws.column_dimensions[get_column_letter(i + 1)].width = w
    factors = keep.get("factor", {})
    for si, st in enumerate(M.STATES):
        r = DASH_ROW0 + si
        S = lambda k, extra="": f"SUMIFS({rng(k)},{rng('state')},$A{r}{extra})"
        dn, rn = names[st]
        cells = [st, f"{M.STATES[st]} Senate" + (" (bellwether)" if st in M.BELLWETHER else ""), dn, rn,
                 f"=({S('d24')}-{S('r24')})/({S('d24')}+{S('r24')})",
                 f"=-{S('mw')}/{S('w')}",
                 factors.get(st, 1.0),
                 f"={S('exp')}",
                 f"={S('tc')}",
                 f"=IF(H{r}>0,I{r}/H{r},0)",
                 f"=COUNTIFS({rng('state')},$A{r},{rng('done')},1)",
                 f"=COUNTIF({rng('state')},$A{r})",
                 f"=IF(H{r}>0,{S('exp', ','+rng('done')+',1')}/H{r},0)",
                 f"=IF(K{r}>0,{S('swv')}/{S('tc', ','+rng('done')+',1')},\"\")",
                 f"=IF(N{r}=\"\",\"\",N{r}-F{r})",
                 f"=IF(I{r}>0,({S('dc')}-{S('rc')})/I{r},\"\")",
                 f"=IF(K{r}>0,({S('pd')}-{S('pr')})/({S('pd')}+{S('pr')}),\"\")",
                 f"=IF(Q{r}=\"\",\"\",IF(Q{r}>=0,C{r},D{r})&\" +\"&TEXT(ABS(Q{r})*100,\"0.0\"))",
                 _call_formula(f"Q{r}", f"C{r}", f"D{r}"),
                 f"=IF(K{r}=0,\"none\",IF(OR(M{r}<0.1,K{r}<5),\"low\",IF(M{r}<0.4,\"medium\",\"high\")))",
                 f"=IFERROR(INDEX('Early Vote'!$C$6:$C${5 + len(M.STATES)},MATCH($A{r},'Early Vote'!$A$6:$A${5 + len(M.STATES)},0)),\"\")",
                 f"=IFERROR(INDEX('Early Vote'!$E$6:$E${5 + len(M.STATES)},MATCH($A{r},'Early Vote'!$A$6:$A${5 + len(M.STATES)},0)),\"\")",
                 f"=IF(K{r}>0,{S('tc', ','+rng('done')+',1')}/{S('w', ','+rng('done')+',1')},\"\")",
                 f"=IF(K{r}>=3,W{r},G{r})",
                 f"={S('pv')}/{S('w')}",
                 (f"=IFERROR(100*{S('tc', ','+rng('done')+',1,'+rng('be')+',\">=0.1\"')}/"
                  f"{S('par', ','+rng('done')+',1,'+rng('be')+',\">=0.1\"')},\"\")"),
                 (f"=IFERROR(100*{S('tc', ','+rng('done')+',1,'+rng('be')+',\"<=-0.1\"')}/"
                  f"{S('par', ','+rng('done')+',1,'+rng('be')+',\"<=-0.1\"')},\"\")"),
                 (reads or {}).get(st, "")]
        for i, v in enumerate(cells):
            cell = ws.cell(row=r, column=i + 1, value=v)
            cell.font = F()
            cell.border = THIN
        ws.cell(row=r, column=7).fill = YELLOW
        ws.cell(row=r, column=7).font = F(color="0000FF")
        ws.cell(row=r, column=7).number_format = "0.00"
        for col in (5, 16, 17):
            ws.cell(row=r, column=col).number_format = MARGIN
        for col in (6, 14, 15):
            ws.cell(row=r, column=col).number_format = SWING
        for col in (8, 9, 21):
            ws.cell(row=r, column=col).number_format = NUM
        for col in (10, 13, 22):
            ws.cell(row=r, column=col).number_format = PCT
        for col in (23, 24, 25):
            ws.cell(row=r, column=col).number_format = "0.00"
        for col in (26, 27):
            ws.cell(row=r, column=col).number_format = "0"
        ws.cell(row=r, column=28).alignment = WRAP
        ws.cell(row=r, column=19).font = F(bold=True)
        ws.row_dimensions[r].height = 45
    ws.cell(row=DASH_ROW0 - 1, column=7).comment = Comment(
        "Expected 2026 turnout as a multiple of 2022. 1.10 means 10% more votes than 2022. Early vote running well "
        "ahead of 2022 is a reason to raise it; it mainly affects '% counted' and how much weight unreported counties get.",
        "County Watch")
    ws.cell(row=DASH_ROW0 - 1, column=6).comment = Comment(
        "Uniform swing from 2024 county margins that would tie the race, with 2022's (midterm) turnout pattern. "
        "Positive = toward D. Column E is the actual 2024 result, with 2024's turnout.", "County Watch")
    ws.cell(row=DASH_ROW0 - 1, column=25).comment = Comment(
        "Projected two-party votes / two-party votes at 2022 turnout. Every county's par (tie-pace) vote is its 2022-pattern "
        "vote x this number, so turnout indexes compare each county with the state as a whole.", "County Watch")
    ws.cell(row=DASH_ROW0 - 1, column=26).comment = Comment(
        "Complete counties where the Democratic side's break-even is D+10 or more: their votes / their par votes x 100. "
        "Above 100 = the Democratic base is a bigger share of the vote than a uniform-turnout tie assumes.", "County Watch")
    last_d = DASH_ROW0 + len(M.STATES) - 1
    _tier_colors(ws, f"S{DASH_ROW0}:S{last_d}", f"$Q{DASH_ROW0}",
                 [f"$Q{DASH_ROW0}>=0.05", f"$Q{DASH_ROW0}>=0.01", f"$Q{DASH_ROW0}>-0.01", f"$Q{DASH_ROW0}>-0.05", "TRUE"])
    _tier_colors(ws, f"Q{DASH_ROW0}:Q{last_d}", f"$Q{DASH_ROW0}",
                 [f"$Q{DASH_ROW0}>=0.05", f"$Q{DASH_ROW0}>=0.01", f"$Q{DASH_ROW0}>-0.01", f"$Q{DASH_ROW0}>-0.05", "TRUE"])
    ws.freeze_panes = f"B{DASH_ROW0}"
    note_r = DASH_ROW0 + len(M.STATES) + 1
    for i, line in enumerate([
        "How to read it",
        "• Before anything is fully counted there's no projection. The raw margin will mislead early on because states count one vote method first (see each state's read).",
        "• Confidence: low under 10% of the expected vote in complete counties or fewer than 5 counties; medium under 40%; high above.",
        "• Nebraska: the Democratic side is Dan Osborn (independent). His benchmarks use Harris's 2024 margins, which understate him in rural counties (he ran 14 points ahead of Harris statewide in 2024).",
        "• Alaska reports by state House district, not county, and most results come in after 1 AM ET.",
        "• Mississippi's 2022 column is the US House races (no statewide race that year), so its 2022 margins are rough.",
    ]):
        c = ws.cell(row=note_r + i, column=1, value=line)
        c.font = F(bold=(i == 0), color="404040")

    # ---------------- Key Counties
    _keys_sheet(keys, data, names, rowmap, trow)

    # ---------------- Benchmarks and first counties
    _bench_sheet(bench, data, names, rowmap)
    _first_sheet(first, data, names, rowmap)

    # ---------------- Early Vote
    _early_sheet(ev, uf, nc)

    # ---------------- Read Me
    _readme(readme)
    wb.save(path)
    return path


KEY_COLS = [("State", 6), ("County", 14), ("Why it matters", 44), ("What to watch", 44), ("Type", 16),
            ("Margin 2024", 9), ("Swing 20→24", 9), ("Margin 2022", 9), ("Share of state vote 2024", 9),
            ("Expected share (2022 turnout)", 9), ("D break-even", 9), ("Live margin", 9), ("Est. % in", 7),
            ("Live vs break-even", 9), ("Margin status", 18), ("Turnout index (100 = state pace)", 9),
            ("Break-even at actual turnout", 10), ("Turnout index needed at live margin", 10), ("Turnout effect (pts)", 9),
            ("Margin + turnout vs tie pace (pts)", 10), ("Net votes vs tie pace", 11), ("Overall read", 20), ("FIPS", 7)]
KL = {h: get_column_letter(i + 1) for i, (h, _) in enumerate(KEY_COLS)}


def _keys_sheet(ws, data, names, rowmap, trow):
    ws["A1"] = "Key counties: why they matter and how they're doing, on margin and turnout"
    ws["A1"].font = F(bold=True, size=14)
    ws["A2"] = ("Break-even = the margin the Democratic side needs in that county for a statewide tie if every county swings "
                "equally from 2024 and turnout follows 2022's pattern. Turnout index = the county's votes vs its par (2022 "
                "pattern x the state's pace): 100 = in line with the state. Weak turnout in a Democratic county raises the "
                "margin it needs; strong turnout in a Republican county means Democrats must hold it closer. 'Margin + "
                "turnout' combines both in margin points; turnout columns fill in once a county is fully counted. The last "
                "row in each state is all its small-town and rural counties together.")
    ws["A2"].font = F(italic=True, color="595959")
    ws["A2"].alignment = WRAP
    ws.merge_cells(f"A2:{KL['Overall read']}2")
    ws.row_dimensions[2].height = 44
    for i, (h, w) in enumerate(KEY_COLS):
        ws.cell(row=4, column=i + 1, value=h)
        ws.column_dimensions[get_column_letter(i + 1)].width = w
    _style_header(ws, 4, len(KEY_COLS), 66)
    K = KL
    r = 5
    for st in M.STATES:
        kc = [c for c in data[st] if c["key"]]
        if not kc:
            continue
        dn, rn = _surname(names[st][0]), _surname(names[st][1])
        ws.cell(row=r, column=1, value=f"{M.STATES[st]}  ·  2024: {M.fmt(M.state_m24(data[st]))} "
                                       f"({M.fmt(M.base(st))} with 2022's turnout pattern)  ·  {names[st][0]} vs {names[st][1]}")
        for col in range(1, len(KEY_COLS) + 1):
            ws.cell(row=r, column=col).fill = BAND
            ws.cell(row=r, column=col).font = F(bold=True)
        r += 1
        for c in sorted(kc, key=lambda c: -c["t24"]):
            cr = rowmap[c["fips"]]
            look = lambda k: f"=Counties!{CL[k]}{cr}"
            ov = f"{K['Margin + turnout vs tie pace (pts)']}{r}"
            vals = {"State": st, "County": c["name"], "Why it matters": c["why"], "What to watch": c["watch"],
                    "Type": c["type"], "Margin 2024": look("m24"), "Swing 20→24": look("sw2024"), "Margin 2022": look("m22"),
                    "Share of state vote 2024": look("share"),
                    "Expected share (2022 turnout)": f"=Counties!{CL['w']}{cr}/SUMIFS(Counties!${CL['w']}:${CL['w']},Counties!$A:$A,\"{st}\")",
                    "D break-even": look("be"), "Live margin": look("lm"), "Est. % in": look("ein"),
                    "Live vs break-even": look("vbe"),
                    "Margin status": (f'=IF({K["Live margin"]}{r}="","not in yet",' + _tier_text(f"{K['Live vs break-even']}{r}", dn, rn)
                                      + f'&IF(Counties!{CL["done"]}{cr}=1,""," (partial)"))'),
                    "Turnout index (100 = state pace)": look("ti"), "Break-even at actual turnout": look("tbe"),
                    "Turnout index needed at live margin": look("tneed"), "Turnout effect (pts)": look("teff"),
                    "Margin + turnout vs tie pace (pts)": look("ov"),
                    "Net votes vs tie pace": f'=IF({ov}="","",{ov}*Counties!{CL["tc"]}{cr})',
                    "Overall read": (f'=IF({ov}="",IF({K["Live margin"]}{r}="","not in yet","waiting for the full count"),'
                                     + _tier_text(ov, dn, rn) + ")"),
                    "FIPS": c["fips"]}
            _key_row(ws, r, vals)
            r += 1
        tr = trow.get((st, "Small town & rural"))
        rural = [c for c in data[st] if c["type"] == "Small town & rural"]
        if tr and rural and sum(c["share22"] for c in rural) >= 0.10:
            T = lambda h: f"=Types!{TL[h]}{tr}"
            ov = f"{K['Margin + turnout vs tie pace (pts)']}{r}"
            vals = {"State": st, "County": f"All rural ({len(rural)})",
                    "Why it matters": "Every small-town and rural county together: the Republican base's turnout test.",
                    "What to watch": "Turnout index from its finished counties. Above 100 means the rural base is a bigger share "
                                     "of the vote than a tie assumes; the margin rarely moves much.",
                    "Type": "Small town & rural", "Margin 2024": T("2024 margin (2022 pattern)"),
                    "Expected share (2022 turnout)": T("Share of state vote (2022 pattern)"),
                    "D break-even": T("Type margin if state ends tie"), "Live margin": T("Projected type margin"),
                    "Est. % in": f"=IFERROR(Types!E{tr}/Types!D{tr},\"\")",
                    "Live vs break-even": f'=IF({K["Live margin"]}{r}="","",{K["Live margin"]}{r}-{K["D break-even"]}{r})',
                    "Margin status": (f'=IF(Types!F{tr}=0,"not in yet",' + _tier_text(f"{K['Live vs break-even']}{r}", dn, rn)
                                      + '&" (projected)")'),
                    "Turnout index (100 = state pace)": T("Turnout index (complete counties)"),
                    "Break-even at actual turnout": T("Break-even at that turnout"),
                    "Turnout effect (pts)": f'=IF({K["Break-even at actual turnout"]}{r}="","",{K["D break-even"]}{r}-{K["Break-even at actual turnout"]}{r})',
                    "Margin + turnout vs tie pace (pts)": T("Margin + turnout vs tie pace (pts)"),
                    "Net votes vs tie pace": T("Net votes vs tie pace"),
                    "Overall read": f'=IF({ov}="","not in yet",' + _tier_text(ov, dn, rn) + '&" (finished counties)")'}
            _key_row(ws, r, vals, italic=True)
            ws.cell(row=r, column=7).value = None
            r += 1
    m = f"${KL['Live vs break-even']}5"
    o = f"${KL['Margin + turnout vs tie pace (pts)']}5"
    _tier_colors(ws, f"{KL['Margin status']}5:{KL['Margin status']}{r}", m, TIER_CONDS(m))
    _tier_colors(ws, f"{KL['Live margin']}5:{KL['Live margin']}{r}", m, TIER_CONDS(m))
    _tier_colors(ws, f"{KL['Overall read']}5:{KL['Overall read']}{r}", o, TIER_CONDS(o))
    t = f"${KL['Turnout effect (pts)']}5"
    _tier_colors(ws, f"{KL['Turnout index (100 = state pace)']}5:{KL['Turnout index (100 = state pace)']}{r}", t, TIER_CONDS(t))
    ws.freeze_panes = "C5"
    ws.column_dimensions[KL["FIPS"]].hidden = True


def _key_row(ws, r, vals, italic=False):
    for i, (h, _) in enumerate(KEY_COLS):
        cell = ws.cell(row=r, column=i + 1, value=vals.get(h))
        cell.font = F(italic=italic)
        cell.alignment = WRAP
        cell.border = THIN
    for h in ("Margin 2024", "Margin 2022", "D break-even", "Live margin", "Break-even at actual turnout"):
        ws[f"{KL[h]}{r}"].number_format = MARGIN
    for h in ("Swing 20→24", "Live vs break-even", "Turnout effect (pts)", "Margin + turnout vs tie pace (pts)"):
        ws[f"{KL[h]}{r}"].number_format = SWING
    for h in ("Share of state vote 2024", "Expected share (2022 turnout)"):
        ws[f"{KL[h]}{r}"].number_format = "0.0%"
    ws[f"{KL['Est. % in']}{r}"].number_format = PCT
    for h in ("Turnout index (100 = state pace)", "Turnout index needed at live margin"):
        ws[f"{KL[h]}{r}"].number_format = "0"
    ws[f"{KL['Net votes vs tie pace']}{r}"].number_format = NET
    ws.row_dimensions[r].height = 54



# ------------------------------------------------------------------ colored callers
TIER_FILLS = ["1F4E9A", "9DC3E6", "E7E6E6", "F4B6B0", "B4231B"]     # great D, D on track, toss-up, R on track, great R
TIER_FONT = ["FFFFFF", "1F1F1F", "1F1F1F", "1F1F1F", "FFFFFF"]


def _call_formula(m: str, dname: str, rname: str) -> str:
    """'Great for Ossoff' / 'Ossoff on track' / 'Toss-up' / 'Collins on track' / 'Great for Collins'."""
    def sur(ref):
        return f'TRIM(RIGHT(SUBSTITUTE({ref}," ",REPT(" ",50)),50))'
    return (f'=IF({m}="","",IF({m}>=0.05,"Great for "&{sur(dname)},IF({m}>=0.01,{sur(dname)}&" on track",'
            f'IF({m}>-0.01,"Toss-up",IF({m}>-0.05,{sur(rname)}&" on track","Great for "&{sur(rname)})))))')


def _tier_text(diff: str, dn: str, rn: str) -> str:
    """Excel text for a margin measured against break-even (0 = tie)."""
    return (f'IF({diff}>=0.05,"Great for {dn}",IF({diff}>=0.01,"{dn} on track",IF({diff}>-0.01,"Toss-up",'
            f'IF({diff}>-0.05,"{rn} on track","Great for {rn}"))))')


TIER_CONDS = lambda d: [f"{d}>=0.05", f"{d}>=0.01", f"{d}>-0.01", f"{d}>-0.05", "TRUE"]


def _tier_colors(ws, cells: str, value_ref: str, conds: list[str]):
    """Five-step blue-to-red fill on `cells`, one rule per tier, first match wins. conds are Excel formulas
    written for the first row of the range (relative rows); blank value_ref = no color."""
    from openpyxl.formatting.rule import FormulaRule
    for i, cond in enumerate(conds):
        ws.conditional_formatting.add(cells, FormulaRule(
            formula=[f'AND({value_ref}<>"",{cond})'], stopIfTrue=True,
            fill=PatternFill("solid", fgColor=TIER_FILLS[i], bgColor=TIER_FILLS[i]),
            font=Font(name=FONT, color=TIER_FONT[i], bold=True)))


# ------------------------------------------------------------------ benchmark helpers (Excel text)
SCEN = [(-0.05, "R+5"), (-0.01, "R+1"), (0.0, "tie"), (0.01, "D+1"), (0.05, "D+5")]
NET = '[Blue]"D +"#,##0;[Red]"R +"#,##0;"even"'


def _mt(ref: str) -> str:
    """Excel text for a margin cell: D+12.3 / R+4.0."""
    return f'IF({ref}>=0,"D+","R+")&TEXT(ABS({ref})*100,"0.0")'


def _nt(ref: str) -> str:
    return f'IF({ref}>=0,"D +","R +")&TEXT(ABS({ref}),"#,##0")'


def _surname(n: str) -> str:
    return n.split()[-1] if n else n


def _bench_sheet(ws, data, names, rowmap):
    ws["A1"] = "County benchmarks: what each key county needs to show when it finishes counting"
    ws["A1"].font = F(bold=True, size=14)
    ws["A2"] = ("Each column is the county margin that matches a statewide result, if every county moves the same amount "
                "from 2024 and turnout follows 2022's pattern. Net votes = that margin x the county's par vote (its 2022-pattern "
                "two-party vote x the state's projected turnout pace, so it rescales on the night). Read a county's FINAL "
                "number against these; partial counts are skewed by vote method (see First Counties). Once a county is "
                "fully counted, its turnout index adjusts the break-even and the reading uses margin and turnout together.")
    ws["A2"].font = F(italic=True, color="595959")
    ws["A2"].alignment = WRAP
    ws.merge_cells("A2:Y2")
    ws.row_dimensions[2].height = 42
    hdr = ["State", "County", "Type", "Par 2-party votes (tie pace)", "Margin 2024", "Margin 2022"] + \
          [f"Margin if state ends {lab}" for _, lab in SCEN] + [f"Net votes if state ends {lab}" for _, lab in SCEN] + \
          ["D votes needed for a tie", "Live margin", "Live net votes", "Est. % in", "Turnout index (100 = state pace)",
           "Break-even at actual turnout", "Margin + turnout vs tie pace (pts)", "Reading", "What to look for"]
    for i, h in enumerate(hdr):
        ws.cell(row=4, column=i + 1, value=h)
    _style_header(ws, 4, len(hdr), 54)
    widths = [6, 13, 16, 11, 9, 9] + [9] * 5 + [11] * 5 + [11, 9, 11, 8, 9, 10, 10, 26, 100]
    for i, w in enumerate(widths):
        ws.column_dimensions[get_column_letter(i + 1)].width = w
    r = 5
    for si, st in enumerate(M.STATES):
        kc = sorted([c for c in data[st] if c["key"]], key=lambda c: -c["t24"])
        if not kc:
            continue
        dn, rn = (_surname(n) for n in names[st])
        sm = M.state_m24(data[st])
        head = (f"{M.STATES[st]}: {names[st][0]} vs {names[st][1]} · 2024 statewide {M.fmt(sm)} "
                f"({M.fmt(M.base(st))} with 2022's turnout pattern)")
        if st == "NE":
            head += " · Osborn's columns use Harris's 2024 margins, which understate him in rural counties"
        ws.cell(row=r, column=1, value=head)
        for col in range(1, len(hdr) + 1):
            ws.cell(row=r, column=col).fill = BAND
            ws.cell(row=r, column=col).font = F(bold=True)
        r += 1
        drow = DASH_ROW0 + si
        for c in kc:
            cr = rowmap[c["fips"]]
            C_ = lambda k: f"Counties!{CL[k]}{cr}"
            vals = [st, c["name"], c["type"], f"={C_('par')}", f"={C_('m24')}", f"={C_('m22')}"]
            for x, _ in SCEN:      # county margin = its 2024 margin moved by (target statewide - baseline statewide)
                vals.append(f"=MAX(-1,MIN(1,{C_('m24')}+{x}+Dashboard!${D_NEED}${drow}))")
            for j in range(5):
                vals.append(f"={get_column_letter(7 + j)}{r}*D{r}")
            vals.append(f"=D{r}*(1+I{r})/2")
            vals += [f"={C_('lm')}", f'=IF({C_("lm")}="","",{C_("lm")}*{C_("tc")})', f"={C_('ein')}",
                     f"={C_('ti')}", f"={C_('tbe')}", f"={C_('ov')}"]
            lm = f"R{r}"
            vals.append(
                f'=IF({lm}="","not in yet",IF(W{r}<>"",' + _tier_text(f"W{r}", dn, rn) + '&" (margin + turnout)",'
                + _tier_text(f"({lm}-I{r})", dn, rn)
                + f'&IF(Counties!{CL["done"]}{cr}=1,""," (partial count)")))')
            vals.append(
                f'="Break-even "&{_mt(f"I{r}")}&" ("&{_nt(f"N{r}")}&" net, about "&TEXT(Q{r},"#,##0")&" {dn} votes). '
                f'{dn} at "&{_mt(f"K{r}")}&" or better = great night; "&{_mt(f"J{r}")}&" = on track to win. '
                f'{rn} holding it to "&{_mt(f"H{r}")}&" = {rn} on track; "&{_mt(f"G{r}")}&" or worse for {dn} = great for {rn}. '
                f'Turnout: par is about "&TEXT(D{r},"#,##0")&" two-party votes ("&TEXT(Counties!{CL["w"]}{cr}/SUMIFS(Counties!${CL["w"]}:${CL["w"]},Counties!$A:$A,"{st}"),"0.0%")'
                f'&" of the state). At 95% of par the break-even moves to "&{_mt(f"(I{r}/0.95)")}&"; at 105%, to "&{_mt(f"(I{r}/1.05)")}&"."')
            for j, v in enumerate(vals):
                cell = ws.cell(row=r, column=j + 1, value=v)
                cell.font = F()
                cell.border = THIN
            for col in [5, 6, 7, 8, 9, 10, 11, 17]:
                ws.cell(row=r, column=col).number_format = MARGIN
            for col in [12, 13, 14, 15, 16, 18]:
                ws.cell(row=r, column=col).number_format = NET
            ws.cell(row=r, column=4).number_format = NUM
            ws.cell(row=r, column=17 - 1).number_format = NET
            ws.cell(row=r, column=17).number_format = NUM          # D votes needed
            ws.cell(row=r, column=18).number_format = MARGIN        # live margin
            ws.cell(row=r, column=19).number_format = NET           # live net
            ws.cell(row=r, column=20).number_format = PCT
            ws.cell(row=r, column=21).number_format = "0"
            ws.cell(row=r, column=22).number_format = MARGIN
            ws.cell(row=r, column=23).number_format = SWING
            ws.cell(row=r, column=25).alignment = WRAP
            ws.cell(row=r, column=9).fill = PatternFill("solid", fgColor="F2F2F2")
            ws.row_dimensions[r].height = 58
            r += 1
    sc = 'IF($W5<>"",$W5,$R5-$I5)'
    _tier_colors(ws, f"X5:X{r}", "$R5", TIER_CONDS(sc))
    _tier_colors(ws, f"R5:R{r}", "$R5", TIER_CONDS(sc))
    ws.freeze_panes = "C5"


TYPE_COLS = ["Key", "State", "Type", "Counties", "Complete", "Complete 2-party votes", "Type swing", "Swing used",
             "Complete 2022-pattern votes", "Turnout pace observed (vs 2022)", "Turnout pace used",
             "Share of state vote (2022 pattern)", "2024 margin (2022 pattern)"] + \
            [f"Type margin if state ends {lab}" for _, lab in [(-0.05, "R+5"), (-0.01, "R+1"), (0.0, "tie"), (0.01, "D+1"), (0.05, "D+5")]] + \
            ["Projected type margin", "Swing vs what D needs", "Reading", "Turnout index (complete counties)",
             "Break-even at that turnout", "Margin + turnout vs tie pace (pts)", "Net votes vs tie pace", "What it needs to look like"]
TL = {h: get_column_letter(i + 1) for i, h in enumerate(TYPE_COLS)}


def _types_sheet(ws, data, names, rng):
    ws["A1"] = ("County types: what each kind of county needs to look like, and how it's doing. Benchmarks assume every county "
                "moves the same amount from 2024 with 2022's turnout pattern. Unfinished counties are projected with their "
                "type's swing and turnout pace (or the state's, when none of that type is in).")
    ws["A1"].font = F(italic=True)
    ws["A1"].alignment = WRAP
    ws.merge_cells(f"A1:{TL['What it needs to look like']}1")
    ws.row_dimensions[1].height = 30
    for i, h in enumerate(TYPE_COLS):
        ws.cell(row=TYPE_ROW0 - 1, column=i + 1, value=h)
    _style_header(ws, TYPE_ROW0 - 1, len(TYPE_COLS), 66)
    r = TYPE_ROW0
    T = TL
    for si, st in enumerate(M.STATES):
        present = [t for t in M.TYPES if any(c["type"] == t for c in data[st])]
        drow = DASH_ROW0 + si
        dn, rn = (_surname(n) for n in names[st])
        need, pace_st, pace_proj = f"Dashboard!${D_NEED}${drow}", f"Dashboard!${D_PACE_USED}${drow}", f"Dashboard!${D_PACE}${drow}"
        S = lambda k, extra="": f"SUMIFS({rng(k)},{rng('state')},$B{r},{rng('type')},$C{r}{extra})"
        done = f",{rng('done')},1"
        for t in present:
            scen = {lab: f"={T['2024 margin (2022 pattern)']}{r}+{x}+{need}"
                    for x, lab in [(-0.05, "R+5"), (-0.01, "R+1"), (0.0, "tie"), (0.01, "D+1"), (0.05, "D+5")]}
            tie = f"{T['Type margin if state ends tie']}{r}"
            ti = f"{T['Turnout index (complete counties)']}{r}"
            vals = {
                "Key": f"{st}|{t}", "State": st, "Type": t,
                "Counties": f"=COUNTIFS({rng('state')},B{r},{rng('type')},C{r})",
                "Complete": f"=COUNTIFS({rng('state')},B{r},{rng('type')},C{r},{rng('done')},1)",
                "Complete 2-party votes": f"={S('tc', done)}",
                "Type swing": f"=IF(F{r}>0,{S('swv')}/F{r},\"\")",
                "Swing used": f"=IF(G{r}<>\"\",G{r},IF(Dashboard!$N${drow}<>\"\",Dashboard!$N${drow},0))",
                "Complete 2022-pattern votes": f"={S('w', done)}",
                "Turnout pace observed (vs 2022)": f"=IF(I{r}>0,F{r}/I{r},\"\")",
                "Turnout pace used": f"=IF(J{r}=\"\",{pace_st},{pace_st}+(J{r}-{pace_st})*E{r}/(E{r}+{M.PACE_K}))",
                "Share of state vote (2022 pattern)": f"={S('w')}/SUMIFS({rng('w')},{rng('state')},$B{r})",
                "2024 margin (2022 pattern)": f"={S('mw')}/{S('w')}",
                **{f"Type margin if state ends {lab}": v for lab, v in scen.items()},
                "Projected type margin": f"=IF(Dashboard!${D_DONE}${drow}>0,{T['2024 margin (2022 pattern)']}{r}+H{r},\"\")",
                "Swing vs what D needs": f"=IF(G{r}=\"\",\"\",G{r}-{need})",
                "Reading": (f"=IF(F{r}=0,\"no county of this type finished\","
                            + _tier_text(f"{T['Margin + turnout vs tie pace (pts)']}{r}", dn, rn) + ")"),
                "Turnout index (complete counties)": f"=IF(I{r}>0,100*F{r}/(I{r}*{pace_proj}),\"\")",
                "Break-even at that turnout": f"=IF({ti}=\"\",\"\",{tie}/({ti}/100))",
                "Margin + turnout vs tie pace (pts)": f"=IF(F{r}>0,({S('mv', done)}+{S('tv', done)})/F{r},\"\")",
                "Net votes vs tie pace": f"={S('nvt')}",
                "What it needs to look like": (
                    f'="{t}: "&TEXT({T["Share of state vote (2022 pattern)"]}{r},"0.0%")&" of the expected vote. Tie: "&{_mt(tie)}'
                    f'&". {dn} on track at "&{_mt(T["Type margin if state ends D+1"] + str(r))}&", great at "&{_mt(T["Type margin if state ends D+5"] + str(r))}'
                    f'&"; {rn} on track at "&{_mt(T["Type margin if state ends R+1"] + str(r))}&", great at "&{_mt(T["Type margin if state ends R+5"] + str(r))}'
                    f'&". Turnout: if these counties vote at 95% of the state pace, the break-even moves to "&{_mt(f"({tie}/0.95)")}'
                    f'&"; at 105%, "&{_mt(f"({tie}/1.05)")}&"."'),
            }
            for i, h in enumerate(TYPE_COLS):
                cell = ws.cell(row=r, column=i + 1, value=vals[h])
                cell.font = F()
                cell.border = THIN
            for h in ("Type swing", "Swing used", "Swing vs what D needs", "Margin + turnout vs tie pace (pts)"):
                ws[f"{T[h]}{r}"].number_format = SWING
            for h in ("Complete 2-party votes", "Complete 2022-pattern votes"):
                ws[f"{T[h]}{r}"].number_format = NUM
            for h in ("Turnout pace observed (vs 2022)", "Turnout pace used"):
                ws[f"{T[h]}{r}"].number_format = "0.00"
            ws[f"{T['Share of state vote (2022 pattern)']}{r}"].number_format = "0.0%"
            for h in ["2024 margin (2022 pattern)", "Projected type margin", "Break-even at that turnout"] + \
                     [x for x in TYPE_COLS if x.startswith("Type margin if")]:
                ws[f"{T[h]}{r}"].number_format = MARGIN
            ws[f"{T['Turnout index (complete counties)']}{r}"].number_format = "0"
            ws[f"{T['Net votes vs tie pace']}{r}"].number_format = NET
            ws[f"{T['Type margin if state ends tie']}{r}"].fill = PatternFill("solid", fgColor="F2F2F2")
            ws[f"{T['What it needs to look like']}{r}"].alignment = WRAP
            ws.row_dimensions[r].height = 40
            r += 1
    last = r - 1
    ov = f"${TL['Margin + turnout vs tie pace (pts)']}{TYPE_ROW0}"
    for col in ("Reading", "Projected type margin"):
        _tier_colors(ws, f"{TL[col]}{TYPE_ROW0}:{TL[col]}{last}", ov, TIER_CONDS(ov))
    widths = {"Key": 26, "State": 6, "Type": 20, "Reading": 20, "What it needs to look like": 95}
    for h, col in TL.items():
        ws.column_dimensions[col].width = widths.get(h, 10)
    for h in ("Key", "Complete 2022-pattern votes"):
        ws.column_dimensions[TL[h]].hidden = True

    # National read: every fully counted county in every state, pooled
    c0 = len(TYPE_COLS) + 2
    L_ = lambda i: get_column_letter(c0 + i)
    ws.cell(row=TYPE_ROW0 - 2, column=c0, value="National read (all states pooled)").font = F(bold=True)
    for i, h in enumerate(["County type", "Complete counties", "Complete 2-party votes", "Swing vs 2024", "Turnout vs 2022"]):
        cell = ws.cell(row=TYPE_ROW0 - 1, column=c0 + i, value=h)
        cell.font, cell.fill = F(bold=True, color="FFFFFF"), HEAD
        cell.alignment = Alignment(wrap_text=True, vertical="center", horizontal="center")
    rr = TYPE_ROW0
    for t in ["All counties"] + M.TYPES:
        cond = "" if t == "All counties" else f",{rng('type')},{L_(0)}{rr}"
        ws.cell(row=rr, column=c0, value=t)
        ws.cell(row=rr, column=c0 + 1, value=f"=COUNTIFS({rng('done')},1{cond})")
        ws.cell(row=rr, column=c0 + 2, value=f"=SUMIFS({rng('tc')},{rng('done')},1{cond})")
        ws.cell(row=rr, column=c0 + 3, value=f"=IF({L_(2)}{rr}>0,SUMIFS({rng('swv')},{rng('done')},1{cond})/{L_(2)}{rr},\"\")")
        ws.cell(row=rr, column=c0 + 4, value=f"=IF({L_(1)}{rr}>0,SUMIFS({rng('tc')},{rng('done')},1{cond})/SUMIFS({rng('w')},{rng('done')},1{cond}),\"\")")
        for c in range(c0, c0 + 5):
            ws.cell(row=rr, column=c).font = F(bold=(t == "All counties"))
        ws.cell(row=rr, column=c0 + 2).number_format = NUM
        ws.cell(row=rr, column=c0 + 3).number_format = SWING
        ws.cell(row=rr, column=c0 + 4).number_format = PCT
        rr += 1
    _tier_colors(ws, f"{L_(3)}{TYPE_ROW0}:{L_(3)}{rr}", f"${L_(3)}{TYPE_ROW0}", TIER_CONDS(f"${L_(3)}{TYPE_ROW0}"))
    for i, w in enumerate((24, 11, 14, 12, 11)):
        ws.column_dimensions[L_(i)].width = w
    ws.freeze_panes = f"D{TYPE_ROW0}"
    return {(ws.cell(row=i, column=2).value, ws.cell(row=i, column=3).value): i for i in range(TYPE_ROW0, last + 1)}



def _first_sheet(ws, data, names, rowmap):
    import night as N
    cfg = json.loads((M.CW_DIR / "first_counties.json").read_text(encoding="utf-8"))
    ws["A1"] = "First counties: how to read the first hour, state by state"
    ws["A1"].font = F(bold=True, size=14)
    ws["A2"] = ("The first numbers are usually one vote method (early or mail), which leans one way. 'First-batch "
                "benchmark' = the county's break-even plus how much more Democratic its early/mail vote was than its "
                "total in 2022. A first batch above that pace is a good sign for the Democratic side even if the raw "
                "number looks lopsided. In North Carolina it's also shifted by how the 2026 early electorate differs "
                "from 2022's (party and race mix; see Early Vote).")
    ws["A2"].font = F(italic=True, color="595959")
    ws["A2"].alignment = WRAP
    ws.merge_cells("A2:L2")
    ws.row_dimensions[2].height = 54
    hdr = ["State", "County", "Polls close", "Break-even (final)", "2022 early/mail vs total", "2026 early electorate shift (NC)",
           "First-batch benchmark", "Live margin", "Est. % in", "First-batch read", "What to look for", "Source"]
    for i, h in enumerate(hdr):
        ws.cell(row=4, column=i + 1, value=h)
    _style_header(ws, 4, len(hdr), 54)
    for i, w in enumerate([6, 13, 16, 10, 11, 11, 11, 9, 8, 26, 90, 24]):
        ws.column_dimensions[get_column_letter(i + 1)].width = w
    r = 5
    for ent in cfg["states"]:
        st = ent["state"]
        dn, rn = (_surname(n) for n in names[st])
        close = N.fmt(N.first_close(st), ZoneInfo("America/Chicago")) if st in N.CLOSINGS else ""
        ws.cell(row=r, column=1, value=f"{M.STATES[st]} · polls close {close} · {ent['first']}")
        ws.cell(row=r, column=1).alignment = Alignment(wrap_text=True, vertical="top")
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=11)
        ws.cell(row=r, column=12, value=ent.get("source") or "")
        for col in range(1, 13):
            ws.cell(row=r, column=col).fill = BAND
            ws.cell(row=r, column=col).font = F(bold=(col == 1))
        ws.row_dimensions[r].height = 44
        note = M.mode_note(st)
        r += 1
        if note:
            ws.cell(row=r, column=1, value=note).font = F(italic=True, color="595959")
            ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=11)
            r += 1
        rows = {c["name"]: c for c in data[st]}
        for name in ent["counties"]:
            c = rows.get(name)
            if not c:
                continue
            cr = rowmap[c["fips"]]
            C_ = lambda k: f"Counties!{CL[k]}{cr}"
            skew = None
            m = c["modes22"]
            if m and c["m22"] is not None:
                ed, er = m["early"][0] + m.get("mail", (0, 0))[0], m["early"][1] + m.get("mail", (0, 0))[1]
                em = M.m2(ed, er)
                skew = (em - c["m22"]) if em is not None else None
            shift = (f"=IFERROR(INDEX('Early Vote'!$Q:$Q,MATCH(\"{c['name']}\",'Early Vote'!$A:$A,0)),0)"
                     if st == "NC" else None)
            bench = f"=D{r}+IF(E{r}=\"\",0,E{r})+IF(F{r}=\"\",0,F{r})"
            read = (f'=IF(H{r}="","not in yet",' + _tier_text(f"(H{r}-M{r})", dn, rn)
                    + f'&IF(Counties!{CL["done"]}{cr}=1," (final)"," (early batch)"))')
            if skew is not None:
                look = (f'="{c["name"]}\'s early/mail vote ran "&{_mt(f"E{r}")}&" vs its total in 2022. So a first batch around "'
                        f'&{_mt(f"G{r}")}&" is only break-even for {dn}. Well above it is a good sign; below it is a warning, '
                        f'because Election Day votes will pull it toward {rn}."')
            else:
                look = (f'="No 2022 vote-method split for this county. The final break-even is "&{_mt(f"D{r}")}&". '
                        + ("The first Texas batch is most of the vote, so it should land within a few points of the final."
                           if st == "TX" else
                           "New Hampshire has almost no early voting, so read partial counts by which towns are in, not vote method."
                           if st == "NH" else
                           "If the first batch is early/mail votes, expect it to overstate the Democratic side.")
                        + '"')
            vals = [st, c["name"], close, f"={C_('be')}", skew, shift, bench, f"={C_('lm')}", f"={C_('ein')}", read, look,
                    ent.get("source") and "" or "", f"=IF(Counties!{CL['done']}{cr}=1,D{r},G{r})"]
            for j, v in enumerate(vals):
                cell = ws.cell(row=r, column=j + 1, value=v)
                cell.font = F()
                cell.border = THIN
            for col in (4, 7, 8):
                ws.cell(row=r, column=col).number_format = MARGIN
            for col in (5, 6):
                ws.cell(row=r, column=col).number_format = SWING
            ws.cell(row=r, column=9).number_format = PCT
            ws.cell(row=r, column=11).alignment = WRAP
            ws.row_dimensions[r].height = 42
            r += 1
        r += 1
    _tier_colors(ws, f"J5:J{r}", "$H5", TIER_CONDS("($H5-$M5)"))
    _tier_colors(ws, f"H5:H{r}", "$H5", TIER_CONDS("($H5-$M5)"))
    ws.column_dimensions["M"].hidden = True
    ws.freeze_panes = "C5"


def _early_sheet(ws, uf, nc):
    uf = uf or {}
    ws["A1"] = "Early vote"
    ws["A1"].font = F(bold=True, size=14)
    ws["A2"] = ("Statewide: UF Election Lab early vote tracker (Michael McDonald), CC BY-NC-ND 4.0, personal non-commercial use. "
                "Party registration is not vote choice. New Hampshire doesn't report early vote.")
    ws["A2"].font = F(italic=True, color="595959")
    ws["A3"] = "Updated:"
    ws["B3"] = (nc or {}).get("uf_fetched") or ""
    hdr = ["State", "As of", "Early votes so far", "2022 total turnout", "% of 2022 turnout", "In person", "Mail",
           "Reg. Dem", "Reg. Rep", "Reg. other", "Dem − Rep share", "Black share", "Ages 18-25 share", "Over 65 share",
           "Source"]
    for i, h in enumerate(hdr):
        ws.cell(row=5, column=i + 1, value=h)
    _style_header(ws, 5, len(hdr))
    for i, w in enumerate([6, 11, 13, 13, 10, 12, 12, 11, 11, 11, 11, 9, 9, 9, 50]):
        ws.column_dimensions[get_column_letter(i + 1)].width = w
    for i, st in enumerate(M.STATES):
        r = 6 + i
        u = uf.get(st) or {}
        age = u.get("age") or [0, 0, 0, 0]
        vals = [st, u.get("updated"), u.get("voted"), u.get("turn22"), f"=IFERROR(C{r}/D{r},\"\")",
                u.get("inperson") or None, u.get("mail") or None, u.get("dem") or None, u.get("rep") or None,
                u.get("none") or None,
                f"=IFERROR(IF(H{r}+I{r}+J{r}>0,(H{r}-I{r})/(H{r}+I{r}+J{r}),\"\"),\"\")",
                (u.get("black") / u["voted"]) if u.get("black") and u.get("voted") else None,
                (age[0] / sum(age)) if sum(age) else None, (age[3] / sum(age)) if sum(age) else None,
                u.get("source")]
        for j, v in enumerate(vals):
            cell = ws.cell(row=r, column=j + 1, value=v)
            cell.font = F()
            cell.border = THIN
        for col in (3, 4, 6, 7, 8, 9, 10):
            ws.cell(row=r, column=col).number_format = NUM
        for col in (5, 12, 13, 14):
            ws.cell(row=r, column=col).number_format = PCT
        ws.cell(row=r, column=11).number_format = '[Blue]"D+"0%;[Red]"R+"0%;"even"'

    # NC county detail
    top = 6 + len(M.STATES) + 3
    ws.cell(row=top, column=1, value="North Carolina by county: 2026 so far vs 2022 at the same number of days out").font = F(bold=True, size=12)
    model = E.load_json("nc_ev_model.json") or {"shares": {g: 0.5 for g in E.NC_GROUPS}}
    ws.cell(row=top + 1, column=1, value=(
        "Modeled lean = share of each group's early votes assumed to go to the Democrat (yellow cells, fitted to 2022 county "
        f"results: R² {model.get('r2', '?')}, typical county miss {model.get('mae_pts', '?')} pts). It measures who has "
        "voted, not how they voted. If unaffiliated voters are more Republican than in 2022, it overstates Democrats.")).font = F(italic=True, color="595959")
    ws.cell(row=top + 2, column=1, value="Group").font = F(bold=True)
    for j, g in enumerate(E.NC_GROUPS):
        ws.cell(row=top + 2, column=2 + j, value=g.replace("_", " ").title()).font = F(bold=True)
        c = ws.cell(row=top + 3, column=2 + j, value=model["shares"][g])
        c.fill, c.font, c.number_format = YELLOW, F(color="0000FF"), PCT
    ws.cell(row=top + 3, column=1, value="D share").font = F(bold=True)
    coef = [f"${get_column_letter(2 + j)}${top + 3}" for j in range(len(E.NC_GROUPS))]
    nc = nc or {}
    days = nc.get("days_before")
    ws.cell(row=top + 4, column=1, value=(f"2026 data through {nc.get('through')} ({days} days before the election)"
                                          if days is not None else "No NC file loaded yet")).font = F(color="595959")
    h0 = top + 6
    hdr = ["County", "2026 so far", "2022 same point", "Change"] + [f"2026 {g.replace('_', ' ').title()}" for g in E.NC_GROUPS] + \
          [f"2022 {g.replace('_', ' ').title()}" for g in E.NC_GROUPS] + \
          ["Modeled lean 2026", "Modeled lean 2022 same point", "Shift", "2022 actual early-vote margin", "Black share 2026"]
    for i, h in enumerate(hdr):
        ws.cell(row=h0, column=i + 1, value=h)
    _style_header(ws, h0, len(hdr), 54)
    rows = nc.get("rows") or []
    r = h0 + 1
    first = r
    for rec in rows:
        vals = [rec["county"], f"=SUM(E{r}:I{r})", f"=SUM(J{r}:N{r})", f"=IFERROR(B{r}/C{r}-1,\"\")"] + \
               [rec["g26"].get(g, 0) for g in E.NC_GROUPS] + [rec["g22"].get(g, 0) for g in E.NC_GROUPS]
        lean26 = "+".join(f"{get_column_letter(5 + j)}{r}*{coef[j]}" for j in range(5))
        lean22 = "+".join(f"{get_column_letter(10 + j)}{r}*{coef[j]}" for j in range(5))
        vals += [f"=IFERROR(2*({lean26})/B{r}-1,\"\")", f"=IFERROR(2*({lean22})/C{r}-1,\"\")",
                 f"=IFERROR(O{r}-P{r},\"\")", rec.get("actual22"), rec.get("black26")]
        for j, v in enumerate(vals):
            c = ws.cell(row=r, column=j + 1, value=v)
            c.font = F()
        for col in range(2, 15):
            ws.cell(row=r, column=col).number_format = NUM
        ws.cell(row=r, column=4).number_format = '+0%;-0%;0%'
        for col in (15, 16, 18):
            ws.cell(row=r, column=col).number_format = MARGIN
        ws.cell(row=r, column=17).number_format = SWING
        ws.cell(row=r, column=19).number_format = PCT
        r += 1
    if rows:
        last = r - 1
        tot = ["Statewide"] + [f"=SUM({get_column_letter(col)}{first}:{get_column_letter(col)}{last})" for col in range(2, 15)]
        lean26 = "+".join(f"{get_column_letter(5 + j)}{r}*{coef[j]}" for j in range(5))
        lean22 = "+".join(f"{get_column_letter(10 + j)}{r}*{coef[j]}" for j in range(5))
        tot[3] = f"=IFERROR(B{r}/C{r}-1,\"\")"
        tot += [f"=IFERROR(2*({lean26})/B{r}-1,\"\")", f"=IFERROR(2*({lean22})/C{r}-1,\"\")", f"=IFERROR(O{r}-P{r},\"\")",
                nc.get("actual22_state"), None]
        for j, v in enumerate(tot):
            c = ws.cell(row=r, column=j + 1, value=v)
            c.font = F(bold=True)
            c.fill = BAND
        for col in range(2, 15):
            ws.cell(row=r, column=col).number_format = NUM
        ws.cell(row=r, column=4).number_format = '+0%;-0%;0%'
        for col in (15, 16, 18):
            ws.cell(row=r, column=col).number_format = MARGIN
        ws.cell(row=r, column=17).number_format = SWING
    for i in range(2, 20):
        ws.column_dimensions[get_column_letter(i)].width = max(ws.column_dimensions[get_column_letter(i)].width or 0, 10)
    ws.column_dimensions["A"].width = 14
    ws.freeze_panes = "A6"


def _readme(ws):
    lines = [
        ("County Watch 2026", "title"),
        ("What this is", "h"),
        ("A county-by-county read of the competitive Senate races (AK, GA, IA, KS, ME, MI, MN, MS, NC, NE, NH, OH, SC, TX, FL), plus Virginia and Kentucky as early-closing bellwethers for the national swing.", ""),
        ("Before election night it holds the benchmarks (2024 and 2022 results by county, demographics, break-even margins) and the early vote. On the night it projects each race from the counties that have finished counting.", ""),
        ("Sheets", "h"),
        ("Dashboard: one row per state with the projection, swing, confidence and the bot's written read.", ""),
        ("Key Counties: 6 to 11 counties per state, on both sides (Democratic vote banks and Republican bases), with why they matter, what to watch, and live status on margin and on turnout. Each state ends with an 'All rural' row: every small-town and rural county together.", ""),
        ("County Benchmarks: for each key county, the final margin and net votes that match each statewide outcome (R+5, R+1, tie, D+1, D+5), plus a plain-English line on what to look for. Read final county numbers against these.", ""),
        ("First Counties: the first counties to report in each state, in poll-closing order. Shows what arrives first (early, mail or Election Day votes), how lopsided that batch was in 2022, and the first-batch number that only means break-even.", ""),
        ("Counties: every county. Grey Feed columns are filled by the bot for GA, NC, MN and NE. Yellow Manual columns are yours: type D and R votes from any source (NYT, AP, a county site) and put Y in 'Manual complete' when a county is fully counted. Manual numbers override the feed.", ""),
        ("Types: each kind of county (metro cores, college suburbs, Black Belt, Hispanic, rural...) as a block: its share of the vote, the margin it needs for each statewide outcome, what weaker or stronger turnout does to that, and live swing and turnout. Its swing and turnout pace fill in counties that haven't reported.", ""),
        ("Early Vote: statewide early vote for each state, plus North Carolina county by county compared with 2022 at the same point.", ""),
        ("Colors", "h"),
        ("Yellow fill, blue text: inputs you can change. Grey fill: written by the bot. Blue numbers lean D, red lean R.", ""),
        ("How the projection works", "h"),
        ("1. A county is complete when all precincts report (feed), when you mark it Y, or when its count reaches the expected vote.", ""),
        ("2. Swing = its 2026 margin minus its 2024 presidential margin.", ""),
        ("3. Each county type's swing is the vote-weighted average over its complete counties in that state.", ""),
        ("4. Unfinished counties are projected at 2024 margin + their type's swing, across their whole expected vote. Partial counts are ignored for the margin, because they're usually one vote method.", ""),
        ("5. Votes for unfinished counties = 2022 turnout x a turnout pace: the Dashboard turnout factor before results, then each type's pace from its finished counties (shrunk toward the state's pace when only a few are in).", ""),
        ("Turnout", "h"),
        ("Benchmarks use 2022's turnout pattern (a midterm) with 2024's county margins. In Georgia that baseline is R+1.1 rather than the actual 2024 R+2.2, because the midterm electorate was relatively heavier in metro Atlanta.", ""),
        ("Par vote = a county's two-party vote at 2022 turnout x the state's projected turnout pace. Turnout index = actual votes / par x 100: 100 means the county is the same share of the state vote as in 2022.", ""),
        ("Break-even at actual turnout = break-even / (index / 100). A Democratic county at index 95 needs a margin about 5% bigger; a Republican county at 105 must be held about 5% closer.", ""),
        ("Margin + turnout vs tie pace = live margin minus break-even at actual turnout, in margin points. Times the county's votes, it's net votes vs the tie pace; across every county those add up to the statewide net margin.", ""),
        ("Turnout differences between county types aren't visible to the projection until counties of that type finish, so a metro turnout shortfall shows up late. Watch the D-leaning and R-leaning turnout indexes on the Dashboard.", ""),
        ("Limits", "h"),
        ("It assumes counties of the same type move together. A candidate with a home-region bounce (Jackson in northern Maine, Osborn in rural Nebraska) will break that.", ""),
        ("Small, rural counties usually finish first, so early projections lean on a narrow, unrepresentative slice. Treat 'low' confidence as a weather report, not a call.", ""),
        ("Margins are two-party. Third-party votes are recorded but left out of margins.", ""),
        ("Sources", "h"),
        ("2024 and 2020 county results: Tony McGovern's county-level election results (GitHub, tonmcg/US_County_Level_Election_Results_08-24).", ""),
        ("2022 results and vote methods: MIT Election Data + Science Lab, 2022-elections-official (precinct returns, summed to counties).", ""),
        ("Demographics: Census Bureau Vintage 2023 county population estimates (race/Hispanic origin) and ACS 2019-2023 5-year table B15003 (bachelor's degree or higher, age 25+).", ""),
        ("Live feeds: Georgia SOS and Virginia Department of Elections results APIs, NC State Board of Elections precinct file, Florida Division of Elections results file, Minnesota SOS media files, Nebraska SOS results service, Iowa and South Carolina Clarity results files, Alaska Division of Elections precinct file. Ohio, Texas and Kansas come from the County Watch grab button (the states' own results sites, read in your browser).", ""),
        ("Early vote: UF Election Lab 2026 tracker (Michael McDonald), and NC State Board of Elections absentee files for 2026 and 2022.", ""),
        ("Key county notes are the bot author's judgment; the numbers next to them come from the data above.", ""),
    ]
    ws.column_dimensions["A"].width = 130
    for i, (t, kind) in enumerate(lines, start=1):
        c = ws.cell(row=i, column=1, value=t)
        c.font = F(bold=True, size=16) if kind == "title" else F(bold=True, size=11) if kind == "h" else F()
        c.alignment = Alignment(wrap_text=True, vertical="top")


# ------------------------------------------------------------------ carry over inputs / live update
def read_inputs(path: Path) -> dict:
    """Manual county inputs and turnout factors from an existing workbook."""
    if not path.exists():
        return {}
    wb = load_workbook(path, read_only=True)
    keep: dict = {"factor": {}}
    ws = wb["Counties"]
    idx = {k: i for i, (_, k, _, _) in enumerate(CCOLS)}
    for row in ws.iter_rows(min_row=2, values_only=True):
        if not row or not row[idx["fips"]]:
            continue
        m = {k: row[idx[k]] for k in ("md", "mr", "mo", "mc") if row[idx[k]] not in (None, "")}
        if m:
            keep[str(row[idx["fips"]])] = m
    ws = wb["Dashboard"]
    for row in ws.iter_rows(min_row=DASH_ROW0, max_row=DASH_ROW0 + len(M.STATES) - 1, values_only=True):
        if row[0] in M.STATES and isinstance(row[6], (int, float)):
            keep["factor"][row[0]] = row[6]
    wb.close()
    return keep


def is_locked(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        with open(path, "r+b"):
            return False
    except PermissionError:
        return True


def push_open(path: Path, live: dict, reads: dict, stamp: str) -> bool:
    """Write feed values into the workbook while it's open in Excel (needs xlwings). True on success."""
    try:
        import xlwings as xw
    except ImportError:
        return False
    try:
        book = next(b for app in xw.apps for b in app.books if Path(b.fullname).resolve() == path.resolve())
    except (StopIteration, Exception):
        return False
    sh = book.sheets["Counties"]
    fips_col = sh.range(f"{CL['fips']}2").expand("down").value
    block = []
    for f in fips_col:
        row = None
        for s, rows in live.items():
            if str(f) in rows:
                row = rows[str(f)]
                break
        block.append([row.get("D"), row.get("R"), row.get("O"), row.get("pct_in")] if row else [None] * 4)
    sh.range(f"{CL['fd']}2").value = block
    d = book.sheets["Dashboard"]
    for i, st in enumerate(M.STATES):
        if st in reads:
            d.range(f"{D_READ}{DASH_ROW0 + i}").value = reads[st]
    d.range("A3").value = f"Last bot update: {stamp}"
    return True


def write_csv(path: Path, live: dict):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["state", "fips", "D", "R", "O", "pct_in"])
        for st, rows in live.items():
            for fips, r in rows.items():
                w.writerow([st, fips, r.get("D"), r.get("R"), r.get("O"), r.get("pct_in")])


def update(path: Path, *, live: dict, uf: dict | None, nc: dict | None, reads: dict,
           extra_manual: dict | None = None) -> str:
    """Refresh the workbook. Returns what happened, for the log."""
    stamp = datetime.now().strftime("%b %d %I:%M %p")
    if is_locked(path):
        if push_open(path, live, reads, stamp):
            return "updated the open workbook"
        write_csv(path.with_name("County Watch live.csv"), live)
        return "workbook is open and xlwings isn't available, so wrote County Watch live.csv instead"
    keep = read_inputs(path)
    keep.update(extra_manual or {})          # /countyset entries show up in the Manual columns
    keep["updated"] = stamp
    build(path, keep=keep, live=live, uf=uf, nc=nc, reads=reads)
    return "rebuilt the workbook"

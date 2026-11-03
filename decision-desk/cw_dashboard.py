"""County Watch: the Dashboard tab. One row per race, in poll-closing order, reading left to right: who's ahead, what sets
the confidence, whether the trailer can catch up, swing and turnout by county type, then the raw numbers. The bot's
written take for each race sits under the table. Graphics are cell fills and data bars, so they update with the
numbers and work in Excel Online. The column layout lives in cw_workbook (DCOLS), because other tabs read it."""
from __future__ import annotations

import math

from openpyxl.cell.rich_text import CellRichText, TextBlock
from openpyxl.cell.text import InlineFont
from openpyxl.comments import Comment
from openpyxl.formatting.rule import DataBarRule, FormulaRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import column_index_from_string as idx
from openpyxl.utils import get_column_letter

import cw_glance as G
import cw_model as M
import cw_workbook as W

F, DC, TL = W.F, W.DC, W.TL
NAVY = "1F2A44"
HAIR = Side(style="thin", color="D9D9D9")
GAP = Side(style="thick", color="FFFFFF")        # keeps the strip and bar of one row from running into the next
fill = lambda c: PatternFill("solid", fgColor=c, bgColor=c)
CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)
TURN_FILLS = ["7A4FA3", "CBB6E0", "EDEDED", "F9D9A8", "D98A1F"]    # turnout: not partisan, so not blue and red
TURN_FONT = ["FFFFFF", "1F1F1F", "1F1F1F", "1F1F1F", "FFFFFF"]

NOTES = {
    "call": "A colored label for the projected margin: Great for (5+), on track (1 to 5), Toss-up (within 1). It describes the margin and ignores confidence.",
    "proj": "Finished counties as counted, plus every other county's 2024 margin moved by its type's swing, across its expected votes.",
    "raw": "The margin of every vote counted so far, with half-counted counties included. Early votes and untyped counties can distort it, so a gap from the projection isn't a contradiction.",
    "rangetxt": "Where the final result should land 9 times in 10, by a judgment-based formula. It widens with the vote still out and with how much finished counties disagree. It can't see a home-region bounce or a county type that hasn't reported.",
    "conf": "Set only by how much of the vote is from finished counties. Low: under 5 counties, or under 10% of the expected vote. Medium: under 40%. High: 40% or more. It says nothing about how close the race is.",
    "doneshare": "The share of the state's expected vote sitting in fully counted counties. This is the number confidence is built on.",
    "done": "Counties fully counted: every precinct in, marked Y, or at 97% of expected votes with no precinct count. Under 5 forces confidence to low.",
    "left": "Projected final two-party votes minus votes counted.",
    "needs": "The share of remaining two-party votes the projected loser needs to tie. Over 100% is impossible. Under 50% means that candidate is ahead in the raw count.",
    "pace": "The share of remaining votes the projection expects that candidate to get.",
    "gap": "Needs minus pace, in points. A bigger bar means a harder catch-up.",
    "need": "How far every county must move from 2024 for a tie, on 2022's midterm turnout mix. '+7.2% R' means the D side can lose 7.2 points and still tie.",
    "swing": "Average change from 2024 across finished counties, weighted by votes.",
    "ahead": "Swing in finished counties minus the swing the D side needs. Blue is ahead of that pace, red is behind.",
    "paceproj": "The whole state's projected two-party vote against 2022's pattern. 1.05 means 5% more votes.",
    "paceobs": "Votes in finished counties divided by what those counties cast on 2022's pattern.",
    "idxd": "Turnout in finished counties whose break-even is D+10 or more. 100 means in line with the state.",
    "idxr": "Turnout in finished counties whose break-even is R+10 or worse for the D side. 100 means in line with the state.",
    "idxgap": "D-leaning index minus R-leaning index. Positive means the D side's base is out-voting the R side's.",
    "polladj": "This race's final poll moved by tonight's average miss (Polls tab, cell E3).",
    "pollerr": "Projection minus poll, once the state reaches medium confidence. Negative means more Republican than the poll.",
    "implied": "Takes the swings that the same kinds of counties are showing in OTHER states tonight, and applies them to this state's counties. Example: if rural Midwest counties elsewhere are up 6 for Democrats, this state's rural counties get +6. It never feeds the projection.",
    "impliedn": "How many finished counties in other states sit behind the 'other states imply' number.",
    "factor": "Expected 2026 turnout as a multiple of 2022, used until 3 counties are known to be finished. Early vote running well ahead of 2022 is a reason to raise it.",
    "spread": "How much finished counties disagree on their swing. Fixed at 5 points until 5 counties finish, and never under 2 after that.",
}
NEEDS_NOTE = ("Needs: this type's margin if the state ends in a tie, with every county moving the same amount from 2024 on 2022's turnout. The D side has to beat it and the R side "
              "has to hold it below that. A miss here can be made up in another type. Pace: the type's 2024 margin moved by the swing in its finished counties. Swing: that movement. "
              "Last line: counties finished and the type's share of the state's vote. County types stand in for groups of voters. They aren't exit polls.")
KEY_NOTE = ("A race's biggest key counties. Needs: the county's margin in a statewide tie, adjusted for its actual turnout once it's finished. At: its margin so far. "
            "Color appears once a county is finished, because a partial count can lean either way. Key Counties and County Benchmarks have the full list and more targets.")
NOTES.update({f"sw{i}": NEEDS_NOTE for i in range(len(W.TYPE_SLOTS))})
NOTES.update({f"kc{i}": KEY_NOTE for i in range(W.KEY_N)})


def _swing_txt(ref: str) -> str:
    return f'IF({ref}>=0,"+"&TEXT({ref}*100,"0.0")&" D","+"&TEXT(-{ref}*100,"0.0")&" R")'


def _lvl(ref: str) -> str:
    return W._mt(f"MAX(-1,MIN(1,{ref}))")


def _hdr_lines(text: str, width: float) -> int:
    per = max(1, int(width * 1.05))
    n, cur = 1, 0
    for w in text.split():
        if cur and cur + 1 + len(w) > per:
            n, cur = n + 1, len(w)
        else:
            cur += (1 if cur else 0) + len(w)
    return n


def dashboard_sheet(ws, data, names, rng, trow, keep, reads, rowmap):
    ws.sheet_view.zoomScale = 85
    ws.sheet_view.showGridLines = False
    # Frozen columns clip text at their edge in Excel Online, so only short text goes in A and the long line starts in C
    ws["A1"] = "County Watch 2026"
    ws["A1"].font = F(bold=True, size=16)
    ws.row_dimensions[1].height = 26
    ws["C2"] = ("One row per race, in poll-closing order. Read left to right: who's ahead, how sure we can be, whether the trailer can catch up, "
                "then swing and turnout by county type. The raw numbers are at the far right. The bot's written take on each race is under the table.")
    ws["C2"].font = F(italic=True, color="595959")
    ws["C2"].alignment = Alignment(wrap_text=True, vertical="center")
    ws.merge_cells(f"C2:{DC['rangetxt']}2")       # Excel Online cuts off long text that only overflows, so these lines are merged
    ws["A3"] = f"Last bot update: {keep.get('updated') or 'not yet'}"
    ws["A3"].font = F(color="595959")
    ws.row_dimensions[2].height = 30
    ws.row_dimensions[3].height = 16
    hr = W.DASH_ROW0 - 1
    rows = W.dash_order()
    r0, rN = W.DASH_ROW0, W.DASH_ROW0 + len(rows) - 1

    for key, head, width in W.DCOLS:
        ws.column_dimensions[DC[key]].width = width
        if head:
            ws[f"{DC[key]}{hr}"] = head
        if key.startswith("h_"):
            ws.column_dimensions[DC[key]].hidden = True
    last = max(idx(DC[k]) for k, _, _ in W.DCOLS if not k.startswith("h_"))

    # zone bands
    for title, colour, a, b in W.DZONES:
        x0, x1 = idx(DC[a]), idx(DC[b])
        ws.merge_cells(start_row=hr - 1, start_column=x0, end_row=hr - 1, end_column=x1)
        ws.cell(row=hr - 1, column=x0, value=title)
        for x in range(x0, x1 + 1):
            ws.cell(row=hr - 1, column=x).fill = fill(colour)
        ws.cell(row=hr - 1, column=x0).font = F(bold=True, size=10, color="FFFFFF")
        ws.cell(row=hr - 1, column=x0).alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[hr - 1].height = 22

    # headers
    for x in range(1, last + 1):
        c = ws.cell(row=hr, column=x)
        c.font = F(bold=True, color="FFFFFF")
        c.fill = fill(NAVY)
        c.alignment = CENTER
    strip0, stripN = idx(DC["s0"]), idx(DC[f"s{W.STRIP_N - 1}"])
    conf0, confN = idx(DC["c0"]), idx(DC[f"c{W.CONF_N - 1}"])
    ws.merge_cells(start_row=hr, start_column=strip0, end_row=hr, end_column=stripN)
    ws.cell(row=hr, column=strip0, value="◀ R+15      even      D+15 ▶\nBlack = projection, grey = 90% range. One block = 1 point. Empty until a county finishes.")
    ws.merge_cells(start_row=hr, start_column=conf0, end_row=hr, end_column=confN)
    ws.cell(row=hr, column=conf0, value="Vote counted. Dark = finished counties, mid = half-counted. Red zone = low confidence, amber = medium, green = high. Empty until votes are counted.")
    need = 3
    for key, head, width in W.DCOLS:
        if head and not key.startswith("h_"):
            need = max(need, _hdr_lines(head, width))
    ws.row_dimensions[hr].height = max(need * 14 + 10, 84)

    # rows
    for st in rows:
        r = W.dash_row(st)
        c = lambda k: f"{DC[k]}{r}"
        S = lambda k, extra="": f"SUMIFS({rng(k)},{rng('state')},$A{r}{extra})"
        done1 = "," + rng("done") + ",1"
        dn, rn = names[st]
        sd, sr = W._surname(dn), W._surname(rn)
        none = f"{c('done')}=0"
        ty = lambda h, tr: f"Types!${TL[h]}${tr}"
        forms = {
            "st": st,
            "race": CellRichText([TextBlock(InlineFont(rFont=W.FONT, b=True, sz=11, color=NAVY), M.STATES[st] + "\n"),
                                  TextBlock(InlineFont(rFont=W.FONT, sz=9, color="595959"),
                                            f"{sd} v {sr}" + (" (bellwether)" if st in M.BELLWETHER else ""))]),
            "dem": dn, "rep": rn,
            "m24": f"=({S('d24')}-{S('r24')})/({S('d24')}+{S('r24')})",
            "need": f"=-{S('mw')}/{S('w')}",
            "factor": (keep.get("factor") or {}).get(st, 1.0),
            "exp": f"={S('exp')}",
            "counted": f"={S('tc')}",
            "pct": f"=IF({c('exp')}>0,{c('counted')}/{c('exp')},0)",
            "done": f"=COUNTIFS({rng('state')},$A{r},{rng('done')},1)",
            "of": f"=COUNTIF({rng('state')},$A{r})",
            "doneshare": f"=IF({c('exp')}>0,{S('exp', done1)}/{c('exp')},0)",
            "swing": f"=IF({c('done')}>0,{S('swv')}/{S('tc', done1)},\"\")",
            "ahead": f"=IF({c('swing')}=\"\",\"\",{c('swing')}-{c('need')})",
            "raw": f"=IF({c('counted')}>0,({S('dc')}-{S('rc')})/{c('counted')},\"\")",
            "proj": f"=IF({c('done')}>0,({S('pd')}-{S('pr')})/({S('pd')}+{S('pr')}),\"\")",
            "leader": f"=IF({c('proj')}=\"\",\"\",IF({c('proj')}>=0,{c('dem')},{c('rep')})&\" +\"&TEXT(ABS({c('proj')})*100,\"0.0\"))",
            "call": f'=IF({none},"No results yet",' + W._call_formula(c("proj"), c("dem"), c("rep"))[1:] + ")",
            "conf": f"=IF({c('done')}=0,\"none\",IF(OR({c('doneshare')}<0.1,{c('done')}<5),\"low\",IF({c('doneshare')}<0.4,\"medium\",\"high\")))",
            "why": (f'=IF({none},"No county finished yet. Medium takes 5 counties and 10% of the vote. High takes 40%.",'
                    f'IF({c("conf")}="low",{c("done")}&IF({c("done")}=1," county, "," counties, ")&TEXT({c("doneshare")},"0%")&" of the vote. Medium takes 5 counties and 10%.",'
                    f'IF({c("conf")}="medium",TEXT({c("doneshare")},"0%")&" of the vote from "&{c("done")}&" counties. High takes 40%.",'
                    f'TEXT({c("doneshare")},"0%")&" of the vote from "&{c("done")}&" counties. Past the 40% line for high.")))'),
            "early": f"=IFERROR(INDEX('Early Vote'!$C$6:$C${5 + len(M.STATES)},MATCH($A{r},'Early Vote'!$A$6:$A${5 + len(M.STATES)},0)),\"\")",
            "earlypct": f"=IFERROR(INDEX('Early Vote'!$E$6:$E${5 + len(M.STATES)},MATCH($A{r},'Early Vote'!$A$6:$A${5 + len(M.STATES)},0)),\"\")",
            "paceobs": f"=IF({c('done')}>0,{S('tc', done1)}/{S('w', done1)},\"\")",
            "paceused": f"=IF({c('done')}>=3,{c('paceobs')},{c('factor')})",
            "paceproj": f"={S('pv')}/{S('w')}",
            "idxd": (f"=IFERROR(100*{S('tc', done1 + ',' + rng('be') + ',\">=0.1\"')}/"
                     f"{S('par', done1 + ',' + rng('be') + ',\">=0.1\"')},\"\")"),
            "idxr": (f"=IFERROR(100*{S('tc', done1 + ',' + rng('be') + ',\"<=-0.1\"')}/"
                     f"{S('par', done1 + ',' + rng('be') + ',\"<=-0.1\"')},\"\")"),
            "idxgap": f"=IF(AND(ISNUMBER({c('idxd')}),ISNUMBER({c('idxr')})),{c('idxd')}-{c('idxr')},\"\")",
            "exppace": (f"=IF(COUNTIFS({rng('state')},$A{r},{rng('sure')},1)>={M.SURE_MIN},"
                        f"{S('tc', ',' + rng('sure') + ',1')}/{S('w', ',' + rng('sure') + ',1')},{c('factor')})"),
            "out": f"=IF({c('done')}>0,{S('pv', ',' + rng('done') + ',0')}/{S('pv')},\"\")",
            "spread": (f"=IF({none},\"\",IF({c('done')}>=5,MAX(SQRT({S('dev')}/{S('tc', done1)}),{M.SPREAD_MIN}),{M.SPREAD_DEFAULT}))"),
            "lo": (f"=IF({none},\"\",MAX(-1,{c('proj')}-{M.RANGE_Z}*({c('out')}*SQRT({M.RANGE_TAU}^2+{c('spread')}^2/{c('done')})+{M.RANGE_FLOOR})))"),
            "hi": (f"=IF({none},\"\",MIN(1,{c('proj')}+{M.RANGE_Z}*({c('out')}*SQRT({M.RANGE_TAU}^2+{c('spread')}^2/{c('done')})+{M.RANGE_FLOOR})))"),
            "rangetxt": f'=IF({none},"–",{W._mt(c("lo"))}&" to "&{W._mt(c("hi"))})',
            "implied": (f"=IF(SUMIFS(Types!${TL['Other states: votes in all-state fallback']}:${TL['Other states: votes in all-state fallback']},"
                        f"Types!$B:$B,$A{r})=0,\"\",SUMIFS(Types!${TL['Share x implied margin (helper)']}:${TL['Share x implied margin (helper)']},"
                        f"Types!$B:$B,$A{r}))"),
            "impliedn": f"=COUNTIFS({rng('done')},1,{rng('state')},\"<>\"&$A{r},{rng('state')},\"<>NE\")",
            "left": f"=MAX(0,{S('pd')}+{S('pr')}-{c('counted')})",
            "cnet": f"={S('dc')}-{S('rc')}",
            "pnet": f"={S('pd')}-{S('pr')}",
            "loser": f'=IF(OR({none},{c("left")}<=0),"",IF({c("pnet")}>=0,"R","D"))',
            "needs": f'=IF({c("loser")}="","",(1+IF({c("loser")}="D",1,-1)*(-{c("cnet")})/{c("left")})/2)',
            "pace": f'=IF({c("loser")}="","",(1+IF({c("loser")}="D",1,-1)*({c("pnet")}-{c("cnet")})/{c("left")})/2)',
            "trailer": f'=IF({c("loser")}="","–",IF({c("loser")}="D","{sd}","{sr}"))',
            "gap": f'=IF({c("loser")}="","",({c("needs")}-{c("pace")})*100)',
            "polladj": f'={G.poll_ref(st, "adj")}',
            "pollerr": f'={G.poll_ref(st, "err")}',
            # helpers for the graphics
            "h_m": f'=IF({none},"",MAX(-15,MIN(15,{c("proj")}*100)))',
            "h_lo": f'=IF({none},"",MAX(-15,MIN(15,{c("lo")}*100)))',
            "h_hi": f'=IF({none},"",MAX(-15,MIN(15,{c("hi")}*100)))',
        }
        tslots = W.TYPE_SLOTS if st != "AK" else [("Alaska district", W.TYPE_SLOTS[0][1])]
        for i, (tname, _) in enumerate(tslots):
            tr = trow.get((st, tname))
            if not tr:
                continue
            E, D_, Fv, Gs, Lsh = (f"Types!$E${tr}", f"Types!$D${tr}", f"Types!$F${tr}", f"Types!$G${tr}", f"Types!$L${tr}")
            vs, ti, po = ty("Swing vs what D needs", tr), ty("Turnout index (complete counties)", tr), ty("Turnout pace observed (vs 2022)", tr)
            tie, base = ty("Type margin if state ends tie", tr), ty("2024 margin (2022 pattern)", tr)
            head = "Alaska districts: " if st == "AK" else ""
            forms[f"sw{i}"] = (f'="{head}Needs "&{_lvl(tie)}&CHAR(10)&IF({Gs}="","no swing yet","Pace "&{_lvl(f"({base}+{Gs})")}&CHAR(10)&"Swing "&{_swing_txt(Gs)})'
                               f'&CHAR(10)&{E}&" of "&{D_}&" · "&IF({Lsh}<0.01,"<1%",TEXT({Lsh},"0%"))')
            forms[f"h_sw{i}"] = f'=IF({vs}="","",{vs})'
            forms[f"to{i}"] = f'="{head}"&IF({po}="","–",TEXT({po},"0%")&CHAR(10)&"index "&TEXT({ti},"0"))'
            forms[f"h_to{i}"] = f'=IF({ti}="","",{ti}-100)'
        keys = sorted((x for x in data[st] if x["key"]), key=lambda x: -x["t24"])[:W.KEY_N]
        for i, x in enumerate(keys):
            K = lambda k: f"Counties!${W.CL[k]}${rowmap[x['fips']]}"
            forms[f"h_kn{i}"] = f'=MAX(-1,MIN(1,IF({K("tbe")}="",{K("be")},{K("tbe")})))'
            forms[f"h_kc{i}"] = f'=IF({K("done")}=1,{K("lm")}-{c(f"h_kn{i}")},"")'
            forms[f"kc{i}"] = (f'="{x["name"]}"&CHAR(10)&"Needs "&{W._mt(c(f"h_kn{i}"))}&CHAR(10)&IF({K("lm")}="","not in yet","At "&{W._mt(K("lm"))}'
                               f'&CHAR(10)&IF({K("done")}=1,"final",TEXT({K("ein")},"0%")&" in"))')
        for key, head, _ in W.DCOLS:
            cell = ws[f"{DC[key]}{r}"]
            if key in forms:
                cell.value = forms[key]
            cell.font = F()
            if not (key[0] in "sc" and key[1:].isdigit()):
                cell.alignment = CENTER
                cell.border = Border(bottom=HAIR)
        ws[f"{DC['race']}{r}"].alignment = Alignment(wrap_text=True, vertical="center", indent=1)
        ws[f"{DC['race']}{r}"].hyperlink = f"#Dashboard!A{W.take_row(st)}"
        if st == "AK":
            for pre in ("sw", "to"):
                ws.merge_cells(f"{DC[pre + '0']}{r}:{DC[pre + str(len(W.TYPE_SLOTS) - 1)]}{r}")
        ws[f"{DC['factor']}{r}"].fill = W.YELLOW
        ws[f"{DC['factor']}{r}"].font = F(color="0000FF")
        for k in ("call", "conf"):
            ws[f"{DC[k]}{r}"].font = F(bold=True, size=11)
        ws[f"{DC['proj']}{r}"].font = F(bold=True, size=12)
        ws[f"{DC['why']}{r}"].font = F(size=9)
        ws[f"{DC['why']}{r}"].alignment = Alignment(wrap_text=True, vertical="center")
        for i in range(len(W.TYPE_SLOTS)):
            ws[f"{DC[f'sw{i}']}{r}"].font = F(size=9)
            ws[f"{DC[f'to{i}']}{r}"].font = F(size=9)
        for i in range(W.KEY_N):
            ws[f"{DC[f'kc{i}']}{r}"].font = F(size=9)
        # the scale behind the strip and the zones behind the confidence bar
        for i in range(W.STRIP_N):
            v = i - 15
            col = "F8CFCA" if v <= -5 else "FCEBE9" if v <= -1 else "EDEDED" if v < 1 else "E8F0F8" if v < 5 else "BBD4EC"
            cell = ws[f"{DC[f's{i}']}{r}"]
            cell.fill = fill(col)
            if v == 0:
                cell.border = Border(left=Side(style="thin", color="404040"), right=Side(style="thin", color="404040"), top=GAP, bottom=GAP)
            else:
                cell.border = Border(top=GAP, bottom=GAP)
        for i in range(W.CONF_N):
            ws[f"{DC[f'c{i}']}{r}"].fill = fill("F8CFCA" if i < 2 else "FBEBC8" if i < 8 else "DFF0E3")
            ws[f"{DC[f'c{i}']}{r}"].border = Border(top=GAP, bottom=GAP)
        ws.row_dimensions[r].height = 58

    # number formats
    fmts = {W.MARGIN: ("raw", "m24", "lo", "hi", "polladj"), W.SWING: ("need", "swing", "ahead", "pollerr"),
            W.NUM: ("exp", "counted", "early", "left"), "0.0%": ("spread", "needs", "pace"),
            "0.00": ("factor", "paceused", "exppace"), W.PCT: ("pct", "doneshare", "earlypct", "out", "paceobs", "paceproj"), "0": ("idxd", "idxr"), '+0;-0;0': ("idxgap",),
            W.NET: ("cnet", "pnet"), G.PLAIN: ("proj", "implied"), '0.0" pts"': ("gap",)}
    for f_, keys in fmts.items():
        for k in keys:
            for r in range(r0, rN + 1):
                ws[f"{DC[k]}{r}"].number_format = f_

    # ---- graphics
    cf = ws.conditional_formatting.add
    s0L, sNL = DC["s0"], DC[f"s{W.STRIP_N - 1}"]
    strip = f"{s0L}{r0}:{sNL}{rN}"
    tick = f"(COLUMN()-COLUMN(${s0L}$1)-15)"
    hm, hlo, hhi, dn_ = (f"${DC['h_m']}{r0}", f"${DC['h_lo']}{r0}", f"${DC['h_hi']}{r0}", f"${DC['done']}{r0}")
    cf(strip, FormulaRule(formula=[f'AND({dn_}>0,ISNUMBER({hm}),ABS({tick}-{hm})<0.5)'], stopIfTrue=True, fill=fill("1A1A1A")))
    cf(strip, FormulaRule(formula=[f'AND(ISNUMBER({hlo}),{tick}>={hlo},{tick}<={hhi})'], stopIfTrue=True, fill=fill("9A9A9A")))
    c0L, cNL = DC["c0"], DC[f"c{W.CONF_N - 1}"]
    bar = f"{c0L}{r0}:{cNL}{rN}"
    i_ = f"(COLUMN()-COLUMN(${c0L}$1))"
    cf(bar, FormulaRule(formula=[f"ROUND(${DC['doneshare']}{r0}*{W.CONF_N},0)>{i_}"], stopIfTrue=True, fill=fill(NAVY)))
    cf(bar, FormulaRule(formula=[f"ROUND(MIN(1,${DC['pct']}{r0})*{W.CONF_N},0)>{i_}"], stopIfTrue=True, fill=fill("8C9BB8")))
    for val, bg, fg in (("high", "2E7D4F", "FFFFFF"), ("medium", "FBEBC8", "5A4500"), ("low", "F4B6B0", "1F1F1F"), ("none", "F2F2F2", "7F7F7F")):
        ref = f"{DC['conf']}{r0}"
        cf(f"{ref}:{DC['conf']}{rN}", FormulaRule(formula=[f'{ref}="{val}"'], fill=fill(bg), font=Font(color=fg, bold=True)))
    q = f"${DC['proj']}{r0}"
    for k in ("call", "proj"):
        W._tier_colors(ws, f"{DC[k]}{r0}:{DC[k]}{rN}", q, W.TIER_CONDS(q))
    for k in ("polladj", "implied"):
        ref = f"${DC[k]}{r0}"
        W._tier_colors(ws, f"{DC[k]}{r0}:{DC[k]}{rN}", ref, W.TIER_CONDS(ref))
    cf(f"{DC['gap']}{r0}:{DC['gap']}{rN}", DataBarRule(start_type="num", start_value=0, end_type="num", end_value=40, color="B7791F", showValue=True))
    nd = f"{DC['needs']}{r0}"
    cf(f"{nd}:{DC['needs']}{rN}", FormulaRule(formula=[f'AND(ISNUMBER({nd}),{nd}>1)'], stopIfTrue=True, fill=fill("DFF0E3"), font=Font(bold=True, color="2E7D4F")))
    cf(f"{nd}:{DC['needs']}{rN}", FormulaRule(formula=[f'AND(ISNUMBER({nd}),{nd}<0.5)'], stopIfTrue=True, fill=fill("FBEBC8"), font=Font(bold=True, color="5A4500")))
    # heat maps: swing colored against what the race needs, turnout against the state's own pace
    sw = f"{DC['sw0']}{r0}:{DC[f'sw{len(W.TYPE_SLOTS) - 1}']}{rN}"
    hsw = f"{DC['h_sw0']}{r0}"
    W._tier_colors_num(ws, sw, hsw, [f"{hsw}>=0.05", f"{hsw}>=0.01", f"{hsw}>-0.01", f"{hsw}>-0.05", "TRUE"])
    to = f"{DC['to0']}{r0}:{DC[f'to{len(W.TYPE_SLOTS) - 1}']}{rN}"
    hto = f"{DC['h_to0']}{r0}"
    for i, cond in enumerate((f"{hto}>=3", f"{hto}>=1", f"{hto}>-1", f"{hto}>-3", "TRUE")):
        cf(to, FormulaRule(formula=[f'AND(ISNUMBER({hto}),{cond})'], stopIfTrue=True, fill=fill(TURN_FILLS[i]),
                           font=Font(name=W.FONT, size=9, color=TURN_FONT[i], bold=True)))
    cf(sw, FormulaRule(formula=[f'AND({DC["sw0"]}{r0}<>"",{hsw}="")'], font=Font(name=W.FONT, size=9, color="595959")))
    kc = f"{DC['kc0']}{r0}:{DC[f'kc{W.KEY_N - 1}']}{rN}"
    hkc = f"{DC['h_kc0']}{r0}"
    W._tier_colors_num(ws, kc, hkc, [f"{hkc}>=0.05", f"{hkc}>=0.01", f"{hkc}>-0.01", f"{hkc}>-0.05", "TRUE"])

    for key, note in NOTES.items():
        cm = Comment(note, "County Watch")
        cm.width, cm.height = 340, 40 + 20 * math.ceil(len(note) / 52)
        ws[f"{DC[key]}{hr}"].comment = cm

    # ---- legend under the table
    y = W.LEGEND_ROW0
    for i, line in enumerate([
        "How to read it",
        "• Confidence is only about how much is finished: low = under 5 counties or under 10% of the expected vote, medium = under 40%, high = 40% or more. It doesn't look at how close the race is.",
        "• County types: Needs = the margin this type posts if the state ends in a tie. The D side has to beat it and the R side has to hold it below that. Pace = where the type is heading on its finished counties' swing. Color = pace against needs (dark blue 5+ ahead, light blue 1 to 5 ahead, grey within 1, light red 1 to 5 behind, dark red 5+ behind).",
        "• Key counties: the race's biggest, with the margin each needs in a statewide tie and where it stands. It takes color once finished, on the same scale. A miss in one place can be made up in another. County types stand in for groups of voters, and aren't exit polls.",
        "• Turnout by county type: the percent is votes compared with 2022 in that type's finished counties. The index compares it with the state: 100 = same pace. Purple = above the state's pace, orange = below.",
        "• Needs of the rest to tie: green = over 100%, so the trailer can't catch up. Amber = under 50%, so the 'trailer' is actually ahead in the raw count and nothing is settled.",
        "• Other states imply: apply the swings that similar kinds of counties are showing in other states tonight to this state. It's a second opinion from outside the state and never feeds the projection.",
        "• Nebraska's D side is Dan Osborn, and its 2024 columns are Osborn's 2024 Senate race, not the presidential race. Alaska reports by state House district. Mississippi's 2022 column is US House races, so its 2022 margins are rough.",
    ]):
        cell = ws.cell(row=y + i, column=3, value=line)
        cell.font = F(bold=(i == 0), color="404040")
        cell.alignment = Alignment(wrap_text=True, vertical="top")
        ws.merge_cells(f"C{y + i}:{DC[f'c{W.CONF_N - 1}']}{y + i}")
        ws.row_dimensions[y + i].height = 30 if len(line) > 170 else 16

    # ---- the bot's takes
    b = W.TAKE_ROW0 - 2
    end = idx(W.take_span(3)[1])
    ws.merge_cells(start_row=b, start_column=3, end_row=b, end_column=end)
    ws.cell(row=b, column=3, value="THE BOT'S TAKE · click a state's name in the table to jump to it")
    for x in range(1, end + 1):
        ws.cell(row=b, column=x).fill = fill("2F5D50")
        ws.cell(row=b + 1, column=x).fill = fill(NAVY)
    ws.cell(row=b, column=3).font = F(bold=True, size=11, color="FFFFFF")
    ws.cell(row=b, column=3).alignment = Alignment(vertical="center", indent=1)
    ws.row_dimensions[b].height = 24
    for j, t in enumerate(("Where it stands", "Swing and turnout", "Key counties ahead of the D side's pace", "Key counties behind the D side's pace")):
        a_, z_ = W.take_span(j)
        ws.merge_cells(f"{a_}{b + 1}:{z_}{b + 1}")
        ws[f"{a_}{b + 1}"] = t
        ws[f"{a_}{b + 1}"].font = F(bold=True, color="FFFFFF")
        ws[f"{a_}{b + 1}"].alignment = Alignment(horizontal="center", vertical="center")
    ws.merge_cells(f"A{b + 1}:B{b + 1}")
    ws[f"A{b + 1}"] = "Race"
    ws[f"A{b + 1}"].font = F(bold=True, color="FFFFFF")
    ws[f"A{b + 1}"].alignment = Alignment(horizontal="center", vertical="center")
    for k, st in enumerate(rows):
        r = W.take_row(st)
        ws.merge_cells(f"A{r}:B{r}")
        ws[f"A{r}"] = M.STATES[st]
        ws[f"A{r}"].font = F(bold=True, size=11, color=NAVY)
        ws[f"A{r}"].alignment = Alignment(vertical="top", indent=1, wrap_text=True)
        secs = (reads or {}).get(st) or ["", "", "", ""]
        for j in range(4):
            a_, z_ = W.take_span(j)
            ws.merge_cells(f"{a_}{r}:{z_}{r}")
            cell = ws[f"{a_}{r}"]
            cell.value = secs[j]
            cell.font = F(size=10)
            cell.alignment = Alignment(wrap_text=True, vertical="top", indent=1)
            if k % 2 == 0:
                for x in range(idx(a_), idx(z_) + 1):
                    ws.cell(row=r, column=x).fill = fill("F7F8FA")
        if k % 2 == 0:
            ws[f"A{r}"].fill = fill("F7F8FA")
            ws[f"B{r}"].fill = fill("F7F8FA")
        for x in range(1, end + 1):
            ws.cell(row=r, column=x).border = Border(bottom=HAIR)
        ws.row_dimensions[r].height = W.TAKE_HEIGHT
    ws.freeze_panes = f"C{W.DASH_ROW0}"
    ws.sheet_properties.tabColor = "1F4E9A"
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth, ws.page_setup.fitToHeight = 1, 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True

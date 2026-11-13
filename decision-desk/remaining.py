"""The vote that's still out: where it is, county by county, and what it would take to change the result.

Nobody types a number in. Everything comes from County Watch's projection of a state (cw_model.project): for each
county, the votes counted so far and the votes the model still expects, from its turnout pace and the swing in
counties of the same type that have finished. This module only does the arithmetic on top of that:

  breakdown(p)   the lead in the count, the vote still expected, the share of it the trailing candidate needs to
                 tie, the share the model has them getting, and the counties holding the most of what's left
  what_if(b, m)  the final margin if the outstanding vote broke a given way instead
  lines(...)     the same as text for Discord

Margins are two-party, as fractions, with the Democratic side positive (an independent the group backs counts as that
side, as it does everywhere in County Watch). Estimates of the outstanding vote are the model's, and are only as
good as its turnout pace: a line says so whenever few counties have finished.
"""
from __future__ import annotations


def _votes(v: float) -> str:
    v = abs(v)
    return f"{v / 1e6:.2f}M" if v >= 1e6 else f"{v / 1e3:.0f}K" if v >= 10_000 else f"{v:,.0f}"


def _lean(m: float, d: str = "D", r: str = "R") -> str:
    pts = abs(m) * 100
    return "even" if pts < 0.05 else f"{d if m > 0 else r}+{pts:.1f}"


def breakdown(p: dict, top: int = 8) -> dict:
    """What's counted, what's left and who needs what, from one state's projection."""
    counties = []
    for r in p.get("rows", []):
        d, rep = r.get("D") or 0, r.get("R") or 0
        pd, pr = r.get("proj_D", d), r.get("proj_R", rep)
        left = max(0.0, (pd + pr) - (d + rep))
        net = (pd - d) - (pr - rep)                    # what the model has the outstanding vote adding, D side +
        counties.append({"name": r.get("name") or r.get("fips"), "type": r.get("type"), "complete": bool(r.get("complete")),
                         "counted": d + rep, "margin": (d - rep) / (d + rep) if d + rep else None,
                         "left": left, "left_margin": net / left if left > 0.5 else None, "left_net": net})
    cd = sum(r.get("D") or 0 for r in p.get("rows", []))
    cr = sum(r.get("R") or 0 for r in p.get("rows", []))
    left = sum(c["left"] for c in counties)
    net = sum(c["left_net"] for c in counties)
    lead = cd - cr                                       # votes, D side +
    out = {"counted": cd + cr, "D": cd, "R": cr, "lead": lead, "left": left, "left_net": net,
           "left_margin": net / left if left > 0.5 else None,
           "final": lead + net, "final_margin": (lead + net) / (cd + cr + left) if cd + cr + left else None,
           "share_in": (cd + cr) / (cd + cr + left) if cd + cr + left else 0.0,
           "confidence": p.get("confidence", "none"), "done": p.get("done", 0),
           "behind": None, "need_share": None, "expected_share": None, "out_of_reach": False}
    if lead and left > 0.5:
        behind = "R" if lead > 0 else "D"
        sign = 1 if behind == "D" else -1
        need_m = abs(lead) / left                        # the trailer's margin in what's left that would tie it
        out.update(behind=behind, need_share=(1 + need_m) / 2, expected_share=(1 + sign * net / left) / 2,
                   out_of_reach=need_m > 1)
    elif lead:
        out.update(behind="R" if lead > 0 else "D", out_of_reach=True)
    for c in counties:
        c["share_of_left"] = c["left"] / left if left > 0.5 else 0.0
    out["counties"] = sorted((c for c in counties if c["left"] > 0.5), key=lambda c: -c["left"])
    out["top"] = out["counties"][:top]
    out["d_ground"] = sum(c["left"] for c in out["counties"] if (c["left_margin"] or 0) > 0)
    out["r_ground"] = left - out["d_ground"]
    return out


def what_if(b: dict, left_margin: float) -> dict:
    """The final result if everything still out broke `left_margin` (D side positive) instead of the model's read."""
    net = left_margin * b["left"]
    total = b["counted"] + b["left"]
    final = b["lead"] + net
    return {"left_margin": left_margin, "final": final, "final_margin": final / total if total else None,
            "winner": "D" if final > 0 else "R" if final < 0 else None}


def headline(b: dict, d: str = "D", r: str = "R") -> str:
    """One line for beside a call: the count, the model's finish, and what the trailing side needs."""
    if not b["counted"]:
        return "No votes counted yet."
    who = lambda side: d if side == "D" else r       # noqa: E731
    out = (f"Count: {who('D' if b['lead'] > 0 else 'R')} +{_votes(b['lead'])} "
           f"({_lean(b['lead'] / b['counted'], d, r)}) · {b['share_in']:.0%} of the expected vote in")
    if b["left"] <= 0.5:
        return out
    if b["final_margin"] is not None:
        out += f" · model finish {_lean(b['final_margin'], d, r)}"
    if b["out_of_reach"]:
        out += f" · {who(b['behind'])} can't catch up on what's left"
    elif b["need_share"] is not None:
        out += f" · {who(b['behind'])} needs {b['need_share']:.1%} of the ~{_votes(b['left'])} left"
    return out


def lines(b: dict, d: str = "D", r: str = "R", scenario: float | None = None) -> list[str]:
    """The full read, a line at a time."""
    if not b["counted"]:
        return ["No votes counted yet, so there's nothing to work out."]
    who = lambda side: d if side == "D" else r       # noqa: E731
    lead_side = "D" if b["lead"] > 0 else "R"
    out = [f"**Counted:** {who(lead_side)} leads by {_votes(b['lead'])} votes "
           f"({_lean(b['lead'] / b['counted'], d, r)}) with {_votes(b['counted'])} in, "
           f"about {b['share_in']:.0%} of the vote the model expects."]
    if b["left"] <= 0.5:
        return out + ["**Still out:** nothing the model expects. This is the finish."]
    out.append(f"**Still out:** about {_votes(b['left'])} votes: {_votes(b['d_ground'])} in counties where what's left "
               f"should favor {d}, {_votes(b['r_ground'])} where it should favor {r}.")
    if b["out_of_reach"]:
        out.append(f"**{who(b['behind'])} can't catch up:** the lead is bigger than everything still expected.")
    elif b["need_share"] is not None:
        gap = (b["need_share"] - b["expected_share"]) * 100
        verdict = ("on track to pass" if gap <= 0 else "still live" if gap < 5 else "uphill" if gap <= 15
                   else "nearly out of reach")
        out.append(f"**{who(b['behind'])} needs {b['need_share']:.1%}** of what's left to tie. The model has them "
                   f"getting {b['expected_share']:.1%}: {verdict}.")
    out.append(f"**Model finish:** {_lean(b['final_margin'], d, r)} ({who('D' if b['final'] > 0 else 'R')} by "
               f"{_votes(b['final'])}).")
    if scenario is not None:
        w = what_if(b, scenario)
        out.append(f"**If what's left breaks {_lean(scenario, d, r)} instead:** final {_lean(w['final_margin'], d, r)} "
                   f"({who(w['winner']) if w['winner'] else 'a tie'}"
                   + (f" by {_votes(w['final'])})." if w["winner"] else ")."))
    if b["confidence"] in ("none", "low"):
        out.append(f"-# Rough: only {b['done']} counties have finished, so the estimate of what's left leans on "
                   "2022 turnout more than on tonight's.")
    return out


def county_lines(b: dict, d: str = "D", r: str = "R") -> list[str]:
    """Where the outstanding vote is: the counties holding the most of it."""
    out = []
    for c in b["top"]:
        now = _lean(c["margin"], d, r) if c["margin"] is not None else "nothing counted"
        rest = _lean(c["left_margin"], d, r) if c["left_margin"] is not None else "?"
        out.append(f"**{c['name']}** ~{_votes(c['left'])} left ({c['share_of_left']:.0%} of it) · counted {now} · "
                   f"expected from here {rest} → {d if c['left_net'] > 0 else r} nets {_votes(c['left_net'])}")
    rest_n = len(b["counties"]) - len(b["top"])
    if rest_n > 0:
        rest_v = sum(c["left"] for c in b["counties"][len(b["top"]):])
        out.append(f"-# plus ~{_votes(rest_v)} across {rest_n} more counties")
    return out

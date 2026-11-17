"""Leave one election year out: fit on every other year, forecast the held-out year, score each layer.

    python pre_backtest.py                      # a short table
    python pre_backtest.py --json out.json      # everything
    python pre_backtest.py --model model.json   # also save the model fitted on all years (error_model.json)

Layers scored side by side on the same races: polls only, ratings only, fundamentals only, polls + ratings,
polls + fundamentals, all three, and the yardstick "polling average with one fixed width per office".
Standard library only.
"""
import argparse
import json
import math
from collections import defaultdict
from pathlib import Path

import cw_forecast
import cw_pre as P

HERE = Path(__file__).resolve().parent
FIRST_YEAR = 2006

CONFIGS = [("polls", ("polls",)), ("ratings", ("ratings",)), ("fund", ("fund",)),
           ("polls+ratings", ("polls", "ratings")), ("polls+fund", ("polls", "fund")),
           ("all three", ("polls", "ratings", "fund")), ("model", ("polls", "ratings", "fund")), ("yardstick", None)]
# "all three" is scored only where every layer exists; "model" is the same blend on every race, using whichever
# layers a race has (what the bot would show)
SCOPES = ("complete", "polled", "unpolled")     # complete: polls, ratings and fundamentals all present
BINS = ((0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 0.95), (0.95, 0.99), (0.99, 1.01))


def fold_model(history, ratings, year, hp=None):
    """The model for forecasting `year`: fitted on every other year (never on `year`)."""
    years = sorted({r["year"] for r in history} - {year})
    h = dict(hp or {})
    h["target_year"] = year
    return P.fit(history, ratings, years, h)


def _yard(model, row):
    """The polling average with one fixed width per office."""
    y = model["yardstick"].get(row["office"]) or model["yardstick"].get("ALL")
    if not P._has_polls(row) or y is None:
        return None
    return {"margin": row["avg_margin"], "sd": y["width"], "win_prob": cw_forecast.win_prob(row["avg_margin"], y["width"])}


def run(history, ratings, first_year=FIRST_YEAR, hp=None):
    """Returns {"folds": {year: info}, "records": [...], "scores": ..., "years": ...}. A record is one race in one
    layer configuration: margin, sd, win_prob, result, office, year, scope flags."""
    years = sorted({r["year"] for r in history})
    records, folds = [], {}
    for y in years:
        if y < first_year:
            continue
        model = fold_model(history, ratings, y, hp)
        mine = [r for r in history if r["year"] == y]
        model = P.with_moods(model, mine, ratings)
        folds[y] = model
        for r in mine:
            if r["result_margin"] is None:
                continue
            lv = ratings.get(r["race"])
            full = P.forecast(r, lv, model, layers=P.LAYERS)
            has = {l: full["parts"][l][0] is not None for l in P.LAYERS}
            polled = P._has_polls(r)
            flags = {"polled": polled, "unpolled": not polled, "complete": all(has.values())}
            for name, layers in CONFIGS:
                if layers is None:
                    f = _yard(model, r)
                    if f is None:
                        continue
                else:
                    f = P.forecast(r, lv, model, layers=None if name == "model" else layers)
                    if f["margin"] is None:
                        continue
                    if name != "model" and any(f["parts"][l][0] is None for l in layers):
                        continue
                records.append({"race": r["race"], "year": y, "office": r["office"], "config": name,
                                "margin": f["margin"], "sd": f["sd"], "half90": f.get("range90"), "win_prob": f["win_prob"],
                                "result": r["result_margin"], **flags})
    return {"folds": folds, "records": records}


def _half(x):
    """Half the width of a record's 90% range."""
    return x["half90"] if x.get("half90") is not None else P.Z90 * x["sd"]


def score(records):
    """Margin miss (rms, mean absolute), how often the 90% range held, Brier, wrong winners, n."""
    if not records:
        return None
    err = [x["result"] - x["margin"] for x in records]
    return {"n": len(records),
            "rmse": math.sqrt(sum(e * e for e in err) / len(err)),
            "mae": sum(abs(e) for e in err) / len(err),
            "cover90": sum(abs(e) <= _half(x) for e, x in zip(err, records)) / len(err),
            "brier": sum((x["win_prob"] - (x["result"] > 0)) ** 2 for x in records) / len(records),
            "wrong": sum((x["margin"] > 0) != (x["result"] > 0) for x in records)}


def calibration(records):
    """The favourite's stated chance, in bins, against how often that side won."""
    out = []
    for lo, hi in BINS:
        pick = []
        for x in records:
            fav = max(x["win_prob"], 1 - x["win_prob"])
            if lo <= fav < hi:
                won = (x["result"] > 0) == (x["win_prob"] >= 0.5)
                pick.append((fav, won))
        out.append({"bin": f"{int(lo * 100)}-{int(min(hi, 1) * 100)}" if hi <= 1 else "99+", "n": len(pick),
                    "stated": sum(p for p, _ in pick) / len(pick) if pick else None,
                    "won": sum(w for _, w in pick) / len(pick) if pick else None})
    return out


def summarize(history, ratings, first_year=FIRST_YEAR, hp=None):
    res = run(history, ratings, first_year, hp)
    recs = res["records"]
    table = {}
    for scope in SCOPES:
        table[scope] = {}
        for name, _ in CONFIGS:
            table[scope][name] = {}
            for office in ("ALL",) + P.OFFICES:
                sel = [x for x in recs if x["config"] == name and x[scope] and (office == "ALL" or x["office"] == office)]
                table[scope][name][office] = score(sel)
    calib = {}
    for scope in ("polled", "unpolled"):
        calib[scope] = {name: calibration([x for x in recs if x["config"] == name and x[scope]]) for name, _ in CONFIGS}
    peryear = {}
    for y, model in res["folds"].items():
        polled = [r for r in history if r["year"] == y and r["result_margin"] is not None and P._has_polls(r)]
        err = [r["result_margin"] - r["avg_margin"] for r in polled]
        c = P._c_for(model["c"], any(r["midterm"] for r in history if r["year"] == y))
        outside = {}
        for name in ("polls", "model"):
            sel = [x for x in recs if x["year"] == y and x["config"] == name and x["polled"]]
            outside[name] = {"n": len(sel), "outside": sum(abs(x["result"] - x["margin"]) > _half(x) for x in sel)}
        peryear[y] = {"races": len(err), "actual_shared_miss": sum(err) / len(err) if err else None,
                      "fitted_C": c, "outside90": outside}
    return {"first_year": first_year, "table": table, "calibration": calib, "per_year": peryear,
            "folds": {str(y): {"fitted_on": m["fitted_on"], "poll": m["poll"], "c": m["c"], "c_note": m["c_note"]}
                      for y, m in res["folds"].items()}}


def _fmt(s):
    if not s:
        return "      -"
    return f"{s['n']:4d} {s['rmse']:5.2f} {s['mae']:5.2f} {s['cover90'] * 100:4.0f}% {s['brier']:.3f} {s['wrong']:3d}"


def print_report(out):
    for scope in SCOPES:
        print(f"\n== {scope} races, held-out years {out['first_year']}+  (n, rmse, mae, 90% held, Brier, wrong)")
        for office in ("ALL",) + P.OFFICES:
            print(f"-- {office}")
            for name, _ in CONFIGS:
                print(f"  {name:14s}{_fmt(out['table'][scope][name][office])}")
    print("\n== per year: shared miss of the polls vs the fitted C; races outside the 90% range")
    for y, d in out["per_year"].items():
        o = d["outside90"]
        print(f"  {y}: races {d['races']:3d} shared miss {d['actual_shared_miss']:+5.2f} C {d['fitted_C']:.2f} "
              f"outside: polls {o['polls']['outside']}/{o['polls']['n']} model {o['model']['outside']}/{o['model']['n']}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--history", default=str(HERE / "history" / "pre_history.csv"))
    ap.add_argument("--ratings", default=str(HERE / "history" / "ratings.csv"))
    ap.add_argument("--first-year", type=int, default=FIRST_YEAR)
    ap.add_argument("--json", help="write every number to this file")
    ap.add_argument("--model", help="write the model fitted on all years to this file")
    a = ap.parse_args(argv)
    history, ratings = P.load_history(a.history), P.load_ratings(a.ratings)
    out = summarize(history, ratings, a.first_year)
    print_report(out)
    allm = P.fit(history, ratings, sorted({r["year"] for r in history}))
    if a.json:
        Path(a.json).write_text(json.dumps({"backtest": out, "fit_all_years": allm}, indent=1), encoding="utf-8")
    if a.model:
        Path(a.model).write_text(json.dumps(allm, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()

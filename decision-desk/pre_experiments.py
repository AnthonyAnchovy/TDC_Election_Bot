"""Questions about the pre-election model's shared miss, answered on the leave-one-year-out backtest.

    python pre_experiments.py [--json out.json] [--boot 1000]

1. SHARED_DF: the shared miss as a bell (inf), as Student-t with 10, 5, 3 degrees of freedom, and the current
   shape (None: win_prob's fat-tailed bell). Per setting: how often the 90% range held, overall and per year, the
   Brier score, the calibration table.
2. Calibration that respects years: the table again, with each bin's range from resampling whole years.
3. C with 12 or 13 years: its leave-one-year-out range and a bootstrap interval, and one C for all years against
   midterm and presidential apart.
Standard library only.
"""
import argparse
import json
import math
import random
from collections import defaultdict
from pathlib import Path

import cw_pre as P
import pre_backtest as B

HERE = Path(__file__).resolve().parent
DFS = (None, float("inf"), 10, 5, 3)
CONFIGS = ("polls", "model")


def name(df):
    return "current (win_prob)" if df is None else "bell" if df == float("inf") else f"t{df}"


def cal_by_year(records, bins=B.BINS):
    """{year: [(n, won) per bin]} for the favourite's stated chance."""
    out = defaultdict(lambda: [[0, 0] for _ in bins])
    for x in records:
        fav = max(x["win_prob"], 1 - x["win_prob"])
        won = (x["result"] > 0) == (x["win_prob"] >= 0.5)
        for i, (lo, hi) in enumerate(bins):
            if lo <= fav < hi:
                out[x["year"]][i][0] += 1
                out[x["year"]][i][1] += won
    return out


def bootstrap_calibration(records, boot, seed=7):
    """Each bin's won-rate with a 5% to 95% range from resampling whole years (so races of one year stay together)."""
    by = cal_by_year(records)
    years = sorted(by)
    rnd = random.Random(seed)
    draws = [[] for _ in B.BINS]
    for _ in range(boot):
        pick = [rnd.choice(years) for _ in years]
        for i in range(len(B.BINS)):
            n = sum(by[y][i][0] for y in pick)
            if n:
                draws[i].append(sum(by[y][i][1] for y in pick) / n)
    rows = []
    base = B.calibration(records)
    for i, c in enumerate(base):
        d = sorted(draws[i])
        lo, hi = (d[int(0.05 * len(d))], d[int(0.95 * len(d)) - 1]) if len(d) >= 20 else (None, None)
        rows.append({**c, "lo": lo, "hi": hi})
    return rows


def run_setting(history, ratings, hp, first_year=B.FIRST_YEAR):
    res = B.run(history, ratings, first_year, hp)
    out = {}
    for cfg in CONFIGS:
        recs = [x for x in res["records"] if x["config"] == cfg and x["polled"]]
        years = defaultdict(list)
        for x in recs:
            years[x["year"]].append(x)
        out[cfg] = {"score": B.score(recs), "calibration": B.calibration(recs),
                    "per_year_cover": {y: B.score(v)["cover90"] for y, v in sorted(years.items())},
                    "per_year_outside": {y: sum(abs(x["result"] - x["margin"]) > B._half(x) for x in v)
                                         for y, v in sorted(years.items())}, "records": recs}
    return out


# ---------------------------------------------------------------- C
def year_table(history, years):
    """Per year: the mean poll miss over all polled races, how many races, midterm or not; and the pooled own
    variance (what is left in a race's miss once its year's mean is taken out)."""
    by = defaultdict(list)
    for r in history:
        if r["year"] in years and r["result_margin"] is not None and P._has_polls(r):
            by[r["year"]].append(r["result_margin"] - r["avg_margin"])
    shared = {y: (sum(v) / len(v), len(v)) for y, v in by.items() if len(v) >= 5}
    within = [(e - shared[y][0]) ** 2 * len(v) / (len(v) - 1) for y, v in by.items() if y in shared for e in v]
    mid = {r["year"] for r in history if r["midterm"]}
    return shared, sum(within) / len(within), mid


def c_value(ys, shared, own2):
    if not ys:
        return None
    return math.sqrt(max(0.0, sum(shared[y][0] ** 2 for y in ys) / len(ys) - sum(own2 / shared[y][1] for y in ys) / len(ys)))


def c_intervals(history, boot, seed=11):
    years = sorted({r["year"] for r in history})
    shared, own2, mid = year_table(history, set(years))
    groups = {"midterm": sorted(y for y in shared if y in mid), "presidential": sorted(y for y in shared if y not in mid),
              "all years": sorted(shared)}
    rnd = random.Random(seed)
    out = {}
    for g, ys in groups.items():
        full = c_value(ys, shared, own2)
        loo = [c_value([z for z in ys if z != y], shared, own2) for y in ys]
        bs = sorted(c_value([rnd.choice(ys) for _ in ys], shared, own2) for _ in range(boot))
        out[g] = {"years": len(ys), "C": full, "leave_one_out_min": min(loo), "leave_one_out_max": max(loo),
                  "boot_5": bs[int(0.05 * boot)], "boot_95": bs[int(0.95 * boot) - 1]}
    return out, own2


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--history", default=str(HERE / "history" / "pre_history.csv"))
    ap.add_argument("--ratings", default=str(HERE / "history" / "ratings.csv"))
    ap.add_argument("--json")
    ap.add_argument("--boot", type=int, default=1000)
    a = ap.parse_args(argv)
    history, ratings = P.load_history(a.history), P.load_ratings(a.ratings)
    result = {"shared_df": {}, "c_split": {}}
    print("== SHARED_DF: polled races 2006-2024 (config model = polls + ratings)")
    print(f"{'setting':20s}{'config':7s}{'n':>5}{'rmse':>6}{'90%held':>8}{'Brier':>7}  per-year 90% held")
    for df in DFS:
        r = run_setting(history, ratings, {"SHARED_DF": df})
        result["shared_df"][name(df)] = {}
        for cfg in CONFIGS:
            s = r[cfg]["score"]
            yrs = " ".join(f"{str(y)[2:]}:{c * 100:.0f}" for y, c in r[cfg]["per_year_cover"].items())
            print(f"{name(df):20s}{cfg:7s}{s['n']:5d}{s['rmse']:6.2f}{s['cover90'] * 100:7.1f}%{s['brier']:7.4f}  {yrs}")
            boot = bootstrap_calibration(r[cfg]["records"], a.boot)
            d = {k: v for k, v in r[cfg].items() if k != "records"}
            d["calibration_year_bootstrap"] = boot
            result["shared_df"][name(df)][cfg] = d
        # same for the 90% range's overall, kept above; calibration for the model config:
        print("   calibration (model): " + "  ".join(
            f"{c['bin']}: {c['stated']:.2f}->{c['won']:.2f} [{c['lo']:.2f},{c['hi']:.2f}] n{c['n']}"
            for c in result["shared_df"][name(df)]["model"]["calibration_year_bootstrap"] if c["n"] and c["lo"] is not None))
    print("\n== C: leave-one-year-out and bootstrap over years (own variance held at its full-data value)")
    ci, own2 = c_intervals(history, a.boot)
    result["c_intervals"] = ci
    result["own_variance_pooled"] = own2
    for g, d in ci.items():
        print(f"  {g:13s} years {d['years']:2d}  C {d['C']:.2f}  leave-one-out {d['leave_one_out_min']:.2f} to "
              f"{d['leave_one_out_max']:.2f}  bootstrap 90% {d['boot_5']:.2f} to {d['boot_95']:.2f}")
    print("\n== C split by midterm/presidential against one C for all years (SHARED_DF current and t5)")
    for df in (None, 5):
        for split in (True, False):
            r = run_setting(history, ratings, {"SHARED_DF": df, "C_SPLIT": split})
            s = r["model"]["score"]
            result["c_split"][f"{name(df)} split={split}"] = {"score": s, "per_year_cover": r["model"]["per_year_cover"]}
            print(f"  {name(df):20s} split={split!s:5s} n {s['n']} rmse {s['rmse']:.2f} 90% held {s['cover90'] * 100:.1f}% "
                  f"Brier {s['brier']:.4f}")
    if a.json:
        Path(a.json).write_text(json.dumps(result, indent=1, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()

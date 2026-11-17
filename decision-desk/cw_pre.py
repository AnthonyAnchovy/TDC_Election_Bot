"""The forecast before any votes: fit on past races, forecast a race, add the races up.

Three layers each give a margin (Democratic minus Republican, in points) for a race:
  polls     the final polling average (cw_polls.average)
  ratings   what expert ratings turned into a margin, with the national mood they assume taken out
  fund      the fundamentals: the place's lean, the generic ballot and who holds the seat

`fit()` learns, from past years only, how wrong each layer is and how much to trust each, and returns a plain
dict that can be saved as JSON (error_model.json). `forecast()` uses that dict for one race. `simulate()` draws
many possible elections so races can be added up (Senate control and so on).

Widths: every forecast has an OWN width (this race's own miss) and a SHARED width (C, the miss every race in the
year shares, which does not shrink with more polls). The two add as squares. A race's chance of a Democratic win
comes from cw_forecast.win_prob, which has slightly fat tails.

Standard library only (the bot's PC has no numpy). No Discord. Nothing here reads a file unless asked
(load_history, load_ratings).
"""
import csv
import math
import random
from collections import defaultdict

import cw_forecast
import cw_polls

# ---------------------------------------------------------------- settings (every one can be passed in `hp`)
RECENCY = 0.8          # a past year's weight in the fundamentals fit is RECENCY ** (years away from the target year)
MIN_LEVEL_N = 8        # a rating level with fewer races than this borrows from its neighbours
MIN_CELL = 12          # fewer races than this in a (office, poll group, layers) cell: borrow from all offices pooled
MIN_YEAR_RACES = 5     # a year with fewer polled races than this is left out of the shared-miss estimate
MIN_YEARS_SPLIT = 5    # shared miss is fitted apart for midterm and presidential years only if each has this many
STEP = 0.05            # the blend weights go in steps of this size
MOOD_MAX_LEVEL = 1.5   # the mood raters assume is read only from races they call competitive (Lean or closer);
#                        the safe seats say nothing about it
RATING_MOOD = True     # take the raters' assumed national mood out and put the model's own (generic ballot) in
OWN_FLOOR = 1.0        # a race's own width never goes below this many points
MAX_FUND_MARGIN = 60   # races decided by more than this are left out of the fundamentals fit (uncontested)
STATE_SHARE = 0.5      # share of a race's own variance that races in one state in one year have in common
FAT_TAIL = (0.03, 3.0) # simulate: this share of each draw is three times as wide, as cw_forecast.win_prob does
Z90 = 1.645            # the 90% range is the margin plus or minus this many widths
USE_FUNDAMENTALS = False  # the forecast blends polls and ratings only; fundamentals stay fitted and scored
SHARED_DF = 5          # 5 is the recommended setting (see pre-model.md). None: the shared miss is a bell inside cw_forecast.win_prob's fat-tailed shape.
#                        A number (3, 5, 10, or float("inf") for a plain bell): the shared miss is a scaled
#                        Student-t with that many degrees of freedom, scaled so its spread still equals C
C_SPLIT = True         # C fitted apart for midterm and presidential years (False: one C for all years)

HP = {"RECENCY": RECENCY, "MIN_LEVEL_N": MIN_LEVEL_N, "MIN_CELL": MIN_CELL, "MIN_YEAR_RACES": MIN_YEAR_RACES,
      "MIN_YEARS_SPLIT": MIN_YEARS_SPLIT, "STEP": STEP, "MOOD_MAX_LEVEL": MOOD_MAX_LEVEL,
      "RATING_MOOD": RATING_MOOD, "OWN_FLOOR": OWN_FLOOR, "MAX_FUND_MARGIN": MAX_FUND_MARGIN,
      "USE_FUNDAMENTALS": USE_FUNDAMENTALS, "SHARED_DF": SHARED_DF, "C_SPLIT": C_SPLIT}

LAYERS = ("polls", "ratings", "fund")
OFFICES = ("PRES", "SEN", "GOV")
POLL_GROUPS = ("lt2", "2to5", "5plus")       # effective polls: under 2, 2 to 5, 5 or more
SUBSETS = (("polls",), ("ratings",), ("fund",), ("polls", "ratings"), ("polls", "fund"), ("ratings", "fund"),
           ("polls", "ratings", "fund"))


def _hp(hp=None):
    h = dict(HP)
    h.update(hp or {})
    return h


# ---------------------------------------------------------------- loading
_FLOATS = ("result_margin", "avg_margin", "avg_n_eff", "lean", "generic_ballot", "national_house_vote",
           "avg_newest_days")


def load_history(path):
    """history/pre_history.csv as a list of dicts with the numbers read as numbers (blank is None)."""
    out = []
    with open(path, encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            for k in _FLOATS:
                r[k] = float(r[k]) if r.get(k) not in ("", None) else None
            r["year"] = int(r["year"])
            r["midterm"] = int(r["midterm"] or 0)
            r["polls_final21"] = int(r.get("polls_final21") or 0)
            out.append(r)
    return out


def load_ratings(path):
    """history/ratings.csv as {race: [level, ...]}, one level per rater (levels that are not numbers are left out)."""
    out = defaultdict(list)
    with open(path, encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            try:
                out[r["race"]].append(float(r["level"]))
            except (ValueError, TypeError, KeyError):
                pass
    return dict(out)


# ---------------------------------------------------------------- small helpers
def _mean(xs):
    return sum(xs) / len(xs)


def _rms(xs):
    return math.sqrt(sum(x * x for x in xs) / len(xs))


def _sd(xs):
    if len(xs) < 2:
        return 0.0
    m = _mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / len(xs))


def _group(n_eff):
    return "lt2" if n_eff < 2 else "2to5" if n_eff < 5 else "5plus"


def _inc(row):
    """Incumbency: the sitting officeholder's party if they run (D +1, R -1); open, blank or other is 0."""
    v = (row.get("incumbent_running") or "").strip().upper()
    return 1.0 if v == "D" else -1.0 if v == "R" else 0.0


def _has_polls(row):
    return row.get("avg_margin") is not None and (row.get("avg_n_eff") or 0) > 0


def _level(levels):
    """The average across raters, rounded to the nearest 0.5."""
    if not levels:
        return None
    return math.floor(_mean(levels) * 2 + 0.5) / 2


def _key(level):
    return f"{level:.1f}"


def _solve(a, b):
    k = len(b)
    m = [a[i][:] + [b[i]] for i in range(k)]
    for i in range(k):
        p = max(range(i, k), key=lambda r: abs(m[r][i]))
        m[i], m[p] = m[p], m[i]
        for r in range(k):
            if r != i:
                c = m[r][i] / m[i][i]
                m[r] = [x - c * y for x, y in zip(m[r], m[i])]
    return [m[i][k] / m[i][i] for i in range(k)]


def _wls(xs, ys, ws):
    """Weighted least squares (a tiny ridge keeps a feature that never varies from breaking the solve)."""
    k = len(xs[0])
    a = [[sum(w * x[i] * x[j] for x, w in zip(xs, ws)) + (1e-6 if i == j else 0.0) for j in range(k)]
         for i in range(k)]
    b = [sum(w * x[i] * y for x, y, w in zip(xs, ys, ws)) for i in range(k)]
    return _solve(a, b)


# ---------------------------------------------------------------- layer 1: polls (A, B, C)
def _poll_fit(rows, years, hp):
    """Poll miss by office (A, B) and the yearly shared miss (C). Each year's shared miss is the mean miss of
    all that year's polled races (every office together); it is taken out before A and B are fitted."""
    polled = [r for r in rows if r["year"] in years and r["result_margin"] is not None and _has_polls(r)]
    by_year = defaultdict(list)
    for r in polled:
        by_year[r["year"]].append(r["result_margin"] - r["avg_margin"])
    shared = {y: _mean(v) for y, v in by_year.items() if len(v) >= hp["MIN_YEAR_RACES"]}
    within = []                                    # (office, n_eff, squared own miss)
    for r in polled:
        y = r["year"]
        if y in shared:
            k = len(by_year[y])
            e = r["result_margin"] - r["avg_margin"] - shared[y]
            within.append((r["office"], r["avg_n_eff"], e * e * k / (k - 1)))   # k/(k-1): the mean took a share
    own2 = _mean([w[2] for w in within]) if within else 0.0
    # C: root of the mean of squares of the yearly means, less the part that is only the races' own noise
    mid = {y for y in shared if _is_midterm(rows, y)}
    pres = set(shared) - mid
    noise = lambda ys: _mean([own2 / len(by_year[y]) for y in ys])
    c_sq = lambda ys: max(0.0, _mean([shared[y] ** 2 for y in ys]) - noise(ys)) if ys else 0.0
    if hp["C_SPLIT"] and len(mid) >= hp["MIN_YEARS_SPLIT"] and len(pres) >= hp["MIN_YEARS_SPLIT"]:
        c = {"midterm": math.sqrt(c_sq(mid)), "presidential": math.sqrt(c_sq(pres))}
        c_note = (f"fitted apart: midterm years ({len(mid)}) and presidential years ({len(pres)}), "
                  "the root of the mean of squares of each year's shared miss less its own-noise part")
    else:
        c = {"all": math.sqrt(c_sq(set(shared)))}
        c_note = (f"one value for all {len(shared)} years: " + ("C_SPLIT is off" if not hp["C_SPLIT"] else
                  f"fewer than {hp['MIN_YEARS_SPLIT']} midterm or presidential years to fit them apart"))
    poll = {}
    for office in OFFICES + ("ALL",):
        g = [(n, e2) for o, n, e2 in within if office == "ALL" or o == office]
        if len(g) >= 3:
            poll[office] = _ab(g)
    yard = {}
    for office in OFFICES + ("ALL",):
        e = [r["result_margin"] - r["avg_margin"] for r in polled if office == "ALL" or r["office"] == office]
        if e:
            yard[office] = {"width": _rms(e), "n": len(e)}
    return poll, c, c_note, {str(y): m for y, m in sorted(shared.items())}, yard


def _is_midterm(rows, year):
    return any(r["year"] == year and r["midterm"] for r in rows)


def _ab(g):
    """Least squares of squared miss on [1, 1/n]; each part is kept at zero or more."""
    xs = [1.0 / n for n, _ in g]
    ys = [e2 for _, e2 in g]
    m = len(g)
    sx, sy = sum(xs), sum(ys)
    sxx, sxy = sum(x * x for x in xs), sum(x * y for x, y in zip(xs, ys))
    det = m * sxx - sx * sx
    a2, b2 = sy / m, 0.0                               # the fallback: a flat width
    if abs(det) > 1e-12:
        b = (m * sxy - sx * sy) / det
        a = (sy - b * sx) / m
        if b >= 0 and a >= 0:
            a2, b2 = a, b
        elif a < 0 and sxx > 0:                        # no floor at all: only the few-polls part
            a2, b2 = 0.0, max(0.0, sxy / sxx)
    return {"A": math.sqrt(a2), "B": math.sqrt(b2), "n": m, "rmse": math.sqrt(sy / m)}


def poll_sd(model, office, n_eff):
    """A well-polled race's own miss, wider with few polls: sqrt(A^2 + B^2 / n)."""
    p = model["poll"].get(office) or model["poll"].get("ALL")
    if p is None:
        return None
    return math.sqrt(p["A"] ** 2 + p["B"] ** 2 / max(n_eff, 1e-9))


# ---------------------------------------------------------------- layer 2: ratings
def _lookup(rows, ratings, years, hp):
    """For each rating level: the mean result margin and the spread around it. A level with fewer than
    MIN_LEVEL_N races is filled from the nearest levels (their margins moved along the overall slope to this
    level), widening one step at a time until there are enough."""
    pts = []
    for r in rows:
        lv = _level(ratings.get(r["race"]))
        if r["year"] in years and r["result_margin"] is not None and lv is not None:
            pts.append((lv, r["result_margin"]))
    if len(pts) < 2:
        return {"levels": {}, "slope": 0.0, "n": len(pts)}
    mx, my = _mean([p[0] for p in pts]), _mean([p[1] for p in pts])
    sxx = sum((p[0] - mx) ** 2 for p in pts)
    slope = sum((p[0] - mx) * (p[1] - my) for p in pts) / sxx if sxx else 0.0
    lo, hi = min(p[0] for p in pts), max(p[0] for p in pts)
    levels, lv = {}, lo
    while lv <= hi + 1e-9:
        reach = 0.0
        while True:
            near = [p for p in pts if abs(p[0] - lv) <= reach + 1e-9]
            if len(near) >= hp["MIN_LEVEL_N"] or reach > hi - lo:
                break
            reach += 0.5
        moved = [m + slope * (lv - l) for l, m in near]
        levels[_key(lv)] = {"mean": _mean(moved), "sd": _sd(moved), "n": sum(1 for p in pts if p[0] == lv),
                            "borrowed": reach > 0}
        lv += 0.5
    return {"levels": levels, "slope": slope, "n": len(pts)}


def _rating_cell(lookup, level):
    lv = lookup["levels"]
    if not lv:
        return None
    ks = sorted(lv, key=float)
    k = _key(level)
    if k in lv:
        return lv[k]
    return lv[min(ks, key=lambda s: abs(float(s) - level))]


def year_moods(rows, ratings, lookup, hp=None):
    """The national mood the ratings assume, per year: the mean of (margin the rating implies minus the place's
    lean) over the year's competitive-rated races. Needs 5 such races or the year is left out."""
    h = _hp(hp)
    by = defaultdict(list)
    for r in rows:
        lv = _level(ratings.get(r["race"]))
        if lv is None or abs(lv) > h["MOOD_MAX_LEVEL"] or r.get("lean") is None:
            continue
        cell = _rating_cell(lookup, lv)
        if cell:
            by[r["year"]].append(cell["mean"] - r["lean"])
    return {y: _mean(v) for y, v in by.items() if len(v) >= 5}


def _rating_pred(lookup, levels, row, mood, hp):
    lv = _level(levels)
    if lv is None:
        return None
    cell = _rating_cell(lookup, lv)
    if cell is None:
        return None
    m = cell["mean"]
    if hp["RATING_MOOD"] and mood is not None and row.get("generic_ballot") is not None:
        m += row["generic_ballot"] - mood          # the raters' mood out, the model's own in
    return m


# ---------------------------------------------------------------- layer 3: fundamentals
def _fund_x(row):
    return [1.0, row["lean"], row["generic_ballot"], _inc(row)]


def _fund_ok(row):
    return row.get("lean") is not None and row.get("generic_ballot") is not None


def _fund_fit(rows, years, target, hp):
    """Result margin on lean, generic ballot and incumbency, per office, each year weighted RECENCY ** years away
    from `target`. Returns {office: {"coef": [...], "n": n}} (and "ALL", all offices together, as a fallback)."""
    out = {}
    for office in OFFICES + ("ALL",):
        g = [r for r in rows if r["year"] in years and r["result_margin"] is not None and _fund_ok(r)
             and abs(r["result_margin"]) <= hp["MAX_FUND_MARGIN"] and (office == "ALL" or r["office"] == office)]
        if len(g) >= 5:
            w = [hp["RECENCY"] ** abs(target - r["year"]) for r in g]
            out[office] = {"coef": _wls([_fund_x(r) for r in g], [r["result_margin"] for r in g], w), "n": len(g)}
    return out


def _fund_pred(fund, row):
    if not _fund_ok(row):
        return None
    f = fund.get(row["office"]) or fund.get("ALL")
    if f is None:
        return None
    return sum(c * x for c, x in zip(f["coef"], _fund_x(row)))


# ---------------------------------------------------------------- fitting everything
def _nested(rows, ratings, years, hp):
    """Each training year's layer predictions made from the OTHER training years only, so the blend weights and
    widths are judged on predictions that did not see their own race."""
    out = {}
    for y in years:
        others = [z for z in years if z != y]
        fund = _fund_fit(rows, others, y, hp)
        look = _lookup(rows, ratings, others, hp)
        mine = [r for r in rows if r["year"] == y]
        moods = year_moods(mine, ratings, look, hp)
        for r in mine:
            out[r["race"]] = {"ratings": _rating_pred(look, ratings.get(r["race"]), r, moods.get(y), hp),
                              "fund": _fund_pred(fund, r)}
    return out


def _compositions(k, steps):
    if k == 1:
        yield (steps,)
        return
    for i in range(steps + 1):
        for rest in _compositions(k - 1, steps - i):
            yield (i,) + rest


def _best_weights(samples, layers, step):
    """samples: [(result, {layer: pred})]. The weights (0 to 1 in `step`s, adding to 1) with the smallest
    root-mean-square miss."""
    steps = int(round(1 / step))
    best = None
    for comp in _compositions(len(layers), steps):
        w = [c / steps for c in comp]
        sse = sum((y - sum(wi * p[l] for wi, l in zip(w, layers))) ** 2 for y, p in samples)
        if best is None or sse < best[0] - 1e-12:
            best = (sse, w)
    return best[1], math.sqrt(best[0] / len(samples))


def _cell_groups(subset):
    return POLL_GROUPS if "polls" in subset else ("all", "none")


def _in_group(row, group):
    if group == "all":
        return True
    if group == "none":
        return not _has_polls(row)
    return _has_polls(row) and _group(row["avg_n_eff"]) == group


def _c_for(c, midterm):
    return c.get("midterm" if midterm else "presidential", c.get("all"))


def _fit_cells(rows, nested, c, hp):
    """The blend weights and own width for every (office, layers, poll group) cell."""
    train = []
    for r in rows:
        if r["result_margin"] is None:
            continue
        parts = {}
        if _has_polls(r):
            parts["polls"] = r["avg_margin"]
        n = nested.get(r["race"], {})
        for l in ("ratings", "fund"):
            if n.get(l) is not None:
                parts[l] = n[l]
        train.append((r, parts))
    weights = {}
    for office in OFFICES + ("ALL",):
        weights[office] = {}
        for subset in SUBSETS:
            if subset == ("polls",):
                continue                                         # the polls alone use A and B
            key = "+".join(subset)
            for group in _cell_groups(subset):
                s = [(r, p) for r, p in train if (office == "ALL" or r["office"] == office)
                     and all(l in p for l in subset) and _in_group(r, group)]
                if len(s) < hp["MIN_CELL"]:
                    continue
                w, miss = _best_weights([(r["result_margin"], p) for r, p in s], subset, hp["STEP"])
                shared2 = _mean([_c_for(c, r["midterm"]) ** 2 for r, _ in s])
                own = math.sqrt(max(miss * miss - shared2, hp["OWN_FLOOR"] ** 2))
                full = {l: 0.0 for l in LAYERS}
                full.update(dict(zip(subset, w)))
                weights[office].setdefault(group, {})[key] = {"weights": full, "miss": miss, "own": own, "n": len(s)}
    return weights


def fit(history, ratings, years, hp=None):
    """Everything the forecast needs, fitted on `years` only. A plain dict, ready for json.dump.

    `history` is load_history()'s rows, `ratings` is load_ratings()'s {race: [levels]}. Pass
    hp={"target_year": Y} to weight the fundamentals' years by distance to Y (the default is the latest year
    fitted)."""
    h = _hp(hp)
    years = sorted(set(years))
    rows = [r for r in history if r["year"] in years]
    target = (hp or {}).get("target_year", max(years))
    poll, c, c_note, shared, yard = _poll_fit(rows, set(years), h)
    look = _lookup(rows, ratings, set(years), h)
    fund = _fund_fit(rows, set(years), target, h)
    nested = _nested(rows, ratings, years, h)
    # honest misses of the two other layers: their predictions made without their own year
    res = {"ratings": defaultdict(list), "fund": defaultdict(list)}
    for r in rows:
        if r["result_margin"] is None:
            continue
        for l in ("ratings", "fund"):
            p = nested.get(r["race"], {}).get(l)
            if p is not None:
                res[l][r["office"]].append(r["result_margin"] - p)
                res[l]["ALL"].append(r["result_margin"] - p)
    miss = {l: {o: {"miss": _rms(v), "n": len(v)} for o, v in d.items()} for l, d in res.items()}
    for o, d in fund.items():
        d["miss"] = miss["fund"].get(o, {}).get("miss")
    return {
        "fitted_on": years, "target_year": target, "shared_df": h["SHARED_DF"], "settings": {k: h[k] for k in HP},
        "poll": poll, "c": c, "c_note": c_note, "year_shared_miss": shared,
        "ratings": {"lookup": look["levels"], "slope": look["slope"], "n": look["n"], "miss": miss["ratings"],
                    "moods": {str(y): m for y, m in year_moods(rows, ratings, look, h).items()}},
        "fund": {"coef_order": ["const", "lean", "generic_ballot", "incumbent"], "by_office": fund,
                 "miss": miss["fund"]},
        "weights": _fit_cells(rows, nested, c, h),
        "yardstick": yard,
    }


# ---------------------------------------------------------------- a fat-tailed shared miss
_NODES = {}


def shared_nodes(df, bins=200):
    """`bins` equally likely values standing for a Student-t with `df` degrees of freedom (inf: a bell), scaled so
    its spread (standard deviation) is 1. Each value is the mean of its slice of the distribution, so the tails
    are kept (a little thinner than the true tails: the farthest slice is one value)."""
    key = (df, bins)
    if key in _NODES:
        return _NODES[key]
    step, count = 0.002, 9001                         # t = sinh(u) spreads the grid out into the tails
    ts, ws = [], []
    for i in range(count):
        u = -9 + (i + 0.5) * step
        t = math.sinh(u)
        if math.isinf(df):
            d = math.exp(-t * t / 2)
        else:
            d = math.exp(-(df + 1) / 2 * math.log1p(t * t / df))
        ts.append(t)
        ws.append(d * math.cosh(u) * step)
    total = sum(ws)
    nodes_w, nodes_t = [0.0] * bins, [0.0] * bins
    run = 0.0
    for t, w in zip(ts, ws):
        b = min(bins - 1, int((run + w / 2) / total * bins))
        nodes_w[b] += w
        nodes_t[b] += w * t
        run += w
    spread = 1.0 if math.isinf(df) else math.sqrt(df / (df - 2))
    nodes = [nt / nw / spread for nt, nw in zip(nodes_t, nodes_w) if nw > 0]
    _NODES[key] = nodes
    return nodes


def _ncdf(z):
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def win_prob_t(margin, own, shared, df):
    """Chance of a Democratic win when the race's own miss is a bell (width `own`) and the shared miss is a
    scaled Student-t (spread `shared`, `df` degrees of freedom): the bell's chance averaged over the t."""
    nodes = shared_nodes(df)
    return sum(_ncdf((margin + shared * t) / own) for t in nodes) / len(nodes)


def half_width90(own, shared, df):
    """Half the width of the central 90% range of (own bell + shared t), found by bisection."""
    nodes = shared_nodes(df)
    lo, hi = 0.0, 8 * own + shared * max(abs(t) for t in nodes)
    for _ in range(30):
        mid = (lo + hi) / 2
        if sum(_ncdf((mid - shared * t) / own) for t in nodes) / len(nodes) < 0.95:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


# ---------------------------------------------------------------- forecasting one race
def with_moods(model, rows, ratings):
    """A copy of `model` that also knows the national mood the ratings assume in the years of `rows`
    (it needs only that year's ratings, which are known before the election)."""
    look = {"levels": model["ratings"]["lookup"], "slope": model["ratings"]["slope"]}
    moods = year_moods(rows, ratings, look, model["settings"])
    m = dict(model)
    m["ratings"] = dict(model["ratings"])
    m["ratings"]["moods"] = dict(model["ratings"]["moods"])
    m["ratings"]["moods"].update({str(y): v for y, v in moods.items()})
    return m


def _cell(model, office, group, key):
    for o in (office, "ALL"):
        c = model["weights"].get(o, {}).get(group, {}).get(key)
        if c is not None:
            return c
    return None


def forecast(race_row, ratings_for_race, model, polls=None, as_of=None, layers=None):
    """One race. Returns {"margin", "sd_own", "sd_shared", "sd", "win_prob", "parts", "group", "n_eff"}.

    `layers` defaults to polls and ratings (and fundamentals too if the model's USE_FUNDAMENTALS is on). The model's
    SHARED_DF, when it is a number, makes the chance and `range90` (the half width of the 90% range) come from a
    bell own miss plus a Student-t shared miss; with None `range90` is None and the chance is win_prob's.
    `ratings_for_race` is the list of raters' levels (or None). With `polls` (cw_polls records) the average is
    taken at `as_of`; otherwise the row's avg_margin and avg_n_eff are used. `layers` limits which layers may be
    used (the backtest scores them one by one). `parts` is {layer: (margin or None, weight)}: a layer that could
    not be used has margin None and weight 0. If no layer can be used, margin and win_prob are None."""
    st = model["settings"]
    if layers is None:
        layers = LAYERS if st.get("USE_FUNDAMENTALS") else ("polls", "ratings")
    if polls is not None:
        avg = cw_polls.average(polls, race_row["race"], as_of)
        pm, n = avg["margin"], avg["effective_polls"]
    else:
        pm, n = race_row.get("avg_margin"), race_row.get("avg_n_eff")
    have = {}
    if "polls" in layers and pm is not None and n and n > 0:
        have["polls"] = pm
    if "ratings" in layers:
        mood = model["ratings"]["moods"].get(str(race_row["year"]))
        look = {"levels": model["ratings"]["lookup"]}
        p = _rating_pred(look, ratings_for_race, race_row, mood, st)
        if p is not None:
            have["ratings"] = p
    if "fund" in layers:
        p = _fund_pred(model["fund"]["by_office"], race_row)
        if p is not None:
            have["fund"] = p
    parts = {l: (None, 0.0) for l in LAYERS}
    out = {"margin": None, "sd_own": None, "sd_shared": None, "sd": None, "win_prob": None, "range90": None, "parts": parts,
           "group": None, "n_eff": n if "polls" in have else 0.0}
    c = _c_for(model["c"], race_row.get("midterm"))
    office = race_row["office"]
    use = tuple(l for l in LAYERS if l in have)
    chosen = None
    while use and chosen is None:
        if use == ("polls",):
            own = poll_sd(model, office, n)
            if own is not None:
                chosen = ({"polls": 1.0}, own, "polls")
        else:
            group = _group(n) if "polls" in use else ("none" if "polls" not in have else "all")
            cell = _cell(model, office, group, "+".join(use))
            if cell is None and group == "none":
                cell = _cell(model, office, "all", "+".join(use))
            if cell is not None:
                chosen = (cell["weights"], cell["own"], group)
        if chosen is None:
            use = use[:1] if len(use) > 1 else ()      # no cell fitted for this mix: the first layer alone
    if chosen is None:
        return out
    w, own, group = chosen
    margin = sum(w.get(l, 0.0) * have[l] for l in use)
    for l in use:
        parts[l] = (have[l], w.get(l, 0.0))
    sd = math.sqrt(own * own + c * c)
    df = st.get("SHARED_DF")
    if df is None:
        chance, half = cw_forecast.win_prob(margin, sd), None
    else:
        chance, half = win_prob_t(margin, own, c, df), half_width90(own, c, df)
    out.update({"margin": margin, "sd_own": own, "sd_shared": c, "sd": sd, "win_prob": chance, "range90": half,
                "group": group})
    return out


# ---------------------------------------------------------------- adding races up
def simulate(forecasts, draws=10000, seed=2026, chamber=None, joint=None, shared_df=SHARED_DF):
    """Draw many possible elections. Each draw has one shared miss per election year (its width is each race's
    sd_shared), one miss per state per year shared by that state's races (STATE_SHARE of each race's own
    variance, only where a state has more than one race that year) and each race's own. Each draw is slightly fat
    tailed: FAT_TAIL[0] of the time it is FAT_TAIL[1] times as wide, the way cw_forecast.win_prob is.

    `forecasts`: dicts with race, year, state, margin, sd_own, sd_shared (forecast() output plus the row's id,
    year, state). `chamber`: {"races": [ids], "held": seats already held, "majority": seats needed}; returns the
    chance the Democratic side reaches the majority and the seat counts. `joint`: a list of ids; returns the most
    common combination of winners among them.
    `shared_df`: a number draws the year's shared miss from a scaled Student-t (spread still sd_shared, no extra
    fat-tail mix for it); None draws it as the bell with the fat-tail mix.
    Returns {"win_prob": {race: p}, "chamber": ..., "joint": ...}."""
    fs = [f for f in forecasts if f.get("margin") is not None]
    rnd = random.Random(seed)
    tail_p, tail_k = FAT_TAIL

    def z():
        return rnd.gauss(0.0, 1.0) * (tail_k if rnd.random() < tail_p else 1.0)

    def zyear():
        if shared_df is None:
            return z()
        if math.isinf(shared_df):
            return rnd.gauss(0.0, 1.0)
        g = rnd.gammavariate(shared_df / 2, 2.0)
        return rnd.gauss(0.0, 1.0) / math.sqrt(g / shared_df) / math.sqrt(shared_df / (shared_df - 2))

    states = defaultdict(int)
    for f in fs:
        states[(f["year"], f["state"])] += 1
    wins = defaultdict(int)
    seats_hist = defaultdict(int)
    joint_hist = defaultdict(int)
    reach = 0
    chamber_ids = set(chamber["races"]) if chamber else set()
    for _ in range(draws):
        year_z, state_z = {}, {}
        dem = 0
        won = {}
        for f in fs:
            y, st = f["year"], (f["year"], f["state"])
            if y not in year_z:
                year_z[y] = zyear()
            shared = f["sd_shared"] * year_z[y]
            if states[st] > 1:
                if st not in state_z:
                    state_z[st] = z()
                state_part = math.sqrt(STATE_SHARE) * f["sd_own"] * state_z[st]
                own = math.sqrt(1 - STATE_SHARE) * f["sd_own"] * z()
            else:
                state_part, own = 0.0, f["sd_own"] * z()
            d = f["margin"] + shared + state_part + own > 0
            won[f["race"]] = d
            wins[f["race"]] += d
            if f["race"] in chamber_ids and d:
                dem += 1
        if chamber:
            total = chamber["held"] + dem
            seats_hist[total] += 1
            reach += total >= chamber["majority"]
        if joint:
            joint_hist[tuple(won.get(r) for r in joint)] += 1
    out = {"win_prob": {f["race"]: wins[f["race"]] / draws for f in fs}, "chamber": None, "joint": None}
    if chamber:
        out["chamber"] = {"p_majority": reach / draws,
                          "mean_seats": sum(k * v for k, v in seats_hist.items()) / draws,
                          "seats": {k: v / draws for k, v in sorted(seats_hist.items())}}
    if joint and joint_hist:
        top, count = max(joint_hist.items(), key=lambda kv: kv[1])
        out["joint"] = {"races": list(joint), "outcome": {r: ("D" if d else "R") for r, d in zip(joint, top)},
                        "p": count / draws}
    return out

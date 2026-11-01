"""The field: every race as a circle on one line from safe Democratic to safe Republican, moving through the night.

Position is the margin the desk's own numbers give the race, and size is the votes counted so far:
  Senate  County Watch's projection once counties finish counting (cw_model). Before that, the state's 2024 margin
          at midterm turnout, or the Safe column for Cook Solid races.
  House   the 82 rated seats at their ratings average. In FL, NC and VA the state's live file pulls a seat toward its
          counted margin as the count comes in, weighted by the share reporting, since early counts lean one way.
Called races leave the field for their party's end zone.

snapshot() records where everything stands, render() draws it (a PNG, or a GIF that tweens from an earlier
snapshot), timelapse() strings a night's snapshots together, and demo_night() makes a simulated night for /fieldtest.
"""
from __future__ import annotations

import io
import json
import math
import random
import time
import zlib
from datetime import datetime, timedelta
from pathlib import Path

from PIL import Image, ImageDraw

import cards as C
import competitive as CMP
import night as N
import races as R
import scoring as S

HERE = Path(__file__).parent
HISTORY = HERE / "countywatch" / "field_history.json"
KINDS = ("senate", "house")
TITLE = {"senate": "SENATE", "house": "HOUSE BATTLEGROUND"}
RATING_M = {"Likely D": (8.6, 14.4), "Lean D": (3.6, 7.4), "Tilt D": (1.2, 2.8), "Toss-up": (-1.0, 1.0),
            "Tilt R": (-2.8, -1.2), "Lean R": (-7.4, -3.6), "Likely R": (-14.4, -8.6)}    # where a rating sits
EDGES = (3, 8, 15, 30)              # toss-up inside 3 points, lean to 8, likely to 15, safe beyond
COLS = ["CALLED", "Safe", "Likely", "Lean", "Toss-up", "Lean", "Likely", "Safe", "CALLED"]
AXIS = ["D+15", "D+8", "D+3", "R+3", "R+8", "R+15"]
SIZE = {"senate": (21, 56, 6_000_000, 17), "house": (18, 30, 350_000, 13)}    # r min, r max, votes at max, end zone r
W, H, PAD, SS = 1600, 900, 60, 2
TOP, BOTTOM = 262, 792
COLW = (W - 2 * PAD) / 9

THEMES = {
    "dark": {"bg": (17, 19, 24), "rule": (48, 52, 62), "ink": (240, 242, 245), "muted": (140, 146, 158),
             "D": (64, 132, 255), "R": (240, 64, 64), "I": (170, 120, 255), "hollow": (17, 19, 24)},
    "paper": {"bg": C.PAPER, "rule": C.RULE, "ink": C.INK, "muted": C.GREY,
              "D": C.ED_COL["D"], "R": C.ED_COL["R"], "I": C.ED_COL["I"], "hollow": C.PAPER},
}


def palette(card_theme: str) -> str:
    return "paper" if card_theme == "editorial" else "dark"


def _candidates() -> dict:
    try:
        data = json.loads((HERE / "candidates.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k.upper(): v for k, v in data.items() if not k.startswith("_")}


def _spread(rid: str, lo: float, hi: float) -> float:
    return lo + (hi - lo) * (zlib.crc32(rid.encode()) % 100) / 99


def safe_m(rid: str, party: str) -> float:
    """A Cook Solid race with nothing counted: somewhere across the Safe column (spread by name, so they don't
    pile up), since there's no margin of ours to place it by."""
    m = _spread(rid, 17, 28)
    return m if party == "D" else -m


def rating_m(rid: str, rating: str) -> float:
    """A rated House seat before it has votes: spread across its rating's band, which is all a rating says."""
    return _spread(rid, *RATING_M[rating])


def race_ids(kind: str) -> list[str]:
    if kind == "senate":
        return [rid for rid, r in R.RACES.items() if r["type"] == "senate"]
    return [rid for rid, _, _ in CMP.RATINGS]


def _side(rid: str, cands: dict) -> str:
    """The party on the left of the field: the Democrat, or the independent where Democrats didn't field one."""
    c = cands.get(rid) or {}
    return "I" if c.get("I") and not c.get("D") else "D"


def _clock(when: datetime) -> str:
    return when.astimezone(N.ET).strftime("%I:%M %p ET").lstrip("0")


# ------------------------------------------------------------------ snapshots
def snapshot(kind: str, calls: dict, *, live: dict | None = None, cw_cache: dict | None = None,
             when: datetime | None = None) -> dict:
    """Where every race stands. races: {rid: [margin in points (D side +), votes counted, state, called party, side]}
    with state "wait" (nothing counted), "count" (counting, no projection yet) or "proj" (projected).
    live: {race_id: race} from results.fetch_state, for House seats in the feed states."""
    cands = _candidates()
    out = {}
    if kind == "senate":
        import countywatch_run as CWR
        import cw_model as M
        cache = cw_cache or CWR.live_cache()
        man, fac = CWR.manual(), CWR.factors()
        for rid in race_ids("senate"):
            st = R.RACES[rid]["state"]
            safe = CMP.SAFE.get(rid)
            m, v, state = safe_m(rid, safe) if safe else 0.0, 0, "wait"
            if st in M.STATES:
                p = CWR.projection(st, cache, man, fac)
                v = int(p["counted"])
                if p["proj_m"] is not None:
                    m, state = 100 * p["proj_m"], "proj"
                else:
                    m = m if safe else 100 * p["base_m"]
                    state = "count" if v else "wait"
            out[rid] = [round(m, 2), v, state, calls.get(rid), _side(rid, cands)]
    else:
        for rid, rating, _ in CMP.RATINGS:
            m, v, state = rating_m(rid, rating), 0, "wait"
            race = (live or {}).get(rid)
            if race and race.get("cands"):
                d = sum(c["votes"] for c in race["cands"] if c.get("party") == "D")
                r = sum(c["votes"] for c in race["cands"] if c.get("party") == "R")
                v = sum(c["votes"] for c in race["cands"])
                if d + r:
                    rep = race.get("reporting")
                    w = rep[0] / rep[1] if rep and rep[1] else 0.5
                    m = m * (1 - w) + 100 * (d - r) / (d + r) * w
                    state = "proj" if w >= 0.5 else "count"
            out[rid] = [round(m, 2), v, state, calls.get(rid), _side(rid, cands)]
    when = when or datetime.now(N.ET)
    return {"kind": kind, "ts": when.timestamp(), "clock": _clock(when), "races": out,
            "counts": S.chamber_counts(kind, calls)}


def _totals(snap: dict) -> dict:
    """Seats each side has (called, plus holdovers for the Senate) and would have if every uncalled race in the field
    went the way it leans now."""
    kind, races, counts = snap["kind"], snap["races"], snap["counts"]
    if kind == "senate":
        have = {p: counts.get(p, 0) for p in ("D", "R", "I")}
        proj = dict(have)
        for m, _v, _st, win, side in races.values():
            if not win and m:
                proj[side if m > 0 else "R"] += 1
        return {"have": have, "proj": proj}
    # House: unrated seats are Safe, so they count for their side; rated seats follow the field
    fav_d = sum(1 for rid, _, _ in CMP.RATINGS if CMP.favored(rid) == "D")
    d = CMP.D_BASELINE - fav_d + sum(1 for m, _v, _st, win, _s in races.values() if (win or ("D" if m > 0 else "R")) == "D")
    i = counts.get("I", 0)
    return {"have": {"D": counts["D"], "R": counts["R"], "I": i}, "proj": {"D": d, "R": R.HOUSE_TOTAL - d - i, "I": i}}


def changed(a: dict | None, b: dict) -> bool:
    return not a or a["races"] != b["races"]


# ------------------------------------------------------------------ history (for GIFs between posts and the timelapse)
def _load_history() -> dict:
    try:
        return json.loads(HISTORY.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def history(kind: str) -> list[dict]:
    return _load_history().get(kind, [])


def record(snap: dict):
    """Keep a snapshot when something moved since the last one kept."""
    h = _load_history()
    rows = h.setdefault(snap["kind"], [])
    if changed(rows[-1] if rows else None, snap):
        rows.append(snap)
        HISTORY.parent.mkdir(exist_ok=True)
        HISTORY.write_text(json.dumps(h), encoding="utf-8")


def clear_history():
    HISTORY.unlink(missing_ok=True)


# ------------------------------------------------------------------ geometry
def x_of(m: float) -> float:
    """Margin (D side positive) to x: equal-width columns for toss-up, lean, likely and safe, D on the left."""
    a, u = abs(m), 0.0
    lo, ulo = 0.0, 0.0
    for edge, uhi in zip(EDGES, (0.5, 1.5, 2.5, 3.4)):
        if a <= edge:
            u = ulo + (uhi - ulo) * (a - lo) / (edge - lo)
            break
        lo, ulo = edge, uhi
    else:
        u = 3.4
    return W / 2 - math.copysign(u, m) * COLW if m else W / 2


def radius(kind: str, votes: float) -> float:
    rmin, rmax, vmax, _ = SIZE[kind]
    return rmin + (rmax - rmin) * math.sqrt(min(max(votes, 0) / vmax, 1.0))


def _end_slots(kind: str, snap: dict) -> dict:
    """Where each called race sits in its end zone, in race order (a race called later slots in, and the
    others slide over in the GIF)."""
    r = SIZE[kind][3]
    per = max(1, int((COLW - 16) // (2 * r + 6)))
    slots, n = {}, {"L": 0, "R": 0}
    for rid, (_m, _v, _st, win, side) in snap["races"].items():
        if not win:
            continue
        zone = "R" if win == "R" else "L"
        k = n[zone]
        n[zone] += 1
        x0 = PAD + 8 + r if zone == "L" else W - PAD - COLW + 8 + r
        slots[rid] = (x0 + (k % per) * (2 * r + 6), TOP + 120 + (k // per) * (2 * r + 6), r)
    return slots


def _swarm(dots: list[tuple], prev: dict, rounds: int = 60) -> dict:
    """Beeswarm: x stays exact, circles push apart vertically and settle toward the middle line."""
    cy = (TOP + BOTTOM) / 2
    ys = {k: prev.get(k, cy + (zlib.crc32(k.encode()) % 41 - 20)) for k, _, _ in dots}
    for it in range(rounds):
        for i, (ka, xa, ra) in enumerate(dots):
            for kb, xb, rb in dots[i + 1:]:
                need = ra + rb + 4
                dx = xa - xb
                if abs(dx) >= need:
                    continue
                gap = math.sqrt(need * need - dx * dx)
                dy = ys[kb] - ys[ka]
                if abs(dy) < gap:
                    s = 1 if dy > 0 or (dy == 0 and ka < kb) else -1
                    push = (gap - abs(dy)) / 2
                    ys[ka] -= s * push
                    ys[kb] += s * push
        if it < rounds - 15:
            for k in ys:
                ys[k] += (cy - ys[k]) * 0.08
        for k, _, r in dots:
            ys[k] = min(max(ys[k], TOP + r), BOTTOM - r)
    return ys


# ------------------------------------------------------------------ drawing
def _frame(a: dict, b: dict, t: float, theme: str, ys: dict, demo: bool) -> Image.Image:
    """One frame, t of the way from snapshot a to b (ease in and out). ys carries the swarm between frames."""
    P = THEMES[theme]
    kind = b["kind"]
    e = t * t * (3 - 2 * t)
    img = Image.new("RGB", (W * SS, H * SS), P["bg"])
    d = ImageDraw.Draw(img)
    f = lambda name, size, wght=None: C.font(name, size * SS, wght)
    s = lambda v: v * SS

    # heading
    C.tracked(d, (s(PAD), s(78)), f"2026 {TITLE[kind]} FIELD", f(C.FRANKLIN, 34, 800), P["ink"], 0.08)
    C.tracked(d, (s(W - PAD), s(78)), b["clock"], f(C.PLEX, 26), P["muted"], 0.06, anchor="rs")
    if demo:
        d.rectangle([0, 0, s(W), s(30)], fill=C.AMBER)
        d.text((s(W / 2), s(16)), "TEST · SIMULATED NIGHT · NOT REAL RESULTS", font=f(C.PLEX, 20),
               fill=(255, 255, 255), anchor="mm")

    # projected totals and the bar
    ta, tb = _totals(a), _totals(b)
    lerp = lambda p, q: p + (q - p) * e
    pd, pr = lerp(ta["proj"]["D"], tb["proj"]["D"]), lerp(ta["proj"]["R"], tb["proj"]["R"])
    pi = lerp(ta["proj"]["I"], tb["proj"]["I"])
    total = 100 if kind == "senate" else R.HOUSE_TOTAL
    d.text((s(PAD), s(150)), f"D {round(pd)}", font=f(C.ANTON, 56), fill=P["D"], anchor="ls")
    d.text((s(W - PAD), s(150)), f"R {round(pr)}", font=f(C.ANTON, 56), fill=P["R"], anchor="rs")
    mid = f"{'MAJORITY 51' if kind == 'senate' else 'MAJORITY 218'}" + (f"  ·  I {round(pi)}" if round(pi) else "")
    C.tracked(d, (s(W / 2), s(140)), mid, f(C.PLEX, 22), P["muted"], 0.08, anchor="ms")
    bx0, bx1, by = PAD, W - PAD, 166
    xd = bx0 + (bx1 - bx0) * pd / total
    xi = xd + (bx1 - bx0) * pi / total
    have_d = (bx1 - bx0) * lerp(ta["have"]["D"], tb["have"]["D"]) / total
    have_r = (bx1 - bx0) * lerp(ta["have"]["R"], tb["have"]["R"]) / total
    soft = lambda c: tuple(int(v * 0.55 + bg * 0.45) for v, bg in zip(c, P["bg"]))
    d.rectangle([s(bx0), s(by), s(bx1), s(by + 16)], fill=P["rule"])
    d.rectangle([s(bx0), s(by), s(xd), s(by + 16)], fill=soft(P["D"]))
    d.rectangle([s(bx0), s(by), s(bx0 + have_d), s(by + 16)], fill=P["D"])
    if xi > xd:
        d.rectangle([s(xd), s(by), s(xi), s(by + 16)], fill=P["I"])
    d.rectangle([s(xi), s(by), s(bx1), s(by + 16)], fill=soft(P["R"]))
    d.rectangle([s(bx1 - have_r), s(by), s(bx1), s(by + 16)], fill=P["R"])
    xm = bx0 + (bx1 - bx0) * (51 if kind == "senate" else 218) / total
    d.line([s(xm), s(by - 6), s(xm), s(by + 22)], fill=P["ink"], width=s(2))
    have = tb["have"]
    d.text((s(PAD), s(208)), f"{have['D']} in hand", font=f(C.PLEX, 20), fill=P["muted"], anchor="ls")
    d.text((s(W - PAD), s(208)), f"{have['R']} in hand", font=f(C.PLEX, 20), fill=P["muted"], anchor="rs")

    # columns
    slots_a, slots_b = _end_slots(kind, a), _end_slots(kind, b)
    in_field = {}
    for rid, (m, v, st, win, side) in b["races"].items():
        ma, va, sta, wina, _ = a["races"].get(rid, b["races"][rid])
        in_field[rid] = (lerp(ma, m), lerp(va, v), st if t >= 0.5 else sta, win, wina, side)
    col_n = [0] * 9
    for rid, (m, v, st, win, wina, side) in in_field.items():
        if win:
            col_n[0 if win != "R" else 8] += 1
        else:
            col_n[min(8, max(0, int((x_of(m) - PAD) // COLW)))] += 1
    for c in range(9):
        cx = PAD + COLW * (c + 0.5)
        if c:
            d.line([s(PAD + COLW * c), s(TOP - 34), s(PAD + COLW * c), s(BOTTOM + 10)], fill=P["rule"], width=s(1))
        colr = P["D"] if c < 4 else P["R"] if c > 4 else P["ink"]
        label = COLS[c] if c in (0, 8) else f"{col_n[c]} {COLS[c]}"
        C.tracked(d, (s(cx), s(TOP - 12)), label, f(C.FRANKLIN, 22, 700), colr, 0.04, anchor="ms")
    for i, lab in enumerate(AXIS):
        x = x_of([15, 8, 3, -3, -8, -15][i])
        d.text((s(x), s(BOTTOM + 36)), lab, font=f(C.PLEX, 18), fill=P["muted"], anchor="ms")
    d.text((s(W / 2), s(BOTTOM + 36)), "even", font=f(C.PLEX, 18), fill=P["muted"], anchor="ms")
    for zone, party, cx in (("L", "D", PAD + COLW / 2), ("R", "R", W - PAD - COLW / 2)):
        n = col_n[0 if zone == "L" else 8]
        d.text((s(cx), s(TOP + 70)), str(n), font=f(C.ANTON, 64), fill=P[party], anchor="ms")

    # dots: field races swarm; called races slide to their end zone
    dots, moving = [], {}
    for rid, (m, v, st, win, wina, side) in in_field.items():
        r = radius(kind, v)
        if win and wina:
            (ax, ay, ar), (bx, by_, br) = slots_a[rid], slots_b[rid]
            moving[rid] = (ax + (bx - ax) * e, ay + (by_ - ay) * e, ar + (br - ar) * e)
        elif win or wina:
            ax, ay, ar = (slots_a[rid] if wina else (x_of(m), ys.get(rid, (TOP + BOTTOM) / 2), r))
            bx, by_, br = (slots_b[rid] if win else (x_of(m), ys.get(rid, (TOP + BOTTOM) / 2), r))
            moving[rid] = (ax + (bx - ax) * e, ay + (by_ - ay) * e, ar + (br - ar) * e)
        else:
            dots.append((rid, x_of(m), r))
    ys.update(_swarm(dots, ys, 60 if ys else 140))
    pos = {rid: (x, ys[rid], r) for rid, x, r in dots}
    pos.update(moving)
    label_font = {}
    for rid, (x, y, r) in sorted(pos.items(), key=lambda kv: -kv[1][2]):
        m, v, st, win, wina, side = in_field[rid]
        party = (win or wina) if (win or wina) else (side if m > 0.25 else "R" if m < -0.25 else None)
        col = P[party] if party else P["muted"]
        text = rid[:2] if kind == "senate" else rid
        box = [s(x - r), s(y - r), s(x + r), s(y + r)]
        if win or st == "proj":
            d.ellipse(box, fill=col)
            tc = (255, 255, 255)
        elif st == "count":
            d.ellipse(box, fill=soft(col), outline=col, width=s(3))
            tc = P["ink"]
        else:
            d.ellipse(box, fill=P["hollow"], outline=col, width=s(3))
            tc = col
        key = (len(text), round(r))
        if key not in label_font:
            size = min(24, int(r * 0.9))
            while size > 9 and d.textlength(text, font=f(C.FRANKLIN, size, 700)) > s(1.7 * r):
                size -= 1
            label_font[key] = f(C.FRANKLIN, size, 700)
        fnt = label_font[key]
        d.text((s(x), s(y)), text, font=fnt, fill=tc, anchor="mm")

    foot = ("Position: projected margin from counted counties (2024 margin until then) · Size: votes counted · "
            "Called races move to the end zones" if kind == "senate" else
            "Position: ratings average, pulled toward the live margin in FL, NC and VA · Size: votes counted · "
            "Called seats move to the end zones")
    d.text((s(W / 2), s(H - 22)), foot, font=f(C.FRANKLIN, 18, 500), fill=P["muted"], anchor="ms")
    return img.resize((W, H), Image.LANCZOS)


def _png(img: Image.Image) -> io.BytesIO:
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    buf.seek(0)
    return buf


def _gif(frames: list[Image.Image], durations: list[int]) -> io.BytesIO:
    """One shared palette (from the last frame) so colors don't flicker between frames."""
    pal = frames[-1].quantize(colors=96, method=Image.Quantize.MEDIANCUT)
    out = [fr.quantize(palette=pal, dither=Image.Dither.NONE) for fr in frames]
    buf = io.BytesIO()
    out[0].save(buf, "GIF", save_all=True, append_images=out[1:], duration=durations, loop=0, optimize=True)
    buf.seek(0)
    return buf


def render(snap: dict, theme: str = "dark", prev: dict | None = None, steps: int = 14) -> tuple[io.BytesIO, str]:
    """(image, extension). A GIF tweening from prev when anything moved since it, else a PNG."""
    demo = snap.get("demo", False)
    ys: dict = {}
    if prev is None or not changed(prev, snap):
        return _png(_frame(snap, snap, 1.0, theme, ys, demo)), "png"
    frames = [_frame(prev, snap, i / steps, theme, ys, demo) for i in range(steps + 1)]
    return _gif(frames, [900] + [70] * (steps - 1) + [4000]), "gif"


def timelapse(snaps: list[dict], theme: str = "dark", most: int = 14, steps: int = 6) -> tuple[io.BytesIO, str]:
    """The night so far, a few frames per snapshot (up to `most` snapshots, evenly spread)."""
    if len(snaps) > most:
        idx = sorted({round(i * (len(snaps) - 1) / (most - 1)) for i in range(most)})
        snaps = [snaps[i] for i in idx]
    if len(snaps) < 2:
        return render(snaps[-1], theme)
    demo = snaps[-1].get("demo", False)
    ys: dict = {}
    frames, durs = [], []
    for a, b in zip(snaps, snaps[1:]):
        for i in range(steps):
            frames.append(_frame(a, b, i / steps, theme, ys, demo))
            durs.append(500 if i == 0 else 60)
    frames.append(_frame(snaps[-1], snaps[-1], 1.0, theme, ys, demo))
    durs.append(5000)
    return _gif(frames, durs), "gif"


# ------------------------------------------------------------------ simulated night for /fieldtest
def demo_night(kind: str, seed: int | None = None) -> list[dict]:
    """A made-up night from 7 PM to 1 AM ET, every 30 minutes, starting from where the field stands now. Real races
    and starting positions, invented counts, margins and calls. Nothing is stored."""
    rng = random.Random(seed)
    base = snapshot(kind, {})
    expected = {}
    if kind == "senate":
        import countywatch_run as CWR
        import cw_model as M
        for rid in base["races"]:
            st = R.RACES[rid]["state"]
            expected[rid] = (sum(r["exp2"] for r in CWR.projection(st, {"states": {}})["rows"]) if st in M.STATES
                             else R.HOUSE_SEATS.get(st, 1) * 260_000)
    else:
        expected = {rid: rng.randint(260_000, 340_000) for rid in base["races"]}
    final = {rid: m + rng.gauss(0, 3 if rid in CMP.SAFE else 5) for rid, (m, *_r) in base["races"].items()}
    span = {rid: rng.uniform(2.0, 4.5) for rid in base["races"]}
    snaps, calls = [], {}
    for step in range(13):
        when = N.ELECTION_DAY + timedelta(hours=19, minutes=30 * step)
        races = {}
        for rid, (m0, _v, _st, _w, side) in base["races"].items():
            closed = N.first_close(R.RACES[rid]["state"])
            hours = (when - closed).total_seconds() / 3600
            frac = 0.0 if hours < 0 else min(1.0, (hours / span[rid]) ** 0.8 + 0.05)
            fm = final[rid]
            if frac == 0:
                m, state = m0, "wait"
            elif kind == "senate" and frac < 0.25:
                m, state = m0, "count"
            else:
                m = fm + rng.gauss(0, 5 * (1 - frac))
                if kind == "house":
                    m = m0 * (1 - frac) + m * frac
                state = "proj"
            if rid not in calls and frac and ((rid in CMP.SAFE) or (frac > 0.5 and abs(fm) > 6)
                                              or (frac > 0.85 and abs(fm) > 1.5) or frac >= 1):
                calls[rid] = side if fm > 0 else "R"
            races[rid] = [round(m, 2), int(expected[rid] * frac), state, calls.get(rid), side]
        snaps.append({"kind": kind, "ts": when.timestamp(), "clock": _clock(when), "races": races,
                      "counts": S.chamber_counts(kind, dict(calls)), "demo": True})
    return snaps

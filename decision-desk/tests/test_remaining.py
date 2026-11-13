"""The outstanding vote: arithmetic on hand-made projections, then on County Watch's real model."""
import pytest

import cw_model as M
import remaining as REM


def row(name, d, r, proj_d, proj_r, complete=False, kind="rural"):
    return {"name": name, "fips": name, "type": kind, "D": d, "R": r, "proj_D": proj_d, "proj_R": proj_r,
            "complete": complete}


def state(*rows, confidence="medium", done=6):
    return {"rows": list(rows), "confidence": confidence, "done": done}


# A Mississippi-shaped night: the Republican leads the count, the big blue county is half in.
TIGHT = state(row("Rural", 20_000, 40_000, 20_000, 40_000, complete=True),
              row("Suburb", 30_000, 32_000, 36_000, 40_000),
              row("City", 40_000, 20_000, 80_000, 38_000))


def test_the_count_the_vote_left_and_the_finish():
    b = REM.breakdown(TIGHT)
    assert (b["D"], b["R"], b["lead"]) == (90_000, 92_000, -2_000)
    assert b["left"] == 72_000                           # 14K in the suburb, 58K in the city
    assert b["left_net"] == 22_000 - 2_000               # the city adds D+22K, the suburb R+2K
    assert b["final"] == 18_000 and b["final_margin"] == pytest.approx(18_000 / 254_000)
    assert b["share_in"] == pytest.approx(182_000 / 254_000)


def test_what_the_trailing_side_needs_and_is_on_pace_for():
    b = REM.breakdown(TIGHT)
    assert b["behind"] == "D" and not b["out_of_reach"]
    assert b["need_share"] == pytest.approx((1 + 2_000 / 72_000) / 2)        # 51.4% of what's left ties it
    assert b["expected_share"] == pytest.approx((1 + 20_000 / 72_000) / 2)   # the model has them at 63.9%
    assert b["expected_share"] > b["need_share"]


def test_counties_are_ranked_by_what_they_still_hold():
    b = REM.breakdown(TIGHT)
    assert [c["name"] for c in b["counties"]] == ["City", "Suburb"]          # the finished county isn't listed
    city = b["counties"][0]
    assert city["left"] == 58_000 and city["share_of_left"] == pytest.approx(58 / 72)
    assert city["left_margin"] == pytest.approx(22_000 / 58_000) and city["margin"] == pytest.approx(1 / 3)
    assert (b["d_ground"], b["r_ground"]) == (58_000, 14_000)
    assert len(REM.breakdown(TIGHT, top=1)["top"]) == 1


def test_a_lead_bigger_than_everything_left_is_out_of_reach():
    b = REM.breakdown(state(row("A", 10_000, 60_000, 10_000, 60_000, complete=True), row("B", 5_000, 5_000, 15_000, 9_000)))
    assert b["behind"] == "D" and b["left"] == 14_000 and b["out_of_reach"] and b["need_share"] > 1
    assert "can't catch up" in REM.headline(b, "Colom", "Hyde-Smith")
    done = REM.breakdown(state(row("A", 10, 20, 10, 20, complete=True)))
    assert done["left"] == 0 and done["out_of_reach"] and done["need_share"] is None


def test_nothing_counted_and_a_dead_heat():
    empty = REM.breakdown(state(row("A", 0, 0, 500, 500)))
    assert empty["counted"] == 0 and empty["behind"] is None
    assert REM.headline(empty) == "No votes counted yet." and len(REM.lines(empty)) == 1
    tied = REM.breakdown(state(row("A", 100, 100, 200, 180)))
    assert tied["lead"] == 0 and tied["behind"] is None and tied["final"] == 20


def test_a_count_that_ran_past_the_estimate_leaves_nothing_negative():
    b = REM.breakdown(state(row("A", 600, 500, 550, 450)))       # more counted than the model expected in total
    assert b["left"] == 0 and b["counties"] == []


def test_what_if_the_rest_breaks_another_way():
    b = REM.breakdown(TIGHT)
    assert REM.what_if(b, 0.0)["final"] == -2_000 and REM.what_if(b, 0.0)["winner"] == "R"
    tie = REM.what_if(b, 2_000 / 72_000)
    assert tie["final"] == pytest.approx(0) and abs(tie["final_margin"]) < 1e-9
    assert REM.what_if(b, 0.10)["winner"] == "D" and REM.what_if(b, -1.0)["final"] == -74_000


def test_the_text_names_the_candidates_and_flags_a_rough_estimate():
    b = REM.breakdown(TIGHT)
    head = REM.headline(b, "Colom", "Hyde-Smith")
    assert head.startswith("Count: Hyde-Smith +2,000 (Hyde-Smith+1.1)")
    assert "72% of the expected vote in" in head and "model finish Colom+7.1" in head
    assert "Colom needs 51.4% of the ~72K left" in head
    text = "\n".join(REM.lines(b, "Colom", "Hyde-Smith", scenario=-0.05))
    assert "Hyde-Smith leads by 2,000 votes" in text and "Colom needs 51.4%" in text and "on track to pass" in text
    assert "58K in counties where what's left should favor Colom, 14K where it should favor Hyde-Smith" in text
    assert "If what's left breaks Hyde-Smith+5.0 instead:** final Hyde-Smith+2.2 (Hyde-Smith by 5,600)" in text
    assert "Rough" not in text
    rough = REM.lines(REM.breakdown(state(*TIGHT["rows"], confidence="low", done=1)))
    assert rough[-1].startswith("-# Rough: only 1 counties")
    counties = REM.county_lines(REM.breakdown(TIGHT, top=1), "Colom", "Hyde-Smith")
    assert counties[0].startswith("**City** ~58K left (81% of it) · counted Colom+33.3 · expected from here Colom+37.9")
    assert counties[1] == "-# plus ~14K across 1 more counties"


# ------------------------------------------------------------------ on the real model
def test_it_reads_a_real_county_watch_projection():
    """Mississippi from cw_model: finish the small counties at their 2024 split, leave the largest ones half counted."""
    rows = sorted(M.load()["MS"], key=lambda c: c["w"])
    live = {}
    for i, c in enumerate(rows):
        v = c["w"] * (1.0 if i < len(rows) - 6 else 0.5)
        d = v * (1 + c["m24"]) / 2
        live[c["fips"]] = {"D": round(d), "R": round(v - d), "O": 0, "pct_in": 1.0 if i < len(rows) - 6 else 0.5,
                           "complete": i < len(rows) - 6}
    p = M.project("MS", live)
    b = REM.breakdown(p)
    assert b["counted"] == p["counted"] and b["left"] == pytest.approx(p["remaining"], rel=1e-6)
    assert {c["name"] for c in b["counties"]} == {c["name"] for c in rows[-6:]}       # only the unfinished ones
    assert all(c["left"] > 0 and c["name"] for c in b["counties"])
    if p["need_share"] is not None and b["need_share"] is not None and (b["behind"] == p["trailer"]):
        assert b["need_share"] == pytest.approx(p["need_share"], rel=1e-6)
    assert REM.headline(b, "Colom", "Hyde-Smith") and REM.county_lines(b)

"""
test_deceleration_threshold.py — "Decelerating" means YoY revenue growth
fell by more than 5 percentage points between the latest two quarters (the
card's own ±5 pp "stable" band). A smaller dip mustn't trigger the
deceleration rules: company Blue, growth-score cap at 40, layer Blue, rating C.

On true YoY the old < 0 rule turned steady compounders Blue/C on noise:
the 2026-10-24 replay had S at −0.2 pp, MSFT −0.5 pp, OKTA −0.6 pp.
Approved by Ray, 2026-10-24.

No pytest in this environment — plain script. Run: python test_deceleration_threshold.py
"""

import main
import render
from score_engine import ScoreEngine
from test_regime_label import SESSIONS, _ticker
from test_unmeasured_acceleration import MACRO, _render


def test_small_dip_is_not_deceleration():
    eng = ScoreEngine(MACRO)
    dip   = eng.process_sector("cloud", dict(_ticker("DIP"), growth_curr=0.200, growth_prev=0.205))
    drop  = eng.process_sector("cloud", dict(_ticker("DRP"), growth_curr=0.200, growth_prev=0.260))
    assert dip["color"] != "Blue", f"−0.5 pp dip went Blue: {dip['status']}"
    assert drop["color"] == "Blue", f"−6 pp drop should be Blue, got {drop['color']}"
    assert render.company_rating(dip["score"], -0.5, dip["color"], False) != "C", "−0.5 pp dip rated C"
    assert render.company_rating(drop["score"], -6.0, drop["color"], False) == "C", "−6 pp drop not rated C"
    hot = dict(growth_curr=1.5, gm_delta=0.2, gross_margin=0.3)
    a_dip  = eng.calculate_acceleration(dict(hot, growth_prev=1.52), [])[0]
    a_drop = eng.calculate_acceleration(dict(hot, growth_prev=1.60), [])[0]
    assert a_dip > 40 and a_drop == 40, f"growth-score cap: dip {a_dip}, drop {a_drop}"
    print("PASS: company — a −0.5 pp dip isn't decelerating; −6 pp is (Blue, C, capped)")


def test_layer_of_small_dips_is_not_blue():
    md = {lid: [_ticker(lid[:4].upper())] for lid in render.LAYER_NAMES}
    md["security"] = [dict(_ticker("SA"), growth_curr=0.20, growth_prev=0.23),
                      dict(_ticker("SB"), growth_curr=0.10, growth_prev=0.12)]
    md["energy"]   = [dict(_ticker("EA"), growth_curr=0.10, growth_prev=0.30),
                      dict(_ticker("EB"), growth_curr=0.05, growth_prev=0.15)]
    scored, _ = _render(md)
    assert scored["security"]["best"]["color"] != "Blue", scored["security"]["best"]["status"]
    assert scored["energy"]["best"]["color"] == "Blue", scored["energy"]["best"]["status"]
    print("PASS: layer — every company −2..−3 pp isn't Blue; every company −10..−20 pp is")


def test_one_company_acceleration_no_longer_makes_a_layer_instant_red():
    """The old _confirmed_color made a layer Red at once when one company's
    delta exceeded 60 pp, skipping the 65 threshold and confirmation. Removed
    (Ray, 2026-10-24): on true YoY it fires mostly on recoveries off a trough.
    A layer at ~50 with sessions of history and one +70 pp company is not Red."""
    import os, json, tempfile, datetime
    md = {lid: [_ticker(lid[:4].upper())] for lid in render.LAYER_NAMES}
    md["memory"] = [dict(_ticker("JMP"), growth_curr=0.90, growth_prev=0.20)]   # +70 pp
    cwd = os.getcwd()
    with tempfile.TemporaryDirectory() as tmp:
        os.chdir(tmp)
        try:
            hist = [{"date": str(s), "scores": {"memory": {"score": 50.0, "color": "Orange"}}} for s in SESSIONS[:-1]]
            json.dump(hist, open("scores_history.json", "w"))
            scored = main.stage_score(md, MACRO, SESSIONS)
        finally:
            os.chdir(cwd)
    mem = scored["memory"]["best"]
    assert mem["score"] <= 80, f"setup: score {mem['score']} would be instant Red on score alone"
    assert mem["color"] != "Red", f"one +70 pp company made the layer {mem['color']}: {mem['status']}"
    print(f"PASS: one +70 pp company doesn't make a {mem['score']:.0f}-score layer instant Red")


if __name__ == "__main__":
    import logging
    logging.disable(logging.CRITICAL)
    test_one_company_acceleration_no_longer_makes_a_layer_instant_red()
    test_small_dip_is_not_deceleration()
    test_layer_of_small_dips_is_not_blue()
    print("\nAll deceleration-threshold tests passed.")

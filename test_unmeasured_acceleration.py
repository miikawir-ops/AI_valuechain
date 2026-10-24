"""
test_unmeasured_acceleration.py — Revenue acceleration needs the YoY growth
of two consecutive quarters, i.e. 6 quarters of revenue. When it can't be
measured (fund_delta None) it must be visible as "not measured", never
scored or shown as a neutral or stable trend:

  - no Red, for a company or a layer, by any path (instant, confirmed, held);
  - no high-margin bonus (it rewards high-margin *accelerators*);
  - an unmeasured company doesn't count as delta 0 when picking the layer's
    top delta, so a layer whose measured companies all decelerate is Blue;
  - the card says "Acceleration not measured", not "stable".

The 50-point neutral delta sub-score stays in the arithmetic (Ray, 2026-10-24).
No pytest in this environment — plain script. Run: python test_unmeasured_acceleration.py
"""

import os
import re
import json
import tempfile

import main
import render
from score_engine import ScoreEngine
from test_regime_label import SESSIONS, _ticker

MACRO = {"vix": 17.0, "yield_10y_change": 10.0, "nasdaq_vs_spx_20d": 0.0, "data_missing": []}

# Every signal maxed, so the score clears 80 (instant Red) whatever the delta does.
HOT = dict(growth_curr=1.5, gm_delta=0.2, gross_margin=0.8, news_velocity=10.0, capex_div=1.0,
           vol_spike=3.0, price_act=0.02, analyst_upgrades=3, short_int_change=-0.5,
           price_30d_return=0.5, price_momentum=4.0)


def _render(market_data):
    cwd = os.getcwd()
    with tempfile.TemporaryDirectory() as tmp:
        os.chdir(tmp)
        try:
            scored = main.stage_score(market_data, MACRO, SESSIONS)
            render.generate_dashboard(scored, "Stub analysis.", MACRO, SESSIONS[-1], market_data, [])
            page = open("index.html", encoding="utf-8").read()
        finally:
            os.chdir(cwd)
    return scored, json.loads(re.search(r"const LAYERS\s*= (\[.*?\]);\n", page).group(1))


def test_engine_unmeasured_is_not_red_and_gets_no_margin_bonus():
    eng = ScoreEngine(MACRO)
    r = eng.process_sector("memory", dict(_ticker("UNM"), **HOT, growth_prev=None))
    assert r["fund_delta"] is None and r.get("accel_measured") is False, r
    assert r["score"] > 65, f"setup: score {r['score']} should clear the Red threshold"
    assert r["color"] != "Red", f"unmeasured acceleration scored Red: {r['status']}"
    hi = eng.calculate_acceleration(dict(HOT, growth_prev=None, gross_margin=0.80), [])[0]
    lo = eng.calculate_acceleration(dict(HOT, growth_prev=None, gross_margin=0.30), [])[0]
    assert hi == lo, f"high-margin bonus applied without measured acceleration: {hi} vs {lo}"
    print("PASS: engine — unmeasured acceleration is never Red and gets no margin bonus")


def test_layer_unmeasured_is_not_red_and_does_not_mask_deceleration():
    md = {lid: [_ticker(lid[:4].upper())] for lid in render.LAYER_NAMES}
    md["memory"] = [dict(_ticker("UNA"), **HOT, growth_prev=None)]
    # Energy: the one measured company decelerates; the other is unmeasured.
    md["energy"] = [dict(_ticker("DEC"), growth_curr=0.10, growth_prev=0.30),
                    dict(_ticker("NOM"), growth_prev=None)]
    scored, layers = _render(md)
    mem, ene = scored["memory"]["best"], scored["energy"]["best"]
    assert mem["score"] > 65, f"setup: memory score {mem['score']} should clear the Red threshold"
    assert mem["color"] != "Red", f"memory Red with no measured acceleration: {mem['status']}"
    assert ene["color"] == "Blue", f"energy {ene['color']} ({ene['status']}): unmeasured NOM counted as delta 0"
    print("PASS: layer — no Red without measured acceleration; unmeasured doesn't mask deceleration")

    t = next(t for l in layers for t in l["tickers"] if t["sym"] == "UNA")
    mem_l = next(l for l in layers if l["id"] == "memory")
    assert t["delta_band"] == "unmeasured", f"delta_band {t['delta_band']!r}, want 'unmeasured'"
    assert t["forecast"]["label"] == "Acceleration not measured", f"forecast {t['forecast']['label']!r}"
    assert mem_l["momentum_label"] == "not measured", f"momentum {mem_l['momentum_label']!r}"
    print("PASS: card — 'Acceleration not measured', not a stable trend")


def test_two_company_red_layer_is_not_downgraded_by_ratings():
    """The Red reality check is removed (Ray, 2026-10-24). It read each
    company's `rating`, which nothing set, so it treated every company as C
    and downgraded any 2+ company Red layer to Orange before confirmation."""
    md = {lid: [_ticker(lid[:4].upper())] for lid in render.LAYER_NAMES}
    md["memory"] = [dict(_ticker("RA"), **HOT, growth_prev=1.2, market_cap=2e11),
                    dict(_ticker("RB"), **HOT, growth_prev=1.2, market_cap=1e11)]
    scored, _ = _render(md)
    mem = scored["memory"]["best"]
    assert mem["score"] > 65, f"setup: score {mem['score']}"
    assert mem["color"] == "Red", f"2-company layer above 65 with measured acceleration: {mem['color']} ({mem['status']})"
    print("PASS: a 2-company layer above 65 with measured acceleration is Red (no rating downgrade)")


def test_layer_card_names_material_unmeasured_acceleration():
    """Above 25% of a layer's market cap unmeasured, the layer card says so
    ("Acceleration not measured: ASML, TSM (26% of layer)", approved by Ray
    2026-10-24); at or below 25% it doesn't."""
    md = {lid: [_ticker(lid[:4].upper())] for lid in render.LAYER_NAMES}
    md["compute"] = [dict(_ticker("NV"), market_cap=7.0e11),
                     dict(_ticker("AS"), growth_prev=None, market_cap=1.6e11),
                     dict(_ticker("TS"), growth_prev=None, market_cap=1.0e11)]
    md["cloud"]   = [dict(_ticker("MS"), market_cap=9.0e11),
                     dict(_ticker("XX"), growth_prev=None, market_cap=1.0e11)]
    _, layers = _render(md)
    by = {l["id"]: l for l in layers}
    u = by["compute"]["unmeasured"]
    assert u and u["syms"] == ["AS", "TS"] and abs(u["share"] - 0.271) < 0.001, u
    assert by["cloud"]["unmeasured"] is None, f"10% share shown: {by['cloud']['unmeasured']}"
    print("PASS: layer card names unmeasured acceleration above 25% of market cap (27%), not at 10%")


if __name__ == "__main__":
    import logging
    logging.disable(logging.CRITICAL)
    test_layer_card_names_material_unmeasured_acceleration()
    test_two_company_red_layer_is_not_downgraded_by_ratings()
    test_engine_unmeasured_is_not_red_and_gets_no_margin_bonus()
    test_layer_unmeasured_is_not_red_and_does_not_mask_deceleration()
    print("\nAll unmeasured-acceleration tests passed.")

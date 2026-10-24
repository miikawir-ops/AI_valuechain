"""
test_divergence_flag.py — Item 7: the price-revenue divergence flag. A
company is flagged when revenue YoY accelerated more than +5 pp (measured
only) while its 30d price return is -15% or lower and at least 10 points
below the NASDAQ's over the same 21 closes. Two readings, stated on the
page and to Gemini; it changes no score, colour or rating (Ray, 2026-10-24).

No pytest in this environment — plain script. Run: python test_divergence_flag.py
"""

import os
import re
import json
import tempfile
import datetime

import main
import render
import analyze
from score_engine import ScoreEngine
from test_regime_label import _ticker

SESSIONS = [datetime.date(2026, 10, d) for d in (19, 20, 21, 22, 23)]
MACRO = {"vix": 19.4, "yield_10y_change": -19.6, "nasdaq_vs_spx_20d": -0.024, "nasdaq_30d_return": -0.041, "data_missing": []}
MU = dict(_ticker("MUX"), name="Micron X", growth_curr=3.793, growth_prev=3.457, price_30d_return=-0.189)


def test_flag_fires_and_changes_nothing_else():
    on  = ScoreEngine(MACRO).process_sector("memory", MU)
    off = ScoreEngine(dict(MACRO, nasdaq_30d_return=None)).process_sector("memory", MU)
    assert on["divergence"] == {"evaluated": True, "flag": True, "accel_pp": 33.6, "ret30_pct": -18.9, "nasdaq_30d_pct": -4.1}, on["divergence"]
    assert off["divergence"] == {"evaluated": False, "flag": False}, "evaluated without the NASDAQ return"
    assert (on["score"], on["color"], on["is_hype"]) == (off["score"], off["color"], off["is_hype"]), "the flag changed a score or colour"
    r = render.company_rating
    assert r(on["score"], 33.6, on["color"], on["is_hype"]) == r(off["score"], 33.6, off["color"], off["is_hype"])
    unm = ScoreEngine(MACRO).process_sector("memory", dict(MU, growth_prev=None))
    assert unm["divergence"]["evaluated"] is False, "evaluated with unmeasured acceleration"
    edge = ScoreEngine(dict(MACRO, nasdaq_30d_return=-0.05)).process_sector(
        "memory", dict(MU, growth_curr=0.30, growth_prev=0.249, price_30d_return=-0.15))
    assert edge["divergence"]["flag"], f"boundary (+5.1 pp, -15.0%, 10 pts below) not flagged: {edge['divergence']}"
    print("PASS: flag fires on MU's figures, is not evaluated without acceleration or NASDAQ, changes no score/colour/rating")


def test_page_and_prompt_state_both_readings():
    md = {lid: [_ticker(lid[:4].upper())] for lid in render.LAYER_NAMES}
    md["memory"] = [MU, dict(_ticker("OTH"))]
    cwd = os.getcwd()
    with tempfile.TemporaryDirectory() as tmp:
        os.chdir(tmp)
        try:
            scored = main.stage_score(md, MACRO, SESSIONS)
            render.generate_dashboard(scored, "Stub.", MACRO, SESSIONS[-1], md, [])
            page = open("index.html", encoding="utf-8").read()
            prompt, facts = analyze.build_prompt(scored, md, MACRO, SESSIONS[-1])
        finally:
            os.chdir(cwd)
    layers = {l["id"]: l for l in json.loads(re.search(r"const LAYERS\s*= (\[.*?\]);\n", page).group(1))}
    assert layers["memory"]["price_div"] == ["MUX"], layers["memory"]["price_div"]
    mux = next(t for t in layers["memory"]["tickers"] if t["sym"] == "MUX")
    assert mux["price_div"]["flag"] and mux["price_div"]["accel_pp"] == 33.6
    assert "Price down sharply while revenue accelerates: ${l.price_div.join" in page
    readings = "the market may be pricing a risk that reported revenue doesn't show yet, or the price may have fallen further than the revenue trend implies"
    assert page.count(readings) >= 2, "both readings missing from the badge or the About text"
    assert "no supporting news signal" not in page, "narrative rule 3 still present"
    assert "price-revenue divergence yes (acceleration +33.6 pp, 30d price -18.9%, NASDAQ -4.1%)" in prompt
    assert "### ⚠️ HYPE & DIVERGENCE" in prompt and "NASDAQ 30d return -4.1%" in prompt
    body = ("No company is flagged for hype. MUX shows price-revenue divergence: acceleration of +33.6 pp while the price fell "
            "18.9% in 30 days against the NASDAQ's -4.1%. The market may be pricing a risk, or the price may have overshot.")
    assert analyze.check_section("### ⚠️ HYPE & DIVERGENCE", body, facts) == [], analyze.check_section("### ⚠️ HYPE & DIVERGENCE", body, facts)
    print("PASS: layer line, company badge and About state both readings; prompt and post-check cover the flag; rule 3 gone")


def test_real_ci_line_gives_todays_six():
    """The first CI line in scores_detail.jsonl (run 2026-10-24 17:49, before
    the NASDAQ 30d field existed) with that session's NASDAQ 30d return,
    -4.1% (^IXIC, 21 closes to 2026-10-23), reproduces the 6 flags counted
    in the item-7 plan."""
    line = json.loads(open("scores_detail.jsonl", encoding="utf-8").readline())
    eng = ScoreEngine(dict(line["macro"], nasdaq_30d_return=-0.041))
    flagged = sorted(c["ticker"] for c in line["companies"]
                     if eng.calculate_divergence(c["inputs"], c["fund_delta"])["flag"])
    assert flagged == ["AMAT", "AMD", "LRCX", "MU", "PWR", "WDC"], flagged
    print(f"PASS: the real 2026-10-23 line flags {flagged}")


if __name__ == "__main__":
    import logging
    logging.disable(logging.CRITICAL)
    test_flag_fires_and_changes_nothing_else()
    test_page_and_prompt_state_both_readings()
    test_real_ci_line_gives_todays_six()
    print("\nAll divergence-flag tests passed.")

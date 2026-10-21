"""
test_regime_label.py — The page's "Market regime" label must be the regime
and multiplier the scores actually used (score_engine.get_macro_multiplier),
not a separate rule in render.py. The old render rule disagreed whenever one
signal crossed a threshold alone: VIX 25 showed "Neutral" while scores got
1.0x, VIX 35 showed "Risk-Off" while scores got 0.85x.

Renders end to end through main.stage_score() and render.generate_dashboard()
in a temporary directory, and reads the label off the rendered page, so it
runs against old and new render.py alike.

No pytest in this environment — plain script. Run: python test_regime_label.py
"""

import os
import re
import datetime
import tempfile

import main
import render
from score_engine import ScoreEngine

SESSIONS = [datetime.date(2026, 10, d) for d in (13, 14, 15, 16, 19, 20)]

CASES = [
    ("VIX 25 alone",            {"vix": 25.0, "yield_10y_change": None,  "nasdaq_vs_spx_20d": None},
     "Market regime: Risk-On · scores × 1.00"),
    ("VIX 35 alone",            {"vix": 35.0, "yield_10y_change": None,  "nasdaq_vs_spx_20d": None},
     "Market regime: Neutral · scores × 0.85"),
    ("VIX 35 + yield +120 bps", {"vix": 35.0, "yield_10y_change": 120.0, "nasdaq_vs_spx_20d": None},
     "Market regime: Risk-Off · scores × 0.65"),
    ("all three missing",       {"vix": None, "yield_10y_change": None,  "nasdaq_vs_spx_20d": None},
     "Market regime: Unknown (macro data unavailable)"),
]


def _ticker(sym: str) -> dict:
    return {
        "ticker": sym, "name": f"{sym} Corp", "price": 100.0, "market_cap": 1e11,
        "growth_curr": 0.30, "growth_prev": 0.20, "gm_delta": 0.01, "gross_margin": 0.5,
        "news_velocity": 4.0, "capex_div": 0.3, "vol_spike": 1.1, "price_act": 0.01,
        "analyst_upgrades": 1, "short_int_change": 0.0, "price_30d_return": 0.05,
        "price_momentum": 1.2, "peer_outperformance": 0.0, "price_history": [100.0] * 30,
        "week52_high": 120.0, "week52_low": 80.0, "revenue_quarterly": 0, "data_missing": [],
    }


def _rendered_label(macro: dict) -> tuple:
    market_data = {lid: [_ticker(lid[:4].upper())] for lid in render.LAYER_NAMES}
    scored = main.stage_score(market_data, macro, SESSIONS)
    render.generate_dashboard(scored, "Stub analysis.", macro, SESSIONS[-1], market_data, [])
    page = open("index.html", encoding="utf-8").read()
    m = re.search(r'<div class="reg-pill" tabindex="0">(.*?)\n\s*<div class="reg-tooltip">', page, re.S)
    used = {(t["regime"], t["multiplier"]) for l in scored.values() for t in l.get("all_tickers", [])}
    return (m.group(1).strip() if m else None), used, page


def test_rendered_regime_label_equals_the_engine_regime():
    cwd, failures = os.getcwd(), []
    with tempfile.TemporaryDirectory() as tmp:
        os.chdir(tmp)
        try:
            for name, macro, want in CASES:
                if os.path.exists("scores_history.json"):
                    os.remove("scores_history.json")
                macro = dict(macro, data_missing=[k for k, v in macro.items() if v is None])
                shown, used, page = _rendered_label(macro)
                mult, regime = ScoreEngine(macro).get_macro_multiplier([])
                ok = (shown == want and want.startswith("Market regime: " + regime)
                      and used == {(regime, mult)})
                print(f"{'PASS' if ok else 'FAIL'}: {name}: page {shown!r}; engine {regime} × {mult}")
                if not ok:
                    failures.append(name)
            rule = "<b>Rule applied:</b> Risk-Off (scores × 0.65) needs two risk signals"
            if rule in page and "5 percentage points behind" in page and "A missing signal doesn't vote." in page:
                print("PASS: the info text states the voting rule the engine applies")
            else:
                print("FAIL: the info text doesn't state the voting rule")
                failures.append("rule text")
        finally:
            os.chdir(cwd)
    assert not failures, f"regime label disagrees with the engine: {failures}"


if __name__ == "__main__":
    import logging
    logging.disable(logging.CRITICAL)
    test_rendered_regime_label_equals_the_engine_regime()
    print("\nAll regime-label tests passed.")

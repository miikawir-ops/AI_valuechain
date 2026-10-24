"""
test_analysis_prompt.py — The Gemini prompt must state the pipeline's real
data: the engine's regime and multiplier, each company's YoY growth with
its quarter-end, acceleration or "not measured", the 30d return, and the
session date. The old prompt read growth from scored results that don't
carry it (every company "growth=0%"), sent no macro data at all while
asking Gemini to state the regime, and dated itself by the run time.

No pytest in this environment — plain script. Run: python test_analysis_prompt.py
"""

import os
import shutil
import tempfile
import datetime

import main
import render
import analyze
from test_regime_label import _ticker

MACRO = {"vix": 35.0, "yield_10y_change": None, "nasdaq_vs_spx_20d": -0.031, "data_missing": ["yield_10y_change"]}
SESSIONS = [datetime.date(2026, 10, d) for d in (19, 20, 21, 22, 23)]


def _prompt():
    md = {lid: [_ticker(lid[:4].upper())] for lid in render.LAYER_NAMES}
    md["memory"] = [dict(_ticker("MUX"), name="Micron X", growth_curr=3.793, growth_prev=3.457,
                         growth_quarter="2026-09-03", price_30d_return=-0.189),
                    dict(_ticker("NOM"), growth_curr=0.21, growth_prev=None, growth_quarter="2026-06-30")]
    cwd = os.getcwd()
    with tempfile.TemporaryDirectory() as tmp:
        os.chdir(tmp)
        try:
            scored = main.stage_score(md, MACRO, SESSIONS)
            return analyze.build_prompt(scored, md, MACRO, SESSIONS[-1])
        finally:
            os.chdir(cwd)


def test_prompt_states_the_real_data():
    prompt, facts = _prompt()
    assert "Data: close of Friday, October 23, 2026" in prompt, "prompt not dated by the session"
    assert "MACRO REGIME (applied by the scoring engine): Neutral · scores × 0.85" in prompt, "engine regime missing"
    assert "VIX 35.0" in prompt and "10Y yield change over 20 sessions missing" in prompt and "-3.1 pp" in prompt
    assert "revenue YoY +379% (quarter ended 2026-09-03), acceleration +33.6 pp, 30d price return -18.9%" in prompt
    assert "NOM (NOM Corp): " in prompt and "acceleration not measured" in prompt
    assert "growth=0%" not in prompt
    assert facts["regime"] == "Neutral · scores × 0.85"
    print("PASS: prompt states session date, engine regime, macro (missing named), YoY + quarter, acceleration")


if __name__ == "__main__":
    import logging
    logging.disable(logging.CRITICAL)
    test_prompt_states_the_real_data()
    print("\nAll analysis-prompt tests passed.")

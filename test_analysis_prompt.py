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


def test_prompt_framing_matches_the_code():
    """Weights 65/20/15 (old prompt: "50/25/25"); Orange is not hype; no
    "Next Nvidia" section until the radar measures acceleration (item 9);
    the signal section allows only Watch or Monitor (old: Buy/Watch/Avoid)."""
    prompt, _ = _prompt()
    assert "65% revenue acceleration signal + 20% constraint signal" in prompt and "15% smart money" in prompt
    assert "50% fundamentals" not in prompt and "Orange=emerging/hype" not in prompt
    assert "Orange does not mean hype" in prompt
    assert "NEXT NVIDIA" not in prompt.upper()
    assert "### 💡 ONE SIGNAL TO WATCH" in prompt and "Use only Watch or Monitor" in prompt
    assert "Buy/Watch/Avoid" not in prompt
    print("PASS: prompt framing — 65/20/15, Orange ≠ hype, no Next Nvidia, Watch/Monitor only")


def test_signal_section_gets_fixed_disclaimer_and_still_parses():
    _, facts = _prompt()
    reply = ("### 🌍 MACRO REGIME\nThe regime is Neutral · scores × 0.85.\n\n### 💡 ONE SIGNAL TO WATCH\n"
             "Company: MUX (Micron X), Watch. Revenue YoY +379%. A slowdown would weaken it.\n")
    out = analyze.finalize(reply, facts)
    assert out.rstrip().endswith("A slowdown would weaken it. Not financial advice."), out
    assert analyze.finalize(out, facts).count("Not financial advice.") == 1, "suffix added twice"
    import re
    m = re.search(r'Company[:\s]+\*?\*?([A-Z]{1,5})', out)   # render.generate_dashboard's track-record parser
    assert m and m.group(1) == "MUX"
    import json
    action = json.loads(render._analysis_sections(out)[1])
    assert action and "SIGNAL TO WATCH" in action["title"].upper() and action["body"].endswith("Not financial advice.")
    print("PASS: signal section ends with a fixed 'Not financial advice.'; ticker and action card still parse")


GOOD = """### 🌍 MACRO REGIME
The market regime is Neutral · scores × 0.85, driven by VIX 35.0, while NASDAQ trails the S&P 500 by 3.1 pp over 20 sessions and the 10Y yield change is missing.

### 🔩 AI CHAIN BOTTLENECK
No layer is Red today. Scores weight revenue acceleration 65%, constraints 20% and smart money 15%. MUX reported revenue YoY of +379% for the quarter ended Sep 3, 2026 (2026-09-03), accelerating by 33.6 pp, though its price fell 18.9% over 30 days. NOM needs 6 quarters before acceleration can be measured; earnings within 14 days matter here.

### ⚠️ HYPE WARNINGS
No company is flagged for hype today.

### 💡 ONE SIGNAL TO WATCH
Company: MUX (Micron X), Watch. Revenue YoY of +379% with acceleration of 33.6 pp is the strongest revenue signal in the data. A slowdown below the 5 pp band would weaken it.
"""


def test_post_check_passes_legitimate_numbers():
    """Weights, thresholds, dates, "14 days", "6 quarters", "S&P 500" and
    the data's own figures must not trip the check (Ray: no false positives)."""
    _, facts = _prompt()
    for part in analyze._split_sections(GOOD):
        title, _, body = part.partition("\n")
        assert analyze.check_section(title, body, facts) == [], (title, analyze.check_section(title, body, facts))
    out = analyze.finalize(GOOD, facts)
    assert analyze.WITHHELD not in out
    print("PASS: post-check passes a reply whose figures all come from the data (weights, dates, 14 days, 6 quarters)")


def test_post_check_withholds_a_section_with_a_wrong_figure():
    _, facts = _prompt()
    cases = {
        "wrong YoY":     GOOD.replace("YoY of +379% for", "YoY of +150% for"),
        "wrong regime":  GOOD.replace("Neutral · scores × 0.85", "Risk-Off · scores × 0.65"),
        "buy":           GOOD.replace("(Micron X), Watch.", "(Micron X), Buy."),
        "unknown co":    GOOD.replace("Company: MUX (Micron X)", "Company: ZZZZ (Nothing Inc)"),
    }
    for name, reply in cases.items():
        out = analyze.finalize(reply, facts)
        assert out.count(analyze.WITHHELD) == 1, f"{name}: {out.count(analyze.WITHHELD)} sections withheld"
    wrong = analyze.finalize(cases["wrong YoY"], facts)
    assert "+150%" not in wrong and "No company is flagged" in wrong, "withheld the wrong section"
    signal = analyze.finalize(cases["buy"], facts)
    assert "Company: MUX" not in signal, "a withheld signal still names a company for the track record"
    print("PASS: a wrong figure, regime, action word or company withholds exactly its section")


def test_page_headings_say_signal_not_action():
    """Page labels match the Gemini section: "ONE SIGNAL TO WATCH" and
    "SIGNAL TRACK RECORD — last 5" (old: "ONE ACTION TODAY", "ONE ACTION
    TRACK RECORD — last 5 picks")."""
    from test_regime_label import _render_page
    page = _render_page()
    assert "ONE SIGNAL TO WATCH" in page and "ONE ACTION TODAY" not in page
    assert "📊 SIGNAL TRACK RECORD — last 5" in page and "ONE ACTION TRACK RECORD" not in page
    assert "Returns are measured from the date the signal was shown." in page
    print("PASS: page headings — ONE SIGNAL TO WATCH, SIGNAL TRACK RECORD — last 5")


if __name__ == "__main__":
    import logging
    logging.disable(logging.CRITICAL)
    test_page_headings_say_signal_not_action()
    test_post_check_passes_legitimate_numbers()
    test_post_check_withholds_a_section_with_a_wrong_figure()
    test_prompt_states_the_real_data()
    test_prompt_framing_matches_the_code()
    test_signal_section_gets_fixed_disclaimer_and_still_parses()
    print("\nAll analysis-prompt tests passed.")

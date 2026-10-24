"""
test_page_text.py — Text the page's JavaScript and card data generate must
say what the code does (JS text audit, Ray 2026-10-24): no "early discovery"
or untested historical claims on the wildcard, no "No accel / LOW confidence"
for an acceleration the radar can't measure, no "buy signal", no D rating
(company_rating never returns one), a missing heat-trail day shown as "no
data" not today's colour, and company descriptions without unsourced
figures or superlatives.

No pytest in this environment — plain script. Run: python test_page_text.py
"""

import re
import itertools

import render
import analyze
from test_regime_label import _render_page


def test_no_forecast_targets_a_rating_that_does_not_exist():
    seen = set()
    for rating, band, hype, color, delta in itertools.product(
            "ABC", ("accelerating", "decelerating", "stable", "unmeasured"), (True, False),
            ("Red", "Orange", "Green", "Blue"), (-12.0, -6.0, 0.0, 6.0, 25.0)):
        f = render.rating_forecast(rating, delta, band, hype, color)
        assert f["target"] in "ABC" and f["label"] != "Avoid", (rating, band, hype, color, delta, f)
        seen.add(f["label"])
    f = render.rating_forecast("C", -6.0, "decelerating", True, "Green")
    assert (f["direction"], f["target"], f["label"]) == ("→", "C", "Deteriorating"), f
    assert "Upgrade likely" not in seen and "Recovery signal" not in seen
    print("PASS: no forecast targets D or says Avoid; C + hype + decelerating → C · Deteriorating")


def test_js_text_matches_the_code():
    page = _render_page()
    for gone in ("early discovery signal", "historically the", "No accel", "confidence</span>", "buy signal",
                 "'Deteriorating'", "estimated", "the brain of AI systems", "Western Digital makes NAND"):
        assert gone not in page, f"still on the page: {gone!r}"
    for here in ("analysts cover it (low coverage)", "hasn't tested whether that predicts returns",
                 "accelerating ${r.consecutive} of last ${r.compared} quarters · avg ${fmt(r.trend_delta)} pp/qtr over ${r.compared} quarters", "Rating up from", "const PREV_SESSION =",
                 "B: neither A nor C", "Western Digital makes hard disk drives (HDDs) for bulk data-centre storage. "
                 "It spun off its flash business as Sandisk in February 2025.", "Gross margin (Yahoo Finance, latest quarter)"):
        assert here in page, f"missing from the page: {here!r}"
    ctx = re.search(r"const ctx = \{.*?\n  \};", page, re.S).group(0)
    assert not re.search(r"[%$+]|\d+\s*(?:B|bn|billion)\b", ctx), "a hardcoded figure in company descriptions"
    for word in ("largest", "only company", "fastest", "most advanced", "near-monopoly", "market leader", "every "):
        assert word not in ctx.lower(), f"superlative in company descriptions: {word!r}"
    print("PASS: JS text — wildcard, radar, hovers, heat trail and company descriptions match the code")


def test_gemini_signal_wording():
    prompt_rule = "Sentence 3: what in the data would weaken it, e.g. acceleration falling below +5 pp or a lower layer score."
    assert prompt_rule in open("analyze.py", encoding="utf-8").read()
    facts = {"regime": "Risk-On · scores × 1.00", "layers": [{"id": "compute", "name": "Semicon", "score": 54.0,
             "unmeasured": None, "companies": [{"ticker": "AVGO", "score": 62, "yoy_pct": 85.5, "accel_pp": 37.6, "ret30_pct": -8.8}]}]}
    reply = ("### 💡 ONE SIGNAL TO WATCH\nCompany: AVGO (Broadcom Inc.), Watch\n"
             "Broadcom reported revenue YoY of +86%. Acceleration below +5 pp would weaken it.\n")
    out = analyze.finalize(reply, facts)
    assert "Company: AVGO (Broadcom Inc.), Watch.\nBroadcom" in out, out
    print("PASS: signal sentence 1 gets its full stop; prompt's sentence-3 rule points at the data")


if __name__ == "__main__":
    import logging
    logging.disable(logging.CRITICAL)
    test_no_forecast_targets_a_rating_that_does_not_exist()
    test_js_text_matches_the_code()
    test_gemini_signal_wording()
    print("\nAll page-text tests passed.")

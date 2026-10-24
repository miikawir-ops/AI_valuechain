"""
test_signal_rule.py — signal-v1 (Ray, 2026-10-24): the "One signal to watch"
pick is fixed by rule from the scored data, not chosen by the model. A-rated
without hype by score → acceleration → ticker ("Watch"); else B-rated with
measured acceleration above +5 pp, without hype ("Monitor"); else "No signal
today". Gemini only explains; naming another company withholds the
explanation, never the pick. A divergence-flagged pick shows the flag. Under
the rule, "no company qualifies" ends the current pick.

No pytest in this environment — plain script. Run: python test_signal_rule.py
"""

import os
import json
import tempfile
import datetime

import main
import render
import analyze
import track_record
from score_engine import signal_pick, SIGNAL_RULE_VERSION
from test_regime_label import _ticker

D = datetime.date
c = lambda tk, rating, score, accel, hype=False: {"ticker": tk, "rating": rating, "score": score,
                                                  "fund_delta": None if accel is None else accel / 100, "is_hype": hype}


def test_rule_order_ties_fallback_and_none():
    cos = [c("AVGO", "A", 61.5, 37.6), c("MU", "A", 69.3, 33.6), c("NVDA", "A", 57.5, 20.6), c("HYP", "A", 90.0, 40.0, hype=True)]
    assert signal_pick(cos)["ticker"] == "MU" and signal_pick(list(reversed(cos)))["ticker"] == "MU", "order-dependent"
    assert signal_pick([c("X", "A", 60.0, 21.0), c("Y", "A", 60.0, 30.0)])["ticker"] == "Y", "tie: higher acceleration"
    assert signal_pick([c("ZZ", "A", 60.0, 30.0), c("AA", "A", 60.0, 30.0)])["ticker"] == "AA", "tie: ticker A–Z"
    b = signal_pick([c("B1", "B", 50.0, 6.0), c("B2", "B", 55.0, 4.0), c("B3", "B", 70.0, None), c("C1", "C", 80.0, 30.0)])
    assert (b["ticker"], b["tier"], b["word"]) == ("B1", "B", "Monitor"), b
    none = signal_pick([c("B2", "B", 55.0, 4.0), c("H", "B", 60.0, 9.0, hype=True)])
    assert none["ticker"] is None and none["rule"] == SIGNAL_RULE_VERSION
    print("PASS: rule — MU on the Oct 23 figures in any order; ties by acceleration then ticker; B fallback 'Monitor'; none")


def _facts(md, macro):
    cwd = os.getcwd()
    with tempfile.TemporaryDirectory() as tmp:
        os.chdir(tmp)
        try:
            scored = main.stage_score(md, macro, [D(2026, 10, d) for d in (19, 20, 21, 22, 23)])
            prompt, facts = analyze.build_prompt(scored, md, macro, D(2026, 10, 23))
        finally:
            os.chdir(cwd)
    return scored, prompt, facts


MACRO = {"vix": 19.4, "yield_10y_change": -19.6, "nasdaq_vs_spx_20d": -0.024, "nasdaq_30d_return": -0.041, "data_missing": []}
HOT = dict(gm_delta=0.2, gross_margin=0.6, news_velocity=10.0, capex_div=1.0, vol_spike=3.0, analyst_upgrades=3,
           short_int_change=-0.5, price_momentum=4.0)


def test_gemini_explains_the_rules_pick_and_cannot_replace_it():
    md = {lid: [_ticker(lid[:4].upper())] for lid in render.LAYER_NAMES}
    md["memory"] = [dict(_ticker("MUX"), name="Micron X", growth_curr=3.793, growth_prev=3.457, price_30d_return=-0.189, **HOT),
                    dict(_ticker("AVX"), name="Broad X", growth_curr=0.855, growth_prev=0.479, price_30d_return=-0.088)]
    scored, prompt, facts = _facts(md, MACRO)
    sig = facts["signal"]
    assert sig["ticker"] == "MUX" and sig["word"] == "Watch", sig
    assert 'Sentence 1, exactly: "Company: MUX (Micron X), Watch."' in prompt
    other = "### 💡 ONE SIGNAL TO WATCH\nCompany: AVX (Broad X), Watch. Revenue YoY +86%. A slowdown would weaken it.\n"
    out = analyze.finalize(other, facts)
    assert "Company: MUX (Micron X), Watch. Explanation withheld" in out and "AVX" not in out, out
    # The divergence flag is shown with its figures on the pick (MUX: +33.6 pp, -18.9%, NASDAQ -4.1%).
    assert "Note: price -18.9% in 30 days (NASDAQ -4.1%) while revenue accelerates +33.6 pp (divergence flag)." in out, out
    ok = analyze.finalize("### 💡 ONE SIGNAL TO WATCH\nCompany: MUX (Micron X), Watch\nRevenue YoY +379%. Acceleration below +5 pp would weaken it.\n", facts)
    assert "(divergence flag). Not financial advice." in ok and "Explanation withheld" not in ok, ok
    # scores_detail records the rule's pick even when the model names another company.
    cwd = os.getcwd()
    with tempfile.TemporaryDirectory() as tmp:
        os.chdir(tmp)
        try:
            line = main.save_scores_detail(md, MACRO, scored, D(2026, 10, 23), [], other)
        finally:
            os.chdir(cwd)
    assert line["signal"]["ticker"] == "MUX" and line["signal"]["rule"] == SIGNAL_RULE_VERSION, line["signal"]
    print("PASS: Gemini names AVX → explanation withheld, MUX still shown and recorded; divergence note on the card")


def test_no_qualifying_company_ends_the_pick():
    rows = [("2026-10-23", "MU", 877.55, "a"), ("2026-10-26", "MU", 880.0, "b"),
            ("2026-10-27", None, None, "c"), ("2026-10-28", "MU", 900.0, "d")]
    closes = {"MU": {D(2026, 10, 23): 877.55, D(2026, 10, 26): 880.0, D(2026, 10, 28): 900.0},
              "^IXIC": {D(2026, 10, 23): 100.0, D(2026, 10, 26): 101.0, D(2026, 10, 28): 102.0}}
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "d.jsonl")
        with open(path, "w") as fh:
            for s, t, cl, r in rows:
                fh.write(json.dumps({"session": s, "run_utc": r, "signal": {"ticker": t, "close": cl, "rule": "signal-v1"}}) + "\n")
        tr = track_record.build([], path, D(2026, 10, 28), "m",
                                closes_for=lambda t, a, b: {d: v for d, v in closes[t].items() if a <= d <= b})
    got = [(r["ticker"], r["first"], r["end"], r["active"]) for r in tr["current"]]
    assert got == [("MU", "2026-10-28", "2026-10-28", True), ("MU", "2026-10-23", "2026-10-26", False)], got
    print("PASS: a session where no company qualifies ends the pick at its last qualifying session")


def test_replay_of_the_four_real_oct_23_runs_picks_mu():
    lines = [json.loads(l) for l in open("scores_detail.jsonl", encoding="utf-8")][:4]
    picks = [signal_pick(l["companies"])["ticker"] for l in lines]
    assert picks == ["MU"] * 4, picks
    print(f"PASS: the rule picks {picks} on the four Oct 23 runs (Gemini picked AVGO, MU, withheld, MU)")


if __name__ == "__main__":
    import logging
    logging.disable(logging.CRITICAL)
    test_rule_order_ties_fallback_and_none()
    test_gemini_explains_the_rules_pick_and_cannot_replace_it()
    test_no_qualifying_company_ends_the_pick()
    test_replay_of_the_four_real_oct_23_runs_picks_mu()
    print("\nAll signal-rule tests passed.")

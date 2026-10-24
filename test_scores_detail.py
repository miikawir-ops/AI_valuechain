"""
test_scores_detail.py — scores_detail.jsonl is the point-in-time record of
every run: one line per run (session + run_utc), pure append, never
replaced. Each line holds every scoring input, sub-score and result per
company, the macro inputs and regime, the radar ranking, the signal pick
with its close, withheld sections, code commit and library versions — and
must be enough to replay that run's scores exactly. Ray, 2026-10-24.

No pytest in this environment — plain script. Run: python test_scores_detail.py
"""

import os
import json
import tempfile
import datetime

import main
import render
from test_regime_label import _ticker

MACRO = {"vix": 17.0, "yield_10y_change": 10.0, "nasdaq_vs_spx_20d": 0.0, "data_missing": []}
SESSIONS = [datetime.date(2026, 10, d) for d in (19, 20, 21, 22, 23)]
RADAR = [{"ticker": "MUX", "layer": "memory", "score": 81.2, "accel": {"measured": True, "latest_growth": 379.3, "consecutive_accel": 1, "compared": 3, "trend_delta": 12.0},
          "gross_margin": 58.1, "analyst_count": 30, "ret_1mo": -18.9},
         {"ticker": "ZZZ", "layer": "infra", "score": None, "accel": {"measured": False, "latest_growth": 104.5},
          "gross_margin": None, "analyst_count": 4, "ret_1mo": 3.0}]
ANALYSIS = ("### 🌍 MACRO REGIME\nRisk-On.\n\n### 🔩 AI CHAIN BOTTLENECK\nSection withheld: figures didn't match the data.\n\n"
            "### 💡 ONE SIGNAL TO WATCH\nCompany: MUX (Micron X), Watch. Signal. Risk. Not financial advice.\n")


def _market():
    md = {lid: [_ticker(lid[:4].upper())] for lid in render.LAYER_NAMES}
    md["memory"] = [dict(_ticker("MUX"), price=877.55, growth_curr=3.793, growth_prev=3.457, growth_quarter="2026-09-03",
                         revenue_source="yfinance + EDGAR us-gaap:Revenues [USD]", market_cap=9e11),
                    dict(_ticker("NOM"), growth_prev=None, market_cap=1e11, revenue_source="yfinance only (EDGAR: no CIK)")]
    return md


def test_detail_line_is_appended_complete_and_replays():
    cwd = os.getcwd()
    with tempfile.TemporaryDirectory() as tmp:
        os.chdir(tmp)
        try:
            md = _market()
            scored = main.stage_score(md, MACRO, SESSIONS)
            main.save_scores_detail(md, MACRO, scored, SESSIONS[-1], RADAR, ANALYSIS)
            main.save_scores_detail(md, MACRO, scored, SESSIONS[-1], RADAR, ANALYSIS)   # e.g. the 06:00 rerun
            lines = [json.loads(l) for l in open("scores_detail.jsonl", encoding="utf-8")]
        finally:
            os.chdir(cwd)
    assert len(lines) == 2, f"{len(lines)} lines — a same-session run must append, not replace"
    line = lines[0]
    assert line["session"] == "2026-10-23" and datetime.date.fromisoformat(line["session"]).weekday() < 5
    assert line["run_utc"].endswith("Z") and line["method"] and "python" in line["versions"] and "yfinance" in line["versions"]
    assert line["macro"]["regime"] == "Risk-On" and line["macro"]["multiplier"] == 1.0
    assert line["signal"] == {"ticker": "MUX", "close": 877.55}, line["signal"]
    assert line["withheld_sections"] == ["🔩 AI CHAIN BOTTLENECK"], line["withheld_sections"]
    assert [r["rank"] for r in line["radar"]] == [1, None] and line["radar"][0]["latest_yoy_pct"] == 379.3
    assert line["radar"][0]["measured"] and not line["radar"][1]["measured"] and line["radar"][0]["consecutive_accel"] == 1
    c = {x["ticker"]: x for x in line["companies"]}
    assert set(c) == {t["ticker"] for ts in md.values() for t in ts}
    assert c["MUX"]["inputs"]["revenue_source"].startswith("yfinance + EDGAR") and c["MUX"]["accel_measured"]
    assert c["NOM"]["fund_delta"] is None and not c["NOM"]["accel_measured"]
    assert set(c["MUX"]["sub_scores"]) >= {"acceleration", "constraints", "smart_money"}
    print("PASS: one line per run appended (2 runs, same session → 2 lines); fields, signal, withheld, radar present")

    # Replay from the line alone: same company and layer scores.
    replay_md = {}
    for x in line["companies"]:
        replay_md.setdefault(x["layer"], []).append(dict(x["inputs"], ticker=x["ticker"]))
    with tempfile.TemporaryDirectory() as tmp:
        os.chdir(tmp)
        try:
            re_scored = main.stage_score(replay_md, line["macro"], SESSIONS)
        finally:
            os.chdir(cwd)
    for lid, res in re_scored.items():
        assert abs(res["best"]["score"] - line["layers"][lid]["score"]) < 1e-9, (lid, res["best"]["score"], line["layers"][lid]["score"])
        for t in res["all_tickers"]:
            assert abs(t["score"] - c[t["ticker"]]["score"]) < 1e-9, (t["ticker"], t["score"], c[t["ticker"]]["score"])
    print("PASS: replaying one line reproduces every company and layer score exactly")


if __name__ == "__main__":
    import logging
    logging.disable(logging.CRITICAL)
    test_detail_line_is_appended_complete_and_replays()
    print("\nAll scores-detail tests passed.")

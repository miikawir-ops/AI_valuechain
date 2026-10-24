"""
test_revenue_growth.py — Revenue growth is year-over-year: a quarter against
the quarter ending ~a year earlier, matched by date. Acceleration (growth
delta) is the change in that YoY between the latest two quarters, so it
needs 6 quarters; with fewer it's unmeasured (None), never estimated.

Root cause: the old code took dropna() columns 0,1,2,3 as q0,q1,q4,q5, i.e.
compared the latest quarter with the one 2 quarters back. MU on 2026-10-24:
code "YoY" +204%, true YoY +346%. growth_prev fell back to info's
earningsGrowth (EPS growth, not revenue) when the 2-back quarter was missing.

No pytest in this environment — plain script. Run: python test_revenue_growth.py
"""

import math
from unittest.mock import patch

import pandas as pd

import fetch_market
from test_nan_handling import FIXED_END, _mock_ticker

NAN = float("nan")


def _hist():
    n = 70
    dates = pd.date_range(end=FIXED_END, periods=n, freq="D")
    return pd.DataFrame({"Close": [100.0 + i for i in range(n)], "Volume": [1_000_000.0] * n}, index=dates)


def _fetch(revenue: dict, _edgar: bool = False, **info):
    """revenue: {quarter_end: value in $bn}, newest first, as yfinance orders it.
    EDGAR is switched off (no network) unless _edgar=True, for tests that
    patch edgar._get themselves."""
    mock = _mock_ticker(_hist(), **info)
    mock.quarterly_financials = pd.DataFrame(
        {pd.Timestamp(k): [v * 1e9] for k, v in revenue.items()}, index=["Total Revenue"])
    with patch("fetch_market.yf.Ticker", return_value=mock):
        if _edgar:
            return fetch_market.fetch_ticker_data("TST", headlines=[], layer_keywords=["x"], layer_tickers=["TST"])
        with patch("edgar.quarterly_revenue", return_value=(None, "off in this test")):
            return fetch_market.fetch_ticker_data("TST", headlines=[], layer_keywords=["x"], layer_tickers=["TST"])


def test_growth_is_date_matched_yoy_not_two_quarters_back():
    # MU's real yfinance revenue on 2026-10-24 ($bn); the latest column is NaN.
    mu = {"2026-08-31": NAN, "2026-05-31": 41.456, "2026-02-28": 23.86,
          "2025-11-30": 13.643, "2025-08-31": 11.315, "2025-05-31": 9.301}
    r = _fetch(mu, earningsGrowth=9.99)
    want = round(41.456 / 9.301 - 1, 4)
    assert r["growth_curr"] is not None and math.isclose(r["growth_curr"], want, abs_tol=1e-4), \
        f"growth_curr {r['growth_curr']}, want true YoY {want} (old code: 2 quarters back = 2.0386)"
    assert r["growth_quarter"] == "2026-05-31", f"growth_quarter {r['growth_quarter']!r}"
    # Feb 2026 has no Feb 2025 quarter here → acceleration unmeasured, not EPS growth.
    assert r["growth_prev"] is None, f"growth_prev {r['growth_prev']}, want None (old code: 1.109 / earningsGrowth)"
    assert "growth_prev" in r["data_missing"], r["data_missing"]
    print(f"PASS: MU growth is date-matched YoY {want:+.1%}; acceleration unmeasured with 5 quarters")


def test_six_quarters_measure_acceleration():
    six = {"2026-06-30": 15.0, "2026-03-31": 13.0, "2025-12-31": 12.0,
           "2025-09-30": 11.0, "2025-06-30": 10.0, "2025-03-31": 10.0}
    r = _fetch(six)
    assert math.isclose(r["growth_curr"], 0.5, abs_tol=1e-4), r["growth_curr"]
    assert math.isclose(r["growth_prev"], 0.3, abs_tol=1e-4), r["growth_prev"]
    print("PASS: 6 quarters give YoY for the latest two quarters (+50%, +30%)")


def test_growth_history_persists_and_fills_unmeasured_acceleration():
    """Each run stores growth_curr at its quarter-end. Next quarter, that
    stored value is the previous-quarter YoY when EDGAR has none (ASML/TSM),
    and a cross-check when it does. A ticker with no computed YoY stores nothing."""
    import os, json, tempfile
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "growth_history.json")
        run1 = {"compute": [{"ticker": "ASML", "growth_quarter": "2026-06-30", "growth_curr": 0.212,
                             "growth_prev": None, "revenue_source": "yfinance only", "data_missing": ["growth_prev"]},
                            {"ticker": "NONE", "growth_quarter": None, "growth_curr": None, "growth_prev": None}]}
        fetch_market.apply_growth_history(run1, path)
        assert json.load(open(path)) == {"ASML": {"2026-06-30": 0.212}}, open(path).read()
        assert run1["compute"][0]["growth_prev"] is None, "nothing stored yet for the quarter before"
        run2 = {"compute": [{"ticker": "ASML", "growth_quarter": "2026-09-30", "growth_curr": 0.30,
                             "growth_prev": None, "revenue_source": "yfinance only", "data_missing": ["growth_prev"]}]}
        fetch_market.apply_growth_history(run2, path)
        t = run2["compute"][0]
        assert t["growth_prev"] == 0.212 and "growth_prev" not in t["data_missing"], t
        assert "growth_history.json (2026-06-30)" in t["revenue_source"], t["revenue_source"]
        assert json.load(open(path))["ASML"] == {"2026-06-30": 0.212, "2026-09-30": 0.30}
    print("PASS: YoY persisted per quarter-end; next quarter it measures acceleration EDGAR can't")


if __name__ == "__main__":
    import logging
    logging.disable(logging.CRITICAL)
    test_growth_is_date_matched_yoy_not_two_quarters_back()
    test_six_quarters_measure_acceleration()
    test_growth_history_persists_and_fills_unmeasured_acceleration()
    print("\nAll revenue-growth tests passed.")

"""
test_nan_handling.py — Verifies fetch_market.py handles yfinance returning
NaN data without ever propagating NaN into price_30d_return / price_momentum
or the final published page.

Root cause: yfinance can append a trailing "today" bar with NaN Close right
after 00:00 UTC, before the market has actually opened. Every live run at
2026-10-08 00:17, 2026-10-09 00:32 and 2026-10-10 00:08 UTC showed
"ret30": NaN for all 28 tickers on the published page, with price_momentum
silently clamped to +5.0 (max(-5, min(5, nan)) == 5.0 in Python — confirmed
directly, not assumed).

No pytest in this environment — plain script, matching test_gemini.py's
convention. Run: python test_nan_handling.py
"""

import math
from unittest.mock import MagicMock, patch

import pandas as pd

import fetch_market


def _mock_ticker(hist: pd.DataFrame, **info_overrides) -> MagicMock:
    info = {
        "currentPrice": hist["Close"].iloc[-1] if not math.isnan(hist["Close"].iloc[-1]) else 100.0,
        "previousClose": 100.0,
        "fiftyTwoWeekHigh": 200.0, "fiftyTwoWeekLow": 50.0,
        "marketCap": 1_000_000_000, "longName": "Test Co",
        "currency": "USD", "numberOfAnalystOpinions": 5, "sector": "Technology",
    }
    info.update(info_overrides)
    m = MagicMock()
    m.info = info
    m.history.return_value = hist
    m.quarterly_financials = pd.DataFrame()
    m.recommendations = pd.DataFrame()
    m.quarterly_cashflow = pd.DataFrame()
    return m


def test_trailing_nan_row_is_dropped_and_does_not_propagate():
    """The primary fix: a trailing NaN Close row (the 00:00 UTC unfinished
    bar) must be dropped before computing anything, so price_30d_return
    and price_momentum come from the real, complete history underneath it
    — not from the polluted series."""
    n_days = 70
    dates  = pd.date_range(end=pd.Timestamp.today(), periods=n_days, freq="D")
    closes = [100.0 + i * 0.5 for i in range(n_days)]
    volumes = [1_000_000.0 for _ in range(n_days)]
    closes[-1]  = float("nan")   # the unfinished "today" bar
    volumes[-1] = float("nan")
    hist = pd.DataFrame({"Close": closes, "Volume": volumes}, index=dates)

    # Independently compute the expected momentum from the CLEAN series
    # (what should remain after the trailing NaN row is dropped), using
    # fetch_market.py's own formula. If the trailing-row-drop regresses,
    # fetch_ticker_data computes off the polluted 70-point series instead
    # — NaN all the way through, then old code's blind clamp turns that
    # into exactly 5.0, which will not match this independently-derived
    # value for a smooth linear ramp.
    clean = closes[:-1]
    ret_5d_exp  = clean[-1] / clean[-5]  - 1
    ret_90d_exp = clean[-1] / clean[-63] - 1
    avg_exp = ret_90d_exp / 18 if ret_90d_exp != 0 else 0.001
    momentum_exp = round(ret_5d_exp / avg_exp, 2) if avg_exp != 0 else 1.0
    momentum_exp = max(-5.0, min(5.0, momentum_exp))
    assert momentum_exp != 5.0, "test setup bug: expected value coincides with the clamp ceiling"

    with patch("fetch_market.yf.Ticker", return_value=_mock_ticker(hist)):
        result = fetch_market.fetch_ticker_data(
            "TEST", headlines=[], layer_keywords=["test"], layer_tickers=["TEST"]
        )

    assert result is not None, "fetch_ticker_data returned None unexpectedly"

    r30, mom = result["price_30d_return"], result["price_momentum"]
    assert not (isinstance(r30, float) and math.isnan(r30)), f"price_30d_return is NaN: {r30}"
    assert not (isinstance(mom, float) and math.isnan(mom)), f"price_momentum is NaN: {mom}"
    assert r30 is not None, "should compute from the clean history, not be marked missing"
    assert mom == momentum_exp, (
        f"expected {momentum_exp} (computed from the clean history), got {mom} — "
        f"a value of exactly 5.0 here would mean the trailing NaN row leaked "
        f"into the ratio and got blindly clamped: the original bug."
    )
    assert result["data_missing"] == []

    print("PASS: trailing NaN row dropped; price_30d_return/price_momentum match the clean history")


def test_mid_series_nan_is_marked_missing_not_nan():
    """Defense in depth: a NaN that isn't the trailing row (e.g. a genuine
    yfinance data gap landing exactly on the -21 index price_30d divides
    by) must still never reach the output as NaN — it must become None,
    named in data_missing."""
    n_days = 70
    dates  = pd.date_range(end=pd.Timestamp.today(), periods=n_days, freq="D")
    closes = [100.0 + i * 0.5 for i in range(n_days)]
    volumes = [1_000_000.0 for _ in range(n_days)]
    closes[-21] = float("nan")   # exactly the index price_30d divides by
    hist = pd.DataFrame({"Close": closes, "Volume": volumes}, index=dates)

    with patch("fetch_market.yf.Ticker", return_value=_mock_ticker(hist)):
        result = fetch_market.fetch_ticker_data(
            "GAP", headlines=[], layer_keywords=["test"], layer_tickers=["GAP"]
        )

    assert result is not None
    r30 = result["price_30d_return"]
    assert not (isinstance(r30, float) and math.isnan(r30)), f"price_30d_return leaked NaN: {r30}"
    assert r30 is None, f"expected None (marked missing), got {r30}"
    assert "price_30d_return" in result["data_missing"]

    print("PASS: mid-series NaN gap correctly marked missing (None + data_missing), not left as NaN")


def test_assert_no_nan_is_the_final_backstop():
    """fail the run rather than write NaN to the page — the last-resort
    check that must run outside any try/except that would swallow it."""
    clean = {"energy": [{"ticker": "CEG", "price_30d_return": 0.05}]}
    fetch_market.assert_no_nan(clean)  # must not raise

    dirty = {"energy": [{"ticker": "CEG", "price_30d_return": float("nan")}]}
    try:
        fetch_market.assert_no_nan(dirty)
        raise AssertionError("assert_no_nan should have raised on NaN input")
    except ValueError as e:
        assert "price_30d_return" in str(e)

    print("PASS: assert_no_nan raises on NaN, passes clean data")


if __name__ == "__main__":
    test_trailing_nan_row_is_dropped_and_does_not_propagate()
    test_mid_series_nan_is_marked_missing_not_nan()
    test_assert_no_nan_is_the_final_backstop()
    print("\nAll NaN-handling tests passed.")

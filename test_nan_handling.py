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
import statistics
from unittest.mock import MagicMock, patch

import pandas as pd

import fetch_market

FIXED_END = "2026-10-09"   # a past session, so no bar is ever "today's"


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
    # Non-empty with the right column but dated well outside the 30-day
    # analyst window — resolves to a real 0 (not missing), so this shared
    # fixture doesn't pollute data_missing for tests that don't care about
    # the analyst signal specifically.
    m.upgrades_downgrades = pd.DataFrame(
        {"ToGrade": ["Buy"]}, index=pd.DatetimeIndex(["2020-01-01"])
    )
    m.quarterly_cashflow = pd.DataFrame()
    return m


def test_trailing_nan_row_is_dropped_and_does_not_propagate():
    """The primary fix: a trailing NaN Close row (the 00:00 UTC unfinished
    bar) must be dropped before computing anything, so price_30d_return
    and price_momentum come from the real, complete history underneath it
    — not from the polluted series."""
    n_days = 70
    # Fixed past dates, not today(): drop_unfinished_bar also drops a bar
    # dated today until 16:00 ET, which would make this time-of-day dependent.
    dates  = pd.date_range(end=FIXED_END, periods=n_days, freq="D")
    closes = [100.0 + i * 0.5 for i in range(n_days)]
    volumes = [1_000_000.0 for _ in range(n_days)]
    closes[-1]  = float("nan")   # the empty bar yfinance appends after 00:00 UTC
    volumes[-1] = float("nan")
    hist = pd.DataFrame({"Close": closes, "Volume": volumes}, index=dates)

    # Independently compute the expected momentum from the CLEAN series
    # (what should remain after the trailing NaN row is dropped), using
    # fetch_market.py's own current formula (ledger-ported from RayDar-
    # DataCenter, 2026-10-11 — the ratio formula this replaced is gone).
    # If the trailing-row-drop regresses, fetch_ticker_data computes off
    # the polluted 70-point series instead — NaN all the way through,
    # which would fail the isnan() checks below regardless of this exact
    # value, so this also still catches that regression.
    clean = closes[:-1]   # 69 points — exactly the window size required
    ret_5d_exp = clean[-1] / clean[-6] - 1
    pace_exp   = (clean[-1] / clean[-64]) ** (5 / 63) - 1
    moves_exp  = [clean[i] / clean[i - 5] - 1 for i in range(len(clean) - 63, len(clean))]
    scale_exp  = max(abs(pace_exp), statistics.pstdev(moves_exp))
    momentum_exp = round(1 + (ret_5d_exp - pace_exp) / scale_exp, 2) if scale_exp > 0 else 1.0
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
        f"a mismatch here means the trailing NaN row leaked into the formula "
        f"instead of being dropped first."
    )
    # Price-derived fields only: the mock has no quarterly financials, so
    # growth_curr/growth_prev are rightly marked missing (since c. 2026-10-24).
    price_fields = {"price", "price_act", "price_30d_return", "price_momentum", "vol_spike"}
    assert not price_fields & set(result["data_missing"]), result["data_missing"]

    print("PASS: trailing NaN row dropped; price_30d_return/price_momentum match the clean history")


def test_mid_series_nan_is_marked_missing_not_nan():
    """Defense in depth: a NaN that isn't the trailing row (e.g. a genuine
    yfinance data gap landing exactly on the -21 index price_30d divides
    by) must still never reach the output as NaN — it must become None,
    named in data_missing."""
    n_days = 70
    dates  = pd.date_range(end=FIXED_END, periods=n_days, freq="D")
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


def test_price_is_the_last_completed_close_not_the_live_quote():
    """The card price and the day change (price_act, a smart-money input)
    come from the completed-session closes, the same bars as the 30d return
    and the "Data: close of ..." label. The old code read info's live quote:
    run #290 (2026-10-21 19:31 UTC) showed MU at a Wednesday intraday $919.11
    under "close of Tuesday, October 20" (Tuesday's close was $896.07)."""
    n_days = 70
    dates  = pd.date_range(end=FIXED_END, periods=n_days, freq="D")
    closes = [100.0 + i * 0.5 for i in range(n_days)]
    hist   = pd.DataFrame({"Close": closes, "Volume": [1_000_000.0] * n_days}, index=dates)
    mock   = _mock_ticker(hist, currentPrice=999.0, regularMarketPrice=999.0, previousClose=1.0)
    with patch("fetch_market.yf.Ticker", return_value=mock):
        r = fetch_market.fetch_ticker_data("LIVE", headlines=[], layer_keywords=["x"], layer_tickers=["LIVE"])
    assert r["price"] == round(closes[-1], 2), f"price {r['price']} is not the last completed close {closes[-1]}"
    assert r["price_act"] == round(closes[-1] / closes[-2] - 1, 4), f"price_act {r['price_act']} not from closes"
    print("PASS: price and price_act come from the last completed closes, not the live quote")


def test_short_history_30d_return_is_missing_not_zero():
    """Fewer than 21 completed closes can't give a 30d return. The old code
    wrote 0.0 without marking it missing, and peer_outperformance started at
    0.0 for every ticker, so a gap read as "flat, in line with peers". Both
    are None now, and the 30d return is named in data_missing; the hype
    rules skip what they can't evaluate instead of reading a stand-in."""
    n_days = 15
    dates  = pd.date_range(end=FIXED_END, periods=n_days, freq="D")
    hist   = pd.DataFrame({"Close": [100.0 + i for i in range(n_days)],
                           "Volume": [1_000_000.0] * n_days}, index=dates)
    with patch("fetch_market.yf.Ticker", return_value=_mock_ticker(hist)):
        r = fetch_market.fetch_ticker_data("NEW", headlines=[], layer_keywords=["x"], layer_tickers=["NEW"])
    assert r is not None, "fetch returned nothing for a short history"
    assert r["price_30d_return"] is None, f"30d return {r['price_30d_return']!r} on {n_days} closes, want None"
    assert "price_30d_return" in r["data_missing"], f"30d return not marked missing: {r['data_missing']}"
    fetch_market.add_peer_outperformance([r])
    assert r["peer_outperformance"] is None, f"peer_outperformance {r['peer_outperformance']!r}, want None"
    from score_engine import ScoreEngine
    hype, reasons = ScoreEngine({}).calculate_hype(
        {"price_30d_return": None, "growth_curr": None, "peer_outperformance": None}, None, [])
    assert not hype and not reasons, f"hype fired on missing inputs: {reasons}"
    print("PASS: a 30d return that can't be computed is None + data_missing, not 0%")


def _macro_tickers(tnx_raises: bool = False):
    def bars(closes):
        return pd.DataFrame({"Close": closes}, index=pd.bdate_range(end=FIXED_END, periods=len(closes)))
    vix = MagicMock(); vix.history.return_value = bars([17.0] * 5)
    tnx = MagicMock()
    if tnx_raises:
        tnx.history.side_effect = ConnectionError("simulated ^TNX outage")
    else:
        tnx.history.return_value = bars([4.0] * 30)
    nasdaq = MagicMock(); nasdaq.history.return_value = bars([100.0] * 23 + [102.0])   # >= 21 closes: 20-session and 30d returns both computable
    spx    = MagicMock(); spx.history.return_value = bars([100.0] * 20)
    return {"^VIX": vix, "^TNX": tnx, "^IXIC": nasdaq, "^GSPC": spx}


def test_failed_macro_fetch_is_missing_not_a_default():
    """A failed macro fetch is None, named in data_missing — never a made-up
    number. The old code ran all three fetches in one try: a ^TNX failure
    left yield_10y_change at the hardcoded 0.0 and discarded the NASDAQ/S&P
    signal that would have come after it (also 0.0)."""
    tickers = _macro_tickers(tnx_raises=True)
    with patch("fetch_market.yf.Ticker", side_effect=lambda sym: tickers[sym]):
        macro = fetch_market.fetch_macro()
    assert macro["yield_10y_change"] is None, f"failed fetch became {macro['yield_10y_change']!r}, not None"
    assert macro.get("data_missing") == ["yield_10y_change"], f"data_missing: {macro.get('data_missing')}"
    assert macro["vix"] == 17.0, "a later failure must not blank out VIX"
    assert macro["nasdaq_vs_spx_20d"] == 0.02, (
        f"NASDAQ/S&P was fetchable and must not be discarded: {macro['nasdaq_vs_spx_20d']!r}")
    print("PASS: a failed macro fetch is None + data_missing; the other signals survive")


def test_missing_macro_input_is_left_out_of_the_multiplier():
    """A missing macro input is excluded from the regime vote and named —
    not a neutral default. With the old `.get("vix", 20.0)` a present-but-
    None VIX crashed on `None > 30`, which process_sector caught as a
    scoring error: every ticker scored 0, Green."""
    from score_engine import ScoreEngine
    missing = []
    engine = ScoreEngine({"vix": None, "yield_10y_change": 120.0, "nasdaq_vs_spx_20d": -0.07})
    assert engine.get_macro_multiplier(missing) == (0.65, "Risk-Off")
    assert missing == ["macro_vix"], f"expected VIX named as missing, got {missing}"

    none = ScoreEngine({"vix": None, "yield_10y_change": None, "nasdaq_vs_spx_20d": None})
    assert none.get_macro_multiplier() == (1.0, "Unknown (macro data unavailable)")

    r = engine.process_sector("memory_MU", {
        "growth_curr": 1.11, "growth_prev": 0.47, "gm_delta": 0.06, "news_velocity": 8.0,
        "capex_div": 0.5, "vol_spike": 1.4, "price_act": 0.02, "analyst_upgrades": 2,
        "short_int_change": -0.03, "price_30d_return": 0.79, "price_momentum": 1.8,
        "peer_outperformance": 0.35,
    })
    assert "Scoring error" not in r["status"], f"a missing macro input broke scoring: {r['status']}"
    assert r["score"] > 0 and "macro_vix" in r["data_status"], (r["score"], r["data_status"])
    print("PASS: a missing macro input is left out of the vote and named; scoring still works")


if __name__ == "__main__":
    test_trailing_nan_row_is_dropped_and_does_not_propagate()
    test_mid_series_nan_is_marked_missing_not_nan()
    test_assert_no_nan_is_the_final_backstop()
    test_price_is_the_last_completed_close_not_the_live_quote()
    test_short_history_30d_return_is_missing_not_zero()
    test_failed_macro_fetch_is_missing_not_a_default()
    test_missing_macro_input_is_left_out_of_the_multiplier()
    print("\nAll NaN-handling tests passed.")

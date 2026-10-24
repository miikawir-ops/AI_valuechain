"""
test_edgar.py — EDGAR quarterly revenue fills the quarters yfinance lacks,
so revenue acceleration (needs 6 quarters) can be measured, and only from
an XBRL concept that matches yfinance where both have a quarter.

Offline: edgar._get is patched with hand-built companyfacts.
No pytest in this environment — plain script. Run: python test_edgar.py
"""

import math
import datetime
from unittest.mock import patch

import edgar
from test_revenue_growth import _fetch

D = datetime.date


def _fact(start, end, val, form="10-Q", filed="2026-08-01"):
    return {"start": start, "end": end, "val": val, "form": form, "filed": filed}


# Quarterly revenue ($) for a calendar-year filer, 2024-Q1 .. 2026-Q2; Q4s
# only appear inside the 10-K's full-year figure.
QUARTERS = {("2024-01-01", "2024-03-31"): 8e9,  ("2024-04-01", "2024-06-30"): 9e9,
            ("2024-07-01", "2024-09-30"): 10e9, ("2025-01-01", "2025-03-31"): 10e9,
            ("2025-04-01", "2025-06-30"): 10e9, ("2025-07-01", "2025-09-30"): 11e9,
            ("2026-01-01", "2026-03-31"): 13e9, ("2026-04-01", "2026-06-30"): 15e9}
FY = {("2024-01-01", "2024-12-31"): 38e9, ("2025-01-01", "2025-12-31"): 43e9}   # Q4s: 11e9, 12e9


def _facts(scale_recent=1.0):
    rows = [_fact(s, e, v) for (s, e), v in QUARTERS.items()]
    rows += [_fact(s, e, v, form="10-K") for (s, e), v in FY.items()]
    full = {"units": {"USD": rows}}
    # A partial concept (like CEG's contract-only revenue): ~22% short.
    partial = {"units": {"USD": [dict(r, val=r["val"] * 0.78) for r in rows]}}
    return {"facts": {"us-gaap": {"RevenueFromContractWithCustomerExcludingAssessedTax": partial,
                                  "Revenues": full}}}


def _fake_get(facts):
    def get(url):
        if url == edgar.TICKERS_URL:
            return {"0": {"ticker": "TST", "cik_str": 1}}
        return facts
    return get


YF = {"2026-06-30": 15.0, "2026-03-31": 13.0, "2025-12-31": 12.0, "2025-09-30": 11.0, "2025-06-30": 10.0}


def test_q4_is_derived_from_the_full_year():
    q = edgar._quarters(_facts()["facts"]["us-gaap"]["Revenues"]["units"]["USD"])
    assert q[D(2024, 12, 31)] == 11e9 and q[D(2025, 12, 31)] == 12e9, {k: v for k, v in q.items() if k.month == 12}
    print("PASS: Q4 = full year − Q1..Q3")


def test_partial_concept_is_rejected_and_full_one_used():
    edgar._cik_cache = None
    yf_rev = {D.fromisoformat(k): v * 1e9 for k, v in YF.items()}
    with patch("edgar._get", _fake_get(_facts())):
        series, note = edgar.quarterly_revenue("TST", yf_rev)
    assert series is not None and "Revenues" in note, f"chose {note!r}"
    print(f"PASS: the concept that matches yfinance is used ({note}); the 22%-short one is rejected")


def test_edgar_quarters_make_acceleration_measurable():
    edgar._cik_cache = None
    with patch("edgar._get", _fake_get(_facts())):
        r = _fetch(YF, _edgar=True)
    # yfinance alone: 2026-03-31 has no 2025-03-31 → unmeasured. EDGAR adds it.
    assert math.isclose(r["growth_curr"], 0.5, abs_tol=1e-4), r["growth_curr"]       # 15 vs 10
    assert r["growth_prev"] is not None and math.isclose(r["growth_prev"], 0.3, abs_tol=1e-4), \
        f"growth_prev {r['growth_prev']} ({r['revenue_source']}), want 13 vs 10 = +30%"
    assert r["revenue_source"].startswith("yfinance + EDGAR"), r["revenue_source"]
    print("PASS: EDGAR's older quarters make acceleration measurable (+50% vs +30%)")


def test_failed_edgar_fetch_leaves_acceleration_unmeasured():
    edgar._cik_cache = None
    def boom(url):
        raise ConnectionError("simulated SEC outage")
    with patch("edgar._get", boom):
        r = _fetch(YF, _edgar=True)
    assert r["growth_prev"] is None and "growth_prev" in r["data_missing"], (r["growth_prev"], r["data_missing"])
    assert "EDGAR fetch failed" in r["revenue_source"], r["revenue_source"]
    print("PASS: a failed EDGAR fetch leaves acceleration unmeasured and says why")


def test_requests_stay_under_sec_rate_limit():
    """SEC's fair-access limit is 10 requests/s. With instant responses the
    throttle must still space requests MIN_INTERVAL_S apart."""
    import time
    from unittest.mock import MagicMock
    stamps = []
    def instant(url, **kw):
        stamps.append(time.monotonic())
        return MagicMock(json=lambda: {}, raise_for_status=lambda: None)
    with patch("edgar.requests.get", instant):
        for _ in range(12):
            edgar._get("https://data.sec.gov/x")
    gaps = [b - a for a, b in zip(stamps, stamps[1:])]
    assert min(gaps) >= edgar.MIN_INTERVAL_S * 0.95, f"min gap {min(gaps):.3f}s"
    assert edgar.MIN_INTERVAL_S >= 0.1, "throttle allows more than 10 requests/s"
    print(f"PASS: SEC requests spaced ≥{edgar.MIN_INTERVAL_S}s apart (min gap {min(gaps):.3f}s)")


if __name__ == "__main__":
    import logging
    logging.disable(logging.CRITICAL)
    test_q4_is_derived_from_the_full_year()
    test_partial_concept_is_rejected_and_full_one_used()
    test_edgar_quarters_make_acceleration_measurable()
    test_failed_edgar_fetch_leaves_acceleration_unmeasured()
    test_requests_stay_under_sec_rate_limit()
    print("\nAll EDGAR tests passed.")

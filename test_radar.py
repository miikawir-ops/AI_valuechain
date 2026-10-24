"""
test_radar.py — Item 9: the radar's growth comes from the dashboard's path
(yfinance + cross-checked EDGAR quarters, date-matched YoY), with no
QoQ-annualized or TTM stand-ins; acceleration needs 6 quarters and counts a
quarter as accelerating only above +5 pp (the dashboard's band); only
measured companies are ranked, and high growers that can't be measured yet
are named on a separate line (Ray, 2026-10-24).

No pytest in this environment — plain script. Run: python test_radar.py
"""

import json
from unittest.mock import MagicMock, patch

import pandas as pd

import fetch_market
import next_nvidia
import render
from test_nan_handling import FIXED_END


def test_acceleration_needs_six_quarters_and_uses_the_band():
    a = next_nvidia.compute_acceleration_score([40.0, 30.0, 20.0, 10.0])
    assert a["measured"] and a["consecutive_accel"] == 3 and a["compared"] == 3 and a["trend_delta"] == 10.0, a
    b = next_nvidia.compute_acceleration_score([40.0, 38.0, 30.0])
    assert b["consecutive_accel"] == 0, "a +2 pp rise counted as accelerating (band is 5 pp)"
    c = next_nvidia.compute_acceleration_score([104.5])
    assert not c["measured"] and c["accel_score"] is None and c["latest_growth"] == 104.5
    assert next_nvidia.compute_nvidia_score({"gross_margin": 60, "analyst_count": 5, "ret_1mo": 10}, c) is None
    print("PASS: acceleration — 6 quarters needed, ±5 pp band, unmeasured isn't scored")


def _mock(revenue_bn: dict):
    n = 130
    hist = pd.DataFrame({"Close": [100.0 + i for i in range(n)]}, index=pd.date_range(end=FIXED_END, periods=n, freq="D"))
    m = MagicMock()
    m.info = {"currentPrice": 999.0, "marketCap": 1e11, "grossMargins": 0.6, "numberOfAnalystOpinions": 8,
              "revenueGrowth": 0.77}
    m.history.return_value = hist
    m.quarterly_financials = pd.DataFrame({pd.Timestamp(k): [v * 1e9] for k, v in revenue_bn.items()}, index=["Total Revenue"])
    return m


def test_growth_comes_from_the_dashboard_path_without_stand_ins():
    two = {"2026-06-30": 15.0, "2026-03-31": 12.0}
    with patch("next_nvidia.yf.Ticker", return_value=_mock(two)), \
         patch("edgar.quarterly_revenue", return_value=(None, "off in this test")):
        d = next_nvidia.fetch_multi_quarter_data("TST")
    assert d["growth_quarters"] == [], f"stand-in growth from 2 quarters: {d['growth_quarters']} (old: QoQ annualized / TTM)"
    assert d["price"] == 229.0, f"price {d['price']}: not the last completed close (old: live quote 999)"
    six = {"2026-06-30": 15.0, "2026-03-31": 13.0, "2025-12-31": 12.0, "2025-09-30": 11.0,
           "2025-06-30": 10.0, "2025-03-31": 10.0}
    with patch("next_nvidia.yf.Ticker", return_value=_mock(six)), \
         patch("edgar.quarterly_revenue", return_value=(None, "off in this test")):
        d = next_nvidia.fetch_multi_quarter_data("TST")
    revenue = {pd.Timestamp(k).date(): v * 1e9 for k, v in six.items()}
    dash_curr, dash_prev, dash_q = fetch_market.quarterly_yoy(revenue)
    assert d["growth_quarters"][:2] == [round(dash_curr * 100, 1), round(dash_prev * 100, 1)], (d["growth_quarters"], dash_curr, dash_prev)
    assert d["growth_quarter"] == dash_q.isoformat()
    print("PASS: radar YoY = dashboard YoY on the same quarters; no QoQ/TTM stand-in; price from closes")


def test_top5_is_measured_only_and_unmeasured_high_growers_are_named():
    m = lambda tk, s, g: {"ticker": tk, "name": tk, "layer": "infra", "score": s, "gross_margin": 50, "analyst_count": 9,
                          "ret_1mo": None, "growth_quarters": [g, g - 10], "growth_quarter": "2026-09-30",
                          "accel": next_nvidia.compute_acceleration_score([g, g - 10])}
    u = lambda tk, g: {"ticker": tk, "name": tk, "layer": "infra", "score": None, "gross_margin": 50, "analyst_count": 3,
                       "ret_1mo": 5.0, "growth_quarters": [g], "accel": next_nvidia.compute_acceleration_score([g])}
    radar = [m("A1", 80, 90), m("A2", 70, 60), u("IPO", 104.5), u("SLOW", 12.0)]
    data = json.loads(render._radar_js_data(radar))
    assert [r["ticker"] for r in data["top5"]] == ["A1", "A2"], data["top5"]
    assert data["top5"][0]["consecutive"] == 1 and data["top5"][0]["quarter"] == "Sep 30, 2026"
    assert data["unmeasured"] == [{"ticker": "IPO", "latest_growth": 104.5}], data["unmeasured"]
    print("PASS: top 5 = measured only; 'acceleration not yet measurable' line names IPO (+104%), not SLOW (12%)")


if __name__ == "__main__":
    import logging
    logging.disable(logging.CRITICAL)
    test_acceleration_needs_six_quarters_and_uses_the_band()
    test_growth_comes_from_the_dashboard_path_without_stand_ins()
    test_top5_is_measured_only_and_unmeasured_high_growers_are_named()
    print("\nAll radar tests passed.")

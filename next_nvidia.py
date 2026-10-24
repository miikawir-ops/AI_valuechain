"""
next_nvidia.py — Next Nvidia Radar: multi-quarter acceleration scoring.

Combines the 25 tracked dashboard tickers + 33 screener universe companies.
Scores each on Nvidia-like trajectory signals:
  1. Multi-quarter revenue acceleration (3-4 quarters, not just 2)
  2. Acceleration confidence (how many consecutive quarters accelerating)
  3. Gross margin expansion (pricing power forming)
  4. Analyst coverage (low = earlier opportunity)
  5. Price momentum confirmation

Ranks only companies whose acceleration is measured (6+ quarters of revenue);
the rest follow in the list, marked, for render to name.
"""

import json
import logging
import datetime
from pathlib import Path
import pandas as pd
import yfinance as yf

from fetch_market import drop_unfinished_bar, merged_revenue, yoy_series

log = logging.getLogger(__name__)

# Combined universe: dashboard tickers + screener additions
RADAR_UNIVERSE = [
    # Energy
    {"ticker": "CEG",   "layer": "energy",   "name": "Constellation Energy"},
    {"ticker": "VST",   "layer": "energy",   "name": "Vistra Corp"},
    {"ticker": "PWR",   "layer": "energy",   "name": "Quanta Services"},
    # Compute
    {"ticker": "NVDA",  "layer": "compute",  "name": "Nvidia"},
    {"ticker": "AMD",   "layer": "compute",  "name": "AMD"},
    {"ticker": "AVGO",  "layer": "compute",  "name": "Broadcom"},
    {"ticker": "ASML",  "layer": "compute",  "name": "ASML"},
    {"ticker": "TSM",   "layer": "compute",  "name": "TSMC"},
    # Memory
    {"ticker": "MU",    "layer": "memory",   "name": "Micron Technology"},
    {"ticker": "AMAT",  "layer": "memory",   "name": "Applied Materials"},
    {"ticker": "LRCX",  "layer": "memory",   "name": "Lam Research"},
    {"ticker": "WDC",   "layer": "memory",   "name": "Western Digital"},
    # Infra
    {"ticker": "VRT",   "layer": "infra",    "name": "Vertiv Holdings"},
    {"ticker": "ANET",  "layer": "infra",    "name": "Arista Networks"},
    {"ticker": "EQIX",  "layer": "infra",    "name": "Equinix"},
    {"ticker": "SMCI",  "layer": "infra",    "name": "Super Micro Computer"},
    # Cloud
    {"ticker": "MSFT",  "layer": "cloud",    "name": "Microsoft"},
    {"ticker": "GOOGL", "layer": "cloud",    "name": "Alphabet"},
    {"ticker": "AMZN",  "layer": "cloud",    "name": "Amazon"},
    {"ticker": "META",  "layer": "cloud",    "name": "Meta"},
    # Software
    {"ticker": "PLTR",  "layer": "software", "name": "Palantir"},
    {"ticker": "NOW",   "layer": "software", "name": "ServiceNow"},
    {"ticker": "SNOW",  "layer": "software", "name": "Snowflake"},
    {"ticker": "CRM",   "layer": "software", "name": "Salesforce"},
    # Screener additions — under-covered high-growth
    {"ticker": "MRVL",  "layer": "infra",    "name": "Marvell Technology"},
    {"ticker": "CDNS",  "layer": "compute",  "name": "Cadence Design Systems"},
    {"ticker": "ONTO",  "layer": "memory",   "name": "Onto Innovation"},
    {"ticker": "AXON",  "layer": "software", "name": "Axon Enterprise"},
    {"ticker": "TTD",   "layer": "software", "name": "The Trade Desk"},
    {"ticker": "APP",   "layer": "software", "name": "AppLovin"},
    {"ticker": "CIEN",  "layer": "infra",    "name": "Ciena Corp"},
    {"ticker": "COHR",  "layer": "infra",    "name": "Coherent Corp"},
    {"ticker": "WOLF",  "layer": "compute",  "name": "Wolfspeed"},
    # New additions from gap analysis
    {"ticker": "GEV",   "layer": "energy",   "name": "GE Vernova"},
    {"ticker": "ETN",   "layer": "energy",   "name": "Eaton Corp"},
    {"ticker": "ARM",   "layer": "compute",  "name": "ARM Holdings"},
    {"ticker": "CSCO",  "layer": "infra",    "name": "Cisco Systems"},
    {"ticker": "DDOG",  "layer": "software", "name": "Datadog"},
    {"ticker": "CRWD",  "layer": "security", "name": "CrowdStrike"},
    {"ticker": "PANW",  "layer": "security", "name": "Palo Alto Networks"},
    {"ticker": "S",     "layer": "security", "name": "SentinelOne"},
    {"ticker": "KLAC",  "layer": "memory",   "name": "KLA Corporation"},
    {"ticker": "TER",   "layer": "compute",  "name": "Teradyne"},
    # Edge computing watch list
    {"ticker": "QCOM",  "layer": "edge",     "name": "Qualcomm"},
    {"ticker": "INTC",  "layer": "edge",     "name": "Intel"},
    # Optical networking — from FactSet bottleneck analysis (Kauppalehti May 2026)
    # These showed 500-1000%+ returns as AI bandwidth demand exploded
    {"ticker": "LITE",  "layer": "infra",    "name": "Lumentum Holdings"},
    {"ticker": "CORZ",  "layer": "infra",    "name": "Core & Main"},
    {"ticker": "GLW",   "layer": "infra",    "name": "Corning Inc"},
    {"ticker": "ALAB",  "layer": "infra",    "name": "Astera Labs"},
    # Energy bottleneck additions — nuclear & power equipment
    {"ticker": "BWXT",  "layer": "energy",   "name": "BWX Technologies"},
    {"ticker": "NRG",   "layer": "energy",   "name": "NRG Energy"},
]

RADAR_CACHE_FILE = "next_nvidia_cache.json"


def fetch_multi_quarter_data(ticker: str) -> dict:
    """
    Fetch 4 quarters of revenue data to compute multi-quarter acceleration.
    Returns growth rates for Q1 (most recent) through Q4 (oldest available).
    """
    try:
        t = yf.Ticker(ticker)
        info = t.info

        # Price and returns from completed sessions (no live quote, no partial
        # bar); a return that can't be computed is None, not 0.
        hist = drop_unfinished_bar(t.history(period="6mo"), label=ticker)
        closes = hist["Close"].dropna().tolist() if not hist.empty else []
        price = closes[-1] if closes else None
        ret_1mo = round((closes[-1]/closes[-21]-1)*100, 1) if len(closes) >= 21 else None
        ret_3mo = round((closes[-1]/closes[-63]-1)*100, 1) if len(closes) >= 63 else None
        # Sparkline: sample 30 points from 6mo history
        if closes and len(closes) >= 5:
            step = max(1, len(closes) // 30)
            price_history = [round(closes[j], 2) for j in range(0, len(closes), step)][-30:]
        else:
            price_history = []

        # Fundamentals
        market_cap    = info.get("marketCap", 0)
        gross_margin  = info.get("grossMargins")
        analyst_count = info.get("numberOfAnalystOpinions") or 0
        fwd_pe        = info.get("forwardPE")

        # Multi-quarter revenue growth: the dashboard's own path (yfinance
        # quarters + cross-checked EDGAR quarters, date-matched YoY). No QoQ-
        # annualized or TTM stand-ins: what can't be measured is missing.
        growth_quarters, growth_quarter, revenue_source = [], None, "none"
        try:
            fin = t.quarterly_financials
            rev_row = next((fin.loc[l] for l in ("Total Revenue", "Revenue")
                            if fin is not None and not fin.empty and l in fin.index), None)
            if rev_row is not None:
                yf_rev = {pd.Timestamp(k).date(): float(v) for k, v in rev_row.dropna().items()}
                revenue, revenue_source = merged_revenue(ticker, yf_rev)
                series = yoy_series(revenue, 4)
                growth_quarters = [round(g * 100, 1) for _, g in series]
                growth_quarter = series[0][0].isoformat() if series else None
        except Exception as e:
            log.debug(f"  {ticker} quarterly error: {e}")

        return {
            "ticker":         ticker,
            "price":          round(float(price), 2) if price is not None else None,
            "price_history":  price_history,
            "market_cap":     market_cap,
            "gross_margin":   round(gross_margin * 100, 1) if gross_margin else None,
            "analyst_count":  analyst_count,
            "fwd_pe":         round(fwd_pe, 1) if fwd_pe else None,
            "ret_1mo":        ret_1mo,
            "ret_3mo":        ret_3mo,
            "growth_quarters": growth_quarters,
            "growth_quarter":  growth_quarter,
            "revenue_source":  revenue_source,
            "ok":             True,
        }
    except Exception as e:
        log.warning(f"  {ticker}: fetch failed — {e}")
        return {"ticker": ticker, "ok": False, "error": str(e)}


ACCEL_BAND_PP = 5.0   # a quarter "accelerates" when YoY rose more than 5 pp (the dashboard's band)


def compute_acceleration_score(growth_quarters: list) -> dict:
    """
    Multi-quarter acceleration. growth_quarters: YoY growth % for consecutive
    quarters, newest first (fetch_multi_quarter_data). Needs at least two —
    i.e. 6 quarters of revenue — or acceleration is not measured, and the
    company isn't ranked (Ray, 2026-10-24).
    """
    gq = [g for g in (growth_quarters or []) if g is not None]
    latest = gq[0] if gq else None
    if len(gq) < 2:
        return {"measured": False, "consecutive_accel": None, "compared": 0, "trend_delta": None,
                "latest_growth": latest, "accel_score": None, "all_quarters": gq}
    deltas = [gq[i] - gq[i + 1] for i in range(len(gq) - 1)]
    consecutive = 0
    for d in deltas:
        if d > ACCEL_BAND_PP:
            consecutive += 1
        else:
            break
    trend_delta = round(sum(deltas) / len(deltas), 1)
    growth_pts = min(40, (latest / 100) * 40) if latest > 0 else 0
    consec_pts = min(30, consecutive * 10)
    delta_pts  = min(30, max(0, trend_delta * 1.5))
    return {
        "measured":          True,
        "consecutive_accel": consecutive,
        "compared":          len(deltas),
        "trend_delta":       trend_delta,
        "latest_growth":     latest,
        "accel_score":       round(growth_pts + consec_pts + delta_pts, 1),
        "all_quarters":      gq,
    }


def compute_nvidia_score(data: dict, accel: dict) -> float:
    """
    Final composite Nvidia-likeness score (0-100).
    """
    if not accel.get("measured"):
        return None
    accel_score = accel.get("accel_score", 0)
    latest_growth = accel.get("latest_growth") or 0
    consecutive = accel.get("consecutive_accel", 0)

    confirmation_bonus = min(15, consecutive * 5)
    accel_score = min(100, accel_score + confirmation_bonus)

    quarters_available = len(accel.get("all_quarters", []))
    if quarters_available == 1:
        accel_score *= 0.8

    gm = data.get("gross_margin")
    margin_score = min(100, (gm / 80) * 100) if gm and gm > 0 else 0

    ac = data.get("analyst_count", 20)
    discovery_score = max(0, min(100, (20 - ac) * 5))

    ret = data.get("ret_1mo") or 0
    momentum_score = 0
    if latest_growth > 20 and ret > 0:
        momentum_score = min(100, ret * 2)

    composite = (
        accel_score     * 0.50 +
        margin_score    * 0.20 +
        discovery_score * 0.15 +
        momentum_score  * 0.15
    )
    return round(composite, 1)


def run_radar(verbose: bool = True) -> list:
    """
    Run the full Next Nvidia Radar.
    Returns top 5 companies ranked by Nvidia-likeness score.
    """
    if verbose:
        print(f"\n🔬 Running Next Nvidia Radar ({len(RADAR_UNIVERSE)} companies)...")

    results = []
    for entry in RADAR_UNIVERSE:
        ticker = entry["ticker"]
        if verbose:
            print(f"  Scoring {ticker}...")
        data = fetch_multi_quarter_data(ticker)
        if not data.get("ok"):
            continue

        accel = compute_acceleration_score(data["growth_quarters"])
        score = compute_nvidia_score(data, accel)

        # Build sparkline from price history
        ph = data.get("price_history", [])
        if ph and len(ph) >= 5:
            sparkline = [round(float(p), 2) for p in ph[-30:]]
        else:
            sparkline = []

        results.append({
            "ticker":        ticker,
            "name":          entry["name"],
            "layer":         entry["layer"],
            "score":         score,
            "price":         data["price"],
            "market_cap":    data["market_cap"],
            "ret_1mo":       data["ret_1mo"],
            "ret_3mo":       data["ret_3mo"],
            "gross_margin":  data["gross_margin"],
            "analyst_count": data["analyst_count"],
            "fwd_pe":        data["fwd_pe"],
            "accel":         accel,
            "growth_quarters": data["growth_quarters"],
            "growth_quarter":  data["growth_quarter"],
            "revenue_source":  data["revenue_source"],
            "sparkline":     sparkline,   # ← always preserved for every ticker
        })

    # Rank only measured acceleration (Ray, 2026-10-24); weak candidates out.
    measured = sorted([r for r in results if r["score"] is not None
                       and (r["accel"].get("latest_growth") or 0) >= 10
                       and (r.get("gross_margin") or 0) > 0],
                      key=lambda x: x["score"], reverse=True)
    # Unmeasured (fewer than 6 quarters) follow, marked, so render can name
    # high growers instead of dropping them silently.
    unmeasured = sorted([r for r in results if not r["accel"].get("measured")
                         and r["accel"].get("latest_growth") is not None],
                        key=lambda x: -x["accel"]["latest_growth"])
    ranked = measured + unmeasured

    # Save cache — store FULL ranked list, not just top5,
    # so render.py can find wildcard candidates (e.g. APP) with sparklines intact
    Path(RADAR_CACHE_FILE).write_text(json.dumps({
        "date":  datetime.datetime.now().strftime("%Y-%m-%d"),
        "time":  datetime.datetime.now().strftime("%H:%M"),
        "top5":  ranked[:5],
        "all":   ranked,          # ← full list with sparklines for every ticker
    }, indent=2, default=str))

    if verbose:
        print(f"\n🏆 Top 5 Next Nvidia candidates:")
        for i, r in enumerate(measured[:5]):
            a = r["accel"]
            print(f"  #{i+1} {r['ticker']:<6} [{r['layer']:<8}] score={r['score']:.0f} "
                  f"revenue={a['latest_growth']:.0f}% accelerating {a['consecutive_accel']} of {a['compared']} quarters")

    # The full list, as load_cached_radar returns: render needs the wildcard
    # candidates and the unmeasured high growers, not just the top 5.
    return ranked


def load_cached_radar() -> list:
    """Load today's cached radar results if available.

    Returns the FULL ranked list (not just top5) so that wildcard candidates
    sitting outside the top 5 (e.g. APP) still carry their sparkline data
    through to render.py.
    """
    p = Path(RADAR_CACHE_FILE)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text())
        if data.get("date") == datetime.datetime.now().strftime("%Y-%m-%d"):
            # Prefer full list; fall back to top5 for older cache files
            return data.get("all", data.get("top5", []))
    except Exception:
        pass
    return []


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    top5 = run_radar(verbose=True)
    print(json.dumps(top5, indent=2, default=str))
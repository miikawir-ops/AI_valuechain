"""
next_nvidia.py — Next Nvidia Radar: multi-quarter acceleration scoring.
 
Combines the 25 tracked dashboard tickers + 33 screener universe companies.
Scores each on Nvidia-like trajectory signals:
  1. Multi-quarter revenue acceleration (3-4 quarters, not just 2)
  2. Acceleration confidence (how many consecutive quarters accelerating)
  3. Gross margin expansion (pricing power forming)
  4. Analyst coverage (low = earlier opportunity)
  5. Price momentum confirmation
 
Returns top 5 companies with the strongest sustained Nvidia-like trajectory.
"""
 
import json
import logging
import datetime
from pathlib import Path
import yfinance as yf
 
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
 
        # Price data
        price = (info.get("currentPrice") or
                 info.get("regularMarketPrice") or
                 info.get("previousClose", 0))
 
        hist = t.history(period="6mo")
        closes = hist["Close"].tolist() if not hist.empty else []
        ret_1mo = round((closes[-1]/closes[-21]-1)*100, 1) if len(closes) >= 21 else 0
        ret_3mo = round((closes[-1]/closes[-63]-1)*100, 1) if len(closes) >= 63 else 0
 
        # Fundamentals
        market_cap    = info.get("marketCap", 0)
        gross_margin  = info.get("grossMargins")
        analyst_count = info.get("numberOfAnalystOpinions") or 0
        fwd_pe        = info.get("forwardPE")
 
        # Multi-quarter revenue growth
        # Strategy: try quarterly_income_stmt first (8 quarters),
        # fall back to quarterly_financials (4 quarters),
        # then use QoQ sequential comparison if YoY unavailable
        growth_quarters = []
        try:
            # Try income_stmt first — more history
            rev_cols = None
            for attr in ["quarterly_income_stmt", "quarterly_financials"]:
                try:
                    fin = getattr(t, attr)
                    if fin is not None and not fin.empty:
                        for label in ["Total Revenue", "Revenue"]:
                            if label in fin.index:
                                rev_cols = fin.loc[label].dropna()
                                break
                    if rev_cols is not None and len(rev_cols) >= 2:
                        break
                except Exception:
                    continue
 
            if rev_cols is not None and len(rev_cols) >= 2:
                import pandas as pd
                n = len(rev_cols)
 
                # Date-based YoY matching — find quarter from ~1 year ago
                # More reliable than iloc[i+4] which assumes no gaps
                matched = 0
                for i in range(min(3, n)):
                    curr_date = rev_cols.index[i]
                    curr_val  = rev_cols.iloc[i]
                    # Look for a quarter ~335-395 days ago
                    target_days_min = 335
                    target_days_max = 395
                    best_match = None
                    best_diff  = 9999
                    for j in range(i+1, n):
                        prev_date = rev_cols.index[j]
                        days_diff = (curr_date - prev_date).days
                        if target_days_min <= days_diff <= target_days_max:
                            diff = abs(days_diff - 365)
                            if diff < best_diff:
                                best_diff  = diff
                                best_match = j
                    if best_match is not None:
                        prev_val = rev_cols.iloc[best_match]
                        if prev_val and prev_val != 0:
                            growth = round((curr_val - prev_val) / abs(prev_val) * 100, 1)
                            growth_quarters.append(growth)
                            matched += 1
 
                # If no YoY matches found, use QoQ annualised
                if not growth_quarters and n >= 2:
                    for i in range(min(3, n - 1)):
                        q_curr = rev_cols.iloc[i]
                        q_prev = rev_cols.iloc[i + 1]
                        if q_prev and q_prev != 0 and q_prev > 0:
                            qoq = (q_curr / q_prev) ** 4 - 1
                            growth_quarters.append(round(qoq * 100, 1))
 
        except Exception as e:
            log.debug(f"  {ticker} quarterly error: {e}")
 
        # Final fallback: TTM growth from info
        if not growth_quarters:
            ttm = info.get("revenueGrowth")
            if ttm:
                growth_quarters = [round(ttm * 100, 1)]
 
        return {
            "ticker":         ticker,
            "price":          round(float(price), 2),
            "market_cap":     market_cap,
            "gross_margin":   round(gross_margin * 100, 1) if gross_margin else None,
            "analyst_count":  analyst_count,
            "fwd_pe":         round(fwd_pe, 1) if fwd_pe else None,
            "ret_1mo":        ret_1mo,
            "ret_3mo":        ret_3mo,
            "growth_quarters": growth_quarters,  # [Q1_most_recent, Q2, Q3, Q4]
            "ok":             True,
        }
    except Exception as e:
        log.warning(f"  {ticker}: fetch failed — {e}")
        return {"ticker": ticker, "ok": False, "error": str(e)}
 
 
def compute_acceleration_score(growth_quarters: list) -> dict:
    """
    Multi-quarter acceleration scoring.
 
    growth_quarters: [Q1_recent, Q2, Q3, Q4] — YoY growth % each quarter
 
    Signals:
      - consecutive_accel: how many consecutive quarters of acceleration (Q1>Q2>Q3)
      - trend_delta: average quarter-over-quarter change in growth rate
      - latest_growth: most recent quarter growth
      - confidence: HIGH / MEDIUM / LOW based on consistency
    """
    if not growth_quarters:
        return {
            "consecutive_accel": 0,
            "trend_delta":       0,
            "latest_growth":     None,
            "confidence":        "LOW",
            "accel_score":       0,
        }
 
    latest = growth_quarters[0]
 
    # Count consecutive quarters of acceleration (most recent first)
    consecutive = 0
    for i in range(len(growth_quarters) - 1):
        if growth_quarters[i] > growth_quarters[i+1]:
            consecutive += 1
        else:
            break
 
    # Average quarter-over-quarter delta in growth rate
    deltas = []
    for i in range(len(growth_quarters) - 1):
        deltas.append(growth_quarters[i] - growth_quarters[i+1])
    trend_delta = round(sum(deltas) / len(deltas), 1) if deltas else 0
 
    # Confidence level
    if consecutive >= 3:
        confidence = "HIGH"
    elif consecutive >= 2:
        confidence = "MEDIUM"
    elif consecutive >= 1 and trend_delta > 0:
        confidence = "MEDIUM"
    else:
        confidence = "LOW"
 
    # Acceleration score (0-100)
    # Components:
    # - Latest growth level (0-40 pts): 100% growth = 40 pts
    # - Consecutive acceleration (0-30 pts): 3+ quarters = 30 pts
    # - Trend delta (0-30 pts): avg +20pp per quarter = 30 pts
    growth_pts = min(40, (latest / 100) * 40) if latest and latest > 0 else 0
    consec_pts = min(30, consecutive * 10)
    delta_pts  = min(30, max(0, trend_delta * 1.5))
    accel_score = round(growth_pts + consec_pts + delta_pts, 1)
 
    return {
        "consecutive_accel": consecutive,
        "trend_delta":       trend_delta,
        "latest_growth":     latest,
        "confidence":        confidence,
        "accel_score":       accel_score,
        "all_quarters":      growth_quarters,
    }
 
 
def compute_nvidia_score(data: dict, accel: dict) -> float:
    """
    Final composite Nvidia-likeness score (0-100).
 
    Signals:
      - Acceleration score (50%): revenue growth + multi-quarter confirmation
      - Margin signal (20%): gross margin expansion = pricing power forming
      - Discovery signal (15%): low analyst coverage = earlier in cycle
      - Momentum (15%): price momentum confirming fundamentals
    """
    accel_score = accel.get("accel_score", 0)
    latest_growth = accel.get("latest_growth") or 0
    consecutive = accel.get("consecutive_accel", 0)
 
    # Boost for multi-quarter confirmation
    # Each consecutive quarter of acceleration adds 5 bonus points
    confirmation_bonus = min(15, consecutive * 5)
    accel_score = min(100, accel_score + confirmation_bonus)
 
    # Penalty for single-quarter data (less reliable)
    quarters_available = len(accel.get("all_quarters", []))
    if quarters_available == 1:
        accel_score *= 0.8  # 20% penalty for single quarter only
 
    # Margin signal — higher gross margin = stronger pricing power
    gm = data.get("gross_margin")
    margin_score = min(100, (gm / 80) * 100) if gm and gm > 0 else 0
 
    # Discovery signal — fewer analysts = earlier opportunity
    ac = data.get("analyst_count", 20)
    discovery_score = max(0, min(100, (20 - ac) * 5))
 
    # Momentum signal — price confirms fundamentals
    ret = data.get("ret_1mo", 0)
    # Only reward momentum if fundamentals are strong (growth > 20%)
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
 
        results.append({
            "ticker":      ticker,
            "name":        entry["name"],
            "layer":       entry["layer"],
            "score":       score,
            "price":       data["price"],
            "market_cap":  data["market_cap"],
            "ret_1mo":     data["ret_1mo"],
            "ret_3mo":     data["ret_3mo"],
            "gross_margin":data["gross_margin"],
            "analyst_count":data["analyst_count"],
            "fwd_pe":      data["fwd_pe"],
            "accel":       accel,
            "growth_quarters": data["growth_quarters"],
        })
 
    # Filter out weak candidates:
    # - Negative revenue growth
    # - Less than 10% revenue growth (too slow to be "next Nvidia")
    # - No gross margin data AND no growth (data quality issue)
    results = [r for r in results
               if (r["accel"].get("latest_growth") or 0) >= 10
               and (r.get("gross_margin") or 0) > 0]
 
    # Rank by score
    ranked = sorted(results, key=lambda x: x["score"], reverse=True)
 
    # Save cache
    Path(RADAR_CACHE_FILE).write_text(json.dumps({
        "date":    datetime.datetime.now().strftime("%Y-%m-%d"),
        "time":    datetime.datetime.now().strftime("%H:%M"),
        "top5":    ranked[:5],
        "all":     ranked,
    }, indent=2, default=str))
 
    if verbose:
        print(f"\n🏆 Top 5 Next Nvidia candidates:")
        for i, r in enumerate(ranked[:5]):
            conf = r["accel"].get("confidence", "?")
            consec = r["accel"].get("consecutive_accel", 0)
            latest = r["accel"].get("latest_growth")
            print(f"  #{i+1} {r['ticker']:<6} [{r['layer']:<8}] "
                  f"score={r['score']:.0f} "
                  f"revenue={latest:.0f}% " if latest else f"  #{i+1} {r['ticker']:<6} score={r['score']:.0f} "
                  f"accel={consec}Q {conf} confidence")
 
    return ranked[:5]
 
 
def load_cached_radar() -> list:
    """Load today's cached radar results if available."""
    p = Path(RADAR_CACHE_FILE)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text())
        if data.get("date") == datetime.datetime.now().strftime("%Y-%m-%d"):
            return data.get("top5", [])
    except Exception:
        pass
    return []
 
 
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    top5 = run_radar(verbose=True)
    print(json.dumps(top5, indent=2, default=str))
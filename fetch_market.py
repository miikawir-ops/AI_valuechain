"""
fetch_market.py — Market data pipeline for the AI Investment Agent.
 
Fetches all data needed by score_engine.py from free sources:
  - yfinance  : prices, revenue growth, gross margins, volume, short interest
  - feedparser: news headlines for keyword scoring
 
Fix v4 — news cross-contamination fix:
  - Ticker prefix REMOVED from headline text (was causing every layer to match
    every other layer's headlines — all layers scored 10.0 artificially)
  - Ticker stored separately in headline dict, NOT prepended to text
  - score_news_velocity() now filters ticker-specific headlines by layer ownership:
    only headlines from THIS layer's tickers count for this layer's score
  - Generic headlines still matched by keywords across all layers
  - Result: realistic news scores 0-6 per layer instead of universal 10.0
  - gross_margin (absolute level) retained from v3
 
Returns two objects:
  run_pipeline() -> dict of {layer_id: [list of ticker dicts]}
  fetch_macro()  -> dict with vix, yield_10y_change, nasdaq_vs_spx_20d
"""
 
import json
import math
import logging
import datetime
import argparse
import statistics
import feedparser
import pandas as pd
import yfinance as yf
from pathlib import Path
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

# ── Session dating (ported from RayDar-DataCenter, ledger R33b/R40/R41) ───────
# Every daily-bar fetch drops a bar dated today (US/Eastern) until the
# session has closed, so a run during US trading hours scores the last
# completed session, not a partial one. Early-close days (13:00 ET) are
# treated as closing at 16:00: a run between 13:00 and 16:00 on those days
# uses the previous session, which is conservative rather than wrong.
US_EASTERN           = ZoneInfo("America/New_York")
SESSION_CLOSE_ET     = datetime.time(16, 0)
SESSION_REFERENCE    = "^GSPC"   # trading calendar: one daily bar per US session
MAX_SESSION_AGE_DAYS = 7


def drop_unfinished_bar(hist, now: datetime.datetime = None, label: str = ""):
    """Return the daily bars without trailing bars that aren't a completed
    session: today's bar while today's US session hasn't closed, and any
    trailing row whose Close is NaN, whatever its date (the 00:00 UTC
    empty bar that put NaN on every ticker of the live page). `now` is for
    tests; default is the current time."""
    if hist is None or hist.empty:
        return hist
    now_et = (now or datetime.datetime.now(datetime.timezone.utc)).astimezone(US_EASTERN)
    while not hist.empty:
        unfinished = hist.index[-1].date() == now_et.date() and now_et.time() < SESSION_CLOSE_ET
        if unfinished or pd.isna(hist["Close"].iloc[-1]):
            log.info(f"  {label or 'bars'}: dropped {'unfinished' if unfinished else 'empty (NaN close)'} "
                     f"bar dated {hist.index[-1].date()}")
            hist = hist.iloc[:-1]
        else:
            break
    return hist


def fetch_trading_sessions(now: datetime.datetime = None) -> list:
    """Completed US trading sessions (datetime.date, oldest first) from
    ^GSPC's daily bars. sessions[-1] is the session the run's data is "as
    of"; colour confirmation takes its expected sessions from here, so a
    market holiday is never reported as a missing day.

    Raises instead of returning an empty or stale calendar — every
    date-keyed output depends on it, so the run fails loudly rather than
    silently falling back to the UTC run date."""
    try:
        bars = yf.Ticker(SESSION_REFERENCE).history(period="1mo")
        hist = drop_unfinished_bar(bars, now, SESSION_REFERENCE)
    except Exception as e:
        raise RuntimeError(f"Trading-session calendar unavailable ({SESSION_REFERENCE} daily bars): {e}") from e
    if hist is None or hist.empty:
        raise RuntimeError(f"Trading-session calendar unavailable: {SESSION_REFERENCE} returned no completed daily bars")
    sessions = [ts.date() for ts in hist.index]
    today_et = (now or datetime.datetime.now(datetime.timezone.utc)).astimezone(US_EASTERN).date()
    if (today_et - sessions[-1]).days > MAX_SESSION_AGE_DAYS:
        raise RuntimeError(f"Trading-session calendar is stale: latest {SESSION_REFERENCE} session is "
                           f"{sessions[-1]}, more than {MAX_SESSION_AGE_DAYS} days before {today_et}")
    return sessions
 
AI_CHAIN_LAYERS = {
    "energy": {
        "name": "Energy & Power Infrastructure",
        "tickers": ["CEG", "VST", "PWR", "GEV", "ETN"],
        "keywords": [
            "nuclear", "power", "grid", "electricity", "energy", "ppa",
            "megawatt", "gigawatt", "utility", "transformer", "switchgear",
            "power equipment", "power delivery", "uranium", "reactor",
            "data center power", "AI power", "power demand", "power infrastructure",
            "hyperscaler power", "power contract", "power purchase agreement",
            "electricity demand", "grid expansion", "energy crisis",
            "data center energy", "power capacity", "load growth",
            "nuclear power", "clean energy", "carbon free", "energy storage",
            "SMR", "small modular reactor", "nuclear renaissance",
            "nuclear plant", "power grid", "renewable", "blackout",
            "capacity expansion", "watts", "kilowatt", "terawatt",
        ]
    },
    "compute": {
        "name": "Semiconductors & Chip Design",
        "tickers": ["NVDA", "AMD", "AVGO", "ASML", "TSM", "ARM", "CDNS"],
        "keywords": [
            "gpu", "chip", "semiconductor", "H100", "H200", "B200", "B100",
            "blackwell", "hopper", "GB200", "NVL72", "MI300", "MI300X",
            "AI chip", "AI accelerator", "accelerator", "custom silicon", "ASIC",
            "wafer", "foundry", "fab", "CoWoS", "advanced packaging",
            "chip shortage", "chip supply", "leading edge", "3nm", "2nm",
            "EUV", "lithography", "TSMC capacity",
            "chip design", "EDA", "IP licensing", "CUDA", "silicon design",
            "ARM architecture", "chip IP", "neural processing",
            "compute", "inference", "training", "processor", "graphics card",
        ]
    },
    "memory": {
        "name": "HBM Memory & Storage",
        "tickers": ["MU", "WDC", "AMAT", "LRCX"],
        "keywords": [
            "HBM", "HBM3", "HBM3e", "HBM4", "high bandwidth memory",
            "memory bandwidth", "memory stack", "memory bottleneck",
            "DRAM", "NAND", "flash memory", "memory chip",
            "DRAM price", "memory market", "memory supply", "memory demand",
            "AI memory", "memory capacity", "memory shortage", "HBM supply",
            "memory revenue", "memory growth", "data storage",
            "etch equipment", "deposition", "memory fab", "memory production",
        ]
    },
    "infra": {
        "name": "Data Center & Networking",
        "tickers": ["VRT", "ANET", "EQIX", "SMCI", "CSCO", "CIEN"],
        "keywords": [
            "liquid cooling", "direct liquid cooling", "DLC", "cooling",
            "thermal management", "power density", "heat dissipation",
            "data center", "data centre", "colocation", "hyperscale",
            "AI infrastructure", "AI factory", "GPU cluster", "AI server",
            "server rack", "rack density", "compute infrastructure",
            "networking", "ethernet", "InfiniBand", "interconnect",
            "400G", "800G", "optical networking", "optical transceiver",
            "fiber", "wavelength", "switching", "network bandwidth",
            "data center networking", "Ultra Ethernet",
        ]
    },
    "cloud": {
        "name": "Cloud & Hyperscalers",
        "tickers": ["MSFT", "GOOGL", "AMZN", "META"],
        "keywords": [
            "azure", "AWS", "google cloud", "amazon web services",
            "cloud revenue", "cloud growth", "cloud spending",
            "copilot", "gemini", "bedrock", "llama", "openai",
            "foundation model", "large language model", "LLM",
            "AI assistant", "AI product", "generative AI",
            "capex", "hyperscaler", "AI investment", "data center spending",
            "AI spending", "cloud capex", "infrastructure investment",
            "cloud margin", "AI revenue", "AI monetization",
            "subscription growth", "enterprise AI", "AI adoption",
        ]
    },
    "software": {
        "name": "AI Software & Observability",
        "tickers": ["PLTR", "NOW", "SNOW", "CRM", "DDOG"],
        "keywords": [
            "AI software", "AI platform", "AI agent", "agentic",
            "enterprise AI", "AI workflow", "AI deployment", "AI operations",
            "generative AI", "AI application", "AI tool", "AI adoption",
            "SaaS", "observability", "monitoring", "AI monitoring",
            "model ops", "ML ops", "LLMops", "AI observability",
            "automation", "workflow automation", "AI automation",
            "remaining performance obligation", "RPO", "net revenue retention",
            "NRR", "annual recurring revenue", "ARR", "SaaS revenue",
        ]
    },
    "security": {
        "name": "AI Security & Governance",
        "tickers": ["CRWD", "PANW", "S", "OKTA"],
        "keywords": [
            "breach", "cyberattack", "ransomware", "zero day", "CVE",
            "nation state", "CISA", "vulnerability", "incident response",
            "data breach", "hack", "malware", "threat actor",
            "AI security", "cybersecurity", "model security", "AI governance",
            "AI threat", "AI compliance", "AI risk", "model protection",
            "zero trust", "threat detection", "endpoint security",
            "SIEM", "SOC", "security platform", "security AI",
            "data protection", "data privacy", "identity security",
            "platformization", "security spending", "cyber spending",
        ]
    },
}
 
GENERIC_FEEDS = [
    "https://feeds.reuters.com/reuters/businessNews",
    "https://finance.yahoo.com/news/rssindex",
    "https://feeds.content.dowjones.io/public/rss/mw_realtimeheadlines",
    "https://www.cnbc.com/id/20910258/device/rss/rss.html",
]
 
ALL_TICKERS = [
    t for layer in AI_CHAIN_LAYERS.values()
    for t in layer["tickers"]
]
 
# Reverse lookup: ticker → layer_id
TICKER_TO_LAYER = {
    ticker: layer_id
    for layer_id, layer in AI_CHAIN_LAYERS.items()
    for ticker in layer["tickers"]
}
 
 
def fetch_ticker_headlines(tickers: list, max_per_ticker: int = 10) -> list:
    """
    Fetch Yahoo Finance RSS for each specific ticker.
 
    v4 fix: ticker symbol is stored separately and NOT prepended to headline text.
    Previously: f"{ticker} {title}".lower() caused cross-layer contamination —
    memory layer keywords matched energy ticker headlines because "CEG" appeared
    as text prefix on every energy headline, and energy keywords like "power"
    matched memory headlines via ticker context.
 
    Now: headlines are cleanly separated by ticker ownership.
    score_news_velocity() filters by layer_tickers to prevent cross-contamination.
    """
    headlines = []
    for ticker in tickers:
        url = f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={ticker}&region=US&lang=en-US"
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:max_per_ticker]:
                title   = entry.get("title", "")
                summary = entry.get("summary", "")[:200]
                if title:
                    headlines.append({
                        "text":   f"{title} {summary}".lower(),  # NO ticker prefix
                        "ticker": ticker.upper(),                  # stored separately
                        "source": "ticker",
                    })
        except Exception as e:
            log.debug(f"  Ticker feed failed ({ticker}): {e}")
    return headlines
 
 
def fetch_generic_headlines(max_per_feed: int = 20) -> list:
    """Fetch generic business news as supplementary signal."""
    headlines = []
    for url in GENERIC_FEEDS:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:max_per_feed]:
                title   = entry.get("title", "")
                summary = entry.get("summary", "")[:200]
                if title:
                    headlines.append({
                        "text":   f"{title} {summary}".lower(),
                        "ticker": "",      # no ticker — generic source
                        "source": "generic",
                    })
        except Exception as e:
            log.warning(f"Generic feed failed ({url}): {e}")
    return headlines
 
 
def fetch_all_headlines() -> list:
    """Fetch ticker-specific (primary) + generic (supplementary) headlines."""
    log.info("  Fetching ticker-specific headlines...")
    ticker_headlines = fetch_ticker_headlines(ALL_TICKERS, max_per_ticker=10)
    log.info(f"  Got {len(ticker_headlines)} ticker-specific headlines")
 
    log.info("  Fetching generic news headlines...")
    generic_headlines = fetch_generic_headlines(max_per_feed=20)
    log.info(f"  Got {len(generic_headlines)} generic headlines")
 
    return ticker_headlines + generic_headlines
 
 
def score_news_velocity(
    headlines: list,
    keywords: list,
    layer_tickers: list = None,
) -> float:
    """
    Score news velocity with layer-ownership filtering.
 
    v4 fix: ticker-specific headlines are ONLY counted for the layer
    that owns that ticker. This prevents cross-contamination where
    Energy ticker (CEG) headlines were being counted for Memory layer.
 
    Algorithm:
      - Ticker-specific headline from THIS layer's ticker + keyword match → 2.0 pts
      - Generic headline + keyword match → 1.0 pts
      - Ticker-specific headline from ANOTHER layer's ticker → SKIPPED entirely
 
    Cap: 20 weighted points → scaled to 0-10 output range.
 
    Expected realistic scores:
      Active bottleneck layer (e.g. memory with MU news): 4-8
      Quiet layer (e.g. security on calm day): 0-3
      Breaking news day (e.g. major breach for security): 7-10
    """
    kw_lower = [kw.lower() for kw in keywords]
    # Normalise layer tickers to uppercase for comparison
    layer_tickers_upper = set(t.upper() for t in (layer_tickers or []))
 
    weighted_hits = 0.0
    for h in headlines:
        if isinstance(h, dict):
            text      = h.get("text", "")
            source    = h.get("source", "generic")
            h_ticker  = h.get("ticker", "").upper()
        else:
            # Legacy string format fallback
            text     = h.lower()
            source   = "generic"
            h_ticker = ""
 
        # ── Layer ownership filter ────────────────────────────────────────────
        # Ticker-specific headlines only count for the layer that owns the ticker
        if source == "ticker" and layer_tickers_upper:
            if h_ticker not in layer_tickers_upper:
                continue   # skip — belongs to a different layer
 
        weight = 2.0 if source == "ticker" else 1.0
 
        if any(kw in text for kw in kw_lower):
            weighted_hits += weight
 
    # Cap at 20 weighted points, scale to 0-10 output
    return round(min(weighted_hits, 20.0) / 2.0, 1)


# Buy-side grades counted by the analyst signal, among actions within the
# last ANALYST_WINDOW_DAYS up to the ticker's own last session (ported from
# RayDar-DataCenter, ledger R45 item 1 / R49).
BUY_GRADES = ("Buy", "Strong Buy", "Overweight", "Outperform", "Positive")
ANALYST_WINDOW_DAYS = 30


def fetch_ticker_data(ticker: str, headlines: list, layer_keywords: list,
                      layer_tickers: list = None):
    try:
        t    = yf.Ticker(ticker)
        info = t.info
 
        price = (info.get("currentPrice")
                 or info.get("regularMarketPrice")
                 or info.get("previousClose", 0))
        prev        = info.get("previousClose") or price
        week52_high = info.get("fiftyTwoWeekHigh", 0) or 0
        week52_low  = info.get("fiftyTwoWeekLow",  0) or 0
        market_cap  = info.get("marketCap", 0) or 0
        price_act   = round((price - prev) / prev, 4) if prev else 0.0
        gross_margin = info.get("grossMargins")
 
        data_missing: list = []

        hist = drop_unfinished_bar(t.history(period="6mo"), label=ticker)

        closes  = hist["Close"].tolist() if not hist.empty else []
        if closes:
            step = max(1, len(closes) // 30)
            price_history = [round(closes[i], 2) for i in range(0, len(closes), step)][-30:]
        else:
            price_history = []
        volumes = hist["Volume"].tolist() if not hist.empty else []
 
        price_30d = round((closes[-1] / closes[-21] - 1), 4) if len(closes) >= 21 else 0.0
        if isinstance(price_30d, float) and math.isnan(price_30d):
            data_missing.append("price_30d_return")
            price_30d = None

        vol_avg   = sum(volumes[-20:]) / 20 if len(volumes) >= 20 else None
        vol_spike = round(volumes[-1] / vol_avg, 2) if vol_avg and volumes else 1.0
 
        # Momentum: the last 5 sessions' return against the average (compound)
        # 5-session pace of the last 63 sessions (~90 days); 1.0 means exactly
        # on pace. The excess over the pace is measured in units of the pace
        # itself when the trend is clear, or of the typical 5-session move
        # when the trend is weaker than that noise (ported from
        # RayDar-DataCenter, ledger R45 item 2). This replaces a ratio
        # (ret_5d / avg_5d_from_90d) that had two bugs: two negative returns
        # divided to a positive ratio (wrong sign — declining both short- and
        # long-term read as bullish), and a near-zero 90-day return drove the
        # denominator toward a 0.001 floor, blowing the ratio up to the clamp
        # ceiling regardless of the actual 5-day move. Also fixes an off-by-
        # one in the windows: the old code's closes[-5]/closes[-63] spanned 4
        # and 62 sessions respectively, not 5 and 90/18-ish.
        window = closes[-69:]
        if len(window) == 69 and all(math.isfinite(c) for c in window):
            ret_5d = closes[-1] / closes[-6] - 1
            pace   = (closes[-1] / closes[-64]) ** (5 / 63) - 1
            moves  = [closes[i] / closes[i - 5] - 1 for i in range(len(closes) - 63, len(closes))]
            scale  = max(abs(pace), statistics.pstdev(moves))
            price_momentum = round(1 + (ret_5d - pace) / scale, 2) if scale > 0 else 1.0
            price_momentum = max(-5.0, min(5.0, price_momentum))
        else:
            price_momentum = None
            data_missing.append("price_momentum")
 
        growth_curr       = None
        growth_prev       = None
        gm_delta          = 0.0
        revenue_quarterly = 0
        financials        = None
 
        try:
            financials = t.quarterly_financials
            if financials is not None and not financials.empty:
                rev_row = None
                for label in ["Total Revenue", "Revenue"]:
                    if label in financials.index:
                        rev_row = financials.loc[label]
                        break
                if rev_row is not None:
                    cols = rev_row.dropna()
                    if len(cols) >= 4:
                        q0, q1, q4, q5 = cols.iloc[0], cols.iloc[1], cols.iloc[2], cols.iloc[3]
                        if q4 and q4 != 0:
                            growth_curr = round((q0 - q4) / abs(q4), 4)
                        if q5 and q5 != 0:
                            growth_prev = round((q1 - q5) / abs(q5), 4)
 
                gm_row   = None
                rev_row2 = None
                for label in ["Gross Profit", "GrossProfit"]:
                    if label in financials.index:
                        gm_row = financials.loc[label]
                        break
                for label in ["Total Revenue", "Revenue"]:
                    if label in financials.index:
                        rev_row2 = financials.loc[label]
                        break
                if gm_row is not None and rev_row2 is not None:
                    gm_cols  = gm_row.dropna()
                    rev_cols = rev_row2.dropna()
                    if len(gm_cols) >= 2 and len(rev_cols) >= 2:
                        gm_c = gm_cols.iloc[0] / rev_cols.iloc[0] if rev_cols.iloc[0] else 0
                        gm_p = gm_cols.iloc[1] / rev_cols.iloc[1] if rev_cols.iloc[1] else 0
                        gm_delta = round(gm_c - gm_p, 4)
        except Exception as e:
            log.debug(f"  {ticker} quarterly error: {e}")
 
        if growth_curr is None:
            try:
                if financials is not None and not financials.empty:
                    for label in ["Total Revenue", "Revenue"]:
                        if label in financials.index:
                            rev_row = financials.loc[label]
                            rev_vals = [v for v in rev_row.values
                                        if v is not None and str(v) != "nan" and v == v]
                            if rev_vals:
                                revenue_quarterly = float(rev_vals[0])
                            break
            except Exception:
                revenue_quarterly = 0
            growth_curr = info.get("revenueGrowth")
        if growth_prev is None:
            growth_prev = info.get("earningsGrowth")
 
        # Analyst grades: per-action rows from upgrades_downgrades, not
        # t.recommendations — that's a monthly summary table with no grade
        # column in current yfinance, so "To Grade" never matched anything
        # and this silently scored every company 0 (ported fix, RayDar-
        # DataCenter ledger R45 item 1 / R49). Counted: how many actions in
        # the 30 days up to this ticker's own last session set a buy-side
        # grade, at most the 10 most recent of them (the original limit).
        # No actions in the window is a real 0; a failed fetch or no table
        # at all is missing data (None + data_missing), not a silent 0.
        analyst_upgrades = None
        try:
            ud = t.upgrades_downgrades
            if ud is not None and "ToGrade" in ud.columns and not ud.empty:
                session = hist.index[-1].date() if not hist.empty else datetime.date.today()
                dates   = pd.Index([ts.date() for ts in ud.index])
                inside  = (dates > session - datetime.timedelta(days=ANALYST_WINDOW_DAYS)) & (dates <= session)
                recent  = ud[inside].sort_index().tail(10)
                analyst_upgrades = int(recent["ToGrade"].isin(BUY_GRADES).sum())
        except Exception as e:
            log.debug(f"  {ticker} upgrades_downgrades error: {e}")
        if analyst_upgrades is None:
            data_missing.append("analyst_upgrades")

        short_int_change = 0.0
        try:
            short_info = info.get("shortPercentOfFloat")
            if short_info:
                short_int_change = round((short_info - 0.05) * -1, 4)
        except Exception:
            short_int_change = 0.0
 
        capex_div = 0.0
        try:
            cf = t.quarterly_cashflow
            if cf is not None and not cf.empty:
                capex_row = None
                ocf_row   = None
                for label in ["Capital Expenditure", "CapitalExpenditure"]:
                    if label in cf.index:
                        capex_row = cf.loc[label]
                        break
                for label in ["Operating Cash Flow", "OperatingCashFlow"]:
                    if label in cf.index:
                        ocf_row = cf.loc[label]
                        break
                if capex_row is not None and ocf_row is not None:
                    capex_val = abs(capex_row.dropna().iloc[0]) if not capex_row.dropna().empty else 0
                    ocf_val   = ocf_row.dropna().iloc[0] if not ocf_row.dropna().empty else 1
                    if ocf_val and ocf_val > 0:
                        capex_div = round(min(capex_val / ocf_val, 1.0), 4)
        except Exception as e:
            log.debug(f"  {ticker} capex error: {e}")
 
        # News velocity — layer-filtered to prevent cross-contamination
        # Only this layer's ticker headlines + generic headlines are counted
        news_velocity = score_news_velocity(
            headlines,
            layer_keywords,
            layer_tickers=layer_tickers,   # ← ownership filter
        )
 
        return {
            "ticker":            ticker,
            "name":              info.get("longName") or info.get("shortName", ticker),
            "price":             round(float(price), 2),
            "currency":          info.get("currency", "USD"),
            "growth_curr":       growth_curr,
            "growth_prev":       growth_prev,
            "gm_delta":          gm_delta,
            "gross_margin":      gross_margin,
            "news_velocity":     news_velocity,
            "capex_div":         capex_div,
            "vol_spike":         vol_spike,
            "price_act":         price_act,
            "analyst_upgrades":  analyst_upgrades,
            "short_int_change":  short_int_change,
            "price_30d_return":  price_30d,
            "price_history":     price_history,
            "week52_high":       week52_high,
            "week52_low":        week52_low,
            "market_cap":        info.get("marketCap"),
            "revenue_quarterly": revenue_quarterly,
            "price_momentum":    price_momentum,
            "peer_outperformance": 0.0,
            "analyst_count":     info.get("numberOfAnalystOpinions", 0),
            "sector":            info.get("sector", "N/A"),
            "data_date":         datetime.datetime.now().strftime("%Y-%m-%d"),
            "data_age_note":     "Quarterly financials may be up to 90 days old",
            "data_missing":      data_missing,
        }
 
    except Exception as e:
        log.warning(f"  {ticker}: fetch failed — {e}")
        return None
 
 
def add_peer_outperformance(ticker_list: list) -> list:
    valid = [t for t in ticker_list if t and t.get("price_30d_return") is not None]
    if not valid:
        return ticker_list
    avg_30d = sum(t["price_30d_return"] for t in valid) / len(valid)
    for t in ticker_list:
        if t and t.get("price_30d_return") is not None:
            t["peer_outperformance"] = round(t["price_30d_return"] - avg_30d, 4)
    return ticker_list
 
 
def find_nan_paths(obj, path: str = "") -> list:
    """Recursively locate any float('nan') values in a nested dict/list.

    Final backstop for the NaN-to-live-page bug: individual fields are
    converted to None at the source (see data_missing above), but this
    catches anything that slips through — any remaining NaN anywhere in
    the fetched data means the run should fail rather than publish it.
    """
    paths = []
    if isinstance(obj, float) and math.isnan(obj):
        paths.append(path or "<root>")
    elif isinstance(obj, dict):
        for k, v in obj.items():
            paths.extend(find_nan_paths(v, f"{path}.{k}" if path else str(k)))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            paths.extend(find_nan_paths(v, f"{path}[{i}]"))
    return paths


def assert_no_nan(market_data: dict) -> None:
    """Raise if any NaN remains in fetched data. Call this OUTSIDE any
    try/except that would swallow it — the whole point is for the run to
    fail loudly (non-zero exit, no commit) instead of publishing NaN."""
    paths = find_nan_paths(market_data)
    if paths:
        shown = ", ".join(paths[:10])
        more = f" (+{len(paths) - 10} more)" if len(paths) > 10 else ""
        raise ValueError(f"NaN in fetched market data at: {shown}{more} — refusing to render/publish.")


def fetch_macro() -> dict:
    log.info("  Fetching macro signals...")
    result = {"vix": 20.0, "yield_10y_change": 0.0, "nasdaq_vs_spx_20d": 0.0}
    try:
        # Last completed session's close, not the live quote — the same
        # session every other daily series is scored on (RayDar-DataCenter).
        vix = drop_unfinished_bar(yf.Ticker("^VIX").history(period="5d"), label="^VIX")
        if not vix.empty:
            result["vix"] = round(float(vix["Close"].iloc[-1]), 2)
        tnx = drop_unfinished_bar(yf.Ticker("^TNX").history(period="35d"), label="^TNX")
        if not tnx.empty and len(tnx) >= 21:
            result["yield_10y_change"] = round(
                (tnx["Close"].iloc[-1] - tnx["Close"].iloc[-21]) * 100, 2
            )
        nasdaq = drop_unfinished_bar(yf.Ticker("^IXIC").history(period="25d"), label="^IXIC")
        spx    = drop_unfinished_bar(yf.Ticker("^GSPC").history(period="25d"), label="^GSPC")
        if len(nasdaq) >= 20 and len(spx) >= 20:
            nasdaq_ret = nasdaq["Close"].iloc[-1] / nasdaq["Close"].iloc[-20] - 1
            spx_ret    = spx["Close"].iloc[-1]    / spx["Close"].iloc[-20]    - 1
            result["nasdaq_vs_spx_20d"] = round(nasdaq_ret - spx_ret, 4)
        log.info(f"  Macro: VIX={result['vix']} "
                 f"yield_chg={result['yield_10y_change']}bps "
                 f"nasdaq_rel={result['nasdaq_vs_spx_20d']*100:.1f}%")
    except Exception as e:
        log.warning(f"  Macro fetch error: {e} — using defaults")
    return result
 
 
def run_pipeline(portfolio_file: str = "portfolio.json") -> dict:
    log.info("Fetching all headlines (ticker-specific + generic)...")
    headlines = fetch_all_headlines()
    results   = {}
    for layer_id, layer_config in AI_CHAIN_LAYERS.items():
        log.info(f"  Layer: {layer_config['name']}")
        tickers_data = []
        layer_tickers = layer_config["tickers"]   # pass to filter news correctly
        for ticker in layer_tickers:
            log.info(f"    Fetching {ticker}...")
            data = fetch_ticker_data(
                ticker, headlines,
                layer_config["keywords"],
                layer_tickers=[ticker],             # ← this ticker's own headlines only
            )
            if data:
                tickers_data.append(data)
        tickers_data      = add_peer_outperformance(tickers_data)
        results[layer_id] = tickers_data
        log.info(f"    {len(tickers_data)}/{len(layer_tickers)} tickers OK")
    return results
 
 
if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S"
    )
 
    parser = argparse.ArgumentParser()
    parser.add_argument("--macro",  action="store_true", help="Macro data only")
    parser.add_argument("--layer",  type=str, default=None, help="One layer only")
    parser.add_argument("--news",   action="store_true", help="Test news fetch + scoring")
    args = parser.parse_args()
 
    if args.macro:
        print("\nFetching macro signals...")
        print(json.dumps(fetch_macro(), indent=2))
 
    elif args.news:
        print("\nTesting news fetch and velocity scoring (v4 — layer-filtered)...")
        headlines = fetch_all_headlines()
        ticker_count  = sum(1 for h in headlines if isinstance(h, dict) and h.get("source") == "ticker")
        generic_count = sum(1 for h in headlines if isinstance(h, dict) and h.get("source") == "generic")
        print(f"\nTotal headlines: {len(headlines)}")
        print(f"  Ticker-specific: {ticker_count} (weight ×2, layer-filtered)")
        print(f"  Generic:         {generic_count} (weight ×1, keyword-matched)")
 
        print("\nNews velocity by layer (layer-filtered — no cross-contamination):")
        for layer_id, layer in AI_CHAIN_LAYERS.items():
            score = score_news_velocity(
                headlines,
                layer["keywords"],
                layer_tickers=layer["tickers"],
            )
            bar   = "█" * int(score)
            print(f"  {layer_id:<12} {score:>4.1f}  {bar}")
 
        print("\nSanity check — memory layer with NO filter (old broken behaviour):")
        score_unfiltered = score_news_velocity(headlines, AI_CHAIN_LAYERS["memory"]["keywords"])
        print(f"  memory (unfiltered): {score_unfiltered}  ← was always 10.0")
        score_filtered = score_news_velocity(
            headlines,
            AI_CHAIN_LAYERS["memory"]["keywords"],
            layer_tickers=AI_CHAIN_LAYERS["memory"]["tickers"],
        )
        print(f"  memory (filtered):   {score_filtered}  ← realistic score")
 
        print("\nTop memory headlines (filtered to MU/WDC/AMAT/LRCX only):")
        mem_kw = [kw.lower() for kw in AI_CHAIN_LAYERS["memory"]["keywords"]]
        mem_tickers = set(t.upper() for t in AI_CHAIN_LAYERS["memory"]["tickers"])
        hits = [
            h for h in headlines
            if isinstance(h, dict)
            and (h.get("source") == "generic" or h.get("ticker", "").upper() in mem_tickers)
            and any(kw in h.get("text", "") for kw in mem_kw)
        ]
        for h in hits[:8]:
            src = f"[{h.get('ticker','generic')}]"
            print(f"  {src:<8} {h.get('text','')[:100]}")
 
    elif args.layer:
        if args.layer not in AI_CHAIN_LAYERS:
            print(f"Unknown layer. Choose from: {list(AI_CHAIN_LAYERS.keys())}")
        else:
            print(f"\nFetching {args.layer} layer only...")
            headlines = fetch_all_headlines()
            layer     = AI_CHAIN_LAYERS[args.layer]
            tickers   = []
            for ticker in layer["tickers"]:
                print(f"  Fetching {ticker}...")
                data = fetch_ticker_data(
                    ticker, headlines,
                    layer["keywords"],
                    layer_tickers=[ticker],
                )
                if data:
                    tickers.append(data)
            tickers = add_peer_outperformance(tickers)
            print(json.dumps(tickers, indent=2, default=str))
 
    else:
        print("\nRunning full pipeline...")
        data = run_pipeline()
        for layer_id, tickers in data.items():
            print(f"\n{layer_id.upper()} — {len(tickers)} tickers")
            for t in tickers:
                g_curr = t.get("growth_curr")
                g_prev = t.get("growth_prev")
                delta  = round(g_curr - g_prev, 3) if g_curr and g_prev else "N/A"
                gm     = f"{t.get('gross_margin', 0)*100:.0f}%" if t.get("gross_margin") else "N/A"
                print(f"  {t['ticker']:<6} price=${t['price']:<8} "
                      f"growth={str(g_curr):<8} delta={str(delta):<8} "
                      f"GM={gm:<6} news={t.get('news_velocity')}")
        print(f"\n✅ Pipeline complete — {sum(len(v) for v in data.values())} tickers")
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
import edgar
from pathlib import Path
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

# ── Session dating (ported from RayDar-DataCenter, ledger R33b/R40/R41/R101) ──
# A run's data belongs to the last completed US session, decided once per run
# from the exchange calendar (^GSPC's daily rows: one per session) and the
# clock in US/Eastern, never from the run's own date or from which bars
# happen to be filled: a session is complete once its date is past, or it is
# today and 16:00 ET has passed. Every price series (companies, radar, macro,
# the NASDAQ benchmark, track-record closes) is then cut at that session, and
# a series without a usable bar for it is missing data, never the session
# before under the new date (2026-11-13 01:17 UTC: Yahoo had emptied the
# 11-12 bar for every ticker, the empty bars were dropped, and 11-11 prices
# were published as 11-12; DataCenter ledger R101). Early-close days (13:00
# ET) are treated as closing at 16:00: a run between 13:00 and 16:00 on those
# days uses the previous session, which is conservative rather than wrong.
US_EASTERN           = ZoneInfo("America/New_York")
SESSION_CLOSE_ET     = datetime.time(16, 0)
SESSION_REFERENCE    = "^GSPC"   # trading calendar: one daily bar per US session
MAX_SESSION_AGE_DAYS = 7
# More than this share of companies without a bar for the session means
# Yahoo hasn't got the session's bars, not that a few companies lack them:
# the run fails (no commit, no deploy) instead of publishing without prices.
MAX_NO_SESSION_BAR_SHARE = 0.5


class SessionDataError(RuntimeError):
    """Most companies have no bar for the session the run belongs to."""


class FetchFailureError(RuntimeError):
    """More than half the companies' fetches failed this run."""


# What failed in the last run_pipeline() call, reset at its start: shown in
# the page's Data gaps and stored in scores_detail, never left silent.
# failed_companies: {layer_id: [ticker]} (fetch raised; not in the layer score);
# failed_ticker_feeds: [ticker] (its news count is missing); failed_generic_feeds: [url].
FETCH_ISSUES = {"failed_companies": {}, "failed_ticker_feeds": [], "failed_generic_feeds": []}


def _feed_failed(feed) -> bool:
    """A feed that returned nothing because it failed, not because there was
    no news: feedparser doesn't raise on a dead host or an HTTP error, it
    sets bozo / status. A feed with entries, or an empty 200, hasn't failed."""
    if feed.entries:
        return False
    status = feed.get("status")
    return bool(feed.bozo) or (status is not None and status != 200) or status is None


def completed_dates(index, now: datetime.datetime = None) -> list:
    """The dates in a daily-bar index whose US session has closed: any date
    before today (US/Eastern), and today once 16:00 ET has passed. A row
    counts by its date alone, empty or not: Yahoo blanks the Close of the
    session that has just closed for a while after 00:00 UTC, and that
    session still happened. `now` is for tests."""
    now_et = (now or datetime.datetime.now(datetime.timezone.utc)).astimezone(US_EASTERN)
    today, closed = now_et.date(), now_et.time() >= SESSION_CLOSE_ET
    return [d for d in (ts.date() for ts in index) if d < today or (d == today and closed)]


def session_bars(hist, session: datetime.date = None, label: str = "", now: datetime.datetime = None):
    """(bars, has_session): the daily bars up to and including `session`,
    with trailing empty (NaN Close) rows removed, and whether the last bar
    left is that session's. Without a session (tools, the CLI) it is the
    series' own last completed date. When has_session is False the caller
    treats the series as missing for this run: the bar before is another
    session's price, not this one's."""
    if hist is None or hist.empty:
        return hist, False
    if session is None:
        done = completed_dates(hist.index, now)
        if not done:
            return hist.iloc[0:0], False
        session = done[-1]
    later = [ts.date() > session for ts in hist.index]
    if any(later):
        log.info(f"  {label or 'bars'}: dropped {sum(later)} bar(s) after session {session}")
        hist = hist[[not x for x in later]]
    while not hist.empty and pd.isna(hist["Close"].iloc[-1]):
        log.info(f"  {label or 'bars'}: dropped empty (NaN close) bar dated {hist.index[-1].date()}")
        hist = hist.iloc[:-1]
    has_session = not hist.empty and hist.index[-1].date() == session
    if not has_session:
        last = hist.index[-1].date() if not hist.empty else None
        log.warning(f"  {label or 'bars'}: no bar for session {session} (last usable: {last}) — "
                    "treated as missing this run, not as that session's price")
    return hist, has_session


def fetch_trading_sessions(now: datetime.datetime = None) -> list:
    """Completed US trading sessions (datetime.date, oldest first) from
    ^GSPC's daily bars. sessions[-1] is the session the run's data is "as
    of"; colour confirmation takes its expected sessions from here, so a
    market holiday is never reported as a missing day.

    Raises instead of returning an empty or stale calendar — every
    date-keyed output depends on it, so the run fails loudly rather than
    silently falling back to the UTC run date.

    A session's row counts even when its Close is empty (completed_dates). If
    Yahoo has no row at all yet for the weekday that just closed, the session
    is the one before: every series is then cut there too, so the page is a
    session behind but never mis-dated, and main refuses to write a session
    older than the newest stored one."""
    try:
        bars = yf.Ticker(SESSION_REFERENCE).history(period="1mo")
        sessions = completed_dates(bars.index, now) if bars is not None else []
    except Exception as e:
        raise RuntimeError(f"Trading-session calendar unavailable ({SESSION_REFERENCE} daily bars): {e}") from e
    if not sessions:
        raise RuntimeError(f"Trading-session calendar unavailable: {SESSION_REFERENCE} returned no completed daily bars")
    now_et   = (now or datetime.datetime.now(datetime.timezone.utc)).astimezone(US_EASTERN)
    today_et = now_et.date()
    expected = today_et if now_et.time() >= SESSION_CLOSE_ET else today_et - datetime.timedelta(days=1)
    while expected.weekday() >= 5:
        expected -= datetime.timedelta(days=1)
    if sessions[-1] < expected:
        log.warning(f"  {SESSION_REFERENCE} has no row for {expected} (a market holiday, or Yahoo hasn't "
                    f"added it yet): the run's session is {sessions[-1]}")
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
 
 
def fetch_ticker_headlines(tickers: list, max_per_ticker: int = 10) -> tuple:
    """
    Fetch Yahoo Finance RSS for each specific ticker.
 
    v4 fix: ticker symbol is stored separately and NOT prepended to headline text.
    Previously: f"{ticker} {title}".lower() caused cross-layer contamination —
    memory layer keywords matched energy ticker headlines because "CEG" appeared
    as text prefix on every energy headline, and energy keywords like "power"
    matched memory headlines via ticker context.
 
    Now: headlines are cleanly separated by ticker ownership.
    score_news_velocity() filters by layer_tickers to prevent cross-contamination.

    Returns (headlines, failed_tickers): tickers whose feed failed (raised,
    or _feed_failed), so their news count is missing rather than a clean 0
    (ported from RayDar-DataCenter, which counts only exceptions).
    """
    headlines = []
    failed = set()
    for ticker in tickers:
        url = f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={ticker}&region=US&lang=en-US"
        try:
            feed = feedparser.parse(url)
            if _feed_failed(feed):
                failed.add(ticker.upper())
                continue
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
            failed.add(ticker.upper())
    return headlines, failed
 
 
def fetch_generic_headlines(max_per_feed: int = 20) -> tuple:
    """Fetch generic business news as supplementary signal. Returns
    (headlines, failed_feed_urls)."""
    headlines = []
    failed = []
    for url in GENERIC_FEEDS:
        try:
            feed = feedparser.parse(url)
            if _feed_failed(feed):
                log.warning(f"Generic feed failed ({url}): status {feed.get('status')}, {feed.get('bozo_exception')}")
                failed.append(url)
                continue
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
            failed.append(url)
    return headlines, failed
 
 
def fetch_all_headlines() -> tuple:
    """Fetch ticker-specific (primary) + generic (supplementary) headlines.
    Returns (headlines, failed_ticker_feeds, failed_generic_feeds)."""
    log.info("  Fetching ticker-specific headlines...")
    ticker_headlines, failed_ticker = fetch_ticker_headlines(ALL_TICKERS, max_per_ticker=10)
    log.info(f"  Got {len(ticker_headlines)} ticker-specific headlines")
    if failed_ticker:
        log.warning(f"  Ticker feed failed for: {sorted(failed_ticker)}")
 
    log.info("  Fetching generic news headlines...")
    generic_headlines, failed_generic = fetch_generic_headlines(max_per_feed=20)
    log.info(f"  Got {len(generic_headlines)} generic headlines")
 
    return ticker_headlines + generic_headlines, failed_ticker, failed_generic
 
 
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


def merged_revenue(ticker: str, yf_revenue: dict) -> tuple:
    """({quarter_end: revenue}, source). EDGAR fills the quarters yfinance
    lacks (older ones for acceleration, and a newest one yfinance leaves
    blank), only from a concept that matches yfinance where both exist.
    Shared by the dashboard and the radar, so both read the same quarters."""
    revenue = dict(yf_revenue)
    edgar_rev, edgar_note = edgar.quarterly_revenue(ticker, revenue)
    if not edgar_rev:
        return revenue, f"yfinance only (EDGAR: {edgar_note})"
    for d, v in edgar_rev.items():
        if not any(abs((d - y).days) <= edgar.DATE_TOL_DAYS for y in revenue):
            revenue[d] = v
    return revenue, f"yfinance + EDGAR {edgar_note}"


def yoy_series(revenue: dict, n: int = 4) -> list:
    """[(quarter_end, yoy)] for up to n consecutive quarters, newest first,
    each date-matched to the quarter ending 350-380 days earlier. Stops at the
    first quarter without its year-earlier quarter or with a gap — no stand-ins."""
    ends = sorted(revenue, reverse=True)
    out, prev = [], None
    for e in ends:
        if prev is not None and not 75 <= (prev - e).days <= 105:
            break
        base = next((p for p in ends if 350 <= (e - p).days <= 380), None)
        if base is None or not revenue[base]:
            break
        out.append((e, round((revenue[e] - revenue[base]) / abs(revenue[base]), 4)))
        prev = e
        if len(out) == n:
            break
    return out


def quarterly_yoy(revenue: dict) -> tuple:
    """(growth_curr, growth_prev, latest_quarter_end) from {quarter_end: revenue}.

    YoY is a quarter against the quarter ending 350-380 days earlier, matched
    by date, not column position (the old code compared the latest quarter
    with the one 2 quarters back and called it YoY). growth_prev is the YoY of
    the quarter before the latest one, so it needs 6 quarters of revenue.
    Anything without its year-earlier quarter is None — never a stand-in.
    """
    ends = sorted(revenue, reverse=True)
    if not ends:
        return None, None, None

    def yoy(end):
        prior = next((p for p in ends if 350 <= (end - p).days <= 380), None)
        base  = revenue[prior] if prior is not None else None
        return round((revenue[end] - base) / abs(base), 4) if base else None

    q0 = ends[0]
    q1 = next((e for e in ends[1:] if 75 <= (q0 - e).days <= 105), None)
    return yoy(q0), (yoy(q1) if q1 is not None else None), q0


def fetch_ticker_data(ticker: str, headlines: list, layer_keywords: list,
                      layer_tickers: list = None, session: datetime.date = None,
                      failed_ticker_feeds: set = None):
    try:
        t    = yf.Ticker(ticker)
        info = t.info
 
        week52_high = info.get("fiftyTwoWeekHigh", 0) or 0
        week52_low  = info.get("fiftyTwoWeekLow",  0) or 0
        market_cap  = info.get("marketCap", 0) or 0
        gross_margin = info.get("grossMargins")
 
        data_missing: list = []

        # Cut at the run's session; without a bar for it every price value
        # is missing, not the session before under this session's date.
        hist, has_session = session_bars(t.history(period="6mo"), session, label=ticker)
        if not has_session and hist is not None:
            hist = hist.iloc[0:0]

        closes  = hist["Close"].tolist() if not hist.empty else []
        if closes:
            step = max(1, len(closes) // 30)
            price_history = [round(closes[i], 2) for i in range(0, len(closes), step)][-30:]
        else:
            price_history = []
        volumes = hist["Volume"].tolist() if not hist.empty else []

        # Price and day change from the last completed session's close — the
        # same bars as the 30d return and the "Data: close of ..." label, not
        # info's live quote (which was intraday during US trading hours).
        price = closes[-1] if closes else None
        if price is None:
            data_missing.append("price")
        price_act = round(closes[-1] / closes[-2] - 1, 4) if len(closes) >= 2 and closes[-2] else None
        if price_act is None:
            data_missing.append("price_act")

        # Fewer than 21 closes is a missing return, not a flat 0% one.
        price_30d = round((closes[-1] / closes[-21] - 1), 4) if len(closes) >= 21 and closes[-21] else None
        if price_30d is None or math.isnan(price_30d):
            data_missing.append("price_30d_return")
            price_30d = None

        vol_avg   = sum(volumes[-20:]) / 20 if len(volumes) >= 20 else None
        vol_spike = round(volumes[-1] / vol_avg, 2) if vol_avg and volumes else None
        if vol_spike is None:
            data_missing.append("vol_spike")   # scored as before (no points), now named
 
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
        growth_quarter    = None   # quarter-end of growth_curr
        revenue_source    = "none"
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
                    yf_rev = {pd.Timestamp(k).date(): float(v) for k, v in rev_row.dropna().items()}
                    revenue, revenue_source = merged_revenue(ticker, yf_rev)
                    growth_curr, growth_prev, growth_quarter = quarterly_yoy(revenue)
 
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
        # No fallback for growth_prev: earningsGrowth is EPS growth, not revenue.
        # Without 6 quarters, acceleration is unmeasured (None), not estimated.
        if growth_curr is None:
            data_missing.append("growth_curr")
        if growth_prev is None:
            data_missing.append("growth_prev")
 
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
                upto    = session or (hist.index[-1].date() if not hist.empty else datetime.date.today())
                dates   = pd.Index([ts.date() for ts in ud.index])
                inside  = (dates > upto - datetime.timedelta(days=ANALYST_WINDOW_DAYS)) & (dates <= upto)
                recent  = ud[inside].sort_index().tail(10)
                analyst_upgrades = int(recent["ToGrade"].isin(BUY_GRADES).sum())
        except Exception as e:
            log.debug(f"  {ticker} upgrades_downgrades error: {e}")
        if analyst_upgrades is None:
            data_missing.append("analyst_upgrades")

        # Next earnings date (ISO) from t.calendar, for the card badge only —
        # not a score input. A failed fetch or no date is None: the page then
        # shows no badge rather than a guessed one.
        next_earnings = None
        try:
            cal = t.calendar
            dates = cal.get("Earnings Date") if isinstance(cal, dict) else None
            if dates:
                next_earnings = min(dates).isoformat()
        except Exception as e:
            log.debug(f"  {ticker} calendar error: {e}")

        # No shortPercentOfFloat (failed or empty fetch) is missing, not 0.0:
        # None + data_missing, like the 30d return (Ray, 2026-11-17). The
        # score treats it as before (no points), now named in data_status.
        short_int_change = None
        try:
            short_info = info.get("shortPercentOfFloat")
            if short_info is not None:
                short_int_change = round((short_info - 0.05) * -1, 4)
        except Exception as e:
            log.debug(f"  {ticker} short interest error: {e}")
        if short_int_change is None:
            data_missing.append("short_int_change")
 
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
        # If this ticker's own feed failed, its count is missing, not a clean
        # 0. Scored as before (no news points) until the January review
        # decides how a missing count should score; named in Data gaps.
        if failed_ticker_feeds and ticker.upper() in failed_ticker_feeds:
            news_velocity = None
            data_missing.append("news_velocity")
        else:
            news_velocity = score_news_velocity(
                headlines,
                layer_keywords,
                layer_tickers=layer_tickers,   # ← ownership filter
            )
 
        return {
            "ticker":            ticker,
            "name":              info.get("longName") or info.get("shortName", ticker),
            "price":             round(float(price), 2) if price is not None else None,
            "currency":          info.get("currency", "USD"),
            "growth_curr":       growth_curr,
            "growth_prev":       growth_prev,
            "growth_quarter":    growth_quarter.isoformat() if growth_quarter else None,
            "revenue_source":    revenue_source,
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
            "peer_outperformance": None,   # set by add_peer_outperformance when the 30d return exists
            "analyst_count":     info.get("numberOfAnalystOpinions", 0),
            "next_earnings":     next_earnings,
            "sector":            info.get("sector", "N/A"),
            "data_date":         hist.index[-1].date().isoformat() if not hist.empty else None,
            "data_age_note":     "Quarterly financials are the latest reported quarter and can be several months old",
            "data_missing":      data_missing,
            "_session_bar":      has_session,
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


def fetch_macro(session: datetime.date = None) -> dict:
    """
    Each macro signal is fetched independently, so one failure can't blank
    out the others (a single shared try used to discard already-fetched
    signals and return hardcoded defaults for everything). A missing signal
    is None, named in data_missing — never a made-up number (no VIX 20, no
    zeros), per CLAUDE.md's fetch-failure rule. Ported from RayDar-DataCenter.
    Every series is cut at the run's session; one without a bar for it is
    missing too, not the session before.
    """
    log.info("  Fetching macro signals...")
    result = {"vix": None, "yield_10y_change": None, "nasdaq_vs_spx_20d": None, "nasdaq_30d_return": None}
    data_missing = []

    try:
        # Last completed session's close, not the live quote — the same
        # session every other daily series is scored on.
        vix, ok = session_bars(yf.Ticker("^VIX").history(period="5d"), session, label="^VIX")
        if ok:
            result["vix"] = round(float(vix["Close"].iloc[-1]), 2)
    except Exception as e:
        log.warning(f"  VIX fetch error: {e}")

    try:
        tnx, ok = session_bars(yf.Ticker("^TNX").history(period="35d"), session, label="^TNX")
        if ok and len(tnx) >= 21:
            result["yield_10y_change"] = round(
                (tnx["Close"].iloc[-1] - tnx["Close"].iloc[-21]) * 100, 2
            )
    except Exception as e:
        log.warning(f"  10Y yield fetch error: {e}")

    try:
        nasdaq, nq_ok = session_bars(yf.Ticker("^IXIC").history(period="25d"), session, label="^IXIC")
        spx, spx_ok   = session_bars(yf.Ticker("^GSPC").history(period="25d"), session, label="^GSPC")
        if nq_ok and spx_ok and len(nasdaq) >= 20 and len(spx) >= 20:
            nasdaq_ret = nasdaq["Close"].iloc[-1] / nasdaq["Close"].iloc[-20] - 1
            spx_ret    = spx["Close"].iloc[-1]    / spx["Close"].iloc[-20]    - 1
            result["nasdaq_vs_spx_20d"] = round(nasdaq_ret - spx_ret, 4)
    except Exception as e:
        log.warning(f"  Nasdaq/SPX fetch error: {e}")

    # NASDAQ over the same 21 closes as each company's price_30d_return —
    # the benchmark for the price-revenue divergence flag. Not a regime input.
    try:
        nq, ok = session_bars(yf.Ticker("^IXIC").history(period="45d"), session, label="^IXIC")
        nq = nq["Close"].dropna() if ok else []
        if len(nq) >= 21 and nq.iloc[-21]:
            result["nasdaq_30d_return"] = round(float(nq.iloc[-1] / nq.iloc[-21] - 1), 4)
    except Exception as e:
        log.warning(f"  Nasdaq 30d fetch error: {e}")

    # Anything not fetched, or not a finite number, is missing — not a default.
    for key, value in result.items():
        if value is None or not math.isfinite(value):
            result[key] = None
            data_missing.append(key)
    result["data_missing"] = data_missing
    if data_missing:
        log.warning(f"  Macro data missing: {data_missing}")
    else:
        log.info(f"  Macro: VIX={result['vix']} "
                 f"yield_chg={result['yield_10y_change']}bps "
                 f"nasdaq_rel={result['nasdaq_vs_spx_20d']*100:.1f}%")
    return result
 
 
GROWTH_HISTORY_FILE = "growth_history.json"


def apply_growth_history(results: dict, path: str = GROWTH_HISTORY_FILE) -> None:
    """Persist each ticker's YoY revenue growth at its quarter-end, and use
    the stored history for growth_prev (the YoY of the quarter before
    growth_quarter): a fallback when EDGAR has none (ASML, TSM, or an EDGAR
    outage), and a cross-check when it does. {ticker: {"YYYY-MM-DD": yoy}}.
    A value only ever comes from a computed YoY, never an estimate."""
    p = Path(path)
    try:
        hist = json.loads(p.read_text()) if p.exists() else {}
    except Exception as e:
        log.warning(f"  {path} unreadable, starting a new one: {e}")
        hist = {}
    for tickers in results.values():
        for t in (tickers or []):
            q, g = t.get("growth_quarter"), t.get("growth_curr")
            if not q or g is None:
                continue
            mine = hist.setdefault(t["ticker"], {})
            q0   = datetime.date.fromisoformat(q)
            prev = next(((d, v) for d, v in sorted(mine.items(), reverse=True)
                         if 75 <= (q0 - datetime.date.fromisoformat(d)).days <= 105), None)
            if prev and t.get("growth_prev") is None:
                t["growth_prev"] = prev[1]
                t["revenue_source"] = f"{t.get('revenue_source', '')}; prev YoY from {path} ({prev[0]})"
                t["data_missing"] = [m for m in t.get("data_missing", []) if m != "growth_prev"]
            elif prev and abs(t["growth_prev"] - prev[1]) > 0.01:
                log.warning(f"  {t['ticker']}: previous-quarter YoY {t['growth_prev']:+.1%} "
                            f"disagrees with stored {prev[1]:+.1%} for {prev[0]}")
            # One entry per quarter: Yahoo and EDGAR date the same quarter a few
            # days apart (MU 2026-08-31 vs 2026-09-03), so a changed label
            # replaces the stored one instead of adding a duplicate.
            for d in [d for d in mine if d != q and abs((datetime.date.fromisoformat(d) - q0).days) <= edgar.DATE_TOL_DAYS]:
                del mine[d]
            mine[q] = g
    p.write_text(json.dumps(hist, indent=1, sort_keys=True))


def run_pipeline(portfolio_file: str = "portfolio.json", session: datetime.date = None) -> dict:
    """{layer_id: [ticker dicts]}, prices cut at `session`. Raises
    SessionDataError if most companies have no bar for it."""
    log.info("Fetching all headlines (ticker-specific + generic)...")
    FETCH_ISSUES.clear()
    FETCH_ISSUES["failed_companies"] = {}
    headlines, failed_ticker_feeds, failed_generic_feeds = fetch_all_headlines()
    FETCH_ISSUES["failed_ticker_feeds"] = sorted(failed_ticker_feeds)
    FETCH_ISSUES["failed_generic_feeds"] = list(failed_generic_feeds)
    if len(failed_ticker_feeds) >= len(ALL_TICKERS) and len(failed_generic_feeds) >= len(GENERIC_FEEDS):
        raise FetchFailureError("Every news feed failed this run: failing it rather than reading every "
                                "layer's news as zero")
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
                session=session,
                failed_ticker_feeds=failed_ticker_feeds,
            )
            if data:
                tickers_data.append(data)
            else:
                FETCH_ISSUES["failed_companies"].setdefault(layer_id, []).append(ticker)
        tickers_data      = add_peer_outperformance(tickers_data)
        results[layer_id] = tickers_data
        log.info(f"    {len(tickers_data)}/{len(layer_tickers)} tickers OK")
    failed = [t for ts in FETCH_ISSUES["failed_companies"].values() for t in ts]
    total  = sum(len(layer["tickers"]) for layer in AI_CHAIN_LAYERS.values())
    if failed:
        log.warning(f"  Company fetch failed for {failed}: named in Data gaps, not in their layer scores")
    if len(failed) > MAX_NO_SESSION_BAR_SHARE * total:
        raise FetchFailureError(f"{len(failed)} of {total} company fetches failed ({', '.join(failed)}): "
                                "failing the run rather than publishing layers built from a minority")
    fetched = [t for ts in results.values() for t in ts]
    no_bar  = [t["ticker"] for t in fetched if not t.get("_session_bar", True)]
    if fetched and len(no_bar) > MAX_NO_SESSION_BAR_SHARE * len(fetched):
        raise SessionDataError(f"{len(no_bar)} of {len(fetched)} companies have no bar for session {session} "
                               f"({', '.join(no_bar)}): Yahoo hasn't got the session's bars yet. Failing the "
                               "run rather than publishing without prices or under the wrong date")
    for t in fetched:
        t.pop("_session_bar", None)
    apply_growth_history(results)
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
        headlines, failed_ticker, failed_generic = fetch_all_headlines()
        if failed_ticker or failed_generic:
            print(f"Failed feeds: tickers {sorted(failed_ticker)}, generic {failed_generic}")
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
            headlines, _, _ = fetch_all_headlines()
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
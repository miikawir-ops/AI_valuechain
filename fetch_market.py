"""
fetch_market.py — Market data pipeline for the AI Investment Agent.
 
Fetches all data needed by score_engine.py from free sources:
  - yfinance  : prices, revenue growth, gross margins, volume, short interest
  - feedparser: news headlines for keyword scoring
 
Fix v3:
  - news_velocity cap raised from 10 → 20, scaled to 0-10 output
    (previously every layer hit cap=10, losing all discrimination)
  - Ticker-specific hits weighted 2× generic hits for better signal quality
  - gross_margin (absolute level) added to return dict
    (needed for score_engine v3.1 high-margin multiplier)
  - gross_margin passed as ratio (e.g. 0.58 for 58%) matching yfinance format
 
Returns two objects:
  run_pipeline() -> dict of {layer_id: [list of ticker dicts]}
  fetch_macro()  -> dict with vix, yield_10y_change, nasdaq_vs_spx_20d
"""
 
import json
import logging
import datetime
import argparse
import feedparser
import yfinance as yf
from pathlib import Path
 
log = logging.getLogger(__name__)
 
AI_CHAIN_LAYERS = {
    "energy": {
        "name": "Energy & Power Infrastructure",
        "tickers": ["CEG", "VST", "PWR", "GEV", "ETN"],
        "keywords": [
            "CEG", "VST", "PWR", "GEV", "ETN",
            "constellation energy", "vistra", "quanta", "GE vernova", "eaton",
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
            "NVDA", "AMD", "AVGO", "ASML", "TSM", "ARM", "CDNS",
            "nvidia", "broadcom", "TSMC", "cadence", "arm holdings",
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
            "MU", "WDC", "AMAT", "LRCX",
            "micron", "western digital", "applied materials", "lam research",
            "SK hynix", "hynix", "samsung memory",
            "HBM", "HBM3", "HBM3e", "HBM4", "high bandwidth memory",
            "memory bandwidth", "memory stack", "memory bottleneck",
            "DRAM", "NAND", "flash memory", "memory chip",
            "DRAM price", "memory market", "memory supply", "memory demand",
            "AI memory", "memory capacity", "memory shortage", "HBM supply",
            "memory revenue", "memory growth", "data storage",
            "trillion", "market cap memory",
            "etch equipment", "deposition", "memory fab", "memory production",
        ]
    },
    "infra": {
        "name": "Data Center & Networking",
        "tickers": ["VRT", "ANET", "EQIX", "SMCI", "CSCO", "CIEN"],
        "keywords": [
            "VRT", "ANET", "EQIX", "SMCI", "CSCO", "CIEN",
            "vertiv", "arista", "equinix", "supermicro", "cisco", "ciena",
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
            "MSFT", "GOOGL", "AMZN", "META",
            "microsoft", "google", "amazon", "meta",
            "azure", "AWS", "google cloud", "amazon web services",
            "cloud revenue", "cloud growth", "cloud spending",
            "copilot", "gemini", "bedrock", "llama", "openai",
            "foundation model", "large language model", "LLM",
            "AI assistant", "AI product", "generative AI",
            "capex", "hyperscaler", "AI investment", "data center spending",
            "AI spending", "cloud capex", "infrastructure investment",
            "AI infrastructure", "cloud infrastructure",
            "cloud margin", "AI revenue", "AI monetization",
            "subscription growth", "enterprise AI", "AI adoption",
        ]
    },
    "software": {
        "name": "AI Software & Observability",
        "tickers": ["PLTR", "NOW", "SNOW", "CRM", "DDOG"],
        "keywords": [
            "PLTR", "NOW", "SNOW", "CRM", "DDOG",
            "palantir", "servicenow", "snowflake", "salesforce", "datadog",
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
            "CRWD", "PANW", "OKTA",
            "crowdstrike", "palo alto", "sentinelone", "okta",
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
 
 
def fetch_ticker_headlines(tickers: list, max_per_ticker: int = 10) -> list:
    """
    Fetch Yahoo Finance RSS for each specific ticker.
    Returns headlines tagged with source type for weighted scoring.
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
                    # Tag as ticker-specific — weighted 2x in scoring
                    headlines.append({
                        "text":   f"{ticker} {title} {summary}".lower(),
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
 
 
def score_news_velocity(headlines: list, keywords: list) -> float:
    """
    Score news velocity with discrimination.
 
    v3 fix: cap raised from 10 → 20 weighted hits, scaled to 0-10 output.
    Ticker-specific headlines count as 2 hits each (higher quality signal).
    Generic headlines count as 1 hit each.
 
    Previously every layer hit the cap of 10, making news useless for
    discrimination. Now:
      - A quiet layer with 3 generic hits → score 1.5
      - An active layer with 5 ticker + 5 generic hits → score 7.5
      - A true bottleneck with 10 ticker hits → score 10
    """
    kw_lower = [kw.lower() for kw in keywords]
 
    weighted_hits = 0.0
    for h in headlines:
        text   = h["text"] if isinstance(h, dict) else h
        source = h.get("source", "generic") if isinstance(h, dict) else "generic"
        weight = 2.0 if source == "ticker" else 1.0
 
        if any(kw in text for kw in kw_lower):
            weighted_hits += weight
 
    # Cap at 20 weighted hits, scale to 0-10 output
    return round(min(weighted_hits, 20.0) / 2.0, 1)
 
 
def fetch_ticker_data(ticker: str, headlines: list, layer_keywords: list):
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
 
        # Gross margin absolute level — new in v3
        # Used by score_engine v3.1 high-margin multiplier
        gross_margin = info.get("grossMargins")   # e.g. 0.58 for 58%
 
        hist    = t.history(period="6mo")
        closes  = hist["Close"].tolist() if not hist.empty else []
        if closes:
            step = max(1, len(closes) // 30)
            price_history = [round(closes[i], 2) for i in range(0, len(closes), step)][-30:]
        else:
            price_history = []
        volumes = hist["Volume"].tolist() if not hist.empty else []
 
        price_30d = round((closes[-1] / closes[-21] - 1), 4) if len(closes) >= 21 else 0.0
        vol_avg   = sum(volumes[-20:]) / 20 if len(volumes) >= 20 else None
        vol_spike = round(volumes[-1] / vol_avg, 2) if vol_avg and volumes else 1.0
 
        ret_5d  = (closes[-1] / closes[-5]  - 1) if len(closes) >= 5  else 0
        ret_90d = (closes[-1] / closes[-63] - 1) if len(closes) >= 63 else ret_5d
        avg_5d_from_90d = ret_90d / 18 if ret_90d != 0 else 0.001
        price_momentum  = round(ret_5d / avg_5d_from_90d, 2) if avg_5d_from_90d != 0 else 1.0
        price_momentum  = max(-5.0, min(5.0, price_momentum))
 
        growth_curr    = None
        growth_prev    = None
        gm_delta       = 0.0
        revenue_quarterly = 0
        financials     = None
 
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
 
        analyst_upgrades = 0
        try:
            recs = t.recommendations
            if recs is not None and not recs.empty:
                recent = recs.tail(10)
                if "To Grade" in recent.columns:
                    up = recent[recent["To Grade"].isin([
                        "Buy", "Strong Buy", "Overweight", "Outperform", "Positive"
                    ])]
                    analyst_upgrades = len(up)
        except Exception:
            analyst_upgrades = 0
 
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
 
        # News velocity — ticker symbol always included as implicit keyword
        extended_keywords = layer_keywords + [ticker.lower()]
        news_velocity = score_news_velocity(headlines, extended_keywords)
 
        return {
            "ticker":            ticker,
            "name":              info.get("longName") or info.get("shortName", ticker),
            "price":             round(float(price), 2),
            "currency":          info.get("currency", "USD"),
            "growth_curr":       growth_curr,
            "growth_prev":       growth_prev,
            "gm_delta":          gm_delta,
            "gross_margin":      gross_margin,       # ← NEW: absolute GM level (0.58 = 58%)
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
 
 
def fetch_macro() -> dict:
    log.info("  Fetching macro signals...")
    result = {"vix": 20.0, "yield_10y_change": 0.0, "nasdaq_vs_spx_20d": 0.0}
    try:
        vix_info = yf.Ticker("^VIX").info
        result["vix"] = round(
            vix_info.get("regularMarketPrice") or vix_info.get("previousClose", 20.0), 2
        )
        tnx = yf.Ticker("^TNX").history(period="35d")
        if not tnx.empty and len(tnx) >= 21:
            result["yield_10y_change"] = round(
                (tnx["Close"].iloc[-1] - tnx["Close"].iloc[-21]) * 100, 2
            )
        nasdaq = yf.Ticker("^IXIC").history(period="25d")
        spx    = yf.Ticker("^GSPC").history(period="25d")
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
        for ticker in layer_config["tickers"]:
            log.info(f"    Fetching {ticker}...")
            data = fetch_ticker_data(ticker, headlines, layer_config["keywords"])
            if data:
                tickers_data.append(data)
        tickers_data      = add_peer_outperformance(tickers_data)
        results[layer_id] = tickers_data
        log.info(f"    {len(tickers_data)}/{len(layer_config['tickers'])} tickers OK")
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
        print("\nTesting news fetch and velocity scoring...")
        headlines = fetch_all_headlines()
        ticker_count  = sum(1 for h in headlines if isinstance(h, dict) and h.get("source") == "ticker")
        generic_count = sum(1 for h in headlines if isinstance(h, dict) and h.get("source") == "generic")
        print(f"\nTotal headlines: {len(headlines)}")
        print(f"  Ticker-specific: {ticker_count} (weight ×2)")
        print(f"  Generic:         {generic_count} (weight ×1)")
 
        print("\nNews velocity by layer:")
        for layer_id, layer in AI_CHAIN_LAYERS.items():
            score = score_news_velocity(headlines, layer["keywords"])
            bar   = "█" * int(score)
            print(f"  {layer_id:<12} {score:>4.1f}  {bar}")
 
        print("\nTop 10 memory headlines:")
        mem_kw = [kw.lower() for kw in AI_CHAIN_LAYERS["memory"]["keywords"]]
        hits = [h["text"] if isinstance(h, dict) else h
                for h in headlines
                if any(kw in (h["text"] if isinstance(h, dict) else h) for kw in mem_kw)]
        for h in hits[:10]:
            print(f"  • {h[:120]}")
 
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
                data = fetch_ticker_data(ticker, headlines, layer["keywords"])
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
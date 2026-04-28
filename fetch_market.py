"""
fetch_market.py — Market data pipeline for the AI Investment Agent.
 
Fetches all data needed by score_engine.py from free sources:
  - yfinance  : prices, revenue growth, gross margins, volume, short interest
  - feedparser: news headlines for keyword scoring
 
Returns two objects:
  run_pipeline() -> dict of {layer_id: [list of ticker dicts]}
  fetch_macro()  -> dict with vix, yield_10y_change, nasdaq_vs_spx_20d
 
All keys match exactly what score_engine.py expects.
Missing data is handled gracefully — skip and log, never crash.
 
Usage:
  python fetch_market.py              -- full pipeline all layers
  python fetch_market.py --macro      -- macro data only
  python fetch_market.py --layer energy  -- one layer only (fast test)
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
        "name": "Energy & Resources",
        "tickers": ["CEG", "VST", "NEE", "PWR"],
        "keywords": ["nuclear", "power", "grid", "electricity", "energy", "ppa",
                     "megawatt", "gigawatt", "data center power", "utility",
                     "renewable", "solar", "wind farm", "power plant", "natural gas",
                     "constellation", "vistra", "nextera", "quanta", "uranium",
                     "power demand", "energy crisis", "blackout", "capacity"]
    },
    "compute": {
        "name": "Semiconductors & Hardware",
        "tickers": ["NVDA", "AMD", "AVGO", "ASML", "TSM"],
        "keywords": ["gpu", "chip", "semiconductor", "compute", "H100", "H200",
                     "inference", "training", "custom silicon", "ASIC",
                     "nvidia", "AMD chip", "broadcom", "TSMC", "ASML",
                     "chip shortage", "wafer", "foundry", "fab", "AI chip",
                     "graphics card", "processor", "silicon"]
    },
    "memory": {
        "name": "HBM Memory & Storage",
        "tickers": ["MU", "WDC", "AMAT", "LRCX"],
        "keywords": ["HBM", "memory", "DRAM", "NAND", "bandwidth",
                     "HBM3", "storage", "flash memory", "micron", "samsung",
                     "SK hynix", "memory chip", "memory demand", "chip memory",
                     "high bandwidth", "memory supply", "DRAM price", "memory market"]
    },
    "infra": {
        "name": "Data Center Infrastructure",
        "tickers": ["VRT", "ANET", "EQIX", "SMCI"],
        "keywords": ["liquid cooling", "data center", "networking", "cooling",
                     "rack", "interconnect", "InfiniBand", "ethernet",
                     "server", "vertiv", "arista", "equinix", "supermicro",
                     "data centre", "colocation", "hyperscale", "AI infrastructure",
                     "compute infrastructure", "GPU cluster", "AI server"]
    },
    "cloud": {
        "name": "Cloud & Hyperscalers",
        "tickers": ["MSFT", "GOOGL", "AMZN", "META"],
        "keywords": ["cloud", "azure", "AWS", "capex", "hyperscaler",
                     "AI investment", "data center spending", "infrastructure"]
    },
    "software": {
        "name": "AI Software & Applications",
        "tickers": ["PLTR", "NOW", "SNOW", "CRM"],
        "keywords": ["AI software", "SaaS", "AI agent", "enterprise AI",
                     "AI platform", "automation", "generative AI"]
    },
}
 
NEWS_FEEDS = [
    "https://feeds.reuters.com/reuters/businessNews",
    "https://finance.yahoo.com/news/rssindex",
    "https://feeds.content.dowjones.io/public/rss/mw_realtimeheadlines",
    "https://www.cnbc.com/id/20910258/device/rss/rss.html",
]
 
 
def fetch_all_headlines(max_per_feed: int = 8) -> list:
    headlines = []
    for url in NEWS_FEEDS:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:max_per_feed]:
                title   = entry.get("title", "")
                summary = entry.get("summary", "")[:150]
                if title:
                    headlines.append(f"{title} {summary}".lower())
        except Exception as e:
            log.warning(f"News feed failed ({url}): {e}")
    log.info(f"  Fetched {len(headlines)} headlines")
    return headlines
 
 
def score_news_velocity(headlines: list, keywords: list) -> float:
    hits = sum(1 for h in headlines for kw in keywords if kw.lower() in h)
    return min(hits, 10) * 1.0
 
 
def fetch_ticker_data(ticker: str, headlines: list, layer_keywords: list):
    try:
        t    = yf.Ticker(ticker)
        info = t.info
 
        price = (info.get("currentPrice")
                 or info.get("regularMarketPrice")
                 or info.get("previousClose", 0))
        prev  = info.get("previousClose") or price
        price_act = round((price - prev) / prev, 4) if prev else 0.0
 
        hist    = t.history(period="6mo")
        closes  = hist["Close"].tolist() if not hist.empty else []
        volumes = hist["Volume"].tolist() if not hist.empty else []
 
        price_30d  = round((closes[-1] / closes[-21] - 1), 4) if len(closes) >= 21 else 0.0
        vol_avg    = sum(volumes[-20:]) / 20 if len(volumes) >= 20 else None
        vol_spike  = round(volumes[-1] / vol_avg, 2) if vol_avg and volumes else 1.0
 
        ret_5d  = (closes[-1] / closes[-5]  - 1) if len(closes) >= 5  else 0
        ret_90d = (closes[-1] / closes[-63] - 1) if len(closes) >= 63 else ret_5d
        avg_5d_from_90d = ret_90d / 18 if ret_90d != 0 else 0.001
        price_momentum  = round(ret_5d / avg_5d_from_90d, 2) if avg_5d_from_90d != 0 else 1.0
        price_momentum  = max(-5.0, min(5.0, price_momentum))
 
        growth_curr = None
        growth_prev = None
        gm_delta    = 0.0
 
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
 
                gm_row  = None
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
            growth_curr = info.get("revenueGrowth")
        if growth_prev is None:
            growth_prev = info.get("earningsGrowth")
 
        analyst_upgrades = 0
        try:
            recs = t.recommendations
            if recs is not None and not recs.empty:
                cutoff = datetime.datetime.now() - datetime.timedelta(days=30)
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
 
        news_velocity = score_news_velocity(headlines, layer_keywords)
 
        return {
            "ticker":              ticker,
            "name":                info.get("longName") or info.get("shortName", ticker),
            "price":               round(float(price), 2),
            "currency":            info.get("currency", "USD"),
            "growth_curr":         growth_curr,
            "growth_prev":         growth_prev,
            "gm_delta":            gm_delta,
            "news_velocity":       news_velocity,
            "capex_div":           capex_div,
            "vol_spike":           vol_spike,
            "price_act":           price_act,
            "analyst_upgrades":    analyst_upgrades,
            "short_int_change":    short_int_change,
            "price_30d_return":    price_30d,
            "price_momentum":      price_momentum,
            "peer_outperformance": 0.0,
            "market_cap":          info.get("marketCap"),
            "analyst_count":       info.get("numberOfAnalystOpinions", 0),
            "sector":              info.get("sector", "N/A"),
            "data_date":           datetime.datetime.now().strftime("%Y-%m-%d"),
            "data_age_note":       "Quarterly financials may be up to 90 days old",
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
    log.info("Fetching news headlines...")
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
        tickers_data     = add_peer_outperformance(tickers_data)
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
    parser.add_argument("--macro", action="store_true", help="Macro data only")
    parser.add_argument("--layer", type=str, default=None, help="One layer only")
    args = parser.parse_args()
 
    if args.macro:
        print("\nFetching macro signals...")
        print(json.dumps(fetch_macro(), indent=2))
 
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
        print("\nRunning full pipeline — fetching all layers...")
        print("This takes 2–3 minutes.\n")
        data = run_pipeline()
        for layer_id, tickers in data.items():
            print(f"\n{layer_id.upper()} — {len(tickers)} tickers")
            for t in tickers:
                g_curr = t.get("growth_curr")
                g_prev = t.get("growth_prev")
                delta  = round(g_curr - g_prev, 3) if g_curr and g_prev else "N/A"
                print(f"  {t['ticker']:<6} price=${t['price']:<8} "
                      f"growth_curr={str(g_curr):<8} delta={str(delta):<8} "
                      f"vol_spike={t.get('vol_spike')}")
        print("\n✅ Pipeline complete")
        print(f"   Layers: {list(data.keys())}")
        print(f"   Total tickers: {sum(len(v) for v in data.values())}")

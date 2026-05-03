"""
main.py — Entry point for the AI Investment Agent.
 
Pipeline (4 stages, run in sequence):
  1. Fetch    → fetch_market.py   — collect all market data
  2. Score    → score_engine.py   — calculate composite scores per layer
  3. Analyze  → analyze.py        — Gemini AI narrative (Ollama fallback)
  4. Render   → render.py         — HTML dashboard + email/Telegram delivery
 
Usage:
  python main.py            → scheduled run every weekday at 08:00
  python main.py --now      → run immediately (testing)
  python main.py --score    → run fetch + score only (no AI, no delivery)
"""
 
import json
import logging
import argparse
import datetime
import schedule
import time
from pathlib import Path
 
# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger(__name__)
 
MACRO_DEFAULTS = {
    "vix":               20.0,
    "yield_10y_change":  0.0,
    "nasdaq_vs_spx_20d": 0.0,
}
 
 
def stage_fetch() -> tuple[dict, dict]:
    log.info("[1/4] Fetching market data...")
    try:
        from fetch_market import run_pipeline, fetch_macro
        market_data = run_pipeline("portfolio.json")
        macro_data  = fetch_macro()
        log.info(f"  Fetched {len(market_data)} layers, macro: VIX={macro_data.get('vix')}")
        return market_data, macro_data
    except Exception as e:
        log.error(f"  Fetch failed: {e} — using empty data")
        return {}, MACRO_DEFAULTS
 
 
def stage_score(market_data: dict, macro_data: dict) -> dict:
    log.info("[2/4] Calculating scores...")
    from score_engine import ScoreEngine
    engine  = ScoreEngine(macro_data)
    results = {}
 
    for layer_id, tickers_data in market_data.items():
        if not tickers_data:
            log.warning(f"  {layer_id}: no ticker data — skipping")
            continue
        layer_scores = []
        for ticker_data in tickers_data:
            try:
                result = engine.process_sector(layer_id, ticker_data)
                # Preserve ticker symbol and raw market data for render stage
                result['ticker']    = ticker_data.get('ticker', '')
                result['name']      = ticker_data.get('name', '')
                result['price']     = ticker_data.get('price', 0)
                result['price_30d_return'] = ticker_data.get('price_30d_return', 0)
                result['vol_spike'] = ticker_data.get('vol_spike', 1.0)
                result['news_velocity'] = ticker_data.get('news_velocity', 0)
                layer_scores.append(result)
            except Exception as e:
                log.warning(f"  {layer_id}/{ticker_data.get('ticker','?')}: {e}")
                continue
        if not layer_scores:
            continue
        best = max(layer_scores, key=lambda x: x["score"])
        results[layer_id] = {"best": best, "all_tickers": layer_scores, "layer_id": layer_id}
        log.info(f"  {layer_id}: score={best['score']} color={best['color']}")
 
    return results
 
 
def stage_analyze(scored_data: dict, market_data: dict) -> str:
    log.info("[3/4] Running AI analysis...")
    try:
        from analyze import run_analysis
        analysis = run_analysis(scored_data, market_data)
        log.info("  Analysis complete")
        return analysis
    except Exception as e:
        log.error(f"  Analysis failed: {e}")
        return f"Analysis unavailable — error: {e}"
 
 
def stage_render(scored_data: dict, analysis: str, macro_data: dict, market_data: dict = None, radar_data: list = None):
    log.info("[4/4] Rendering and delivering report...")
    try:
        from render import generate_dashboard, deliver
        html_path = generate_dashboard(scored_data, analysis, macro_data, market_data or {}, radar_data or [])
        deliver(analysis, html_path)
        log.info(f"  Dashboard saved: {html_path}")
    except Exception as e:
        log.error(f"  Render/deliver failed: {e}")
        print("\n" + "="*60)
        print("DAILY AI INVESTMENT REPORT")
        print("="*60)
        print(analysis)
        print("="*60)
 
 
def run_full_pipeline(score_only: bool = False):
    start = datetime.datetime.now()
    log.info(f"\n{'='*55}")
    log.info(f"  AI Investment Agent — {start.strftime('%A %B %d %Y %H:%M')}")
    log.info(f"{'='*55}")
 
    market_data, macro_data = stage_fetch()
    scored_data = stage_score(market_data, macro_data)
 
    if score_only:
        log.info("--score flag: stopping after scoring")
        print(json.dumps(
            {k: {"score": v["best"]["score"], "color": v["best"]["color"]}
             for k, v in scored_data.items()}, indent=2))
        return
 
    # Stage 2.5 — Next Nvidia Radar
    log.info("[2.5/4] Running Next Nvidia Radar...")
    try:
        from next_nvidia import run_radar, load_cached_radar
        radar_data = load_cached_radar()
        if not radar_data:
            radar_data = run_radar(verbose=False)
        log.info(f"  Radar complete — top pick: {radar_data[0]['ticker'] if radar_data else 'none'}")
    except Exception as e:
        log.warning(f"  Radar failed: {e}")
        radar_data = []
 
    analysis = stage_analyze(scored_data, market_data)
    stage_render(scored_data, analysis, macro_data, market_data, radar_data)
 
    # Auto-publish to GitHub Pages
    try:
        import importlib.util, sys
        spec = importlib.util.spec_from_file_location("publish_mod", "publish.py")
        mod  = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.publish()
    except Exception as e:
        log.warning(f"  Publish skipped: {e}")
 
    elapsed = (datetime.datetime.now() - start).seconds
    log.info(f"\n✅ Pipeline complete in {elapsed}s")
 
 
def is_weekday() -> bool:
    return datetime.datetime.now().weekday() < 5
 
 
def scheduled_job():
    if is_weekday():
        run_full_pipeline()
    else:
        log.info(f"Weekend — skipping ({datetime.datetime.now().strftime('%A')})")
 
 
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AI Investment Agent")
    parser.add_argument("--now",   action="store_true", help="Run immediately")
    parser.add_argument("--score", action="store_true", help="Fetch + score only")
    args = parser.parse_args()
 
    if args.now or args.score:
        run_full_pipeline(score_only=args.score)
    else:
        from config import REPORT_TIME
        log.info(f"Scheduled for {REPORT_TIME} every weekday. Use --now to test.")
        schedule.every().day.at(REPORT_TIME).do(scheduled_job)
        while True:
            schedule.run_pending()
            time.sleep(30)
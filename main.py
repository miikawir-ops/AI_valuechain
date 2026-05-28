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
 
v2 scoring fix:
  - Layer score is now market-cap weighted composite of all tickers
    (previously: best single ticker score — caused ARM to make Semicon Red
     while NVDA and AMD were both C-rated and decelerating)
  - Red reality check: layer cannot be Red if top-2 companies by market cap
    are both rated C or D — downgrades to Orange automatically
  - Layer color derived from weighted score, not best ticker color
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
 
# Rating quality map — used for Red reality check
RATING_QUALITY = {"A": 4, "B": 3, "C": 2, "D": 1}
 
 
def _layer_color_from_score(score: float, top_delta: float | None) -> tuple[str, str]:
    """
    Derive layer color from the market-cap weighted composite score.
    Mirrors the thresholds in score_engine.determine_color().
    """
    if top_delta is not None and top_delta < 0:
        return "Blue", f"Cooling — dominant ticker decelerating ({top_delta*100:.1f}%)"
    if score > 65:
        return "Red", "Confirmed bottleneck — weighted layer score"
    elif score >= 45:
        return "Orange", "Emerging — acceleration building across layer"
    elif score < 25:
        return "Blue", "Cooling"
    else:
        return "Green", "Neutral — healthy growth, no constraint pressure"
 
 
def _red_reality_check(
    layer_scores: list,
    market_data_tickers: list,
    color: str,
) -> tuple[str, str]:
    """
    A layer cannot be Red if its two largest companies by market cap
    are both rated C or D.
 
    Rationale: NVDA + AMD make up ~90% of Semicon layer market cap.
    If both are decelerating, the layer is not a bottleneck regardless
    of what ARM or CDNS are doing.
 
    Returns (color, status) — possibly downgraded from Red to Orange.
    """
    if color != "Red":
        return color, ""
 
    # Build market-cap lookup from raw market data
    mcap_lookup = {}
    for t in (market_data_tickers or []):
        if t and t.get("ticker"):
            mcap_lookup[t["ticker"]] = t.get("market_cap", 0) or 0
 
    # Sort scored tickers by market cap descending
    sorted_by_mcap = sorted(
        layer_scores,
        key=lambda x: mcap_lookup.get(x.get("ticker", ""), 0),
        reverse=True
    )
 
    top2 = sorted_by_mcap[:2]
    if len(top2) < 2:
        return color, ""   # not enough data to check
 
    top2_ratings = [t.get("rating", "C") for t in top2]
    top2_tickers = [t.get("ticker", "?") for t in top2]
    top2_quality = [RATING_QUALITY.get(r, 2) for r in top2_ratings]
 
    # Both top-2 companies rated C or D → downgrade layer from Red to Orange
    if all(q <= 2 for q in top2_quality):
        reason = (
            f"Downgraded Red→Orange: dominant companies "
            f"{top2_tickers[0]}({top2_ratings[0]}) and "
            f"{top2_tickers[1]}({top2_ratings[1]}) not confirming bottleneck"
        )
        log.info(f"  ⚠️  Red reality check fired: {reason}")
        return "Orange", reason
 
    return color, ""
 
 
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
    """
    Score every ticker, then build a market-cap weighted layer score.
 
    v2 fix: Layer score = weighted average of all ticker scores by market cap.
    Previously: layer score = best single ticker score (caused ARM to inflate Semicon).
    """
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
                # Preserve ticker metadata for render stage
                result["ticker"]           = ticker_data.get("ticker", "")
                result["name"]             = ticker_data.get("name", "")
                result["price"]            = ticker_data.get("price", 0)
                result["price_30d_return"] = ticker_data.get("price_30d_return", 0)
                result["vol_spike"]        = ticker_data.get("vol_spike", 1.0)
                result["news_velocity"]    = ticker_data.get("news_velocity", 0)
                result["market_cap"]       = ticker_data.get("market_cap", 0) or 0
                layer_scores.append(result)
            except Exception as e:
                log.warning(f"  {layer_id}/{ticker_data.get('ticker','?')}: {e}")
                continue
 
        if not layer_scores:
            continue
 
        # ── Market-cap weighted layer score ───────────────────────────────────
        total_mcap = sum(t.get("market_cap", 0) or 0 for t in layer_scores)
 
        if total_mcap > 0:
            weighted_score = sum(
                t["score"] * ((t.get("market_cap", 0) or 0) / total_mcap)
                for t in layer_scores
            )
        else:
            # Fallback: equal weight if no market cap data
            weighted_score = sum(t["score"] for t in layer_scores) / len(layer_scores)
 
        weighted_score = round(min(100, max(0, weighted_score)), 2)
 
        # ── Determine layer color from weighted score ──────────────────────────
        # Use the fund_delta of the highest-scoring ticker as a tiebreaker
        best_ticker  = max(layer_scores, key=lambda x: x["score"])
        top_delta    = best_ticker.get("fund_delta")
        layer_color, layer_status = _layer_color_from_score(weighted_score, top_delta)
 
        # ── Red reality check ─────────────────────────────────────────────────
        layer_color, reality_status = _red_reality_check(
            layer_scores, tickers_data, layer_color
        )
        if reality_status:
            layer_status = reality_status
 
        # ── Build best ticker (highest individual score, for detail panels) ───
        # Best ticker is still the highest individual scorer — used for
        # company-level display. Layer color is now separate from best ticker.
        best = dict(best_ticker)
        best["color"]  = layer_color    # layer color propagates to best for render compat
        best["score"]  = weighted_score # layer weighted score replaces individual score
        best["status"] = layer_status
 
        results[layer_id] = {
            "best":        best,
            "all_tickers": layer_scores,
            "layer_id":    layer_id,
            "weighted_score": weighted_score,
            "layer_color":    layer_color,
        }
 
        log.info(
            f"  {layer_id}: weighted={weighted_score:.1f} "
            f"color={layer_color} "
            f"(best_ticker={best_ticker['ticker']} score={best_ticker['score']:.1f})"
        )
 
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
 
 
def stage_render(scored_data: dict, analysis: str, macro_data: dict,
                 market_data: dict = None, radar_data: list = None):
    log.info("[4/4] Rendering and delivering report...")
    try:
        from render import generate_dashboard, deliver
        html_path = generate_dashboard(
            scored_data, analysis, macro_data,
            market_data or {}, radar_data or []
        )
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
            {k: {
                "weighted_score": v.get("weighted_score", v["best"]["score"]),
                "color":          v.get("layer_color", v["best"]["color"]),
                "best_ticker":    v["best"]["ticker"],
                "best_score":     round(max(t["score"] for t in v["all_tickers"]), 1),
             }
             for k, v in scored_data.items()
            }, indent=2))
        return
 
    # Stage 2.5 — Next Nvidia Radar
    log.info("[2.5/4] Running Next Nvidia Radar...")
    try:
        from next_nvidia import run_radar, load_cached_radar
        radar_data = load_cached_radar()
        if not radar_data:
            radar_data = run_radar(verbose=False)
        log.info(f"  Radar: top pick = {radar_data[0]['ticker'] if radar_data else 'none'}")
    except Exception as e:
        log.warning(f"  Radar failed: {e}")
        radar_data = []
 
    analysis = stage_analyze(scored_data, market_data)
    stage_render(scored_data, analysis, macro_data, market_data, radar_data)
 
    # Auto-publish to GitHub Pages
    try:
        import importlib.util
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
 
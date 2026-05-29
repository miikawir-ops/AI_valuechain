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
 
v3 scoring:
  - Layer score: market-cap weighted composite of all tickers
  - Bottleneck leader boost: floor at Orange if any ticker has fund_delta > 0.40
  - Red reality check: dominant C/D companies downgrade Red → Orange
  - 3-day color confirmation: color requires confirmation across multiple days
    to prevent single-day noise from flipping the dashboard signal
  - Extreme signal override: score > 80 allows instant Red without confirmation
"""
 
import json
import logging
import argparse
import datetime
import schedule
import time
from pathlib import Path
 
SCORES_HISTORY_FILE = "scores_history.json"
 
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
 
# ── Score history helpers ─────────────────────────────────────────────────────
 
def _load_recent_layer_scores(layer_id: str, days: int = 3) -> list[float]:
    """
    Load the last N days of weighted scores for a specific layer
    from scores_history.json. Used for color confirmation.
    Returns list of scores, most recent last. Empty list if no history.
    """
    p = Path(SCORES_HISTORY_FILE)
    if not p.exists():
        return []
    try:
        history = json.loads(p.read_text())
        if not isinstance(history, list):
            return []
        today = datetime.datetime.now().strftime("%Y-%m-%d")
        # Exclude today (not yet saved) — look at previous days only
        prev_days = [e for e in history if e.get("date") != today]
        recent = prev_days[-days:]
        return [
            e.get("scores", {}).get(layer_id, {}).get("score", 0)
            for e in recent
        ]
    except Exception:
        return []
 
 
def _confirmed_color(
    today_score: float,
    today_color: str,
    layer_id: str,
    top_fund_delta: float | None,
) -> tuple[str, str]:
    """
    3-day color confirmation system.
 
    A color change requires confirmation across multiple daily runs to prevent
    single-day data noise from flipping the dashboard signal.
 
    Rules:
      INSTANT RED   — today_score > 80 (extreme signal, no confirmation needed)
                      OR top_fund_delta > 0.60 (very strong fundamental acceleration)
      CONFIRMED RED — today > 65 AND 2+ of last 3 days also > 55
      CONFIRMED ORANGE — today >= 45 AND 2+ of last 3 days also >= 40
      CONFIRMED BLUE — today < 25 AND 2+ of last 3 days also < 30
      DEFAULT GREEN — if today's signal can't be confirmed by history
 
    When history is unavailable (first runs), falls back to today_color directly
    so the dashboard still works from day 1.
 
    Returns (confirmed_color, confirmation_note)
    """
    recent = _load_recent_layer_scores(layer_id, days=3)
 
    # No history yet — trust today's score directly (first days of running)
    if len(recent) < 2:
        note = "unconfirmed (insufficient history — building baseline)"
        log.debug(f"  {layer_id}: {today_color} unconfirmed — only {len(recent)} history days")
        return today_color, note
 
    # ── Instant Red override — extreme signals don't need confirmation ────────
    if today_score > 80:
        log.info(f"  {layer_id}: instant Red — extreme score {today_score:.1f} > 80")
        return "Red", f"Instant Red — extreme score {today_score:.1f}"
 
    if top_fund_delta and top_fund_delta > 0.60:
        log.info(f"  {layer_id}: instant Red — fund_delta {top_fund_delta:.2f} > 0.60")
        return "Red", f"Instant Red — strong fundamental acceleration delta={top_fund_delta:.2f}"
 
    # ── 3-day confirmation checks ─────────────────────────────────────────────
    days_above_55  = sum(1 for s in recent if s > 55)
    days_above_40  = sum(1 for s in recent if s >= 40)
    days_below_30  = sum(1 for s in recent if s < 30)
 
    if today_score > 65 and days_above_55 >= 2:
        return "Red", f"Confirmed Red — {days_above_55}/3 recent days above 55"
 
    if today_score >= 45 and days_above_40 >= 2:
        return "Orange", f"Confirmed Orange — {days_above_40}/3 recent days above 40"
 
    if today_score < 25 and days_below_30 >= 2:
        return "Blue", f"Confirmed Blue — {days_below_30}/3 recent days below 30"
 
    # ── No confirmation — hold previous confirmed color if available ───────────
    # This prevents oscillation: if yesterday was Red but today dipped to 60,
    # we don't immediately drop to Orange — we hold Red until confirmed otherwise
    prev_colors = []
    try:
        p = Path(SCORES_HISTORY_FILE)
        if p.exists():
            history = json.loads(p.read_text())
            today_str = datetime.datetime.now().strftime("%Y-%m-%d")
            prev = [e for e in history if e.get("date") != today_str]
            prev_colors = [
                e.get("scores", {}).get(layer_id, {}).get("color", "")
                for e in prev[-3:]
                if e.get("scores", {}).get(layer_id, {}).get("color")
            ]
    except Exception:
        prev_colors = []
 
    if prev_colors:
        # Most common color in last 3 days — hold it if borderline today
        from collections import Counter
        dominant = Counter(prev_colors).most_common(1)[0][0]
        # Only hold if today's score is within 10 points of the threshold
        threshold_gap = {
            "Red":    today_score - 65,    # negative = below Red threshold
            "Orange": today_score - 45,
            "Green":  0,
            "Blue":   25 - today_score,    # negative = above Blue threshold
        }.get(dominant, 0)
 
        if -10 <= threshold_gap <= 5:
            log.info(f"  {layer_id}: holding {dominant} (borderline score {today_score:.1f}, "
                     f"prev dominant={dominant})")
            return dominant, f"Holding {dominant} — borderline score, prev 3d dominant"
 
    # Fall through to today's raw color
    note = f"Unconfirmed — score {today_score:.1f} not sustained in history"
    log.info(f"  {layer_id}: {today_color} → Green (unconfirmed, fallback)")
    # Default to Green when signal can't be confirmed either way
    if 25 <= today_score < 45:
        return "Green", note
    return today_color, note
 
 
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
 
        # ── Bottleneck leader boost ───────────────────────────────────────────
        # One company can be the genuine bottleneck even if layer peers aren't.
        # If the highest-delta ticker has fund_delta > 0.40 (strong acceleration)
        # but the weighted score is dragged below 45 by diversified peers,
        # floor the layer at Orange (45) so the signal isn't lost.
        # Example: CEG (+69% delta) in Energy layer alongside GEV/ETN (negative delta).
        best_by_delta = max(layer_scores, key=lambda x: (x.get("fund_delta") or 0))
        top_fund_delta = best_by_delta.get("fund_delta") or 0
        if top_fund_delta > 0.40 and weighted_score < 45:
            old_score = weighted_score
            weighted_score = max(weighted_score, 45.0)
            log.info(f"  ↑ Bottleneck leader boost: {best_by_delta.get('ticker','?')} "
                     f"delta={top_fund_delta:.2f} → floor 45 (was {old_score:.1f})")
 
        weighted_score = round(min(100, max(0, weighted_score)), 2)
 
        # ── Determine layer color from weighted score ──────────────────────────
        # Use fund_delta of the highest-delta ticker (not highest score)
        # so the bottleneck leader drives color when peers dilute the average
        best_ticker  = max(layer_scores, key=lambda x: x["score"])
        top_delta    = best_by_delta.get("fund_delta")   # use best_by_delta, not best_ticker
        layer_color, layer_status = _layer_color_from_score(weighted_score, top_delta)
 
        # ── Red reality check ─────────────────────────────────────────────────
        layer_color, reality_status = _red_reality_check(
            layer_scores, tickers_data, layer_color
        )
        if reality_status:
            layer_status = reality_status
 
        # ── 3-day color confirmation ──────────────────────────────────────────
        # Prevents single-day noise from flipping the dashboard color.
        # Colors must be sustained across multiple runs to be confirmed.
        # Extreme signals (score > 80 or fund_delta > 0.60) bypass confirmation.
        layer_color, confirm_note = _confirmed_color(
            weighted_score, layer_color, layer_id, top_fund_delta
        )
        if confirm_note:
            layer_status = confirm_note
 
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
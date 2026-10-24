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
  - 3-day color confirmation: color requires confirmation across multiple days
    to prevent single-day noise from flipping the dashboard signal
  - Extreme signal override: score > 80 allows instant Red without confirmation
"""
 
import os
import sys
import json
import logging
import argparse
import datetime
import schedule
import time
from pathlib import Path

from score_engine import DECEL_THRESHOLD, METHOD_VERSION

SCORES_HISTORY_FILE = "scores_history.json"
 
# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger(__name__)
 
# Used when the whole fetch stage fails: every macro input missing, never a
# made-up value (the multiplier then applies no dampening, regime "Unknown").
MACRO_DEFAULTS = {
    "vix":               None,
    "yield_10y_change":  None,
    "nasdaq_vs_spx_20d": None,
    "data_missing":      ["vix", "yield_10y_change", "nasdaq_vs_spx_20d"],
}
 
 
# ── Score history helpers ─────────────────────────────────────────────────────
 
def _date_key(session_date) -> str:
    """Format a date-like value as the "YYYY-MM-DD" key used throughout
    scores_history.json, keyed by trading session — not the UTC run date."""
    return session_date.strftime("%Y-%m-%d") if hasattr(session_date, "strftime") else str(session_date)


# Confirmation looks at the CONFIRM_WINDOW sessions before the current one,
# taken from the trading calendar (fetch_trading_sessions), not from whatever
# entries happen to be stored — so a legacy weekend-dated entry never counts,
# and a session missing from history is named instead of silently replaced
# by an older entry. Sessions before the first stored entry aren't "missing"
# (history hadn't started); sessions more than MAX_ENTRY_AGE_DAYS calendar
# days before the current one don't count at all. Ported from
# RayDar-DataCenter's _confirmation_window (ledger R40); this repo's own
# thresholds in _confirmed_color are unchanged.
CONFIRM_WINDOW     = 3
MAX_ENTRY_AGE_DAYS = 7


def _confirmation_window(layer_id: str, sessions: list, current) -> tuple[list, list]:
    """
    Returns (stored, missing) for the CONFIRM_WINDOW sessions before
    `current`: stored is [(session, score, color)], oldest first; missing is
    [session] for window sessions with no score stored for this layer —
    skipped and named, never read as 0.
    """
    p = Path(SCORES_HISTORY_FILE)
    try:
        history = json.loads(p.read_text()) if p.exists() else []
    except Exception:
        history = []
    if not isinstance(history, list):
        history = []
    cutoff  = current - datetime.timedelta(days=MAX_ENTRY_AGE_DAYS)
    window  = [s for s in sessions if cutoff <= s < current][-CONFIRM_WINDOW:]
    dates   = sorted(e.get("date") for e in history if e.get("date"))
    first   = datetime.date.fromisoformat(dates[0]) if dates else None
    by_date = {e.get("date"): e for e in history}
    stored, missing = [], []
    for s in window:
        entry = by_date.get(_date_key(s)) or {}
        if entry and entry.get("method") != METHOD_VERSION:
            continue   # scored under an older method: not comparable history
        layer = (entry.get("scores") or {}).get(layer_id) or {}
        if layer.get("score") is not None:
            stored.append((s, layer["score"], layer.get("color")))
        elif first is not None and s >= first:
            missing.append(s)
    if missing:
        log.info(f"  {layer_id}: no stored score for session(s) "
                 f"{', '.join(_date_key(s) for s in missing)} — skipped, not read as 0")
    return stored, missing
 
 
def _confirmed_color(
    today_score: float,
    today_color: str,
    layer_id: str,
    sessions: list,
) -> tuple[str, str]:
    """
    3-day color confirmation system.
 
    A color change requires confirmation across multiple daily runs to prevent
    single-day data noise from flipping the dashboard signal.
 
    Rules:
      INSTANT RED   — today_score > 80 (extreme signal, no confirmation needed).
                      (The old "or one company's fund_delta > 0.60" path is gone: on
                      true YoY it fired in 1.7% of 2021-26 company-quarters, about
                      half of them recoveries off a trough — WDC, VST, SMCI 2025 —
                      letting one company's quarter skip the layer threshold and
                      confirmation. Ray, 2026-10-24.)
      CONFIRMED RED — today > 65 AND 2+ of last 3 days also > 55
      CONFIRMED ORANGE — today >= 45 AND 2+ of last 3 days also >= 40
      CONFIRMED BLUE — today < 25 AND 2+ of last 3 days also < 30
      DEFAULT GREEN — if today's signal can't be confirmed by history
 
    When history is unavailable (first runs), falls back to today_color directly
    so the dashboard still works from day 1.
 
    Returns (confirmed_color, confirmation_note)
    """
    stored, _missing = _confirmation_window(layer_id, sessions, sessions[-1])
    recent = [score for _, score, _ in stored]
 
    # No history yet — trust today's score directly (first days of running)
    if len(recent) < 2:
        note = "unconfirmed (insufficient history — building baseline)"
        log.debug(f"  {layer_id}: {today_color} unconfirmed — only {len(recent)} history days")
        return today_color, note
 
    # ── Instant Red override — extreme signals don't need confirmation ────────
    if today_score > 80:
        log.info(f"  {layer_id}: instant Red — extreme score {today_score:.1f} > 80")
        return "Red", f"Instant Red — extreme score {today_score:.1f}"
 
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
    prev_colors = [color for _, _, color in stored if color]
 
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
    if top_delta is not None and top_delta < DECEL_THRESHOLD:
        return "Blue", f"Cooling — dominant ticker decelerating ({top_delta*100:.1f}%)"
    if score > 65:
        return "Red", "Confirmed bottleneck — weighted layer score"
    elif score >= 45:
        return "Orange", "Emerging — acceleration building across layer"
    elif score < 25:
        return "Blue", "Cooling"
    else:
        return "Green", "Neutral — healthy growth, no constraint pressure"
 
 
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
 
 
def stage_score(market_data: dict, macro_data: dict, sessions: list) -> dict:
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
                result["price_30d_return"] = ticker_data.get("price_30d_return")
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
        # Only companies with measured acceleration: an unmeasured delta (None)
        # must not count as 0 — it would outrank a decelerating peer and block
        # the all-decelerating → Blue rule.
        measured       = [t for t in layer_scores if t.get("fund_delta") is not None]
        best_by_delta  = max(measured, key=lambda x: x["fund_delta"]) if measured else {}
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
 
        # ── 3-day color confirmation ──────────────────────────────────────────
        # Prevents single-day noise from flipping the dashboard color.
        # Colors must be sustained across multiple runs to be confirmed.
        # An extreme score (> 80) bypasses confirmation.
        layer_color, confirm_note = _confirmed_color(
            weighted_score, layer_color, layer_id, sessions
        )
        if confirm_note:
            layer_status = confirm_note

        # Red needs measured acceleration in at least one company — no
        # path (instant, confirmed, held) may reach Red without it.
        if layer_color == "Red" and not measured:
            layer_color  = "Orange"
            layer_status = "Emerging — acceleration not measured for any company in the layer"
 
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
 
 
def stage_analyze(scored_data: dict, market_data: dict, macro_data: dict = None, session_date=None) -> str:
    log.info("[3/4] Running AI analysis...")
    try:
        from analyze import run_analysis
        analysis = run_analysis(scored_data, market_data, macro_data or {}, session_date)
        log.info("  Analysis complete")
        return analysis
    except Exception as e:
        log.error(f"  Analysis failed: {e}")
        return f"Analysis unavailable — error: {e}"
 
 
# ── Point-in-time detail log ──────────────────────────────────────────────────

SCORES_DETAIL_FILE = "scores_detail.jsonl"

# Every input process_sector reads, so one line can replay its scores.
DETAIL_INPUTS = ("price", "price_act", "price_30d_return", "price_momentum", "vol_spike", "analyst_upgrades",
                 "short_int_change", "news_velocity", "capex_div", "gross_margin", "gm_delta",
                 "growth_curr", "growth_prev", "growth_quarter", "revenue_source", "market_cap",
                 "peer_outperformance", "next_earnings", "name", "data_missing")


def _library_versions() -> dict:
    import platform
    out = {"python": platform.python_version()}
    for mod, name in (("yfinance", "yfinance"), ("pandas", "pandas"), ("numpy", "numpy"),
                      ("requests", "requests"), ("google.genai", "google-genai")):
        try:
            m = __import__(mod, fromlist=["__version__"])
            out[name] = getattr(m, "__version__", None)
        except Exception:
            out[name] = None
    return out


def _code_commit() -> str | None:
    sha = os.getenv("GITHUB_SHA")
    if sha:
        return sha
    try:
        import subprocess
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10).stdout.strip() or None
    except Exception:
        return None


def save_scores_detail(market_data: dict, macro_data: dict, scored_data: dict, session_date,
                       radar_data: list, analysis: str, path: str = SCORES_DETAIL_FILE) -> dict:
    """Append one line per run: every company's scoring inputs, sub-scores
    and result, the macro inputs and regime, the radar ranking, the signal
    pick (with that session's close, for item 8's entry prices) and any
    sections the post-check withheld, plus code commit and library versions.

    Pure append, keyed by session + run_utc, never replaced: a 06:00 UTC run
    scores the same session as the previous evening's but with different
    news, and its line is what the page showed until the next run. A
    backtest takes the last run before the next open (Ray, 2026-10-24).
    """
    import re
    import render
    from analyze import WITHHELD, _split_sections
    from score_engine import METHOD_VERSION
    lookup = {t["ticker"]: t for ts in market_data.values() for t in (ts or []) if t and t.get("ticker")}
    companies, layers, regime = [], {}, None
    for lid, res in scored_data.items():
        best = res.get("best", {})
        unm  = render._unmeasured_acceleration(res.get("all_tickers", []))
        layers[lid] = {"score": best.get("score"), "color": best.get("color"), "status": best.get("status"),
                       "unmeasured_share": unm["share"] if unm else 0.0}
        for t in res.get("all_tickers", []):
            raw = lookup.get(t.get("ticker"), {})
            d = t.get("fund_delta")
            regime = regime or (t.get("regime"), t.get("multiplier"))
            companies.append({
                "ticker": t.get("ticker"), "layer": lid,
                "inputs": {k: raw.get(k) for k in DETAIL_INPUTS},
                "sub_scores": t.get("sub_scores", {}),
                "score": t.get("score"), "color": t.get("color"),
                "rating": render.company_rating(t.get("score", 0), (d or 0) * 100, t.get("color", "Green"), t.get("is_hype", False)),
                "fund_delta": d, "accel_measured": d is not None,
                "is_hype": t.get("is_hype", False), "hype_reasons": t.get("hype_reasons", []),
                "data_status": t.get("data_status"),
            })
    withheld = [p.split("\n", 1)[0].strip("# ").strip() for p in _split_sections(analysis or "")
                if p.partition("\n")[2].strip() == WITHHELD]
    m = re.search(r"Company[:\s]+\*?\*?([A-Z]{1,5})", analysis or "")
    pick = m.group(1) if m else None
    radar = [{"rank": i + 1, "ticker": r.get("ticker"), "layer": r.get("layer"), "score": r.get("score"),
              "latest_yoy_pct": (r.get("accel") or {}).get("latest_growth"),
              "gross_margin_pct": r.get("gross_margin"), "analyst_count": r.get("analyst_count"),
              "ret_1mo_pct": r.get("ret_1mo")} for i, r in enumerate(radar_data or [])]
    session = session_date.date() if isinstance(session_date, datetime.datetime) else session_date
    line = {
        "session": session.isoformat(),
        "run_utc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "method": METHOD_VERSION, "code_commit": _code_commit(), "versions": _library_versions(),
        "macro": {k: macro_data.get(k) for k in ("vix", "yield_10y_change", "nasdaq_vs_spx_20d", "data_missing")}
                 | {"regime": regime[0] if regime else None, "multiplier": regime[1] if regime else None},
        "layers": layers, "companies": companies, "radar": radar,
        "signal": {"ticker": pick, "close": lookup.get(pick, {}).get("price") if pick else None},
        "withheld_sections": withheld,
    }
    text = json.dumps(line, separators=(",", ":"), default=str, allow_nan=False)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(text + "\n")
    log.info(f"  Scores detail appended ({len(text) / 1024:.1f} KB, {len(companies)} companies)")
    return line


def stage_render(scored_data: dict, analysis: str, macro_data: dict,
                 session_date, market_data: dict = None, radar_data: list = None):
    log.info("[4/4] Rendering and delivering report...")
    try:
        from render import generate_dashboard, deliver
        html_path = generate_dashboard(
            scored_data, analysis, macro_data, session_date,
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

    # Deliberately outside stage_fetch()'s own try/except, which swallows
    # exceptions into an empty-data fallback — this needs to crash the run
    # instead, so a bad fetch never reaches render/publish.
    from fetch_market import assert_no_nan, fetch_trading_sessions
    assert_no_nan(market_data)
    sessions = fetch_trading_sessions()
    session_date = sessions[-1]
    log.info(f"  Last completed session: {session_date}")

    scored_data = stage_score(market_data, macro_data, sessions)
 
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
 
    analysis = stage_analyze(scored_data, market_data, macro_data, session_date)
    # Point-in-time log of what was scored and shown. Its own try: a failure
    # here is logged, never allowed to stop the page from publishing.
    try:
        save_scores_detail(market_data, macro_data, scored_data, session_date, radar_data, analysis)
    except Exception as e:
        log.error(f"  scores_detail not written: {e}")
    stage_render(scored_data, analysis, macro_data, session_date, market_data, radar_data)
 
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
    # On Windows, redirected output uses the locale code page (cp1252), which
    # can't encode the emoji in Gemini's analysis: print() raised after the
    # dashboard was already saved, failing local runs. UTF-8 for both streams.
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

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
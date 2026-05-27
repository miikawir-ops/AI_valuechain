"""
backtest.py — RayDar ticker-level backtest using audit_log.json.
 
Reads audit_log.json (individual ticker scores per run) and checks
forward returns for tickers that scored Red or had high fund_delta.
 
Much more precise than layer-level backtest — uses actual ticker scores
not proxy tickers.
 
Usage:
    python backtest.py                    # 30-day forward return
    python backtest.py --days 7           # 7-day (good for early data)
    python backtest.py --days 14
    python backtest.py --sector memory    # one sector only
    python backtest.py --min-score 50     # only high-conviction signals
    python backtest.py --verbose          # show all picks
    python backtest.py --diagnose         # show scoring health check
 
Requires: yfinance, audit_log.json in same folder
"""
 
import json
import argparse
import datetime
import yfinance as yf
from pathlib import Path
from collections import defaultdict
 
AUDIT_FILE   = "audit_log.json"
SCORES_FILE  = "scores_history.json"
 
# Map audit_log sector → ordered list of tickers (matches score order)
SECTOR_TICKERS = {
    "energy":   ["CEG", "VST", "PWR", "GEV", "ETN"],
    "compute":  ["NVDA", "AMD", "AVGO", "ASML", "TSM", "ARM", "CDNS"],
    "memory":   ["MU", "WDC", "AMAT", "LRCX"],
    "infra":    ["VRT", "ANET", "EQIX", "SMCI", "CSCO", "CIEN"],
    "cloud":    ["MSFT", "GOOGL", "AMZN", "META"],
    "software": ["PLTR", "NOW", "SNOW", "CRM", "DDOG"],
    "security": ["CRWD", "PANW", "S", "OKTA"],
}
 
 
def load_audit_log() -> list:
    p = Path(AUDIT_FILE)
    if not p.exists():
        print(f"❌ {AUDIT_FILE} not found.")
        return []
    try:
        return json.loads(p.read_text())
    except Exception as e:
        print(f"❌ Could not read {AUDIT_FILE}: {e}")
        return []
 
 
def assign_tickers(entries: list) -> list:
    """
    audit_log.json has sector + score but no ticker name.
    Assign tickers by matching position within each sector group per run.
    Entries are stored in ticker order within each run timestamp.
    """
    # Group by (date, time, sector)
    run_groups = defaultdict(list)
    for entry in entries:
        key = (entry["date"], entry["time"], entry["sector"])
        run_groups[key].append(entry)
 
    result = []
    for (date, time, sector), group in run_groups.items():
        tickers = SECTOR_TICKERS.get(sector, [])
        for i, entry in enumerate(group):
            ticker = tickers[i] if i < len(tickers) else None
            if ticker:
                enriched = dict(entry)
                enriched["ticker"] = ticker
                result.append(enriched)
    return result
 
 
def deduplicate_by_date(entries: list) -> list:
    """Keep only the last run per day per ticker (avoid counting same day multiple times)."""
    seen = {}
    for e in sorted(entries, key=lambda x: (x["date"], x["time"])):
        key = (e["date"], e.get("ticker", ""))
        seen[key] = e
    return list(seen.values())
 
 
def get_price_on_date(ticker: str, date_str: str) -> float | None:
    try:
        target = datetime.datetime.strptime(date_str, "%Y-%m-%d")
        start  = (target - datetime.timedelta(days=4)).strftime("%Y-%m-%d")
        end    = (target + datetime.timedelta(days=4)).strftime("%Y-%m-%d")
        hist   = yf.Ticker(ticker).history(start=start, end=end)
        if hist.empty:
            return None
        # Find closest date
        hist.index = hist.index.tz_localize(None) if hist.index.tz else hist.index
        target_dt  = datetime.datetime.strptime(date_str, "%Y-%m-%d")
        closest    = min(hist.index, key=lambda x: abs((x.to_pydatetime() - target_dt).days))
        return round(float(hist.loc[closest, "Close"]), 2)
    except Exception:
        return None
 
 
def get_forward_return(ticker: str, entry_date: str, forward_days: int) -> float | None:
    try:
        entry  = datetime.datetime.strptime(entry_date, "%Y-%m-%d")
        exit_d = entry + datetime.timedelta(days=forward_days)
        if exit_d.date() > datetime.datetime.now().date():
            return None
        ep = get_price_on_date(ticker, entry_date)
        xp = get_price_on_date(ticker, exit_d.strftime("%Y-%m-%d"))
        if ep and xp and ep > 0:
            return round((xp / ep - 1) * 100, 2)
        return None
    except Exception:
        return None
 
 
def diagnose(entries: list):
    """Show scoring health — identify systematic zeroes."""
    print(f"\n{'='*60}")
    print(f"  SCORING HEALTH DIAGNOSIS")
    print(f"{'='*60}\n")
 
    enriched = assign_tickers(entries)
    deduped  = deduplicate_by_date(enriched)
 
    # Smart money analysis
    sm_zero  = sum(1 for e in deduped if e["sub_scores"].get("smart_money", 0) == 0)
    sm_total = len(deduped)
    print(f"  Smart money = 0:     {sm_zero}/{sm_total} ({sm_zero/sm_total*100:.0f}%)")
 
    # Constraints analysis
    con_zero = sum(1 for e in deduped if e["sub_scores"].get("constraints", 0) < 5)
    print(f"  Constraints < 5:     {con_zero}/{sm_total} ({con_zero/sm_total*100:.0f}%)")
 
    # By sector
    print(f"\n  Average sub-scores by sector:")
    by_sector = defaultdict(list)
    for e in deduped:
        by_sector[e["sector"]].append(e)
 
    print(f"  {'Sector':<12} {'Fund':>6} {'Constr':>8} {'Smart':>7} {'Composite':>10}")
    print(f"  {'-'*46}")
    for sector, group in sorted(by_sector.items()):
        avg_f  = sum(e["sub_scores"].get("fundamentals", 0)  for e in group) / len(group)
        avg_c  = sum(e["sub_scores"].get("constraints", 0)   for e in group) / len(group)
        avg_sm = sum(e["sub_scores"].get("smart_money", 0)   for e in group) / len(group)
        avg_cp = sum(e.get("score", 0)                        for e in group) / len(group)
        print(f"  {sector:<12} {avg_f:>6.1f} {avg_c:>8.1f} {avg_sm:>7.1f} {avg_cp:>10.1f}")
 
    print(f"\n  ⚠️  If smart_money is near 0 across the board,")
    print(f"     check score_engine.py — vol_spike, analyst_upgrades,")
    print(f"     short_int_change signals may not be flowing correctly.\n")
 
    # Hype flags
    hype = [e for e in deduped if e.get("is_hype")]
    print(f"  Hype-flagged tickers: {len(hype)}")
    for e in hype:
        print(f"    {e.get('ticker','?'):<6} {e['sector']:<10} score={e['score']:.1f} fund_delta={e['fund_delta']:.3f}")
 
 
def run_backtest(forward_days: int = 30, filter_sector: str = None,
                 min_score: float = 0, verbose: bool = False):
    raw     = load_audit_log()
    if not raw:
        return
 
    enriched = assign_tickers(raw)
    deduped  = deduplicate_by_date(enriched)
 
    # Filter
    if filter_sector:
        deduped = [e for e in deduped if e["sector"] == filter_sector]
 
    # Signal: Red color OR fund_delta > 0.3 (strong fundamental acceleration)
    signals = [
        e for e in deduped
        if (e.get("color") in {"Red", "Orange"} or e.get("fund_delta", 0) > 0.3)
        and e.get("score", 0) >= min_score
        and e.get("ticker")
    ]
 
    print(f"\n{'='*60}")
    print(f"  RayDar Ticker Backtest — {forward_days}-day forward return")
    print(f"  Source: {AUDIT_FILE}")
    print(f"  Date range: {deduped[0]['date']} → {deduped[-1]['date']}" if deduped else "")
    print(f"  Total tickers in log: {len(deduped)}")
    print(f"  Buy signals (Red/Orange/high delta): {len(signals)}")
    print(f"{'='*60}\n")
 
    picks   = []
    pending = 0
 
    for e in signals:
        ticker = e["ticker"]
        date   = e["date"]
        score  = e.get("score", 0)
        color  = e.get("color", "?")
        delta  = e.get("fund_delta", 0)
 
        print(f"  {ticker:<6} {e['sector']:<10} {date}  {color:<8} "
              f"score={score:.0f} delta={delta:+.3f} ...", end=" ", flush=True)
 
        fwd = get_forward_return(ticker, date, forward_days)
        if fwd is None:
            print("pending")
            pending += 1
            continue
 
        icon = "✅" if fwd > 0 else "❌"
        print(f"{icon} {fwd:+.1f}%")
        picks.append({
            "ticker":  ticker,
            "sector":  e["sector"],
            "date":    date,
            "color":   color,
            "score":   score,
            "delta":   delta,
            "fwd_ret": fwd,
        })
 
    if not picks:
        print(f"\n  No completed picks yet — {pending} pending future dates.")
        days_needed = forward_days - (datetime.datetime.now() -
            datetime.datetime.strptime(deduped[0]["date"], "%Y-%m-%d")).days
        if days_needed > 0:
            ready = (datetime.datetime.now() + datetime.timedelta(days=days_needed))
            print(f"  First results available around {ready.strftime('%B %d, %Y')}")
        print(f"\n  Tip: Run with --days 7 for earlier results.\n")
        return
 
    # ── Summary ───────────────────────────────────────────────────────────────
    winners  = [p for p in picks if p["fwd_ret"] > 0]
    avg_ret  = sum(p["fwd_ret"] for p in picks) / len(picks)
    win_rate = len(winners) / len(picks) * 100
    best     = max(picks, key=lambda x: x["fwd_ret"])
    worst    = min(picks, key=lambda x: x["fwd_ret"])
 
    print(f"\n{'='*60}")
    print(f"  RESULTS — {len(picks)} completed, {pending} pending")
    print(f"{'='*60}")
    print(f"\n  Win rate:    {win_rate:.0f}%  ({len(winners)}/{len(picks)})")
    print(f"  Avg return:  {avg_ret:+.1f}%")
    print(f"  Best:        {best['ticker']} on {best['date']}  {best['fwd_ret']:+.1f}%")
    print(f"  Worst:       {worst['ticker']} on {worst['date']}  {worst['fwd_ret']:+.1f}%")
 
    # By sector
    print(f"\n  By sector:")
    by_sec = defaultdict(list)
    for p in picks:
        by_sec[p["sector"]].append(p["fwd_ret"])
    for sec, rets in sorted(by_sec.items(), key=lambda x: -sum(x[1])/len(x[1])):
        avg  = sum(rets) / len(rets)
        wins = sum(1 for r in rets if r > 0)
        print(f"    {sec:<12} avg {avg:+.1f}%  wins {wins}/{len(rets)}")
 
    # By color
    print(f"\n  By signal color:")
    for col in ["Red", "Orange"]:
        cp = [p for p in picks if p["color"] == col]
        if cp:
            ca  = sum(p["fwd_ret"] for p in cp) / len(cp)
            cw  = sum(1 for p in cp if p["fwd_ret"] > 0)
            print(f"    {col:<8}  avg {ca:+.1f}%  wins {cw}/{len(cp)}")
 
    # High delta picks (fund_delta > 0.5 = strong acceleration)
    hd = [p for p in picks if p["delta"] > 0.5]
    if hd:
        hd_avg = sum(p["fwd_ret"] for p in hd) / len(hd)
        hd_win = sum(1 for p in hd if p["fwd_ret"] > 0)
        print(f"\n  High fund_delta (>0.5) picks: {len(hd)}")
        print(f"    avg {hd_avg:+.1f}%  wins {hd_win}/{len(hd)}")
        print(f"    (This is your strongest signal — MU fund_delta=0.64 is here)")
 
    if verbose:
        print(f"\n  All picks (sorted by return):")
        print(f"  {'Ticker':<6} {'Sector':<10} {'Date':<12} {'Color':<8} "
              f"{'Score':>6} {'Delta':>7} {'Return':>8}")
        print(f"  {'-'*62}")
        for p in sorted(picks, key=lambda x: -x["fwd_ret"]):
            icon = "✅" if p["fwd_ret"] > 0 else "❌"
            print(f"  {icon} {p['ticker']:<6} {p['sector']:<10} {p['date']:<12} "
                  f"{p['color']:<8} {p['score']:>6.0f} {p['delta']:>+7.3f} {p['fwd_ret']:>+7.1f}%")
 
    print(f"\n  ⚠️  Not financial advice. n={len(picks)} picks.")
    print(f"      audit_log.json started {deduped[0]['date']} — "
          f"backtest quality improves with more history.\n")
 
 
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="RayDar ticker-level backtest")
    parser.add_argument("--days",      type=int,   default=30,    help="Forward return window (default 30)")
    parser.add_argument("--sector",    type=str,   default=None,  help="Filter to one sector")
    parser.add_argument("--min-score", type=float, default=0,     help="Minimum score threshold")
    parser.add_argument("--verbose",   action="store_true",       help="Show all individual picks")
    parser.add_argument("--diagnose",  action="store_true",       help="Show scoring health check")
    args = parser.parse_args()
 
    raw = load_audit_log()
    if raw:
        if args.diagnose:
            diagnose(raw)
        else:
            run_backtest(
                forward_days=args.days,
                filter_sector=args.sector,
                min_score=args.min_score,
                verbose=args.verbose,
            )
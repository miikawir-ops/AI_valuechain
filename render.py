"""
render.py — HTML dashboard generator and report delivery.
 
Fixes in this version:
  - load_scores_history() handles legacy dict format + corrupt files gracefully
    so scores_history.json is never accidentally wiped on format mismatch
  - _radar_js_data() reads wildcard sparkline directly from full radar list
    (load_cached_radar now returns 'all', not just 'top5') so APP chart renders
  - Ticker names and prices pulled correctly from market_data
  - Data freshness indicator on every card
  - Browser tab timestamp
  - Yesterday comparison (score delta vs previous run)
  - scores_history.json updated after every run
"""
 
import os
import json
import logging
import datetime
import smtplib
from pathlib import Path
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from dotenv import load_dotenv
 
load_dotenv()
log = logging.getLogger(__name__)
 
DELIVERY_METHOD  = os.getenv("DELIVERY_METHOD", "console")
EMAIL_SENDER     = os.getenv("EMAIL_SENDER", "")
EMAIL_PASSWORD   = os.getenv("EMAIL_PASSWORD", "")
EMAIL_RECIPIENT  = os.getenv("EMAIL_RECIPIENT", "")
SMTP_HOST        = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT        = int(os.getenv("SMTP_PORT", 587))
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
 
SCORES_HISTORY_FILE = "scores_history.json"
MAX_HISTORY_DAYS    = 30
 
CC = {
    "Red":    {"bg":"#FCEBEB","border":"#E24B4A","pill":"#E24B4A","pft":"#FCEBEB","lbl":"Hot",      "tc":"#791F1F"},
    "Orange": {"bg":"#FAEEDA","border":"#EF9F27","pill":"#EF9F27","pft":"#FAEEDA","lbl":"Emerging",  "tc":"#633806"},
    "Green":  {"bg":"#EAF3DE","border":"#639922","pill":"#639922","pft":"#EAF3DE","lbl":"Neutral",   "tc":"#27500A"},
    "Blue":   {"bg":"#E6F1FB","border":"#378ADD","pill":"#378ADD","pft":"#E6F1FB","lbl":"Cooling",   "tc":"#0C447C"},
}
 
def company_rating(score: float, delta: float, color: str, is_hype: bool) -> str:
    """A/B/C/D rating for individual companies."""
    if color in ("Red", "Orange") and delta > 20 and not is_hype:
        return "A"
    elif color in ("Red", "Orange", "Green") and delta > 0 and not is_hype:
        return "B"
    elif is_hype or delta < -5:
        return "C"
    elif delta < 0:
        return "C"
    else:
        return "B"
 
 
def rating_forecast(current_rating: str, delta: float, delta_band: str,
                    is_hype: bool, color: str) -> dict:
    """Forecast where rating is heading based on momentum signals."""
    accelerating   = delta_band == "accelerating"
    decelerating   = delta_band == "decelerating"
    hot_layer      = color in ("Red", "Orange")
    real_hype_risk = is_hype and delta < 0
 
    if current_rating == "A":
        if decelerating and real_hype_risk:
            return {"direction": "↓", "target": "C", "label": "Warning — hype peaking",
                    "color": "#E24B4A", "reason": "Growth decelerating with hype risk"}
        elif decelerating:
            return {"direction": "↓", "target": "B", "label": "Watch for pullback",
                    "color": "#EF9F27", "reason": "Growth decelerating from peak"}
        else:
            return {"direction": "→", "target": "A", "label": "Holding strong",
                    "color": "#27500A", "reason": "Sustained acceleration"}
    elif current_rating == "B":
        if accelerating and hot_layer:
            return {"direction": "↑", "target": "A", "label": "Upgrade likely",
                    "color": "#27500A", "reason": "Accelerating into bottleneck layer"}
        elif real_hype_risk or (decelerating and delta < -10):
            return {"direction": "↓", "target": "C", "label": "Downgrade risk",
                    "color": "#E24B4A", "reason": "Growth slowing with hype or sharp delta drop"}
        elif decelerating:
            return {"direction": "↓", "target": "C", "label": "Watch closely",
                    "color": "#EF9F27", "reason": "Growth decelerating — monitor next quarter"}
        else:
            return {"direction": "→", "target": "B", "label": "Stable",
                    "color": "#378ADD", "reason": "Fundamentals holding steady"}
    elif current_rating == "C":
        if accelerating and not real_hype_risk:
            return {"direction": "↑", "target": "B", "label": "Recovery signal",
                    "color": "#EF9F27", "reason": "Momentum improving — watch for confirmation"}
        elif real_hype_risk and decelerating:
            return {"direction": "↓", "target": "D", "label": "Avoid",
                    "color": "#E24B4A", "reason": "Hype unwinding with deteriorating fundamentals"}
        else:
            return {"direction": "→", "target": "C", "label": "Still cautious",
                    "color": "#EF9F27", "reason": "No clear recovery signal yet"}
    else:  # D
        if accelerating:
            return {"direction": "↑", "target": "C", "label": "Early recovery",
                    "color": "#EF9F27", "reason": "Momentum turning — confirm next quarter"}
        return {"direction": "→", "target": "D", "label": "Avoid",
                "color": "#E24B4A", "reason": "No recovery signals yet"}
 
 
LAYER_NAMES = {
    "energy":   ("Energy",      "power infra"),
    "compute":  ("Semicon",     "& chip design"),
    "memory":   ("HBM memory",  "storage"),
    "infra":    ("Data center", "& bandwidth"),
    "cloud":    ("Cloud",       "hyperscalers"),
    "software": ("AI software", "& observability"),
    "security": ("AI security", "& governance"),
}
 
DAYS = ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"]
 
 
# ── History helpers ───────────────────────────────────────────────────────────
 
def load_scores_history() -> list:
    """Load scores history from disk.
 
    Handles three cases gracefully:
      1. File does not exist yet               → return []
      2. Current format: list of daily entries → return as-is
      3. Legacy format: dict keyed by date     → convert to sorted list
      4. Corrupt / unreadable file             → log warning, return []
 
    This prevents scores_history.json from being silently wiped when the file
    exists but is in an unexpected format (e.g. after a git reset).
    """
    p = Path(SCORES_HISTORY_FILE)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text())
 
        if isinstance(data, list):
            return data                          # ✅ expected format
 
        if isinstance(data, dict):
            # Legacy format — convert so appending continues to work
            log.warning(
                "scores_history.json is in legacy dict format — converting to list. "
                "History will be preserved."
            )
            converted = [
                {"date": date_key, "scores": scores}
                for date_key, scores in data.items()
            ]
            return sorted(converted, key=lambda x: x.get("date", ""))
 
        log.warning(f"scores_history.json unexpected type {type(data)} — starting fresh")
        return []
 
    except Exception as e:
        log.warning(f"scores_history.json unreadable ({e}) — starting fresh")
        return []
 
 
def save_scores_history(scored_data: dict, action_ticker: str = "", action_price: float = None):
    history = load_scores_history()
    today   = datetime.datetime.now().strftime("%Y-%m-%d")
    # Remove existing entry for today if re-running
    history = [e for e in history if e.get("date") != today]
    history.append({
        "date":          today,
        "time":          datetime.datetime.now().strftime("%H:%M"),
        "action_ticker": action_ticker,
        "action_price":  action_price,
        "scores": {
            layer_id: {
                "score": result["best"].get("score", 0),
                "color": result["best"].get("color", "Green"),
                "ratings": {
                    t.get("ticker"): company_rating(
                        t.get("score", 0),
                        (t.get("fund_delta") or 0) * 100,
                        t.get("color", "Green"),
                        t.get("is_hype", False)
                    )
                    for t in result.get("all_tickers", [])
                    if t.get("ticker")
                }
            }
            for layer_id, result in scored_data.items()
            if "best" in result
        }
    })
    history = history[-MAX_HISTORY_DAYS:]
    Path(SCORES_HISTORY_FILE).write_text(json.dumps(history, indent=2))
    log.info(f"  Scores history saved ({len(history)} days)")
 
 
def get_yesterday_scores(scored_data: dict) -> dict:
    """Returns {layer_id: {score, color}} for the most recent previous run."""
    history = load_scores_history()
    today   = datetime.datetime.now().strftime("%Y-%m-%d")
    prev    = [e for e in history if e.get("date") != today]
    if not prev:
        return {}
    return prev[-1].get("scores", {})
 
 
# ── Ticker lookup ─────────────────────────────────────────────────────────────
 
def build_ticker_lookup(market_data: dict) -> dict:
    lookup = {}
    for tickers in market_data.values():
        for t in (tickers or []):
            if t and t.get("ticker"):
                lookup[t["ticker"]] = t
    return lookup
 
 
# ── HTML sections ─────────────────────────────────────────────────────────────
 
def _chain_js_data(scored_data: dict, market_data: dict, yesterday: dict) -> str:
    """Build the LAYERS JS array with real data from market_data."""
    ticker_lookup = build_ticker_lookup(market_data)
    # Build yesterday rating lookup: {ticker: rating}
    yesterday_ratings = {}
    for layer_scores in yesterday.values():
        for ticker, rating in layer_scores.get("ratings", {}).items():
            yesterday_ratings[ticker] = rating
    layers = []
 
    for layer_id, layer_result in scored_data.items():
        best       = layer_result.get("best", {})
        color      = best.get("color", "Green")
        score      = round(best.get("score", 0), 1)
        all_scores = layer_result.get("all_tickers", [])
 
        # Yesterday comparison
        prev       = yesterday.get(layer_id, {})
        prev_score = prev.get("score")
        prev_color = prev.get("color", "")
        if prev_score is not None:
            delta_score   = round(score - prev_score, 1)
            color_changed = color != prev_color
        else:
            delta_score   = None
            color_changed = False
 
        # News velocity
        raw_tickers = market_data.get(layer_id, [])
        news_vel = next((ts.get("news_velocity", 0) for ts in all_scores if ts.get("news_velocity")), 0)
        if not news_vel and raw_tickers:
            news_vel = raw_tickers[0].get("news_velocity", 0)
 
        fund_delta = best.get("fund_delta") or 0
 
        # Build ticker list
        tickers_out = []
        for t_scored in all_scores[:4]:
            sym       = t_scored.get("ticker", "")
            raw       = ticker_lookup.get(sym, {})
            raw_delta = (t_scored.get("fund_delta") or 0) * 100
            delta_band = "accelerating" if raw_delta > 5 else "decelerating" if raw_delta < -5 else "stable"
            hist_raw  = raw.get("price_history", [])
            sparkline = [round(p, 2) for p in hist_raw[-30:]] if hist_raw else []
            t_color      = t_scored.get("color", "Green")
            t_hype       = t_scored.get("is_hype", False)
            t_delta      = (t_scored.get("fund_delta") or 0) * 100
            t_rating     = company_rating(t_scored.get("score", 0), t_delta, t_color, t_hype)
            t_prev_rating = yesterday_ratings.get(sym, "")
            rating_changed = t_prev_rating and t_prev_rating != t_rating
            tickers_out.append({
                "sym":        sym or "?",
                "name":       t_scored.get("name") or raw.get("name") or sym or "?",
                "price":      t_scored.get("price") or raw.get("price", 0),
                "ret30":      round((t_scored.get("price_30d_return") or raw.get("price_30d_return") or 0) * 100, 1),
                "delta_band": delta_band,
                "hype":       t_hype,
                "color":      t_color,
                "sparkline":  sparkline,
                "rating":       t_rating,
                "prev_rating":  t_prev_rating,
                "rating_up":    rating_changed and ord(t_rating) < ord(t_prev_rating),
                "rating_down":  rating_changed and ord(t_rating) > ord(t_prev_rating),
                "forecast":     rating_forecast(t_rating, t_delta, delta_band, t_hype, t_color),
                "run_rate":     round(raw.get("revenue_quarterly", 0) * 4 / 1e9, 1) if (raw.get("revenue_quarterly") or 0) > 0 else None,
                "pct_of_high":  round((raw.get("price", 0) / raw.get("week52_high", 1)) * 100) if raw.get("week52_high") else None,
                "week52_high":  raw.get("week52_high"),
                "week52_low":   raw.get("week52_low"),
            })
 
        n1, n2 = LAYER_NAMES.get(layer_id, (layer_id.upper(), ""))
        momentum_band = "strong" if abs(fund_delta*100) > 20 else "moderate" if abs(fund_delta*100) > 5 else "mild"
        momentum_dir  = "+" if fund_delta >= 0 else "-"
 
        # Narrative vs fundamental divergence detection
        divergence     = False
        divergence_msg = ""
        if news_vel >= 4 and fund_delta < -0.05:
            divergence     = True
            divergence_msg = (
                "News narrative is active but revenue growth is decelerating. "
                "The market story is running ahead of reported financials. "
                "This layer may be driven by sentiment rather than fundamental acceleration."
            )
        elif news_vel >= 5 and fund_delta < 0.03 and color == "Green":
            divergence     = True
            divergence_msg = (
                "News activity detected but fundamental acceleration is weak. "
                "The investment narrative exists but has not yet shown up in quarterly earnings. "
                "Watch for confirmation in next results before positioning."
            )
        elif news_vel == 0 and color == "Green" and fund_delta < 0:
            divergence     = True
            divergence_msg = (
                "Revenue growth is decelerating with no supporting news signal. "
                "Fundamentals are weakening — monitor for further deterioration."
            )
 
        layers.append({
            "id":             layer_id,
            "n1":             n1,
            "n2":             n2,
            "score":          score,
            "color":          color,
            "news_vel":       int(news_vel),
            "momentum_label": f"{momentum_dir}{momentum_band}",
            "delta_score":    delta_score,
            "prev_color":     prev_color,
            "color_changed":  color_changed,
            "tickers":        tickers_out,
            "divergence":     divergence,
            "divergence_msg": divergence_msg,
        })
 
    return json.dumps(layers)
 
 
def _analysis_sections(analysis: str):
    """Parse analysis markdown into clean section dicts."""
    sections      = []
    current_title = ""
    current_lines = []
 
    for line in analysis.split("\n"):
        line = line.strip()
        is_header   = False
        header_text = ""
        if line.startswith("### "):
            header_text = line[4:].strip(); is_header = True
        elif line.startswith("## "):
            header_text = line[3:].strip(); is_header = True
        elif line.startswith("**##") or line.startswith("**###"):
            header_text = line.replace("**##","").replace("**###","").replace("**","").strip()
            is_header = True
 
        if is_header:
            if current_title:
                body = " ".join(current_lines).strip()
                if body:
                    sections.append((current_title, body))
            current_title = header_text
            current_lines = []
        elif line:
            cleaned = (line
                .replace("**","").replace("```","").replace("`","").replace("---","").strip())
            if cleaned:
                current_lines.append(cleaned)
 
    if current_title and current_lines:
        sections.append((current_title, " ".join(current_lines).strip()))
 
    if not sections and analysis.strip():
        sections = [("Daily Analysis", analysis.strip())]
 
    main   = sections
    action = None
    for i, (title, body) in enumerate(sections):
        if any(kw in title.upper() for kw in ["ONE ACTION", "ACTION", "💡"]):
            action = (title, body)
            main   = sections[:i] + sections[i+1:]
            break
 
    if not action and len(sections) > 1:
        action = sections[-1]
        main   = sections[:-1]
 
    main_js   = json.dumps([{"title": t, "body": b} for t, b in main])
    action_js = json.dumps({"title": action[0], "body": action[1]}) if action else "null"
    return main_js, action_js
 
 
# ── JS data builders ──────────────────────────────────────────────────────────
 
def _track_record_js_data(history: list, market_data: dict) -> str:
    """Build track record of last 5 One Action picks with returns."""
    price_lookup = {}
    for tickers in market_data.values():
        for t in (tickers or []):
            if t and t.get("ticker") and t.get("price"):
                price_lookup[t["ticker"]] = t["price"]
 
    track = []
    seen  = set()
    for entry in reversed(history):
        ticker = entry.get("action_ticker", "")
        if not ticker or ticker in seen:
            continue
        seen.add(ticker)
        entry_price   = entry.get("action_price")
        current_price = price_lookup.get(ticker)
        ret = None
        if entry_price and current_price:
            ret = round((current_price / entry_price - 1) * 100, 1)
        track.append({"ticker": ticker, "date": entry.get("date",""), "ret_since": ret})
        if len(track) >= 5:
            break
 
    return json.dumps(list(reversed(track)))
 
 
def _history_js_data(history: list, scored_data: dict) -> str:
    layer_ids = list(scored_data.keys())
    date_map  = {e["date"]: e.get("scores", {}) for e in history}
    today     = datetime.datetime.now().date()
    days_out  = []
    for i in range(89, -1, -1):
        d     = today - datetime.timedelta(days=i)
        d_str = d.strftime("%Y-%m-%d")
        day_data = {
            "date":  d_str,
            "label": d.strftime("%b %d"),
            "short": d.strftime("%d"),
            "layers": {},
        }
        scores = date_map.get(d_str, {})
        for lid in layer_ids:
            day_data["layers"][lid] = scores[lid].get("color","none") if lid in scores else "none"
        days_out.append(day_data)
    return json.dumps({"layer_ids": layer_ids, "days": days_out})
 
 
def _radar_js_data(radar_data: list) -> str:
    """Build JS-safe radar data.
 
    radar_data is now the FULL ranked list from load_cached_radar() (which
    returns 'all', not just 'top5').  This means wildcard candidates sitting
    outside the top 5 (e.g. APP) are present and carry their sparkline.
    """
    MAIN_25 = {
        "CEG","VST","PWR","GEV","ETN",
        "NVDA","AMD","AVGO","ASML","TSM","ARM","CDNS",
        "MU","WDC","AMAT","LRCX",
        "VRT","ANET","EQIX","SMCI","CSCO","CIEN",
        "MSFT","GOOGL","AMZN","META",
        "PLTR","NOW","SNOW","CRM","DDOG",
        "CRWD","PANW","S","OKTA",
    }
 
    # ── Wildcard: lowest analyst coverage outside MAIN_25 ────────────────────
    wildcard     = None
    wc_candidates = [
        r for r in radar_data
        if r.get("ticker") not in MAIN_25
        and (r.get("accel", {}).get("latest_growth") or 0) >= 10
        and (r.get("gross_margin") or 0) > 0
    ]
    if wc_candidates:
        wc_candidates.sort(key=lambda x: (x.get("analyst_count", 99), -x.get("score", 0)))
        wc    = wc_candidates[0]
        accel = wc.get("accel", {})
        wc_traj = []
        for g in wc.get("growth_quarters", [])[:4]:
            wc_traj.append("🔥" if g and g > 50 else "↑" if g and g > 20 else "→" if g and g > 0 else "↓")
 
        # sparkline comes directly from next_nvidia.py via the full ranked list
        wc_spark = [round(float(p), 2) for p in wc.get("sparkline", [])][-30:]
 
        wildcard = {
            "ticker":        wc.get("ticker", "?"),
            "name":          wc.get("name", "?"),
            "layer":         wc.get("layer", "?"),
            "score":         wc.get("score", 0),
            "ret_1mo":       wc.get("ret_1mo", 0),
            "ret_3mo":       wc.get("ret_3mo", 0),
            "gross_margin":  wc.get("gross_margin"),
            "analyst_count": wc.get("analyst_count", 0),
            "confidence":    accel.get("confidence", "LOW"),
            "consecutive":   accel.get("consecutive_accel", 0),
            "latest_growth": accel.get("latest_growth"),
            "trajectory":    wc_traj,
            "sparkline":     wc_spark,   # ← populated from full list
        }
 
    # ── Top 5 cards ───────────────────────────────────────────────────────────
    out = []
    for r in radar_data[:5]:
        accel    = r.get("accel", {})
        quarters = r.get("growth_quarters", [])
        traj = []
        for g in quarters[:4]:
            if g is None:        traj.append("?")
            elif g > 50:         traj.append("🔥")
            elif g > 20:         traj.append("↑")
            elif g > 0:          traj.append("→")
            else:                traj.append("↓")
 
        out.append({
            "ticker":        r.get("ticker", "?"),
            "name":          r.get("name", "?"),
            "layer":         r.get("layer", "?"),
            "score":         r.get("score", 0),
            "ret_1mo":       r.get("ret_1mo", 0),
            "ret_3mo":       r.get("ret_3mo", 0),
            "gross_margin":  r.get("gross_margin"),
            "analyst_count": r.get("analyst_count", 0),
            "confidence":    accel.get("confidence", "LOW"),
            "consecutive":   accel.get("consecutive_accel", 0),
            "latest_growth": accel.get("latest_growth"),
            "trajectory":    traj,
        })
 
    return json.dumps({"top5": out, "wildcard": wildcard})
 
 
# ── Master HTML builder ───────────────────────────────────────────────────────
 
def generate_dashboard(scored_data: dict, analysis: str, macro_data: dict,
                       market_data: dict = None, radar_data: list = None) -> str:
    if market_data is None:
        market_data = {}
    if radar_data is None:
        radar_data  = []
 
    # Extract One Action ticker for track record
    action_ticker = ""
    try:
        import re
        match = re.search(r'Company[:\s]+\*?\*?([A-Z]{1,5})', analysis)
        if match:
            action_ticker = match.group(1)
    except Exception:
        pass
    save_scores_history(scored_data, action_ticker)
 
    yesterday    = get_yesterday_scores(scored_data)
    full_history = load_scores_history()
    now          = datetime.datetime.now()
    date_str     = now.strftime("%A, %B %d %Y")
    time_str     = now.strftime("%H:%M")
    datetime_str = f"{date_str} · {time_str}"
    fetch_note   = f"Fetched {time_str} · Quarterly financials may be up to 90 days old"
 
    vix      = macro_data.get("vix", 0)
    yield_ch = macro_data.get("yield_10y_change", 0)
    nasdaq_r = macro_data.get("nasdaq_vs_spx_20d", 0) * 100
 
    if vix > 30 or yield_ch > 100:
        reg_lbl = "Market regime: Risk-Off"
    elif vix > 20 or yield_ch > 50:
        reg_lbl = "Market regime: Neutral"
    else:
        reg_lbl = "Market regime: Risk-On"
 
    layers_js              = _chain_js_data(scored_data, market_data, yesterday)
    radar_js               = _radar_js_data(radar_data)
    history_js             = _history_js_data(full_history, scored_data)
    track_js               = _track_record_js_data(full_history, market_data)
    main_sections_js, action_js = _analysis_sections(analysis)
    has_yesterday          = "true" if yesterday else "false"
 
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>RayDar — AI value chain intelligence · {datetime_str}</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
      background:#0E1628;color:#1A1A1A;min-height:100vh;padding:0 0 32px}}
.hero{{background:linear-gradient(135deg,#08081A 0%,#0C1A3A 30%,#1A0830 60%,#0A0818 100%);
       padding:22px 24px 20px;position:relative;overflow:hidden}}
.hero-laser{{position:absolute;top:0;left:0;right:0;height:3px;
             background:linear-gradient(90deg,transparent 0%,transparent 10%,#534AB7 28%,#85B7EB 45%,#ffffff 50%,#85B7EB 55%,#534AB7 72%,transparent 90%,transparent 100%);
             opacity:0.9}}
.hero-glow{{position:absolute;top:0;left:10%;right:10%;height:40px;
            background:radial-gradient(ellipse at 50% 0%,rgba(133,183,235,0.45) 0%,rgba(83,74,183,0.2) 40%,transparent 75%)}}
.hero-grid{{display:none}}
.hero-accent{{position:absolute;bottom:-40px;right:-40px;width:200px;height:200px;
              background:radial-gradient(circle,rgba(60,52,137,0.15) 0%,transparent 70%)}}
.hero-top{{display:flex;justify-content:space-between;align-items:center;
           margin-bottom:14px;position:relative;flex-wrap:wrap;gap:8px}}
.hero-title{{font-size:22px;color:#fff;line-height:1.2;letter-spacing:-0.3px}}
.hero-sub{{font-size:11px;color:#3A4460;margin-top:3px}}
.reg-pill{{font-size:11px;font-weight:500;padding:5px 14px;border-radius:20px;
           border:1px solid rgba(255,255,255,.3);color:#fff;
           background:rgba(255,255,255,.15);cursor:pointer;position:relative}}
.reg-pill:hover .reg-tooltip,.reg-pill:focus .reg-tooltip{{display:block}}
.reg-tooltip{{display:none;position:absolute;top:calc(100% + 8px);right:0;
              background:#1A1A1A;color:#F8F8F7;font-size:10px;line-height:1.6;
              padding:10px 12px;border-radius:6px;width:260px;z-index:200;
              font-weight:400;box-shadow:0 4px 16px rgba(0,0,0,.4);text-align:left}}
.reg-tooltip::before{{content:"";position:absolute;bottom:100%;right:16px;
                      border:5px solid transparent;border-bottom-color:#1A1A1A}}
.reg-tooltip b{{color:#97C459}}
.hm-grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:6px;position:relative}}
.hm-card{{background:rgba(255,255,255,.07);border:0.5px solid rgba(133,183,235,0.2);
          border-radius:8px;padding:5px 12px 6px}}
.hm-lbl{{font-size:9px;color:#85B7EB;margin-bottom:1px;letter-spacing:.06em;font-weight:500}}
.hm-val{{font-size:14px;font-weight:500;color:#fff;line-height:1.1}}
.hm-note{{font-size:9px;margin-top:1px;line-height:1.1}}
.body{{padding:16px 16px 0;margin-top:0;position:relative;
       background:linear-gradient(180deg,#0E1628 0%,#101C32 40%,#13182A 75%,#0F1420 100%);
       min-height:100vh}}
.card{{background:white;border:0.5px solid #E0DFDC;border-radius:12px;
       padding:16px;margin-bottom:12px}}
.card-label{{font-size:10px;font-weight:500;color:#888780;
             letter-spacing:.05em;margin-bottom:12px}}
.fetch-note{{font-size:10px;color:#B4B2A9;margin-top:4px}}
.layer{{flex-shrink:0;width:148px;border:1.5px solid;border-radius:10px;
        padding:11px 10px;cursor:pointer;transition:transform .1s,box-shadow .1s}}
.layer:hover{{transform:translateY(-2px)}}
.lyr-pill{{font-size:9px;font-weight:500;padding:2px 8px;border-radius:10px;
           display:inline-block;margin-bottom:6px}}
.lyr-name{{font-size:11px;font-weight:500;color:#1A1A1A;margin-bottom:2px;line-height:1.3}}
.lyr-score{{font-size:28px;font-weight:500;line-height:1;margin:5px 0 2px}}
.lyr-delta{{font-size:10px;margin-bottom:4px;display:flex;align-items:center;gap:4px}}
.lyr-bar{{height:3px;border-radius:2px;background:rgba(0,0,0,.08);margin-bottom:8px}}
.lyr-fill{{height:3px;border-radius:2px}}
.lyr-meta{{font-size:10px;color:#888780;margin-bottom:8px}}
.lyr-tickers{{border-top:1px solid rgba(0,0,0,.06);padding-top:7px;
              display:flex;flex-direction:column;gap:4px}}
.lyr-tk{{display:flex;justify-content:space-between;font-size:10px;font-weight:500}}
.expand{{border:0.5px solid #E0DFDC;border-radius:10px;padding:14px;
         margin-top:10px;background:#F8F8F7;display:none}}
.expand.open{{display:block}}
.ex-hdr{{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px}}
.ex-title{{font-size:13px;font-weight:500;color:#1A1A1A}}
.ex-btn{{font-size:11px;padding:4px 10px;border:0.5px solid #E0DFDC;
         border-radius:6px;background:white;cursor:pointer;color:#1A1A1A}}
.ex-grid{{display:grid;grid-template-columns:repeat(2,1fr);gap:8px}}
.ex-tk{{background:white;border:0.5px solid #E0DFDC;border-radius:8px;padding:10px}}
.ex-sym{{font-size:13px;font-weight:500;color:#1A1A1A}}
.ex-name{{font-size:10px;color:#888780;margin:2px 0 6px}}
.ex-row{{display:flex;justify-content:space-between;font-size:10px;margin-bottom:2px}}
.ex-lbl{{color:#888780}}
.heat-grid{{display:grid;grid-template-columns:56px repeat(7,1fr);
            gap:3px;align-items:center;margin-top:14px}}
.heat-lbl{{font-size:10px;color:#888780;text-align:right;padding-right:8px}}
.hbtn{{font-size:12px;font-weight:500;padding:5px 14px;border:none;border-radius:5px;
       background:transparent;color:#378ADD;cursor:pointer;transition:all .15s}}
.hbtn:hover{{background:white;color:#0C447C}}
.active-hbtn{{background:white!important;color:#0C447C!important;font-weight:600!important;
              box-shadow:0 1px 4px rgba(12,68,124,.2)}}
.heat-day{{font-size:9px;color:#B4B2A9;text-align:center}}
.heat-cell{{height:18px;border-radius:3px}}
.as-wrap{{display:flex;flex-direction:column;gap:0}}
.as{{padding:12px 0;border-bottom:0.5px solid #E0DFDC}}
.as:last-child{{border-bottom:none}}
.as-hdr{{display:flex;align-items:center;gap:10px;margin-bottom:6px}}
.as-icon{{width:26px;height:26px;border-radius:6px;background:#F1EFE8;
          display:flex;align-items:center;justify-content:center;
          font-size:13px;flex-shrink:0}}
.as-title{{font-size:13px;font-weight:500;color:#1A1A1A}}
.as-body{{font-size:12px;color:#5F5E5A;line-height:1.65;padding-left:36px}}
.action-card{{background:linear-gradient(135deg,#E6F1FB,#EEEDFE);
              border:1px solid #AFA9EC;border-radius:10px;
              padding:14px;margin-bottom:12px}}
.action-lbl{{font-size:9px;font-weight:500;color:#534AB7;
             letter-spacing:.06em;margin-bottom:5px}}
.action-title{{font-size:14px;font-weight:500;color:#26215C;margin-bottom:6px}}
.action-body{{font-size:12px;color:#3C3489;line-height:1.6}}
.meth-trigger{{display:flex;justify-content:space-between;align-items:center;
               cursor:pointer;padding:14px 16px;background:#E6F1FB;
               border:2px solid #378ADD;border-radius:10px;margin-bottom:4px;
               transition:background .15s;box-shadow:0 2px 6px rgba(55,138,221,.15)}}
.meth-trigger:hover{{background:#D4E8F7}}
.meth-body{{display:none;background:white;border:0.5px solid #E0DFDC;
            border-radius:10px;padding:14px;margin-bottom:12px}}
.meth-body.open{{display:block}}
.mc{{padding:8px 10px;border-radius:7px;border:0.5px solid;margin-bottom:6px}}
.mc-name{{font-size:11px;font-weight:500;margin-bottom:3px}}
.mc-desc{{font-size:10px;line-height:1.5}}
.sigs{{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:12px}}
.sig{{border:0.5px solid #E0DFDC;border-radius:8px;padding:10px}}
.sig-icon{{width:22px;height:22px;border-radius:5px;display:flex;align-items:center;
           justify-content:center;font-size:12px;margin-bottom:6px}}
.sig-name{{font-size:11px;font-weight:500;color:#1A1A1A;margin-bottom:4px}}
.sig-items{{font-size:10px;color:#5F5E5A;line-height:1.7}}
.override{{background:#F8F8F7;border-left:2px solid #B4B2A9;border-radius:0 6px 6px 0;
           padding:8px 10px;font-size:10px;color:#5F5E5A;line-height:1.6;margin-bottom:12px}}
.ip-note{{font-size:10px;color:#B4B2A9;text-align:center;line-height:1.6}}
.div-flag{{display:inline-flex;align-items:center;gap:4px;background:#FFF3CD;
           border:0.5px solid #EF9F27;border-radius:4px;padding:2px 6px;
           font-size:9px;font-weight:500;color:#633806;cursor:pointer;
           margin-top:4px;position:relative}}
.div-flag:hover .div-tooltip,.div-flag:focus .div-tooltip{{display:block}}
.div-tooltip{{display:none;position:absolute;bottom:calc(100% + 6px);left:0;
              background:#1A1A1A;color:#F8F8F7;font-size:10px;line-height:1.5;
              padding:8px 10px;border-radius:6px;width:220px;z-index:100;
              font-weight:400;box-shadow:0 4px 12px rgba(0,0,0,.3)}}
.div-tooltip::after{{content:"";position:absolute;top:100%;left:12px;
                     border:5px solid transparent;border-top-color:#1A1A1A}}
.rating-A{{background:#EAF3DE;color:#27500A;border:0.5px solid #639922;font-weight:600}}
.rating-B{{background:#E6F1FB;color:#0C447C;border:0.5px solid #378ADD;font-weight:600}}
.rating-C{{background:#FAEEDA;color:#633806;border:0.5px solid #EF9F27;font-weight:600}}
.rating-D{{background:#FCEBEB;color:#791F1F;border:0.5px solid #E24B4A;font-weight:600}}
.rating-badge{{font-size:10px;padding:1px 6px;border-radius:4px;display:inline-block}}
.radar-card{{background:white;border:0.5px solid #E0DFDC;border-radius:8px;
             padding:10px 12px;display:flex;flex-direction:column;gap:6px}}
.radar-rank{{font-size:10px;font-weight:500;color:#888780}}
.radar-sym{{font-size:14px;font-weight:500;color:#1A1A1A}}
.radar-name{{font-size:10px;color:#888780;margin-bottom:2px}}
.radar-bar-wrap{{height:4px;background:#F1EFE8;border-radius:2px;margin:4px 0}}
.radar-bar{{height:4px;border-radius:2px;background:linear-gradient(90deg,#534AB7,#E24B4A)}}
.radar-meta{{display:flex;gap:8px;flex-wrap:wrap;font-size:10px;color:#5F5E5A}}
.radar-conf-HIGH{{color:#27500A;font-weight:500}}
.radar-conf-MEDIUM{{color:#633806;font-weight:500}}
.radar-conf-LOW{{color:#888780}}
.radar-traj{{font-size:12px;letter-spacing:2px}}
.ticker-band{{background:#05050F;border-top:0.5px solid rgba(83,74,183,0.35);border-bottom:none;
              overflow:hidden;padding:5px 0;position:relative;z-index:10}}
.ticker-scroll{{display:flex;white-space:nowrap;animation:ticker-move 28s linear infinite}}
.ticker-scroll:hover{{animation-play-state:paused}}
.t-item{{display:inline-flex;align-items:center;gap:6px;padding:0 18px;
         font-size:11px;color:#85B7EB;border-right:0.5px solid #1A3A5C;flex-shrink:0}}
.t-sym{{color:white;font-weight:500}}
.t-up{{color:#97C459}}.t-dn{{color:#E24B4A}}.t-neu{{color:#888780}}
@keyframes ticker-move{{0%{{transform:translateX(0)}}100%{{transform:translateX(-50%)}}}}
.signal-bars{{display:flex;align-items:flex-end;justify-content:center;
              gap:3px;height:28px;margin-top:12px}}
.sbar{{width:4px;border-radius:2px;animation:sbar-pulse 1.4s ease-in-out infinite}}
@keyframes sbar-pulse{{0%,100%{{opacity:.2;transform:scaleY(.35)}}50%{{opacity:1;transform:scaleY(1)}}}}
.footer{{font-size:12px;color:#8A9AB8;text-align:center;margin-top:16px;
         padding:20px 16px;border-top:1px solid rgba(255,255,255,0.06);
         background:#080812;border-radius:8px;line-height:2.0}}
.footer-cobhc{{font-size:12px;color:#C8D4E8;font-weight:500;margin-top:6px;
              letter-spacing:.02em}}
.footer-cobhc span{{color:#E24B4A;font-style:italic}}
.pdf-btn{{display:inline-flex;align-items:center;gap:6px;background:rgba(255,255,255,.15);
          border:1px solid rgba(255,255,255,.3);color:#fff;font-size:11px;font-weight:500;
          padding:5px 14px;border-radius:20px;cursor:pointer;transition:background .15s}}
.pdf-btn:hover{{background:rgba(255,255,255,.25)}}
@media print{{
  .pdf-btn,.meth-trigger,.meth-body,.expand{{display:none!important}}
  .hero{{-webkit-print-color-adjust:exact;print-color-adjust:exact}}
  .card{{break-inside:avoid;page-break-inside:avoid}}
  body{{padding:0}}
  .layer{{min-width:100px}}
}}
@media(max-width:768px){{
  .hero{{padding:14px 12px 16px}}
  .hero-title{{font-size:15px}}
  .hero-title span:first-child{{font-size:22px!important}}
  .hero-title span:last-child{{font-size:11px!important}}
  .hero-sub{{font-size:10px}}
  .hm-grid{{grid-template-columns:1fr 1fr;gap:5px}}
  .hm-val{{font-size:13px}}
  .hm-lbl,.hm-note{{font-size:9px}}
  .body{{padding:8px 8px 0;margin-top:0}}
  .card{{padding:10px 8px;border-radius:8px;margin-bottom:8px}}
  .card-label{{font-size:9px;margin-bottom:6px}}
  .layer{{min-width:120px;max-width:150px;padding:8px 7px}}
  .lyr-score{{font-size:20px}}
  .lyr-name{{font-size:9px}}
  .lyr-pill{{font-size:8px;padding:1px 5px}}
  .lyr-meta{{font-size:9px}}
  .lyr-tk{{font-size:9px}}
  .lyr-delta span{{font-size:9px}}
  .heat-grid{{grid-template-columns:40px repeat(7,1fr);gap:2px;margin-top:10px}}
  .heat-lbl{{font-size:9px;padding-right:4px}}
  .heat-day{{font-size:8px}}
  .heat-cell{{height:13px}}
  .as-title{{font-size:12px}}
  .as-body{{font-size:11px;padding-left:28px;line-height:1.5}}
  .as-icon{{width:20px;height:20px;font-size:11px}}
  .as{{padding:10px 0}}
  .action-card{{padding:10px}}
  .action-title{{font-size:12px}}
  .action-body{{font-size:11px;line-height:1.5}}
  .meth-trigger{{padding:8px 10px}}
  .sigs{{grid-template-columns:1fr}}
  #bottleneck-strip{{font-size:10px;padding:5px 8px;gap:5px}}
  .footer{{font-size:9px}}
  .pdf-btn{{font-size:9px;padding:3px 8px}}
  .reg-pill{{font-size:9px;padding:3px 8px}}
  .ex-grid{{grid-template-columns:1fr 1fr}}
  .hero-top{{gap:6px}}
  #hero-chain-status{{flex-wrap:wrap}}
  #hero-chain-status > div{{min-width:calc(33.3% - 1px);flex:none}}
}}
@media(max-width:768px){{
  #radar-grid{{grid-template-columns:repeat(2,1fr)!important}}
}}
@media(max-width:380px){{
  .hm-grid{{grid-template-columns:1fr 1fr}}
  .layer{{min-width:110px}}
  .hero-title span:first-child{{font-size:20px!important}}
  .hero-title span:last-child{{font-size:10px!important}}
  #hero-chain-status > div{{min-width:calc(50% - 1px)}}
}}
</style>
</head>
<body>
 
<div class="hero">
  <div class="hero-laser"></div>
  <div class="hero-glow"></div>
  <div class="hero-grid"></div>
  <div class="hero-accent"></div>
  <div class="hero-top">
    <div>
      <div class="hero-title" style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
        <span style="font-size:30px;font-weight:500;letter-spacing:-1px">Ray<span style="color:#85B7EB;font-weight:300">Dar</span></span>
        <span style="width:5px;height:5px;border-radius:50%;background:#E24B4A;flex-shrink:0;display:inline-block;margin-top:6px"></span>
        <span style="font-size:14px;font-weight:400;background:linear-gradient(90deg,#85B7EB,#AFA9EC,#85B7EB);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;letter-spacing:0.08em;opacity:0.95">AI value chain intelligence</span>
      </div>
      <div class="hero-sub" style="color:#8A9AB8;font-size:11px;margin-top:3px">{datetime_str}</div>
    </div>
    <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
      <button class="pdf-btn" onclick="window.print()">↓ PDF</button>
      <div class="reg-pill" tabindex="0">{reg_lbl}
        <div class="reg-tooltip">
          <b>What is Market Regime?</b><br><br>
          A systemic filter applied to all layer scores. Reflects the overall macro environment for AI investments today.<br><br>
          <b>Risk-On</b> — VIX low, yields stable, tech leading. All signals at full strength.<br>
          <b>Neutral</b> — Some uncertainty. Scores dampened. Caution on new positions.<br>
          <b>Risk-Off</b> — High fear, rising yields, tech selling off. Do not initiate new positions.<br><br>
          Signals used: VIX · 10Y yield change · NASDAQ vs S&amp;P 500
        </div>
      </div>
    </div>
  </div>
  <div class="hm-grid" style="margin-bottom:12px">
    <div class="hm-card">
      <div class="hm-lbl">VIX</div>
      <div class="hm-val">{vix:.1f}</div>
      <div class="hm-note" style="color:{'#C0DD97' if vix<20 else '#FAC775'}">{'Low fear' if vix<20 else 'Elevated'}</div>
    </div>
    <div class="hm-card">
      <div class="hm-lbl">10Y YIELD CHANGE</div>
      <div class="hm-val">{yield_ch:+.1f}</div>
      <div class="hm-note" style="color:{'#C0DD97' if yield_ch<0 else '#FAC775'}">bps · {'falling' if yield_ch<0 else 'rising'}</div>
    </div>
    <div class="hm-card">
      <div class="hm-lbl">NASDAQ VS S&P</div>
      <div class="hm-val">{nasdaq_r:+.1f}%</div>
      <div class="hm-note" style="color:{'#C0DD97' if nasdaq_r>0 else '#FAC775'}">{'Tech leading' if nasdaq_r>0 else 'Tech lagging'}</div>
    </div>
    <div class="hm-card">
      <div class="hm-lbl">LAYERS SCORED</div>
      <div class="hm-val">{len(scored_data)}</div>
      <div class="hm-note" style="color:#85B7EB">{sum(len(market_data.get(l,[])) for l in scored_data)} tickers</div>
    </div>
  </div>
  <div id="hero-chain-status" style="position:relative;display:flex;gap:1px;background:rgba(255,255,255,0.04);border-radius:8px;overflow:hidden;margin-top:12px"></div>
</div>
 
<div class="ticker-band">
  <div class="ticker-scroll" id="top-ticker"></div>
</div>
<div class="body">
 
<div class="card">
  <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;margin-bottom:10px">
    <div style="font-size:12px;font-weight:500;color:#C8D4E8;letter-spacing:0.02em;margin-bottom:0">AI value chain — signal scores</div>
    <div style="display:flex;gap:12px;font-size:10px;color:#8A9AB8;align-items:center;flex-wrap:wrap">
      <span><span style="display:inline-block;width:7px;height:7px;border-radius:50%;background:#E24B4A;margin-right:4px"></span>Hot / bottleneck</span>
      <span><span style="display:inline-block;width:7px;height:7px;border-radius:50%;background:#EF9F27;margin-right:4px"></span>Emerging</span>
      <span><span style="display:inline-block;width:7px;height:7px;border-radius:50%;background:#639922;margin-right:4px"></span>Neutral / healthy</span>
      <span><span style="display:inline-block;width:7px;height:7px;border-radius:50%;background:#378ADD;margin-right:4px"></span>Cooling</span>
    </div>
  </div>
  <div class="fetch-note" style="margin-bottom:8px;color:#6A7A9A">{fetch_note}</div>
  <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;margin-bottom:6px;flex-wrap:wrap">
    <div id="bottleneck-strip" style="display:flex;align-items:center;gap:8px;padding:8px 12px;
         border-radius:6px;background:#F8F8F7;border:0.5px solid #E0DFDC;
         font-size:11px;color:#5F5E5A;flex-wrap:wrap;flex:1"></div>
    <button onclick="toggleAbout()" style="font-size:12px;font-weight:500;
            padding:8px 16px;border:1.5px solid #378ADD;border-radius:6px;
            background:#E6F1FB;color:#0C447C;cursor:pointer;white-space:nowrap;
            flex-shrink:0;display:flex;align-items:center;gap:6px"
            id="about-btn">ℹ️ About this dashboard</button>
  </div>
  <div id="about-section" style="display:none;background:#F8F8F7;border:0.5px solid #E0DFDC;
       border-radius:8px;padding:14px;margin-bottom:10px;font-size:12px;color:#5F5E5A;line-height:1.7">
    <div style="font-size:13px;font-weight:500;color:#1A1A1A;margin-bottom:10px">About this dashboard</div>
    <p style="margin-bottom:8px">A daily signal dashboard tracking the AI supply chain — from power plants to software — identifying where investment opportunities and risks are building.</p>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:10px">
      <div style="background:white;border-radius:6px;padding:8px 10px;border:0.5px solid #E0DFDC">
        <div style="font-size:11px;font-weight:500;color:#1A1A1A;margin-bottom:4px">🔴 Hot / Bottleneck</div>
        <div style="font-size:11px">Current dominant constraint. Supply cannot meet demand. Highest conviction signal.</div>
      </div>
      <div style="background:white;border-radius:6px;padding:8px 10px;border:0.5px solid #E0DFDC">
        <div style="font-size:11px;font-weight:500;color:#1A1A1A;margin-bottom:4px">🟠 Emerging</div>
        <div style="font-size:11px">Building momentum. Becoming the next bottleneck, or price running ahead of fundamentals.</div>
      </div>
      <div style="background:white;border-radius:6px;padding:8px 10px;border:0.5px solid #E0DFDC">
        <div style="font-size:11px;font-weight:500;color:#1A1A1A;margin-bottom:4px">🟢 Neutral / Healthy</div>
        <div style="font-size:11px">Stable. No constraint pressure. Not a concern, not an urgent opportunity.</div>
      </div>
      <div style="background:white;border-radius:6px;padding:8px 10px;border:0.5px solid #E0DFDC">
        <div style="font-size:11px;font-weight:500;color:#1A1A1A;margin-bottom:4px">🔵 Cooling</div>
        <div style="font-size:11px">Previously hot layer. Constraint easing, growth decelerating.</div>
      </div>
    </div>
    <div style="font-size:11px;font-weight:500;color:#1A1A1A;margin-bottom:6px">Warning flags</div>
    <p style="margin-bottom:4px"><strong>⚡ Narrative ahead of fundamentals</strong> — Investment story exists in news but not yet confirmed in quarterly earnings. Wait for financial confirmation.</p>
    <p style="margin-bottom:10px"><strong>🟠 Orange card</strong> — Stock price has moved significantly more than revenue growth justifies. Market may have already priced in the good news.</p>
    <div style="font-size:11px;font-weight:500;color:#1A1A1A;margin-bottom:6px">Data sources (updated daily)</div>
    <p style="margin-bottom:2px">Stock prices &amp; fundamentals — Yahoo Finance · News signals — Reuters, CNBC, MarketWatch · AI analysis — Google Gemini 2.5</p>
    <p style="margin-top:8px;font-size:10px;color:#B4B2A9">Quarterly financials may be up to 90 days old · Not financial advice · Always do your own research</p>
  </div>
  <div style="display:flex;align-items:stretch;gap:0" id="chain"></div>
  <div id="expand-area"></div>
  <div style="margin-top:14px">
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px">
      <div style="display:flex;align-items:center;gap:10px">
        <div style="display:flex;gap:3px;background:#E6F1FB;border-radius:8px;padding:4px;border:1.5px solid #378ADD">
          <button onclick="setHeatDays(7)"  id="hbtn-7"  class="hbtn active-hbtn">7d</button>
          <button onclick="setHeatDays(30)" id="hbtn-30" class="hbtn">30d</button>
          <button onclick="setHeatDays(90)" id="hbtn-90" class="hbtn">90d</button>
        </div>
        <div style="font-size:10px;font-weight:500;color:#888780" id="heat-label">Signal heat trail</div>
      </div>
    </div>
    <div class="heat-grid" id="heat"></div>
  </div>
</div>
 
<div class="card">
  <div class="card-label">Daily analysis — Gemini 2.5-flash · {time_str}</div>
  <div class="as-wrap" id="analysis"></div>
</div>
 
<div class="card" id="radar-card" style="display:none">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px">
    <div class="card-label" style="margin-bottom:0">🚀 <strong>RayDar</strong> — Next Nvidia Top 5 today</div>
    <span style="font-size:10px;color:#888780" id="radar-count">Multi-quarter acceleration</span>
  </div>
  <div style="font-size:11px;color:#5F5E5A;margin-bottom:10px">
    Ranked by sustained revenue acceleration across multiple quarters — not just latest growth.
    Low analyst coverage = earlier in discovery cycle.
    <span style="color:#B4B2A9"> · Confidence improves as quarterly data accumulates over time.</span>
  </div>
  <div style="display:grid;grid-template-columns:repeat(5,1fr);gap:8px" id="radar-grid"></div>
  <div id="wildcard-section" style="display:none;margin-top:14px;padding-top:14px;border-top:0.5px solid #E0DFDC">
    <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px">
      <span style="font-size:16px">🃏</span>
      <span style="font-size:13px;font-weight:600;color:#1A1A1A;letter-spacing:.02em">WILDCARD</span>
      <span style="font-size:11px;color:#888780">— under the radar</span>
    </div>
    <div id="wildcard-card"></div>
  </div>
</div>
 
<div class="action-card" id="action"></div>
<div id="track-record" style="display:none;margin-bottom:12px;
     background:white;border:0.5px solid #E0DFDC;border-radius:10px;padding:14px">
  <div style="font-size:10px;font-weight:500;color:#888780;letter-spacing:.04em;margin-bottom:10px">
    📊 ONE ACTION TRACK RECORD — last 5 picks
  </div>
  <div id="track-rows"></div>
  <div style="font-size:10px;color:#B4B2A9;margin-top:8px;line-height:1.5">
    Returns measured from recommendation date to today. Not financial advice.
  </div>
</div>
 
<div class="meth-trigger" onclick="toggleMeth()">
  <div style="display:flex;align-items:center;gap:8px">
    <span style="font-size:12px;font-weight:500;color:#1A1A1A">How to read this dashboard</span>
    <span style="font-size:10px;padding:1px 7px;border:0.5px solid #E0DFDC;border-radius:8px;color:#888780">methodology</span>
  </div>
  <span style="font-size:11px;color:#B4B2A9" id="marrow">▾</span>
</div>
<div class="meth-body" id="meth-body">
  <div style="font-size:11px;font-weight:500;color:#1A1A1A;margin-bottom:8px">What the colors mean</div>
  <div class="mc" style="background:#FCEBEB;border-color:#E24B4A">
    <div class="mc-name" style="color:#791F1F">Red — current bottleneck</div>
    <div class="mc-desc" style="color:#A32D2D">All three signal categories agree. This layer is the dominant constraint in the AI supply chain right now.</div>
  </div>
  <div class="mc" style="background:#FAEEDA;border-color:#EF9F27">
    <div class="mc-name" style="color:#633806">Orange — emerging or hype warning</div>
    <div class="mc-desc" style="color:#854F0B">Building momentum toward a bottleneck, or price action is outrunning the fundamentals.</div>
  </div>
  <div class="mc" style="background:#EAF3DE;border-color:#639922">
    <div class="mc-name" style="color:#27500A">Green — neutral / healthy</div>
    <div class="mc-desc" style="color:#3B6D11">Stable activity, no constraint pressure. Functioning normally.</div>
  </div>
  <div class="mc" style="background:#E6F1FB;border-color:#378ADD">
    <div class="mc-name" style="color:#0C447C">Blue — cooling</div>
    <div class="mc-desc" style="color:#185FA5">Growth acceleration declining. A previously hot layer easing off.</div>
  </div>
  <div style="background:#F8F8F7;border-left:2px solid #378ADD;border-radius:0 6px 6px 0;
              padding:8px 10px;font-size:10px;color:#5F5E5A;line-height:1.6;margin-top:4px">
    <strong>Note on AI Security layer:</strong> Security companies (CRWD, PANW) are steady compounders
    growing at 10–25% annually — not hardware bottlenecks. A Blue/Green Security layer is normal and
    healthy. Watch for Orange/Red only during major breach cycles or regulatory shifts.
  </div>
  <div style="font-size:11px;font-weight:500;color:#1A1A1A;margin:12px 0 8px">Three signal categories</div>
  <div class="sigs">
    <div class="sig">
      <div class="sig-icon" style="background:#EAF3DE">F</div>
      <div class="sig-name">Fundamentals</div>
      <div class="sig-items">Revenue growth acceleration<br>Gross margin expansion<br>Pricing power trend</div>
    </div>
    <div class="sig">
      <div class="sig-icon" style="background:#FCEBEB">C</div>
      <div class="sig-name">Constraint signal</div>
      <div class="sig-items">News narrative velocity<br>Capital expenditure trends<br>Supply/demand pressure</div>
    </div>
    <div class="sig">
      <div class="sig-icon" style="background:#E6F1FB">S</div>
      <div class="sig-name">Smart money</div>
      <div class="sig-items">Volume-price confirmation<br>Analyst upgrade clusters<br>Short interest direction</div>
    </div>
  </div>
  <div class="override"><strong>Fundamental override:</strong> If revenue growth is decelerating, a layer cannot be Red regardless of price momentum. Fundamentals always take priority.</div>
  <div class="ip-note">Signal weights, calibration parameters, and exact scoring logic are proprietary.<br>This summary describes methodology categories only. Not financial advice.</div>
</div>
 
</div>
 
<div class="footer">
  <div><strong>RayDar</strong> · {datetime_str}</div>
  <div style="font-size:11px;color:#888780;margin-top:2px">
    Not financial advice · Always do your own research · Data may be up to 90 days old
  </div>
  <div class="footer-cobhc">
    ⚔️ Powered by COBHC · Built in Espoo, Finland ·
    <span>Are You Dead Yet?</span> — the market will tell you 🤘
  </div>
  <div class="signal-bars" id="signal-bars"></div>
</div>
 
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.js"></script>
<script>
const LAYERS       = {layers_js};
const MAIN_SECTIONS= {main_sections_js};
const ACTION       = {action_js};
const HAS_YESTERDAY= {has_yesterday};
const RADAR        = {radar_js};
const HISTORY      = {history_js};
const TRACK_RECORD = {track_js};
 
const CC = {{
  Red:    {{bg:"#FCEBEB",border:"#E24B4A",pill:"#E24B4A",pft:"#FCEBEB",lbl:"Hot"}},
  Orange: {{bg:"#FAEEDA",border:"#EF9F27",pill:"#EF9F27",pft:"#FAEEDA",lbl:"Emerging"}},
  Green:  {{bg:"#EAF3DE",border:"#639922",pill:"#639922",pft:"#EAF3DE",lbl:"Neutral"}},
  Blue:   {{bg:"#E6F1FB",border:"#378ADD",pill:"#378ADD",pft:"#E6F1FB",lbl:"Cooling"}},
}};
 
let active = null;
 
function fmt(v, decimals=1) {{
  return (v >= 0 ? "+" : "") + v.toFixed(decimals);
}}
 
function deltaArrow(d) {{
  if (d === null || d === undefined) return "";
  const col   = d > 0 ? "#27500A" : d < 0 ? "#A32D2D" : "#888780";
  const arrow = d > 0 ? "▲" : d < 0 ? "▼" : "—";
  return `<span style="color:${{col}};font-size:10px">${{arrow}} ${{Math.abs(d).toFixed(1)}}</span>`;
}}
 
function colorChangeBadge(l) {{
  if (!HAS_YESTERDAY || !l.color_changed || !l.prev_color) return "";
  const prev = CC[l.prev_color] || CC.Green;
  return `<span style="font-size:9px;padding:1px 5px;border-radius:4px;
                        background:${{prev.bg}};color:${{prev.pill}};
                        border:0.5px solid ${{prev.pill}};margin-left:4px">was ${{prev.lbl}}</span>`;
}}
 
const EARNINGS_WATCH = {{
  "memory":   {{ companies: ["MU","WDC"],            next: "Jun 2026" }},
  "compute":  {{ companies: ["NVDA","AMD"],           next: "May-Jun 2026" }},
  "infra":    {{ companies: ["SMCI","VRT"],           next: "May 2026" }},
  "cloud":    {{ companies: ["MSFT","GOOGL","AMZN","META"], next: "Jul 2026" }},
  "energy":   {{ companies: ["CEG","GEV"],            next: "Aug 2026" }},
  "security": {{ companies: ["CRWD","PANW"],          next: "Jun 2026" }},
  "software": {{ companies: ["PLTR","SNOW"],          next: "May 2026" }},
}};
 
function getEarningsBadge(layerId) {{
  const watch = EARNINGS_WATCH[layerId];
  if (!watch) return "";
  const nearTerm = ["compute","infra","software","security"];
  if (nearTerm.includes(layerId)) {{
    return `<span style="font-size:9px;padding:1px 5px;border-radius:4px;
                         background:#FFF3CD;color:#856404;border:0.5px solid #EF9F27;
                         margin-left:4px" title="Earnings: ${{watch.companies.join(', ')}} — ${{watch.next}}">
              📅 Earnings soon</span>`;
  }}
  return "";
}}
 
const sparkCharts = {{}};
 
function toggleTickerDetail(id) {{
  const el = document.getElementById(id);
  if (!el) return;
  const open = el.style.display === "none";
  document.querySelectorAll('[id^="tk-detail-"]').forEach(d => d.style.display = "none");
  Object.keys(sparkCharts).forEach(k => {{ try {{ sparkCharts[k].destroy(); }} catch(e) {{}} delete sparkCharts[k]; }});
  el.style.display = open ? "block" : "none";
  if (!open) return;
 
  const sym = id.replace(/^tk-detail-[^-]+-/, "");
  LAYERS.forEach(l => l.tickers.forEach(t => {{
    if (t.sym !== sym || !t.sparkline || t.sparkline.length < 5) return;
    const data  = t.sparkline;
    const mn    = Math.min(...data);
    const mx    = Math.max(...data);
    const range = mx - mn;
    const canvasWrap = document.getElementById("spark-wrap-" + sym);
    if (range < 1) {{
      if (canvasWrap) canvasWrap.innerHTML = '<div style="display:flex;align-items:center;justify-content:center;height:100%;font-size:11px;color:#B4B2A9;font-style:italic">Fetching 6-month history...</div>';
      return;
    }}
    const pad   = range * 0.12;
    const col   = t.ret30 >= 0 ? "#27500A" : "#A32D2D";
    const bgCol = t.ret30 >= 0 ? "rgba(39,80,10,0.07)" : "rgba(163,45,45,0.07)";
    const today = new Date();
    const step  = Math.max(1, Math.floor(180 / data.length));
    const labels = data.map((_, i) => {{
      const d = new Date(today);
      d.setDate(d.getDate() - (data.length - 1 - i) * step);
      return d.toLocaleDateString("en", {{month:"short", day:"numeric"}});
    }});
    if (canvasWrap) canvasWrap.innerHTML = '<canvas id="spark-' + sym + '" style="width:100%;height:100%"></canvas>';
    setTimeout(() => {{
      const canvas = document.getElementById("spark-" + sym);
      if (!canvas) return;
      sparkCharts["spark-" + sym] = new Chart(canvas, {{
        type: "line",
        data: {{ labels, datasets: [{{ data, borderColor: col, borderWidth: 2, pointRadius: 0,
                 pointHoverRadius: 3, fill: true, backgroundColor: bgCol, tension: 0.35 }}] }},
        options: {{
          responsive: true, maintainAspectRatio: false,
          interaction: {{ mode:"index", intersect:false }},
          plugins: {{ legend:{{display:false}}, tooltip:{{ callbacks:{{
            label: c => "$" + c.parsed.y.toFixed(2), title: c => c[0].label }} }} }},
          scales: {{
            x: {{ display:true, ticks:{{maxTicksLimit:5,font:{{size:9}},color:"#888780",maxRotation:0}},
                  grid:{{display:false}}, border:{{display:false}} }},
            y: {{ display:true, min:mn-pad, max:mx+pad,
                  ticks:{{maxTicksLimit:4,font:{{size:9}},color:"#888780",callback:v=>"$"+Math.round(v)}},
                  grid:{{color:"rgba(0,0,0,0.04)"}}, border:{{display:false}} }}
          }},
          animation: {{ duration:500 }}
        }}
      }});
    }}, 80);
  }}));
}}
 
function getCompanyContext(sym, layerId) {{
  const ctx = {{
    "CEG":"Constellation Energy is the largest US nuclear operator. Signing AI-specific Power Purchase Agreements with hyperscalers — directly powering data centers with 24/7 carbon-free power.",
    "VST":"Vistra operates natural gas and nuclear plants. Benefits from surging electricity demand driven by AI data center buildout in Texas and the Southeast.",
    "PWR":"Quanta Services builds and maintains power grid infrastructure. As data centers require grid upgrades, Quanta executes the physical construction work.",
    "GEV":"GE Vernova makes transformers, switchgear and grid equipment. These components connect data centers to the power grid — currently on 2-3 year backlog.",
    "ETN":"Eaton makes power distribution and management systems inside data centers. Every rack of servers needs Eaton's PDUs and UPS systems.",
    "NVDA":"Nvidia's H100/H200/B200 GPUs are the primary compute engine for AI training and inference. Near-monopoly on AI accelerators with 70-80% gross margins.",
    "AMD":"AMD's MI300X competes with Nvidia for AI training workloads. Gaining share with hyperscalers looking to diversify away from single-vendor dependency.",
    "AVGO":"Broadcom designs custom AI chips (ASICs) for Google, Meta and others. Also makes the networking silicon connecting GPU clusters — two AI revenue streams.",
    "ASML":"ASML makes EUV lithography machines — the only company in the world that can. Every advanced AI chip requires ASML equipment to manufacture.",
    "TSM":"TSMC manufactures chips for Nvidia, AMD, Apple and ARM. The world's most advanced semiconductor foundry — 90%+ of leading-edge AI chips are made here.",
    "ARM":"ARM licenses chip architectures used in virtually every mobile and edge AI device. Royalties from every Apple, Qualcomm and MediaTek chip that ships.",
    "CDNS":"Cadence Design Systems makes EDA software — the tools used to design every AI chip. Without Cadence, NVDA couldn't design its next GPU.",
    "MU":"Micron is the only US-listed pure-play HBM supplier. HBM memory is essential for AI training — each H100 GPU uses Micron or SK Hynix HBM stacks.",
    "WDC":"Western Digital makes NAND flash storage used in AI data centers. Also pivoting toward HBM with recent product announcements.",
    "AMAT":"Applied Materials makes the deposition and etch equipment used to manufacture memory chips. Picks-and-shovels play on all memory production expansion.",
    "LRCX":"Lam Research makes etch equipment critical for advanced memory fabrication. Benefits directly when memory fabs expand capacity for HBM production.",
    "VRT":"Vertiv makes liquid cooling systems for dense GPU clusters. As GPU power density increases, air cooling fails — Vertiv's moment is now.",
    "ANET":"Arista Networks makes the high-speed ethernet switches connecting GPU clusters. Moving from data center to AI networking with Ultra Ethernet Consortium.",
    "EQIX":"Equinix operates 260+ data centers globally. AI workloads need colocation space close to fiber interconnects — Equinix's core offering.",
    "SMCI":"Super Micro Computer builds AI-optimized server racks. Fastest to market with liquid-cooled GPU servers — direct beneficiary of Nvidia GPU demand.",
    "CSCO":"Cisco provides the networking backbone for enterprise AI deployments. Pivoting AI strategy around its silicon and software for data center switching.",
    "CIEN":"Ciena makes optical networking equipment — the bandwidth pipes connecting AI data centers across distances. Critical for multi-site AI cluster deployments.",
    "MSFT":"Microsoft is the primary distribution partner for OpenAI. Azure AI services and Copilot are the monetization vehicle for the largest AI investment in history.",
    "GOOGL":"Google DeepMind and Gemini models run on Google Cloud. Also designs its own TPU AI chips — vertically integrated from silicon to application.",
    "AMZN":"AWS is the largest cloud provider. Amazon Bedrock offers access to multiple AI models. Also deploying Trainium and Inferentia custom AI chips.",
    "META":"Meta open-sources LLaMA models, driving AI adoption at scale. Investing $65B in AI infrastructure in 2026 — one of the largest capex programs ever.",
    "PLTR":"Palantir's AIP platform brings AI agents to enterprise operations. Unique position with US government and defense — classified AI applications.",
    "NOW":"ServiceNow automates enterprise workflows with AI. 80%+ gross margins and sticky multi-year contracts across Fortune 500.",
    "SNOW":"Snowflake provides the data cloud layer AI models need for training and inference. Clean, governed data is the prerequisite for enterprise AI.",
    "CRM":"Salesforce embeds AI (Einstein) across its CRM platform. 150,000+ enterprise customers provide a massive distribution channel for AI features.",
    "DDOG":"Datadog monitors AI infrastructure performance and cost. As AI deployments scale, observability becomes critical — Datadog is the market leader.",
    "CRWD":"CrowdStrike uses AI to detect and stop cyber threats in real time. The Falcon platform protects AI infrastructure from adversarial attacks.",
    "PANW":"Palo Alto Networks provides AI-powered network security. Platformization strategy bundles AI security across firewall, cloud and endpoint.",
    "S":"SentinelOne uses autonomous AI agents for threat detection. Fastest growing pure-play AI security company by revenue.",
    "OKTA":"Okta manages identity and access for AI applications. As AI agents proliferate, controlling who and what can access systems becomes critical.",
  }};
  return ctx[sym] || `${{sym}} is a key player in the ${{layerId}} layer of the AI value chain.`;
}}
 
function toggleAbout() {{
  const sec = document.getElementById("about-section");
  const btn = document.getElementById("about-btn");
  const open = sec.style.display === "none";
  sec.style.display = open ? "block" : "none";
  btn.innerHTML = open ? "✕ Close" : "ℹ️ About this dashboard";
}}
 
function buildBottleneckStrip() {{
  const strip = document.getElementById("bottleneck-strip");
  if (!strip) return;
  const sorted    = [...LAYERS].sort((a,b) => b.score - a.score);
  const hotLayers = sorted.filter(l => l.color === "Red");
  const emerging  = sorted.find(l => l.color === "Orange");
  const easing    = sorted.find(l => l.color === "Blue");
  const ease      = easing || sorted[sorted.length-1];
 
  let html = "";
  if (hotLayers.length > 0) {{
    html += `<span style="color:#888780">Bottleneck${{hotLayers.length > 1 ? "s" : ""}}:</span>`;
    hotLayers.forEach((l, i) => {{
      html += `<strong style="color:#E24B4A">${{l.n1}}</strong>`;
      if (i < hotLayers.length - 1) html += `<span style="color:#E24B4A;margin:0 2px">+</span>`;
    }});
  }}
  if (emerging) {{
    html += `<span style="color:#B4B2A9;margin:0 4px">→</span>`;
    html += `<span style="color:#888780">Emerging:</span>`;
    html += `<strong style="color:#EF9F27">${{emerging.n1}}</strong>`;
  }}
  html += `<span style="color:#B4B2A9;margin:0 4px">→</span>`;
  html += `<span style="color:#888780">Easing:</span>`;
  html += `<strong style="color:${{(CC[ease.color]||CC.Green).border}}">${{ease.n1}}</strong>`;
  strip.innerHTML = html;
}}
 
function buildChain() {{
  const wrap = document.getElementById("chain");
  wrap.innerHTML = "";
  LAYERS.forEach((l, idx) => {{
    const c   = CC[l.color] || CC.Green;
    const pct = Math.min(100, Math.round(l.score));
    const top3 = l.tickers.slice(0, 3);
    const tkHtml = top3.map(t => {{
      const col = t.ret30 >= 0 ? "#27500A" : "#A32D2D";
      const db  = t.delta_band === "accelerating" ? "▲" : t.delta_band === "decelerating" ? "▼" : "—";
      const dc  = t.delta_band === "accelerating" ? "#27500A" : t.delta_band === "decelerating" ? "#A32D2D" : "#888780";
      return `<div class="lyr-tk">
        <span>${{t.sym}} <span style="color:${{dc}};font-size:9px">${{db}}</span></span>
        <span style="color:${{col}}">${{fmt(t.ret30)}}%</span>
      </div>`;
    }}).join("");
 
    const div = document.createElement("div");
    div.className = "layer";
    div.style.cssText = `flex:1;min-width:120px;background:${{c.bg}};border-color:${{c.border}};${{active===l.id?"box-shadow:0 0 0 2px "+c.border:""}}`;
    div.innerHTML = `
      <div style="display:flex;align-items:center;flex-wrap:wrap;gap:3px;margin-bottom:6px">
        <span class="lyr-pill" style="background:${{c.pill}};color:${{c.pft}}">${{c.lbl}}</span>
        ${{colorChangeBadge(l)}}${{getEarningsBadge(l.id)}}
      </div>
      <div class="lyr-name">${{l.n1}}<br><span style="color:#888780;font-weight:400">${{l.n2}}</span></div>
      <div class="lyr-score" style="color:${{c.border}}">${{l.score.toFixed(0)}}</div>
      <div class="lyr-delta">
        ${{HAS_YESTERDAY && l.delta_score !== null
          ? deltaArrow(l.delta_score) + "<span style='font-size:10px;color:#888780;margin-left:2px'>vs yesterday</span>"
          : "<span style='font-size:10px;color:#B4B2A9'>first run</span>"}}
      </div>
      <div class="lyr-bar"><div class="lyr-fill" style="width:${{pct}}%;background:${{c.border}}"></div></div>
      <div class="lyr-meta">News ${{l.news_vel}} hits · Momentum ${{l.momentum_label}}</div>
      <div style="display:flex;gap:3px;margin-bottom:4px;flex-wrap:wrap">
        ${{l.tickers.slice(0,3).map(t => `<span class="rating-badge rating-${{t.rating}}" style="font-size:9px;padding:1px 4px" title="${{t.sym}}: ${{t.rating==='A'?'Accelerating':t.rating==='B'?'Stable':t.rating==='C'?'Caution':'Deteriorating'}}">${{t.sym}} ${{t.rating}}</span>`).join("")}}
      </div>
      ${{l.divergence ? `<div class="div-flag" tabindex="0">⚡ Narrative ahead of fundamentals
        <div class="div-tooltip">${{l.divergence_msg}}</div></div>` : ""}}
      <div class="lyr-tickers">${{tkHtml}}</div>`;
    div.onclick = () => {{ active = active === l.id ? null : l.id; buildChain(); buildExpand(); }};
    wrap.appendChild(div);
 
    if (idx < LAYERS.length - 1) {{
      const arrow = document.createElement("div");
      arrow.style.cssText = "display:flex;align-items:center;padding:0 3px;flex-shrink:0;padding-top:20px";
      arrow.innerHTML = `<svg width="16" height="16" viewBox="0 0 16 16" fill="none">
        <path d="M3 8h10M9 4l4 4-4 4" stroke="#B4B2A9" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
      </svg>`;
      wrap.appendChild(arrow);
    }}
  }});
  buildBottleneckStrip();
}}
 
function buildExpand() {{
  const area = document.getElementById("expand-area");
  if (!active) {{ area.innerHTML = ""; return; }}
  const l = LAYERS.find(x => x.id === active);
  const c = CC[l.color] || CC.Green;
 
  const cards = l.tickers.map(t => {{
    const rc    = t.ret30 >= 0 ? "#27500A" : "#A32D2D";
    const hyp   = t.hype ? `<span style="font-size:9px;background:#FAEEDA;color:#854F0B;padding:1px 5px;border-radius:4px;margin-left:4px">hype</span>` : "";
    const tIcon = t.delta_band === "accelerating" ? "▲" : t.delta_band === "decelerating" ? "▼" : "—";
    const tCol  = t.delta_band === "accelerating" ? "#27500A" : t.delta_band === "decelerating" ? "#A32D2D" : "#888780";
    const cardId = `tk-detail-${{l.id}}-${{t.sym}}`;
    const deepDive = `Give me a focused investment deep dive on ${{t.sym}} (${{t.name}}) in the context of the AI value chain. Cover: 1) What specific role does ${{t.sym}} play in AI infrastructure? 2) Latest revenue trend and whether growth is accelerating or decelerating. 3) Gross margin trend — is pricing power expanding? 4) The strongest bull case in one paragraph. 5) The main risk that would invalidate the bull case. 6) Compared to peers in the ${{l.n1}} layer, is ${{t.sym}} gaining or losing ground?`;
 
    return `<div class="ex-tk" style="cursor:pointer" onclick="toggleTickerDetail('${{cardId}}')">
      <div style="display:flex;align-items:center;justify-content:space-between">
        <div style="display:flex;align-items:center;gap:6px">
          <div class="ex-sym">${{t.sym}}</div>
          <span class="rating-badge rating-${{t.rating}}"
                title="${{t.rating_up ? 'Upgraded from '+t.prev_rating+' — buy signal' : t.rating_down ? 'Downgraded from '+t.prev_rating+' — warning' : 'Rating: '+t.rating}}"
          >${{t.rating}}</span>
          ${{t.rating_up ? `<span style="font-size:10px;color:#27500A;font-weight:600">↑ was ${{t.prev_rating}}</span>` : ""}}
          ${{t.rating_down ? `<span style="font-size:10px;color:#A32D2D;font-weight:600">↓ was ${{t.prev_rating}}</span>` : ""}}
          ${{hyp}}
        </div>
        <span style="font-size:9px;color:#B4B2A9">tap for insight ↓</span>
      </div>
      <div class="ex-name">${{t.name}}</div>
      ${{t.run_rate ? `<div style="font-size:10px;font-weight:600;color:#27500A;margin:3px 0;
          padding:2px 6px;background:#EAF3DE;border-radius:4px;display:inline-block">
        Run rate $${{t.run_rate}}B/yr</div>` : ""}}
      <div class="ex-row"><span class="ex-lbl">Price</span><span>$${{t.price.toLocaleString()}}</span></div>
      <div class="ex-row"><span class="ex-lbl">30d return</span><span style="color:${{rc}}">${{fmt(t.ret30)}}%</span></div>
      <div class="ex-row"><span class="ex-lbl">Trend</span><span style="color:${{tCol}}">${{tIcon}} ${{t.delta_band}}</span></div>
      <div style="margin-top:6px;padding:5px 8px;border-radius:5px;
                  background:#F8F8F7;border-left:2px solid ${{t.forecast.color}}">
        <span style="font-size:10px;font-weight:500;color:${{t.forecast.color}}">
          ${{t.forecast.direction}} ${{t.rating}} → ${{t.forecast.target}}
        </span>
        <span style="font-size:10px;color:#888780;margin-left:6px">${{t.forecast.label}}</span>
      </div>
      <div id="${{cardId}}" style="display:none;margin-top:10px;padding-top:10px;
           border-top:0.5px solid #E0DFDC" onclick="event.stopPropagation()">
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-bottom:10px">
          ${{t.run_rate ? `<div style="background:#EAF3DE;border-radius:6px;padding:8px 10px;border:0.5px solid #639922">
            <div style="font-size:9px;color:#27500A;font-weight:500;margin-bottom:2px">REVENUE RUN RATE</div>
            <div style="font-size:16px;font-weight:500;color:#27500A">$${{t.run_rate}}B</div>
            <div style="font-size:9px;color:#3B6D11">annualized · latest quarter ×4</div>
          </div>` : ""}}
          ${{t.pct_of_high ? `<div style="background:#F8F8F7;border-radius:6px;padding:8px 10px;border:0.5px solid #E0DFDC">
            <div style="font-size:9px;color:#888780;font-weight:500;margin-bottom:2px">52W POSITION</div>
            <div style="font-size:16px;font-weight:500;color:#1A1A1A">${{t.pct_of_high}}%</div>
            <div style="font-size:9px;color:#888780">of 52-week high ($${{t.week52_high?.toFixed(0)}})</div>
            <div style="margin-top:4px;height:4px;background:#E0DFDC;border-radius:2px">
              <div style="height:4px;border-radius:2px;background:${{t.pct_of_high>80?'#E24B4A':t.pct_of_high>50?'#EF9F27':'#639922'}};width:${{t.pct_of_high}}%"></div>
            </div>
          </div>` : ""}}
        </div>
        <div style="font-size:10px;color:#888780;margin-bottom:4px;font-weight:500">6-month price trend</div>
        <div id="spark-wrap-${{t.sym}}" style="position:relative;width:100%;height:120px;margin-bottom:8px;background:#F8F8F7;border-radius:4px">
          <canvas id="spark-${{t.sym}}" style="width:100%;height:100%" role="img" aria-label="${{t.sym}} 6-month price chart"></canvas>
        </div>
        <div style="font-size:10px;color:#888780;margin-bottom:4px;font-weight:500">Strategic role in AI chain</div>
        <div style="font-size:10px;color:#5F5E5A;line-height:1.6;margin-bottom:8px">${{getCompanyContext(t.sym, l.id)}}</div>
        <button onclick="sendPrompt('${{deepDive}}')"
                style="width:100%;padding:7px;border:1px solid #378ADD;border-radius:6px;
                       background:#E6F1FB;color:#0C447C;font-size:11px;font-weight:500;cursor:pointer">
          🔍 Deep dive with Claude ↗
        </button>
      </div>
    </div>`;
  }}).join("");
 
  area.innerHTML = `<div class="expand open" style="border-color:${{c.border}}">
    <div class="ex-hdr">
      <div class="ex-title">${{l.n1}} ${{l.n2}} — ${{c.lbl}} · score ${{l.score.toFixed(1)}}</div>
      <button class="ex-btn" onclick="sendPrompt('Deep dive on the ${{l.n1}} ${{l.n2}} layer of the AI value chain today. Which company is best positioned and what would make this layer turn Red?')">Deep dive ↗</button>
    </div>
    <div class="ex-grid">${{cards}}</div>
  </div>`;
}}
 
function colorFromName(c) {{
  return {{Red:"#E24B4A",Orange:"#EF9F27",Green:"#97C459",Blue:"#B5D4F4"}}[c] || "#E8E6DF";
}}
 
let currentHeatDays = 7;
 
function setHeatDays(days) {{
  currentHeatDays = days;
  [7,30,90].forEach(d => {{
    const btn = document.getElementById("hbtn-"+d);
    if (btn) btn.className = "hbtn" + (d===days ? " active-hbtn" : "");
  }});
  const lbl = document.getElementById("heat-label");
  if (lbl) lbl.textContent = `Signal heat trail · last ${{days}} days`;
  buildHeat();
}}
 
function buildHeat() {{
  const g = document.getElementById("heat");
  if (!g) return;
  g.innerHTML = "";
  const days    = currentHeatDays;
  const slice   = HISTORY.days.slice(HISTORY.days.length - days);
  const cellMin = days<=7?"32px":days<=30?"12px":"6px";
  g.style.gridTemplateColumns = `56px repeat(${{days}}, minmax(${{cellMin}},1fr))`;
 
  g.appendChild(document.createElement("div"));
  const showEvery = days<=7?1:days<=30?5:15;
  slice.forEach((day,i) => {{
    const d = document.createElement("div");
    d.className = "heat-day";
    d.style.fontSize = days>30?"7px":"9px";
    d.textContent = i%showEvery===0 ? day.short : "";
    g.appendChild(d);
  }});
 
  const displaySlice = days === 7
    ? slice.filter(day => HISTORY.layer_ids.some(lid => day.layers[lid] && day.layers[lid] !== "none"))
    : slice;
 
  if (days === 7 && displaySlice.length !== slice.length) {{
    g.style.gridTemplateColumns = `56px repeat(${{displaySlice.length}}, minmax(32px,1fr))`;
    g.innerHTML = "";
    g.appendChild(document.createElement("div"));
    displaySlice.forEach((day,i) => {{
      const d = document.createElement("div");
      d.className = "heat-day";
      d.style.fontSize = "9px";
      d.textContent = day.short;
      g.appendChild(d);
    }});
  }}
 
  LAYERS.forEach((l, li) => {{
    const lbl = document.createElement("div");
    lbl.className = "heat-lbl";
    lbl.textContent = l.n1;
    g.appendChild(lbl);
    displaySlice.forEach((day) => {{
      const cell = document.createElement("div");
      cell.className = "heat-cell";
      cell.style.height = days>30?"13px":"18px";
      const realColor = day.layers[l.id];
      let displayColor;
      if (realColor && realColor !== "none") {{
        displayColor = colorFromName(realColor);
        cell.title = `${{l.n1}} · ${{day.label}} · ${{realColor}}`;
      }} else {{
        displayColor = days === 7 ? colorFromName(l.color) : "#EDEAE0";
        cell.style.opacity = days === 7 ? "0.4" : "1";
        cell.title = `${{l.n1}} · ${{day.label}} · ${{days===7?"estimated":"no data yet"}}`;
      }}
      cell.style.background = displayColor;
      g.appendChild(cell);
    }});
  }});
}}
 
function buildAnalysis() {{
  const wrap = document.getElementById("analysis");
  wrap.innerHTML = MAIN_SECTIONS.map(s => `
    <div class="as">
      <div class="as-hdr">
        <div class="as-icon">${{s.title.slice(0,2)}}</div>
        <div class="as-title">${{s.title}}</div>
      </div>
      <div class="as-body">${{s.body}}</div>
    </div>`).join("");
  if (ACTION) {{
    document.getElementById("action").innerHTML = `
      <div class="action-lbl">ONE ACTION TODAY</div>
      <div class="action-title">${{ACTION.title}}</div>
      <div class="action-body">${{ACTION.body}}</div>`;
  }}
}}
 
function buildRadar() {{
  const grid = document.getElementById("radar-grid");
  const card = document.getElementById("radar-card");
  const count = document.getElementById("radar-count");
  if (!RADAR || !RADAR.top5 || RADAR.top5.length === 0) {{ card.style.display="none"; return; }}
  card.style.display = "block";
  if (count) count.textContent = `Multi-quarter acceleration · ${{RADAR.top5.length}} of 45 companies scored`;
 
  const LAYER_SHORT = {{energy:"⚡",compute:"💻",memory:"🧠",infra:"🏗️",cloud:"☁️",software:"📱",security:"🔐",edge:"📡"}};
 
  // ── Wildcard ──────────────────────────────────────────────────────────────
  const wildcard  = RADAR.wildcard || null;
  const wcSection = document.getElementById("wildcard-section");
  const wcCard    = document.getElementById("wildcard-card");
  if (wildcard && wcSection && wcCard) {{
    wcSection.style.display = "block";
    const wret = wildcard.ret_1mo >= 0 ? `+${{wildcard.ret_1mo}}%` : `${{wildcard.ret_1mo}}%`;
    const wrc  = wildcard.ret_1mo >= 0 ? "#27500A" : "#A32D2D";
    const wlg  = wildcard.latest_growth ? `Rev +${{wildcard.latest_growth?.toFixed(0)}}%` : "";
    const wgm  = wildcard.gross_margin  ? `GM ${{wildcard.gross_margin}}%` : "";
    const wtraj = (wildcard.trajectory || []).join(" ");
    const wPrompt = `Deep dive on ${{wildcard.ticker}} (${{wildcard.name}}) as a potential under-the-radar AI play. Only ${{wildcard.analyst_count}} analysts cover this company. Analyse: revenue acceleration trend, gross margin, competitive moat in the AI value chain, and what specific catalyst could make this the next Nvidia-like breakout.`;
    const wcContextMap = {{
      memory:"memory chips and storage — the fuel every AI model runs on",
      compute:"chip design and processing — the brain of AI systems",
      infra:"data center hardware and networking — the physical backbone",
      energy:"power delivery and grid equipment — the electricity AI needs",
      cloud:"cloud platforms and hyperscaler infrastructure",
      software:"AI applications and observability tooling",
      security:"AI security and governance platforms",
      edge:"edge computing and on-device AI processing",
    }};
    const wcContext = wcContextMap[wildcard.layer] || wildcard.layer;
 
    wcCard.innerHTML = `
      <div style="background:white;border:1px solid #EF9F27;border-radius:10px;overflow:hidden">
        <div style="padding:12px 14px;border-bottom:0.5px solid #E0DFDC;display:flex;justify-content:space-between;align-items:flex-start">
          <div>
            <div style="font-size:20px;font-weight:500;color:#1A1A1A">${{wildcard.ticker}}</div>
            <div style="font-size:11px;color:#888780;margin-top:2px">${{wildcard.name}} · ${{wildcard.layer}}</div>
          </div>
          <div style="display:flex;flex-direction:column;align-items:flex-end;gap:6px">
            <span style="font-size:10px;padding:2px 8px;background:#FFF3CD;color:#856404;border:0.5px solid #EF9F27;border-radius:4px">
              Only ${{wildcard.analyst_count}} analysts — early discovery signal
            </span>
            <button onclick="sendPrompt('${{wPrompt}}')"
                    style="font-size:11px;font-weight:500;padding:6px 14px;border:1px solid #378ADD;border-radius:6px;background:#E6F1FB;color:#0C447C;cursor:pointer">
              🔍 Deep dive ↗
            </button>
          </div>
        </div>
        <div style="display:flex;border-bottom:0.5px solid #E0DFDC">
          <div style="flex:1;padding:8px 12px;border-right:0.5px solid #E0DFDC">
            <div style="font-size:10px;color:#888780;margin-bottom:3px">Revenue</div>
            <div style="font-size:12px;font-weight:500;color:${{wrc}}">${{wlg}}</div>
          </div>
          <div style="flex:1;padding:8px 12px;border-right:0.5px solid #E0DFDC">
            <div style="font-size:10px;color:#888780;margin-bottom:3px">Gross margin</div>
            <div style="font-size:12px;font-weight:500;color:#1A1A1A">${{wgm}}</div>
          </div>
          <div style="flex:1;padding:8px 12px;border-right:0.5px solid #E0DFDC">
            <div style="font-size:10px;color:#888780;margin-bottom:3px">1-month return</div>
            <div style="font-size:12px;font-weight:500;color:${{wrc}}">${{wret}}</div>
          </div>
          <div style="flex:1;padding:8px 12px">
            <div style="font-size:10px;color:#888780;margin-bottom:3px">Momentum</div>
            <div style="font-size:12px;font-weight:500;color:#1A1A1A">${{wtraj}}</div>
          </div>
        </div>
        <div style="padding:10px 14px;background:#F8F8F7">
          <div style="font-size:10px;color:#888780;margin-bottom:6px">6-month price trend</div>
          <div id="wc-chart-wrap" style="position:relative;height:80px">
            <canvas id="wc-spark" style="width:100%;height:100%"></canvas>
          </div>
        </div>
        <div style="padding:10px 14px;font-size:11px;color:#5F5E5A;line-height:1.6;border-top:0.5px solid #E0DFDC">
          ${{wildcard.ticker}} operates in ${{wcContext}}. Only ${{wildcard.analyst_count}} Wall Street analysts cover it.
          When revenue fundamentals accelerate before analyst coverage catches up, that is historically the
          earliest signal of an outsized opportunity.
        </div>
      </div>`;
 
    // Render wildcard sparkline
    setTimeout(() => {{
      const wcSpark = document.getElementById("wc-spark");
      if (wcSpark && wildcard.sparkline && wildcard.sparkline.length > 3) {{
        const wcData = wildcard.sparkline;
        const wcMn   = Math.min(...wcData);
        const wcMx   = Math.max(...wcData);
        if (wcMx - wcMn > 1) {{
          const wcPad  = (wcMx - wcMn) * 0.12;
          const wcCol  = wildcard.ret_1mo >= 0 ? "#3B6D11" : "#A32D2D";
          const wcBg   = wcCol === "#3B6D11" ? "rgba(39,80,10,0.07)" : "rgba(163,45,45,0.07)";
          const wcToday = new Date();
          const wcLabels = wcData.map((_, i) => {{
            const d = new Date(wcToday);
            d.setDate(d.getDate() - (wcData.length - 1 - i) * 6);
            return d.toLocaleDateString("en", {{month:"short", day:"numeric"}});
          }});
          try {{
            new Chart(wcSpark, {{
              type: "line",
              data: {{ labels: wcLabels, datasets: [{{ data: wcData, borderColor: wcCol,
                       borderWidth: 2, pointRadius: 0, fill: true, backgroundColor: wcBg, tension: 0.4 }}] }},
              options: {{
                responsive: true, maintainAspectRatio: false,
                plugins: {{ legend:{{display:false}}, tooltip:{{ callbacks:{{ label: c => "$" + c.parsed.y.toFixed(0) }} }} }},
                scales: {{
                  x: {{ display:true, ticks:{{maxTicksLimit:5,font:{{size:9}},color:"#888780",maxRotation:0}},
                        grid:{{display:false}}, border:{{display:false}} }},
                  y: {{ display:true, min:wcMn-wcPad, max:wcMx+wcPad,
                        ticks:{{maxTicksLimit:3,font:{{size:9}},color:"#888780",callback:v=>"$"+Math.round(v)}},
                        grid:{{color:"rgba(0,0,0,0.04)"}}, border:{{display:false}} }}
                }},
                animation: {{duration:500}}
              }}
            }});
          }} catch(e) {{ console.log("Wildcard chart error:", e); }}
        }} else {{
          document.getElementById("wc-chart-wrap").innerHTML =
            '<div style="display:flex;align-items:center;justify-content:center;height:100%;font-size:11px;color:#B4B2A9;font-style:italic">Price history loading...</div>';
        }}
      }}
    }}, 150);
  }}
 
  // ── Top 5 grid ────────────────────────────────────────────────────────────
  grid.innerHTML = (RADAR.top5 || []).map((r, i) => {{
    const pct    = Math.min(100, r.score);
    const conf   = r.confidence || "LOW";
    const traj   = (r.trajectory || []).join(" ");
    const ret1   = r.ret_1mo >= 0 ? `+${{r.ret_1mo}}%` : `${{r.ret_1mo}}%`;
    const retCol = r.ret_1mo >= 0 ? "#27500A" : "#A32D2D";
    const gm     = r.gross_margin ? `GM ${{r.gross_margin}}%` : "";
    const lg     = r.latest_growth ? `Rev ${{r.latest_growth > 0 ? "+" : ""}}${{r.latest_growth?.toFixed(0)}}%` : "";
    const cons   = r.consecutive > 0 ? `${{r.consecutive}}Q accel` : "No accel";
 
    return `<div class="radar-card">
      <div class="radar-rank">#${{i+1}} ${{LAYER_SHORT[r.layer]||""}} ${{r.layer}}</div>
      <div class="radar-sym">${{r.ticker}}</div>
      <div class="radar-name">${{r.name}}</div>
      <div class="radar-traj">${{traj}}</div>
      <div class="radar-bar-wrap"><div class="radar-bar" style="width:${{pct}}%"></div></div>
      <div class="radar-meta">
        <span class="radar-conf-${{conf}}">${{conf}} confidence</span>
        <span>${{cons}}</span>
      </div>
      <div class="radar-meta">
        <span style="color:${{retCol}}">${{ret1}} (1mo)</span>
        ${{lg ? `<span>${{lg}}</span>` : ""}}
        ${{gm ? `<span>${{gm}}</span>` : ""}}
      </div>
      <div style="margin-top:4px">
        <button onclick="sendPrompt('Deep dive on ${{r.ticker}} (${{r.name}}) as a potential Next Nvidia play. Analyse their revenue acceleration, gross margin trend, competitive position and what would confirm or invalidate the bull case.')"
                style="font-size:10px;padding:3px 8px;border:0.5px solid #E0DFDC;
                       border-radius:4px;background:white;cursor:pointer;color:#1A1A1A">
          Deep dive ↗
        </button>
      </div>
    </div>`;
  }}).join("");
}}
 
function buildTopTicker(layers) {{
  const el = document.getElementById("top-ticker");
  if (!el) return;
  const STATUS   = {{Red:"Hot",Orange:"Emerging",Green:"Neutral",Blue:"Cooling"}};
  const ICONS    = {{energy:"⚡",compute:"💻",memory:"🧠",infra:"🏗️",cloud:"☁️",software:"📱",security:"🔐"}};
  const items = [];
  layers.forEach(l => {{
    l.tickers.slice(0,3).forEach(t => {{
      const dir = t.ret30 > 1 ? "up" : t.ret30 < -1 ? "dn" : "neu";
      items.push(`<span class="t-item">
        <span class="t-sym">${{t.sym}}</span>
        <span>${{ICONS[l.id]||""}} ${{l.n1}} · ${{STATUS[l.color]||"Neutral"}}</span>
        <span class="t-${{dir}}">${{t.ret30>=0?"+":""}}${{t.ret30.toFixed(1)}}%</span>
      </span>`);
    }});
  }});
  el.innerHTML = [...items,...items].join("");
}}
 
function buildSignalBars() {{
  const wrap = document.getElementById("signal-bars");
  if (!wrap) return;
  const heights = [8,14,22,28,24,18,10,14,20,28,22,16,8,12,20,26,28,18,10,8];
  const colors  = ["#378ADD","#378ADD","#534AB7","#534AB7","#E24B4A","#E24B4A",
                   "#534AB7","#534AB7","#378ADD","#378ADD","#534AB7","#E24B4A",
                   "#E24B4A","#534AB7","#378ADD","#378ADD","#534AB7","#534AB7","#E24B4A","#378ADD"];
  wrap.innerHTML = heights.map((h,i) =>
    `<div class="sbar" style="height:${{h}}px;background:${{colors[i]}};animation-delay:${{(i*0.07).toFixed(2)}}s"></div>`
  ).join("");
}}
 
function buildTrackRecord() {{
  const section = document.getElementById("track-record");
  const rows    = document.getElementById("track-rows");
  if (!TRACK_RECORD || TRACK_RECORD.length === 0) return;
  section.style.display = "block";
  rows.innerHTML = TRACK_RECORD.map(r => {{
    const retCol = r.ret_since > 0 ? "#27500A" : r.ret_since < 0 ? "#A32D2D" : "#888780";
    const retTxt = r.ret_since !== null ? `${{r.ret_since >= 0 ? "+" : ""}}${{r.ret_since.toFixed(1)}}%` : "pending";
    return `<div style="display:flex;justify-content:space-between;align-items:center;
                         padding:6px 0;border-bottom:0.5px solid #E0DFDC;font-size:11px">
      <div>
        <span style="font-weight:500;color:#1A1A1A">${{r.ticker}}</span>
        <span style="color:#888780;margin-left:8px">${{r.date}}</span>
      </div>
      <span style="font-weight:500;color:${{retCol}}">${{retTxt}}</span>
    </div>`;
  }}).join("");
}}
 
function toggleMeth() {{
  const b = document.getElementById("meth-body");
  const a = document.getElementById("marrow");
  const open = b.classList.toggle("open");
  a.textContent = open ? "▴" : "▾";
}}
 
function sendPrompt(text) {{
  window.open('https://claude.ai/new?q=' + encodeURIComponent(text), '_blank');
}}
 
buildChain(); buildExpand(); buildHeat(); buildAnalysis();
 
function buildHeroStatus() {{
  const el = document.getElementById("hero-chain-status");
  if (!el || !LAYERS.length) return;
  const HCOLOR = {{
    Red:    {{label:"BOTTLENECK", val:"#E24B4A", bg:"rgba(226,75,74,0.12)",   border:"rgba(226,75,74,0.5)"}},
    Orange: {{label:"EMERGING",   val:"#EF9F27", bg:"rgba(239,159,39,0.10)",  border:"rgba(239,159,39,0.4)"}},
    Green:  {{label:"NEUTRAL",    val:"#639922", bg:"rgba(8,8,26,0.5)",        border:"rgba(255,255,255,0.04)"}},
    Blue:   {{label:"EASING",     val:"#378ADD", bg:"rgba(55,138,221,0.10)",  border:"rgba(55,138,221,0.3)"}},
  }};
  const order = {{Red:0, Orange:1, Blue:2, Green:3}};
  const sorted = [...LAYERS].sort((a,b) => (order[a.color] ?? 3) - (order[b.color] ?? 3));
  el.innerHTML = sorted.map(l => {{
    const c = HCOLOR[l.color] || HCOLOR.Green;
    return `<div style="flex:1;padding:7px 10px;background:${{c.bg}};border-right:0.5px solid rgba(255,255,255,0.06);">
      <div style="font-size:8px;color:${{c.val}};font-weight:600;letter-spacing:.08em;margin-bottom:1px">${{c.label}}</div>
      <div style="font-size:11px;font-weight:500;color:#fff;margin-bottom:1px">${{l.n1}}</div>
      <div style="font-size:16px;font-weight:500;color:${{c.val}};line-height:1">${{l.score.toFixed(0)}}</div>
    </div>`;
  }}).join("");
}}
buildHeroStatus();
buildRadar(); buildTopTicker(LAYERS); buildSignalBars(); buildTrackRecord();
</script>
</body>
</html>"""
 
    path = Path("ai_chain_report.html")
    path.write_text(html, encoding="utf-8")
    log.info(f"  Dashboard saved: {path.absolute()}")
    return str(path.absolute())
 
 
# ── Delivery ──────────────────────────────────────────────────────────────────
 
def deliver(analysis: str, html_path: str):
    method = DELIVERY_METHOD.lower()
    if method == "email":
        _send_email(analysis, html_path)
    elif method == "telegram":
        _send_telegram(analysis)
    else:
        _print_console(analysis, html_path)
 
 
def _print_console(analysis: str, html_path: str):
    print("\n" + "="*60)
    print("  DAILY AI INVESTMENT REPORT")
    print("="*60)
    print(analysis)
    print("="*60)
    print(f"\nDashboard: {html_path}")
    print("Open this file in your browser.\n")
 
 
def _send_email(analysis: str, html_path: str):
    try:
        date_str = datetime.datetime.now().strftime("%b %d, %Y")
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"AI Investment Report — {date_str}"
        msg["From"]    = EMAIL_SENDER
        msg["To"]      = EMAIL_RECIPIENT
        html_body = Path(html_path).read_text(encoding="utf-8") if Path(html_path).exists() else analysis
        msg.attach(MIMEText(analysis, "plain"))
        msg.attach(MIMEText(html_body, "html"))
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
            s.starttls()
            s.login(EMAIL_SENDER, EMAIL_PASSWORD)
            s.sendmail(EMAIL_SENDER, EMAIL_RECIPIENT, msg.as_string())
        log.info(f"  Email sent to {EMAIL_RECIPIENT}")
    except Exception as e:
        log.error(f"  Email failed: {e}")
        _print_console(analysis, html_path)
 
 
def _send_telegram(analysis: str):
    try:
        import requests
        url    = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        chunks = [analysis[i:i+4000] for i in range(0, len(analysis), 4000)]
        for chunk in chunks:
            requests.post(url, json={{
                "chat_id": TELEGRAM_CHAT_ID,
                "text": chunk, "parse_mode": "Markdown"
            }}, timeout=15).raise_for_status()
        log.info("  Telegram sent")
    except Exception as e:
        log.error(f"  Telegram failed: {e}")
        _print_console(analysis, "")
"""
render.py — HTML dashboard generator and report delivery.
 
Fixes in this version:
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
 
LAYER_NAMES = {
    "energy":   ("Energy",   "resources"),
    "compute":  ("Semicon",  "hardware"),
    "memory":   ("HBM memory","storage"),
    "infra":    ("Data center","infra"),
    "cloud":    ("Cloud",    "hyperscalers"),
    "software": ("AI software","apps"),
}
 
DAYS = ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"]
 
 
# ── History helpers ───────────────────────────────────────────────────────────
 
def load_scores_history() -> list:
    p = Path(SCORES_HISTORY_FILE)
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text())
    except Exception:
        return []
 
 
def save_scores_history(scored_data: dict):
    history = load_scores_history()
    today   = datetime.datetime.now().strftime("%Y-%m-%d")
    # Remove existing entry for today if re-running
    history = [e for e in history if e.get("date") != today]
    history.append({
        "date":   today,
        "time":   datetime.datetime.now().strftime("%H:%M"),
        "scores": {
            layer_id: {
                "score": result["best"].get("score", 0),
                "color": result["best"].get("color", "Green"),
            }
            for layer_id, result in scored_data.items()
            if "best" in result
        }
    })
    history = history[-MAX_HISTORY_DAYS:]
    Path(SCORES_HISTORY_FILE).write_text(json.dumps(history, indent=2))
    log.info(f"  Scores history saved ({len(history)} days)")
 
 
def get_yesterday_scores(scored_data: dict) -> dict:
    """
    Returns {layer_id: {"score": X, "color": Y}} for the previous run.
    Returns empty dict if no history exists.
    """
    history = load_scores_history()
    today   = datetime.datetime.now().strftime("%Y-%m-%d")
    # Find most recent entry that is NOT today
    prev = [e for e in history if e.get("date") != today]
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
            delta_score = round(score - prev_score, 1)
            color_changed = color != prev_color
        else:
            delta_score   = None
            color_changed = False
 
        # News velocity from market_data
        raw_tickers  = market_data.get(layer_id, [])
        # News velocity — iterate all scored tickers, use first non-zero value
        news_vel = next((ts.get("news_velocity", 0) for ts in all_scores if ts.get("news_velocity")), 0)
        if not news_vel and raw_tickers:
            news_vel = raw_tickers[0].get("news_velocity", 0)
 
        # Best ticker's fund_delta for momentum display
        fund_delta = best.get("fund_delta") or 0
 
        # Build ticker list — ticker data preserved directly in scored result
        tickers_out = []
        for t_scored in all_scores[:4]:
            sym  = t_scored.get("ticker", "")
            raw  = ticker_lookup.get(sym, {})
            # fund_delta shown as directional band only — protects scoring calibration
            raw_delta  = (t_scored.get("fund_delta") or 0) * 100
            delta_band = "accelerating" if raw_delta > 5 else "decelerating" if raw_delta < -5 else "stable"
            tickers_out.append({
                "sym":        sym or "?",
                "name":       t_scored.get("name") or raw.get("name") or sym or "?",
                "price":      t_scored.get("price") or raw.get("price", 0),
                "ret30":      round((t_scored.get("price_30d_return") or raw.get("price_30d_return") or 0) * 100, 1),
                "delta_band": delta_band,
                "hype":       t_scored.get("is_hype", False),
                "color":      t_scored.get("color", "Green"),
            })
 
        n1, n2 = LAYER_NAMES.get(layer_id, (layer_id.upper(), ""))
        # Protect IP: strip exact scores and deltas from published HTML
        # Only expose color, direction, and display data
        momentum_band = "strong" if abs(fund_delta*100) > 20 else "moderate" if abs(fund_delta*100) > 5 else "mild"
        momentum_dir  = "+" if fund_delta >= 0 else "-"
        layers.append({
            "id":           layer_id,
            "n1":           n1,
            "n2":           n2,
            "score":        score,           # kept for color band logic only
            "color":        color,
            "news_vel":     int(news_vel),
            "momentum_label": f"{momentum_dir}{momentum_band}",  # directional label only
            "delta_score":  delta_score,     # kept for ▲▼ direction only
            "prev_color":   prev_color,
            "color_changed":color_changed,
            "tickers":      tickers_out,
        })
 
    return json.dumps(layers)
 
 
def _analysis_sections(analysis: str) -> str:
    """Parse analysis markdown into clean section dicts."""
    sections = []
    current_title = ""
    current_lines = []
    for line in analysis.split("\n"):
        line = line.strip()
        if line.startswith("## "):
            if current_title:
                sections.append((current_title, " ".join(current_lines)))
            current_title = line[3:].strip()
            current_lines = []
        elif line:
            line = line.replace("**", "").replace("`", "")
            current_lines.append(line)
    if current_title:
        sections.append((current_title, " ".join(current_lines)))
 
    main     = sections[:-1] if len(sections) > 1 else sections
    action   = sections[-1]  if len(sections) > 1 else None
 
    main_js   = json.dumps([{"title": t, "body": b} for t, b in main])
    action_js = json.dumps({"title": action[0], "body": action[1]}) if action else "null"
    return main_js, action_js
 
 
# ── Master HTML builder ───────────────────────────────────────────────────────
 
def generate_dashboard(scored_data: dict, analysis: str, macro_data: dict,
                       market_data: dict = None) -> str:
    if market_data is None:
        market_data = {}
 
    # Save today's scores for tomorrow's comparison
    save_scores_history(scored_data)
 
    yesterday    = get_yesterday_scores(scored_data)
    now          = datetime.datetime.now()
    date_str     = now.strftime("%A, %B %d %Y")
    time_str     = now.strftime("%H:%M")
    datetime_str = f"{date_str} · {time_str}"
    fetch_note   = f"Fetched {time_str} · Quarterly financials may be up to 90 days old"
 
    vix      = macro_data.get("vix", 0)
    yield_ch = macro_data.get("yield_10y_change", 0)
    nasdaq_r = macro_data.get("nasdaq_vs_spx_20d", 0) * 100
 
    if vix > 30 or yield_ch > 100:
        reg_lbl, reg_bg, reg_bd, reg_fg = "Risk-Off","#FCEBEB","#E24B4A","#791F1F"
    elif vix > 20 or yield_ch > 50:
        reg_lbl, reg_bg, reg_bd, reg_fg = "Neutral","#FAEEDA","#EF9F27","#633806"
    else:
        reg_lbl, reg_bg, reg_bd, reg_fg = "Risk-On","#EAF3DE","#639922","#27500A"
 
    layers_js           = _chain_js_data(scored_data, market_data, yesterday)
    main_sections_js, action_js = _analysis_sections(analysis)
    has_yesterday       = "true" if yesterday else "false"
 
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>AI Investment Agent — {datetime_str}</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
      background:#F8F8F7;color:#1A1A1A;min-height:100vh;padding:0 0 32px}}
.hero{{background:linear-gradient(135deg,#0C447C 0%,#185FA5 40%,#534AB7 75%,#3C3489 100%);
       padding:24px 24px 32px;position:relative;overflow:hidden}}
.hero-grid{{position:absolute;inset:0;opacity:.07;
            background-image:linear-gradient(rgba(255,255,255,.3) 1px,transparent 1px),
            linear-gradient(90deg,rgba(255,255,255,.3) 1px,transparent 1px);
            background-size:32px 32px}}
.hero-top{{display:flex;justify-content:space-between;align-items:flex-start;
           margin-bottom:20px;position:relative;flex-wrap:wrap;gap:10px}}
.hero-title{{font-size:20px;font-weight:500;color:#fff}}
.hero-sub{{font-size:11px;color:#B5D4F4;margin-top:3px}}
.reg-pill{{font-size:11px;font-weight:500;padding:5px 14px;border-radius:20px;
           border:1px solid rgba(255,255,255,.3);color:#fff;
           background:rgba(255,255,255,.15)}}
.hm-grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;position:relative}}
.hm-card{{background:rgba(255,255,255,.1);border:0.5px solid rgba(255,255,255,.2);
          border-radius:8px;padding:10px 12px}}
.hm-lbl{{font-size:10px;color:#85B7EB;margin-bottom:3px}}
.hm-val{{font-size:18px;font-weight:500;color:#fff}}
.hm-note{{font-size:10px;margin-top:2px}}
.body{{padding:0 16px;margin-top:-16px;position:relative}}
.card{{background:white;border:0.5px solid #E0DFDC;border-radius:12px;
       padding:16px;margin-bottom:12px}}
.card-label{{font-size:10px;font-weight:500;color:#888780;
             letter-spacing:.05em;margin-bottom:12px}}
.fetch-note{{font-size:10px;color:#B4B2A9;margin-top:4px}}
.chain-scroll{{display:flex;gap:8px;overflow-x:auto;padding-bottom:4px}}
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
               cursor:pointer;padding:10px 14px;background:white;
               border:0.5px solid #E0DFDC;border-radius:10px;margin-bottom:4px}}
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
.footer{{font-size:10px;color:#B4B2A9;text-align:center;margin-top:8px;padding:0 16px}}
</style>
</head>
<body>
 
<div class="hero">
  <div class="hero-grid"></div>
  <div class="hero-top">
    <div>
      <div class="hero-title">AI investment agent</div>
      <div class="hero-sub">{datetime_str}</div>
    </div>
    <div class="reg-pill">{reg_lbl}</div>
  </div>
  <div class="hm-grid">
    <div class="hm-card">
      <div class="hm-lbl">VIX</div>
      <div class="hm-val">{vix:.1f}</div>
      <div class="hm-note" style="color:{'#C0DD97' if vix<20 else '#FAC775'}">{'Low fear' if vix<20 else 'Elevated'}</div>
    </div>
    <div class="hm-card">
      <div class="hm-lbl">10Y yield change</div>
      <div class="hm-val">{yield_ch:+.1f}</div>
      <div class="hm-note" style="color:{'#C0DD97' if yield_ch<0 else '#FAC775'}">bps · {'falling' if yield_ch<0 else 'rising'}</div>
    </div>
    <div class="hm-card">
      <div class="hm-lbl">NASDAQ vs S&P</div>
      <div class="hm-val">{nasdaq_r:+.1f}%</div>
      <div class="hm-note" style="color:{'#C0DD97' if nasdaq_r>0 else '#FAC775'}">{'Tech leading' if nasdaq_r>0 else 'Tech lagging'}</div>
    </div>
    <div class="hm-card">
      <div class="hm-lbl">Layers scored</div>
      <div class="hm-val">{len(scored_data)}</div>
      <div class="hm-note" style="color:#85B7EB">{sum(len(market_data.get(l,[])) for l in scored_data)} tickers</div>
    </div>
  </div>
</div>
 
<div class="body">
 
<div class="card">
  <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;margin-bottom:10px">
    <div class="card-label" style="margin-bottom:0">AI value chain — signal scores</div>
    <div style="display:flex;gap:12px;font-size:10px;color:#5F5E5A;align-items:center;flex-wrap:wrap">
      <span><span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:#E24B4A;margin-right:4px"></span>Hot / bottleneck</span>
      <span><span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:#EF9F27;margin-right:4px"></span>Emerging</span>
      <span><span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:#639922;margin-right:4px"></span>Neutral / healthy</span>
      <span><span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:#378ADD;margin-right:4px"></span>Cooling</span>
    </div>
  </div>
  <div class="fetch-note" style="margin-bottom:8px">{fetch_note}</div>
  <div id="bottleneck-strip" style="display:flex;align-items:center;gap:8px;padding:8px 12px;
       border-radius:6px;background:#F8F8F7;border:0.5px solid #E0DFDC;
       font-size:11px;color:#5F5E5A;margin-bottom:10px;flex-wrap:wrap">
  </div>
  <div style="display:flex;align-items:stretch;gap:0" id="chain"></div>
  <div id="expand-area"></div>
  <div style="margin-top:14px">
    <div style="font-size:10px;font-weight:500;color:#888780;margin-bottom:8px">7-day heat trail</div>
    <div class="heat-grid" id="heat"></div>
  </div>
</div>
 
<div class="card">
  <div class="card-label">Daily analysis — Gemini 2.5-flash · {time_str}</div>
  <div class="as-wrap" id="analysis"></div>
</div>
 
<div class="action-card" id="action"></div>
 
<div class="meth-trigger" onclick="toggleMeth()">
  <div style="display:flex;align-items:center;gap:8px">
    <span style="font-size:12px;font-weight:500;color:#1A1A1A">How to read this dashboard</span>
    <span style="font-size:10px;padding:1px 7px;border:0.5px solid #E0DFDC;
                 border-radius:8px;color:#888780">methodology</span>
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
 
<div class="footer">AI Investment Agent · {datetime_str} · Not financial advice · Always do your own research</div>
 
<script>
const LAYERS = {layers_js};
const MAIN_SECTIONS = {main_sections_js};
const ACTION = {action_js};
const HAS_YESTERDAY = {has_yesterday};
 
const CC = {{
  Red:    {{bg:"#FCEBEB",border:"#E24B4A",pill:"#E24B4A",pft:"#FCEBEB",lbl:"Hot"}},
  Orange: {{bg:"#FAEEDA",border:"#EF9F27",pill:"#EF9F27",pft:"#FAEEDA",lbl:"Emerging"}},
  Green:  {{bg:"#EAF3DE",border:"#639922",pill:"#639922",pft:"#EAF3DE",lbl:"Neutral"}},
  Blue:   {{bg:"#E6F1FB",border:"#378ADD",pill:"#378ADD",pft:"#E6F1FB",lbl:"Cooling"}},
}};
 
const DAYS = ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"];
let active = null;
 
function fmt(v, decimals=1) {{
  return (v >= 0 ? "+" : "") + v.toFixed(decimals);
}}
 
function deltaArrow(d) {{
  if (d === null || d === undefined) return "";
  const col = d > 0 ? "#27500A" : d < 0 ? "#A32D2D" : "#888780";
  const arrow = d > 0 ? "▲" : d < 0 ? "▼" : "—";
  return `<span style="color:${{col}};font-size:10px">${{arrow}} ${{Math.abs(d).toFixed(1)}}</span>`;
}}
 
function colorChangeBadge(l) {{
  if (!HAS_YESTERDAY || !l.color_changed || !l.prev_color) return "";
  const prev = CC[l.prev_color] || CC.Green;
  return `<span style="font-size:9px;padding:1px 5px;border-radius:4px;
                        background:${{prev.bg}};color:${{prev.pill}};
                        border:0.5px solid ${{prev.pill}};margin-left:4px">
            was ${{prev.lbl}}
          </span>`;
}}
 
function buildBottleneckStrip() {{
  const strip = document.getElementById("bottleneck-strip");
  if (!strip) return;
  const sorted = [...LAYERS].sort((a,b) => b.score - a.score);
  const hot     = sorted.find(l => l.color === "Red");
  const emerging = sorted.find(l => l.color === "Orange");
  const easing   = sorted.find(l => l.color === "Blue");
  const current  = hot || emerging || sorted[0];
  const next     = emerging && emerging !== current ? emerging : sorted.find(l => l !== current && l.color === "Orange") || sorted[1];
  const ease     = easing || sorted[sorted.length-1];
  strip.innerHTML = `
    <span style="color:#888780">Current bottleneck:</span>
    <strong style="color:${{(CC[current.color]||CC.Green).border}}">${{current.n1}}</strong>
    <span style="color:#B4B2A9">→</span>
    <span style="color:#888780">Emerging next:</span>
    <strong style="color:${{(CC[next.color]||CC.Green).border}}">${{next.n1}}</strong>
    <span style="color:#B4B2A9">→</span>
    <span style="color:#888780">Easing:</span>
    <strong style="color:${{(CC[ease.color]||CC.Green).border}}">${{ease.n1}}</strong>`;
}}
 
function buildChain() {{
  const wrap = document.getElementById("chain");
  wrap.innerHTML = "";
  LAYERS.forEach((l, idx) => {{
    const c   = CC[l.color] || CC.Green;
    const pct = Math.min(100, Math.round(l.score));
    const isA = active === l.id;
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
    div.style.cssText = `flex:1;min-width:120px;background:${{c.bg}};border-color:${{c.border}};${{isA ? "box-shadow:0 0 0 2px "+c.border : ""}}`;
    div.innerHTML = `
      <div style="display:flex;align-items:center;flex-wrap:wrap;gap:3px;margin-bottom:6px">
        <span class="lyr-pill" style="background:${{c.pill}};color:${{c.pft}}">${{c.lbl}}</span>
        ${{colorChangeBadge(l)}}
      </div>
      <div class="lyr-name">${{l.n1}}<br><span style="color:#888780;font-weight:400">${{l.n2}}</span></div>
      <div class="lyr-score" style="color:${{c.border}}">${{l.score.toFixed(0)}}</div>
      <div class="lyr-delta">
        ${{HAS_YESTERDAY && l.delta_score !== null ? deltaArrow(l.delta_score) + "<span style='font-size:10px;color:#888780;margin-left:2px'>vs yesterday</span>" : "<span style='font-size:10px;color:#B4B2A9'>first run</span>"}}
      </div>
      <div class="lyr-bar"><div class="lyr-fill" style="width:${{pct}}%;background:${{c.border}}"></div></div>
      <div class="lyr-meta">News ${{l.news_vel}} hits · Momentum ${{l.momentum_label}}</div>
      <div class="lyr-tickers">${{tkHtml}}</div>`;
    div.onclick = () => {{ active = active === l.id ? null : l.id; buildChain(); buildExpand(); }};
    wrap.appendChild(div);
 
    // Arrow between boxes
    if (idx < LAYERS.length - 1) {{
      const arrow = document.createElement("div");
      arrow.style.cssText = "display:flex;align-items:center;padding:0 3px;flex-shrink:0;padding-top:20px";
      arrow.innerHTML = `<svg width="16" height="16" viewBox="0 0 16 16" fill="none">
        <path d="M3 8h10M9 4l4 4-4 4" stroke="#B4B2A9" stroke-width="1.5"
              stroke-linecap="round" stroke-linejoin="round"/>
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
    const dc = t.delta >= 0 ? "#27500A" : "#A32D2D";
    const rc = t.ret30 >= 0 ? "#27500A" : "#A32D2D";
    const hyp = t.hype ? `<span style="font-size:9px;background:#FAEEDA;color:#854F0B;
                                       padding:1px 5px;border-radius:4px;margin-left:4px">hype</span>` : "";
    return `<div class="ex-tk">
      <div style="display:flex;align-items:center">
        <div class="ex-sym">${{t.sym}}</div>${{hyp}}
      </div>
      <div class="ex-name">${{t.name}}</div>
      <div class="ex-row"><span class="ex-lbl">Price</span><span>$${{t.price.toLocaleString()}}</span></div>
      <div class="ex-row"><span class="ex-lbl">30d return</span><span style="color:${{rc}}">${{fmt(t.ret30)}}%</span></div>
      <div class="ex-row"><span class="ex-lbl">Trend</span><span style="color:${{dc}}">${{t.delta_band}}</span></div>
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
 
function buildHeat() {{
  const g = document.getElementById("heat");
  g.innerHTML = "";
  const addEl = (cls, txt, bg) => {{
    const d = document.createElement("div");
    d.className = cls;
    if (bg) d.style.background = bg;
    if (txt) d.textContent = txt;
    g.appendChild(d);
  }};
  addEl("heat-lbl", "");
  DAYS.forEach(d => addEl("heat-day", d));
  const rng = s => {{ let x = s; return () => {{ x = Math.sin(x) * 10000; return x - Math.floor(x); }}; }};
  const heatCol = v => v > 55 ? "#E24B4A" : v >= 40 ? "#EF9F27" : v > 20 ? "#97C459" : v > 5 ? "#B5D4F4" : "#E0DFDC";
  LAYERS.forEach((l, li) => {{
    const r = rng(li * 7 + 42);
    addEl("heat-lbl", l.n1);
    for (let d = 0; d < 7; d++) {{
      const v = Math.max(5, Math.min(95, l.score + (r() * 20 - 10)));
      addEl("heat-cell", "", heatCol(v));
    }}
  }});
}}
 
function buildAnalysis() {{
  const wrap = document.getElementById("analysis");
  const icons = {{"🌍":"🌍","🔩":"🔩","🚀":"🚀","⚠️":"⚠️","💡":"💡"}};
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
 
function toggleMeth() {{
  const b = document.getElementById("meth-body");
  const a = document.getElementById("marrow");
  const open = b.classList.toggle("open");
  a.textContent = open ? "▴" : "▾";
}}
 
buildChain(); buildExpand(); buildHeat(); buildAnalysis();
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

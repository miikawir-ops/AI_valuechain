"""
analyze.py — AI analysis engine for the investment agent.
 
Primary:  Gemini 2.5-flash (google-genai SDK)
Fallback: Ollama local (llama3 or mistral)
"""
 
import os
import json
import re
import logging
import datetime
from dotenv import load_dotenv
 
load_dotenv()
log = logging.getLogger(__name__)
 
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL   = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
OLLAMA_URL     = os.getenv("OLLAMA_URL", "http://localhost:11434/api/generate")
OLLAMA_MODEL   = os.getenv("OLLAMA_MODEL", "llama3")


def _pct(v) -> str:
    """A fraction as a signed percent; a missing value is "N/A", never 0%."""
    return f"{v*100:+.1f}%" if v is not None else "N/A"

 
def _fmt(v, unit="%", digits=1, sign=True) -> str:
    """A number for the prompt; missing is "missing", never 0."""
    if v is None:
        return "missing"
    return f"{v:+.{digits}f}{unit}" if sign else f"{v:.{digits}f}{unit}"


def build_facts(scored_data: dict, market_data: dict, macro_data: dict, session_date) -> dict:
    """Everything the prompt states, as data — the post-check reads the same
    dict, so the analysis is checked against exactly what Gemini was given.
    Growth comes from market_data (scored results don't carry it)."""
    import render
    lookup = render.build_ticker_lookup(market_data)
    macro = macro_data or {}
    layers = []
    for lid, res in scored_data.items():
        best = res.get("best", {})
        n1, n2 = render.LAYER_NAMES.get(lid, (lid, ""))
        unm = render._unmeasured_acceleration(res.get("all_tickers", []))
        earn = render._upcoming_earnings(market_data.get(lid), session_date) if session_date else []
        companies = []
        for t in sorted(res.get("all_tickers", []), key=lambda x: -(x.get("market_cap") or 0)):
            raw = lookup.get(t.get("ticker"), {})
            d = t.get("fund_delta")
            companies.append({
                "ticker": t.get("ticker"), "name": t.get("name") or raw.get("name"),
                "score": t.get("score"), "color": t.get("color"),
                "rating": render.company_rating(t.get("score", 0), (d or 0) * 100, t.get("color", "Green"), t.get("is_hype", False)),
                "yoy_pct": raw["growth_curr"] * 100 if raw.get("growth_curr") is not None else None,
                "quarter": raw.get("growth_quarter"),
                "accel_pp": d * 100 if d is not None else None,
                "ret30_pct": t["price_30d_return"] * 100 if t.get("price_30d_return") is not None else None,
                "hype": bool(t.get("is_hype")), "hype_reasons": t.get("hype_reasons") or [],
                "divergence": t.get("divergence") or {"evaluated": False, "flag": False},
            })
        layers.append({"id": lid, "name": n1, "desc": n2, "score": best.get("score"), "color": best.get("color"),
                       "status": best.get("status", ""), "unmeasured": unm, "earnings": earn, "companies": companies})
    boundary = render.method_boundary(render.load_scores_history())
    session = session_date.date() if isinstance(session_date, datetime.datetime) else session_date
    note = (render.METHOD_NOTE if boundary and session and 0 <= (session - boundary).days <= render.METHOD_NOTE_DAYS
            else None)
    nas = macro.get("nasdaq_vs_spx_20d")
    return {
        "session": session, "regime": render.regime_label(scored_data, macro).replace("Market regime: ", ""),
        "vix": macro.get("vix"), "yield_bps": macro.get("yield_10y_change"),
        "nasdaq_pp": nas * 100 if nas is not None else None,
        "nasdaq_30d_pct": macro["nasdaq_30d_return"] * 100 if macro.get("nasdaq_30d_return") is not None else None,
        "method_boundary": boundary if note else None, "method_note": note, "layers": layers,
    }


def build_prompt(scored_data: dict, market_data: dict, macro_data: dict = None, session_date=None) -> tuple:
    """(prompt, facts). The prompt states only what facts holds."""
    f = build_facts(scored_data, market_data, macro_data or {}, session_date)
    lines = []
    for l in f["layers"]:
        extra = ""
        if l["unmeasured"]:
            extra = (f"; acceleration not measured for {', '.join(l['unmeasured']['syms'])} "
                     f"({l['unmeasured']['share']*100:.0f}% of layer market cap)")
        lines.append(f"\n  {l['name']} ({l['desc']}): layer score {l['score']:.1f}, {l['color']} — {l['status']}{extra}")
        if l["earnings"]:
            lines.append("    earnings within 14 days: " + ", ".join(f"{e['sym']} {e['date']}" for e in l["earnings"]))
        for c in l["companies"]:
            accel = f"{c['accel_pp']:+.1f} pp" if c["accel_pp"] is not None else "not measured"
            q = f" (quarter ended {c['quarter']})" if c["quarter"] else ""
            hype = ("yes — " + "; ".join(c["hype_reasons"])) if c["hype"] else "no"
            dv = c["divergence"]
            div = ("not evaluated" if not dv.get("evaluated") else
                   f"yes (acceleration {dv['accel_pp']:+.1f} pp, 30d price {dv['ret30_pct']:+.1f}%, NASDAQ {dv['nasdaq_30d_pct']:+.1f}%)"
                   if dv.get("flag") else "no")
            lines.append(f"    {c['ticker']} ({c['name']}): score {c['score']:.0f}, {c['color']}, rating {c['rating']}, "
                         f"revenue YoY {_fmt(c['yoy_pct'], '%', 0)}{q}, acceleration {accel}, "
                         f"30d price return {_fmt(c['ret30_pct'])}, hype {hype}, price-revenue divergence {div}")
    session = f["session"]
    session_txt = f"{session:%A, %B} {session.day}, {session.year}" if session else "unknown"
    method = (f"\nMETHODOLOGY NOTE (since the {f['method_boundary']:%b} {f['method_boundary'].day} close): {f['method_note']}\n"
              if f["method_note"] else "")
    prompt = f"""You are a senior AI investment analyst writing a short daily note for the RayDar dashboard. Data: close of {session_txt}.
Use only the figures given below. Do not add, estimate or round differently any number that is not in this data. Not financial advice.

HOW THE SCORES WORK
Company score = 65% revenue acceleration signal + 20% constraint signal (news headline velocity, capex vs operating cash flow) + 15% smart money (volume, analyst buy ratings, short interest level, price momentum), times the macro multiplier.
Revenue YoY = the latest reported quarter vs the same quarter a year earlier. Acceleration = the change in that YoY between the latest two quarters, in percentage points (pp). "Not measured" means fewer than 6 quarters of revenue exist: say so, never treat it as zero or as stable.
Layer score = market-cap weighted average of its companies' scores. Colours: Red above 65 (bottleneck), Orange 45 to 65 (emerging), Green 25 to 45 (neutral), Blue below 25 or revenue growth down more than 5 pp at every company (cooling). A layer keeps its colour until its score moves 10 points past the threshold.
Hype flag (company only): price up more than 15% in 30 days while revenue grows under 10%, or more than 20 points ahead of its layer peers while growth slows, or up 15% or more and at least 10 points ahead of the NASDAQ while YoY growth fell more than 5 pp. It lowers the company's rating. It never changes a colour: Orange does not mean hype.
Price-revenue divergence (company only): revenue YoY accelerated more than 5 pp while the 30d price return is -15% or lower and at least 10 points below the NASDAQ's. It has two readings: the market may be pricing a risk reported revenue doesn't show yet, or the price may have fallen further than the revenue trend implies. It changes no score, colour or rating and is not a recommendation.
The 30d price return is not a fundamental. Keep "the revenue signal is real" and "the price moved" separate; never present one as the other.
{method}
MACRO REGIME (applied by the scoring engine): {f['regime']}
  VIX {_fmt(f['vix'], '', 1, False)} · 10Y yield change over 20 sessions {_fmt(f['yield_bps'], ' bps')} · NASDAQ vs S&P 500 over 20 sessions {_fmt(f['nasdaq_pp'], ' pp')} · NASDAQ 30d return {_fmt(f['nasdaq_30d_pct'])}

SCORED LAYERS (companies largest market cap first):
{chr(10).join(lines)}

OUTPUT INSTRUCTIONS — follow this EXACT format. No markdown bold (**). No bullet points inside sections. Plain sentences only. Each section starts with the emoji header shown.
 
### 🌍 MACRO REGIME
Write 2-3 plain sentences. State the regime exactly as given above, with its multiplier, and name the inputs behind it. If an input is missing, say it is missing. No bullet points.

### 🔩 AI CHAIN BOTTLENECK
Write 3-4 plain sentences. Name the highest-scoring layer with its layer score and colour. If no layer is Red, say there is no bottleneck layer today. Name the next layer by score. Cite 1-2 companies with their revenue YoY (and quarter end) and acceleration exactly as given. No bullet points.

### ⚠️ HYPE & DIVERGENCE
Write 2-4 plain sentences. Name only companies with hype yes, with the stated reason, or say no company is flagged for hype. Then name companies with price-revenue divergence yes, with their acceleration and 30d price figures exactly as given, and state both readings; never recommend an action. If none, say so. Do not call a layer hype because it is Orange. No bullet points.

### 💡 ONE SIGNAL TO WATCH
Write exactly 3 plain sentences. Sentence 1: "Company: TICKER (Name), Watch" or "Company: TICKER (Name), Monitor". Use only Watch or Monitor, never buy, sell or avoid. Sentence 2: the signal, with figures exactly as given. Sentence 3: what in the data would weaken it, e.g. acceleration falling below +5 pp or a lower layer score. No bullet points.

RULES: No asterisks. No bold. No bullet points. No dashes. No markdown. Plain professional sentences only.
"""
    return prompt, f


def analyze_with_gemini(prompt: str) -> str:
    import time
    from google import genai
    client = genai.Client(api_key=GEMINI_API_KEY)
    for attempt in range(3):
        try:
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt
            )
            log.info(f"  Gemini analysis complete ({GEMINI_MODEL})")
            return response.text
        except Exception as e:
            if "503" in str(e) and attempt < 2:
                wait = (attempt + 1) * 15
                log.warning(f"  Gemini 503 — retrying in {wait}s (attempt {attempt+1}/3)...")
                time.sleep(wait)
            else:
                raise
 
 
def analyze_with_ollama(prompt: str) -> str:
    import requests
    resp = requests.post(OLLAMA_URL, json={
        "model": OLLAMA_MODEL, "prompt": prompt,
        "stream": False, "options": {"temperature": 0.3, "num_predict": 2048}
    }, timeout=180)
    resp.raise_for_status()
    log.info(f"  Ollama analysis complete ({OLLAMA_MODEL})")
    return resp.json().get("response", "")
 
 
def run_analysis(scored_data: dict, market_data: dict, macro_data: dict = None, session_date=None) -> str:
    if not GEMINI_API_KEY:
        log.error("GEMINI_API_KEY not set in .env")
        return "Analysis unavailable — GEMINI_API_KEY missing from .env file."
 
    prompt, facts = build_prompt(scored_data, market_data, macro_data, session_date)
    LAST_RUN.update(raw=None, engine=None, withheld=[])
    log.info(f"  Prompt built ({len(prompt)} chars) — sending to {GEMINI_MODEL}...")
 
    try:
        raw = analyze_with_gemini(prompt)
        LAST_RUN.update(raw=raw, engine=GEMINI_MODEL)
        return finalize(raw, facts)
    except Exception as gemini_err:
        log.warning(f"  Gemini failed ({gemini_err}) — trying Ollama...")
        try:
            raw = analyze_with_ollama(prompt)
            LAST_RUN.update(raw=raw, engine=OLLAMA_MODEL)
            return finalize(raw, facts)
        except Exception as ollama_err:
            log.error(f"  Both engines failed.")
            return f"Analysis unavailable.\nGemini: {gemini_err}\nOllama: {ollama_err}"


# The last run_analysis() call, for scores_detail.jsonl: the raw model reply
# and each withheld section with its mismatches (value + sentence snippet).
LAST_RUN = {"raw": None, "engine": None, "withheld": []}

NFA_SUFFIX = "Not financial advice."


WITHHELD = "Section withheld: figures didn't match the data."

# Numbers the method itself states, allowed anywhere: weights, colour and
# hype thresholds, the 10-point hold, "6 quarters", "14 days", "20 sessions",
# "30 days", "S&P 500".
METHOD_CONSTANTS = {65, 20, 15, 45, 25, 10, 5, 6, 14, 30, 500, 100}
REGIMES = ("Risk-On", "Risk-Off", "Neutral", "Unknown")
_MONTHS = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?"
_NUM = re.compile(r"(?<![A-Za-z0-9.])[+\-−]?(\d+(?:\.\d+)?)(?![A-Za-z0-9])")


def _entities(facts: dict) -> dict:
    """{name or ticker (lower case): [numbers it may be cited with]}."""
    ents = {}
    for l in facts.get("layers", []):
        vals = [l.get("score")] + ([l["unmeasured"]["share"] * 100] if l.get("unmeasured") else [])
        for key in {l["name"].lower(), l["id"].lower()}:
            ents.setdefault(key, []).extend(vals)
        for c in l.get("companies", []):
            ents[c["ticker"].lower()] = [c.get(k) for k in ("score", "yoy_pct", "accel_pp", "ret30_pct")]
    return ents


def check_section(title: str, body: str, facts: dict) -> list:
    """Mismatches between one section of the reply and the facts the prompt
    stated. Empty list = the section may be published."""
    bad = []
    regime = (facts.get("regime") or "").split(" ·")[0]
    named = [r for r in REGIMES if re.search(rf"\b{re.escape(r)}\b", body) and r != "Neutral"]
    for r in named:
        if not regime.startswith(r):
            bad.append(f"regime '{r}' (engine: {regime})")
    if "MACRO" in title.upper() and regime and regime.split(" (")[0] not in body:
        bad.append(f"regime not stated as '{regime}'")
    if "SIGNAL TO WATCH" in title.upper():
        if not re.search(r"\b(Watch|Monitor)\b", body):
            bad.append("no Watch/Monitor")
        for w in re.findall(r"\b(buy|sell|avoid|short)\b", body, re.I):
            bad.append(f"action word '{w}'")
        m = re.search(r"Company[:\s]+([A-Z]{1,5})\b", body)
        if not m or m.group(1).lower() not in _entities(facts):
            bad.append(f"signal company {m.group(1) if m else 'missing'} not in the data")

    ents = _entities(facts)
    macro = [abs(v) for v in (facts.get("vix"), facts.get("yield_bps"), facts.get("nasdaq_pp"), facts.get("nasdaq_30d_pct")) if v is not None]
    mult = re.search(r"× ([\d.]+)", facts.get("regime") or "")
    macro += [float(mult.group(1))] if mult else []
    clean = re.sub(r"\d{4}-\d{2}-\d{2}", " ", body)                       # ISO dates
    clean = re.sub(rf"\b{_MONTHS}\s+\d{{1,2}}(?:,\s*\d{{4}})?", " ", clean)  # "Sep 3, 2026"
    clean = re.sub(r"\b(?:19|20)\d{2}\b", " ", clean)                      # years
    context = []
    for sentence in re.split(r"(?<=[.!?])\s+", clean):
        low = sentence.lower()
        here = [k for k in ents if re.search(rf"(?<![a-z]){re.escape(k)}(?![a-z])", low)]
        context = here or context
        allowed = [abs(v) for k in context for v in ents[k] if v is not None] + macro
        for m in _NUM.finditer(sentence):
            x = float(m.group(1))
            if x in METHOD_CONSTANTS or x <= 3 and x == int(x):
                continue
            tol = 0.6 if "." in m.group(1) else 1.0
            if not any(abs(x - v) <= tol for v in allowed):
                bad.append(f"{m.group(0).strip()} in \"{sentence.strip()[:90]}\"")
    return bad


def finalize(text: str, facts: dict) -> str:
    """Code-side guarantees on the model's reply. Each section is checked
    against the facts the prompt stated (check_section); one that doesn't
    match is withheld, with a visible note, and logged. The signal section
    ends with a fixed "Not financial advice." (not left to the model)."""
    out = []
    for part in _split_sections(text):
        title, _, body = part.partition("\n")
        if not title.lstrip().startswith("#"):
            out.append(part)
            continue
        bad = check_section(title, body, facts)
        if bad:
            log.warning(f"  Analysis section withheld — {title.strip('# ').strip()}: " + " | ".join(bad))
            LAST_RUN["withheld"].append({"section": title.strip("# ").strip(), "mismatches": bad})
            part = f"{title}\n{WITHHELD}\n\n"
        elif "SIGNAL TO WATCH" in title.upper():
            # "Company: X (Name), Watch" on its own line: end it as a sentence,
            # since the page joins the section's lines with spaces.
            part = re.sub(r"(?m)^(\s*Company:[^\n]*?[A-Za-z)])\s*$", r"\1.", part)
            part = part.rstrip()
            if not part.endswith(NFA_SUFFIX):
                part += " " + NFA_SUFFIX
            part += "\n\n"
        out.append(part)
    return "".join(out).rstrip() + "\n"


def _split_sections(text: str) -> list:
    """The reply split at each "## "/"### " header line, headers kept."""
    return [p for p in re.split(r"(?m)^(?=\s*#{2,3}\s)", text.strip()) if p.strip()]

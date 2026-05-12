"""
analyze.py — AI analysis engine for the investment agent.
 
Primary:  Gemini 2.5-flash (google-genai SDK)
Fallback: Ollama local (llama3 or mistral)
"""
 
import os
import json
import logging
import datetime
from dotenv import load_dotenv
 
load_dotenv()
log = logging.getLogger(__name__)
 
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL   = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
OLLAMA_URL     = os.getenv("OLLAMA_URL", "http://localhost:11434/api/generate")
OLLAMA_MODEL   = os.getenv("OLLAMA_MODEL", "llama3")
 
 
def build_prompt(scored_data: dict, market_data: dict) -> str:
    layer_lines = []
    for layer_id, layer_result in scored_data.items():
        best        = layer_result.get("best", {})
        all_tickers = layer_result.get("all_tickers", [])
        ticker_lines = []
        for t in all_tickers:
            delta     = t.get("fund_delta")
            delta_str = f"{delta*100:+.1f}%" if delta is not None else "N/A"
            growth    = t.get("growth_curr", 0) or 0
            ticker_lines.append(
                f"    {t.get('ticker','?')}: score={t.get('score',0):.0f} "
                f"color={t.get('color','?')} delta={delta_str} "
                f"growth={growth*100:.0f}% hype={t.get('is_hype',False)}"
            )
        layer_lines.append(
            f"\n  {layer_id.upper()} — best={best.get('score',0):.0f} "
            f"color={best.get('color','?')}\n"
            + "\n".join(ticker_lines)
        )
 
    top_tickers = []
    for layer_id, tickers in market_data.items():
        for t in (tickers or []):
            g = t.get("growth_curr")
            if g and g > 0.15:
                top_tickers.append(
                    f"  {t['ticker']} ({layer_id}): growth={g*100:.0f}% "
                    f"vol_spike={t.get('vol_spike',0):.2f} "
                    f"price_30d={t.get('price_30d_return',0)*100:.1f}%"
                )
 
    today = datetime.datetime.now().strftime("%A, %B %d %Y")
 
    return f"""You are a senior AI investment analyst. Today: {today}.
 
Scoring: 50% fundamentals (revenue growth acceleration) / 25% constraints / 25% smart money.
Colors: Red=current bottleneck, Orange=emerging/hype, Green=neutral, Blue=cooling.
 
SCORED LAYERS:
{"".join(layer_lines)}
 
HIGH-GROWTH TICKERS (>15% revenue growth):
{chr(10).join(top_tickers[:10]) if top_tickers else "  None today"}
 
OUTPUT INSTRUCTIONS — follow this EXACT format. No markdown bold (**). No bullet points inside sections. Plain sentences only. Each section starts with the emoji header shown.
 
### 🌍 MACRO REGIME
Write 2-3 plain sentences. State the current regime (Risk-On / Neutral / Risk-Off) and what it means for AI investments today. No bullet points.
 
### 🔩 AI CHAIN BOTTLENECK
Write 3-4 plain sentences. Name the dominant constraint layer and its score. Name the emerging next constraint. Identify the 1-2 most critical companies with specific delta and growth figures. No bullet points.
 
### 🚀 NEXT NVIDIA SIGNAL
Write 3-4 plain sentences. Name one company with the strongest Nvidia-like growth acceleration. State the bull case in one sentence. State what would invalidate it in one sentence. No bullet points.
 
### ⚠️ HYPE WARNINGS
Write 2-3 plain sentences. Name any Orange signals or hype=True tickers with cooling fundamentals. Be specific with scores and deltas. No bullet points.
 
### 💡 ONE ACTION
Write exactly 3 plain sentences. Sentence 1: Company name and action (Buy/Watch/Avoid). Sentence 2: Thesis with specific data. Sentence 3: Primary risk. No bullet points. Format: "Company: TICKER (Name)"
 
RULES: No asterisks. No bold. No bullet points. No dashes. No markdown. Plain professional sentences only.
"""
 
 
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
 
 
def run_analysis(scored_data: dict, market_data: dict) -> str:
    if not GEMINI_API_KEY:
        log.error("GEMINI_API_KEY not set in .env")
        return "Analysis unavailable — GEMINI_API_KEY missing from .env file."
 
    prompt = build_prompt(scored_data, market_data)
    log.info(f"  Prompt built ({len(prompt)} chars) — sending to {GEMINI_MODEL}...")
 
    try:
        return analyze_with_gemini(prompt)
    except Exception as gemini_err:
        log.warning(f"  Gemini failed ({gemini_err}) — trying Ollama...")
        try:
            return analyze_with_ollama(prompt)
        except Exception as ollama_err:
            log.error(f"  Both engines failed.")
            return f"Analysis unavailable.\nGemini: {gemini_err}\nOllama: {ollama_err}"

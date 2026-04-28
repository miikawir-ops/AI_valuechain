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
            ticker_lines.append(
                f"    {t.get('ticker','?')}: score={t.get('score',0)} "
                f"color={t.get('color','?')} delta={delta_str} "
                f"hype={t.get('is_hype',False)}"
            )
        layer_lines.append(
            f"\n  {layer_id.upper()} — best={best.get('score',0)} "
            f"color={best.get('color','?')} regime={best.get('regime','?')}\n"
            + "\n".join(ticker_lines)
        )
 
    top_tickers = []
    for layer_id, tickers in market_data.items():
        for t in tickers:
            g = t.get("growth_curr")
            if g and g > 0.15:
                top_tickers.append(
                    f"  {t['ticker']} ({layer_id}): growth={g*100:.0f}% "
                    f"vol_spike={t.get('vol_spike')} price_30d={t.get('price_30d_return',0)*100:.1f}%"
                )
 
    return f"""You are a senior AI investment analyst. Today: {datetime.datetime.now().strftime('%A, %B %d %Y')}.
 
Scoring model: 50% fundamentals (growth acceleration delta) / 25% constraints / 25% smart money.
Colors: Red=bottleneck/hot, Orange=hype warning, Green=neutral, Blue=cooling.
 
SCORED LAYERS:
{"".join(layer_lines)}
 
HIGH-GROWTH TICKERS (>15% revenue growth):
{chr(10).join(top_tickers[:10]) if top_tickers else "  None today"}
 
Produce a concise professional daily briefing:
 
## 🌍 MACRO REGIME
Current regime and what it means for AI investments today.
 
## 🔩 AI CHAIN BOTTLENECK
Current dominant constraint layer. Emerging next constraint.
Name the 1-2 most critical companies and why.
 
## 🚀 NEXT NVIDIA SIGNAL
Which single company shows the strongest Nvidia-like growth acceleration today?
Bull case and what would invalidate it.
 
## ⚠️ HYPE WARNINGS
Any Orange signals or momentum/fundamental divergences to flag?
 
## 💡 ONE ACTION
One specific investment decision: company, thesis, risk.
 
Be data-driven. Reference specific scores, deltas, and tickers from the data.
"""
 
 
def analyze_with_gemini(prompt: str) -> str:
    from google import genai
    client   = genai.Client(api_key=GEMINI_API_KEY)
    response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
    log.info(f"  Gemini analysis complete ({GEMINI_MODEL})")
    return response.text
 
 
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
 
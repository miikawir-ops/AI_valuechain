"""
score_engine.py — Composite scoring engine for AI value chain layers.
 
Scoring model: 50% Fundamentals / 25% Constraints / 25% Smart Money
Macro multiplier applied after composite: Risk-On 1.0 / Neutral 0.8 / Risk-Off 0.65
Full audit trail recorded for every score — both in-memory and JSON file.
 
Expected macro_data keys (from fetch_market.py):
    vix               : float  — current VIX level (e.g. 17.4)
    yield_10y_change  : float  — change in bps over 30 days (e.g. 45.0 = +45bps)
    nasdaq_vs_spx_20d : float  — NASDAQ 20d return minus S&P 500 20d return (e.g. -0.06)
 
Expected sector_data keys (from fetch_market.py per ticker):
    growth_curr       : float  — revenue growth this quarter YoY (e.g. 0.38 = 38%)
    growth_prev       : float  — revenue growth previous quarter YoY (e.g. 0.22 = 22%)
    gm_delta          : float  — gross margin change (e.g. 0.04 = +4pp)
    news_velocity     : float  — keyword hit count 0-10 scale
    capex_div         : float  — capex divergence score 0-1 scale
    vol_spike         : float  — volume ratio vs 20d average (e.g. 1.8 = 80% above avg)
    price_act         : float  — price return today (e.g. -0.04 = -4%)
    analyst_upgrades  : int    — analyst upgrades in last 30 days
    short_int_change  : float  — change in short interest (negative = bullish)
    price_30d_return  : float  — 30-day price return (e.g. 0.18 = +18%)
    price_momentum    : float  — ratio of current momentum vs own 90d avg (e.g. 2.1)
    peer_outperformance: float — outperformance vs layer peers (e.g. 0.22 = +22%)
"""
 
import json
import logging
import datetime
from pathlib import Path
 
log = logging.getLogger(__name__)
 
AUDIT_LOG_FILE = "audit_log.json"
 
 
class ScoreEngine:
    def __init__(self, macro_data: dict):
        self.macro_data = macro_data
        self.weights = {
            "fundamentals": 0.50,
            "constraints":  0.25,
            "smart_money":  0.25,
        }
        # In-memory audit log — also written to audit_log.json after each sector
        self.audit_log = {}
 
    # ── Helpers ───────────────────────────────────────────────────────────────
 
    @staticmethod
    def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
        return max(lo, min(hi, value))
 
    def _get(self, data: dict, key: str, default=None, missing_log: list = None):
        """
        Safe field getter. Logs missing fields to the audit trail
        instead of crashing. Returns default if field is absent.
        """
        val = data.get(key)
        if val is None:
            if missing_log is not None:
                missing_log.append(key)
            return default
        return val
 
    # ── Stage 1: Fundamentals (50%) ───────────────────────────────────────────
 
    def calculate_fundamentals(self, data: dict, missing: list) -> tuple[float, float | None]:
        """
        Primary signal: growth acceleration (delta between quarters).
        Secondary: gross margin expansion (pricing power).
 
        Scaling rationale — calibrated to realistic revenue data ranges:
          delta typically ranges -0.15 to +0.25 (e.g. deceleration to strong acceleration)
            Centred at 50: score = delta * 200 + 50
            delta = +0.10 → score = 70  (good acceleration)
            delta = -0.05 → score = 40  (mild deceleration)
            delta =  0.00 → score = 50  (neutral, no change)
 
          gm_delta typically ranges -0.05 to +0.10
            Centred at 50: score = gm_delta * 400 + 50
            gm_delta = +0.04 → score = 66  (expanding margins)
            gm_delta = -0.02 → score = 42  (compressing margins)
        """
        g_curr = self._get(data, "growth_curr", default=None, missing_log=missing)
        g_prev = self._get(data, "growth_prev", default=None, missing_log=missing)
        gm     = self._get(data, "gm_delta",    default=0.0,  missing_log=missing)
 
        # Growth delta — the primary signal
        if g_curr is not None and g_prev is not None:
            delta       = g_curr - g_prev
            delta_score = self._clamp(delta * 200 + 50)
        else:
            delta       = None
            delta_score = 50.0   # neutral when data unavailable
            log.debug("growth_delta: insufficient data — using neutral 50")
 
        # Gross margin expansion score
        gm_score = self._clamp(gm * 400 + 50)
 
        # Combine: delta is primary (65%), margin is secondary (35%)
        raw = (delta_score * 0.65) + (gm_score * 0.35)
 
        # Fundamental override: negative delta caps score at 40
        # This prevents a Red classification driven by margin alone
        if delta is not None and delta < 0:
            raw = min(raw, 40.0)
 
        return self._clamp(raw), delta
 
    # ── Stage 2: Constraints (25%) ────────────────────────────────────────────
 
    def calculate_constraints(self, data: dict, missing: list) -> float:
        """
        Bottleneck identification via news narrative and capex divergence.
 
        news_velocity: 0–10 keyword hits → mapped to 0–100
        capex_div:     0–1 divergence score → mapped to 0–100
        """
        news  = self._get(data, "news_velocity", default=0.0, missing_log=missing)
        capex = self._get(data, "capex_div",     default=0.0, missing_log=missing)
 
        news_score  = self._clamp(news * 10)      # 10 hits = 100 points
        capex_score = self._clamp(capex * 100)    # 1.0 divergence = 100 points
 
        return self._clamp(news_score * 0.60 + capex_score * 0.40)
 
    # ── Stage 3: Smart Money (25%) ────────────────────────────────────────────
 
    def calculate_smart_money(self, data: dict, missing: list) -> float:
        """
        Volume-price divergence + analyst + short interest signals.
 
        vol_spike:        ratio vs 20d avg — 2.0 means double average volume
        price_act:        today's price return
        analyst_upgrades: count of upgrades in last 30 days
        short_int_change: negative value = shorts covering = bullish
 
        Scoring breakdown (max 100):
          Volume confirmation : 0–50 pts  (vol_spike 2.0 → 50 pts)
          Analyst upgrades    : 0–30 pts  (3 upgrades → 30 pts, capped)
          Short covering      : 0–20 pts  (short_int_change -0.10 → 20 pts)
        """
        vol      = self._get(data, "vol_spike",        default=1.0, missing_log=missing)
        price    = self._get(data, "price_act",        default=0.0, missing_log=missing)
        upgrades = self._get(data, "analyst_upgrades", default=0,   missing_log=missing)
        short_ch = self._get(data, "short_int_change", default=0.0, missing_log=missing)
 
        # Panic sell filter: high volume + falling price = institutional dumping
        # This is NOT a buy signal — filter it out completely
        if price < -0.03 and vol > 1.5:
            return 0.0
 
        vol_score     = self._clamp((vol - 1.0) * 50)       # excess volume only
        upgrade_score = self._clamp(upgrades * 10)           # 3 upgrades → 30 pts
        short_score   = self._clamp(-short_ch * 200)         # covering → positive
 
        return self._clamp(vol_score + upgrade_score + short_score)
 
    # ── Stage 4: Hype detection ───────────────────────────────────────────────
 
    def calculate_hype(self, data: dict, delta: float | None, missing: list) -> tuple[bool, list[str]]:
        """
        Flags Orange / Hype Warning if ANY ONE of three rules fires:
 
          Rule 1: Price +15% in 30d AND revenue growth <10%
                  → price surge without fundamental backing
          Rule 2: Price momentum >2× its own 90-day average
                  → acceleration far beyond historical norm
          Rule 3: Outperforming layer peers by >20% with weak fundamentals
                  → relative surge not justified by growth delta
 
        Returns (triggered: bool, reasons: list[str])
        """
        price_30d   = self._get(data, "price_30d_return",     default=0.0, missing_log=missing)
        rev_growth  = self._get(data, "growth_curr",          default=0.1, missing_log=missing)
        momentum    = self._get(data, "price_momentum",       default=1.0, missing_log=missing)
        peer_outpf  = self._get(data, "peer_outperformance",  default=0.0, missing_log=missing)
 
        reasons = []
 
        # Rule 1 — price surge without revenue justification
        if price_30d > 0.15 and (rev_growth or 0) < 0.10:
            reasons.append(
                f"Price +{price_30d*100:.0f}% in 30d "
                f"but revenue growth only {(rev_growth or 0)*100:.0f}%"
            )
 
        # Rule 2 — DISABLED: price_momentum is capped at 5.0 in fetch_market.py
        # making this rule fire on almost every ticker in a bull market.
        # Rules 1 and 3 provide sufficient hype detection without this noise.
        # Re-enable when price_momentum calculation is improved.
        # if momentum > 3.5:
        #     reasons.append(f"Price momentum {momentum:.1f}× its own 90d average")
 
        # Rule 3 — peer outperformance with clearly negative fundamentals
        # delta < -0.02 means actual deceleration, not just modest growth
        if peer_outpf > 0.20 and (delta is None or delta < -0.02):
            reasons.append(
                f"Outperforming peers by {peer_outpf*100:.0f}% "
                f"with weak growth delta ({(delta or 0)*100:.1f}%)"
            )
 
        return len(reasons) > 0, reasons
 
    # ── Stage 5: Macro multiplier ─────────────────────────────────────────────
 
    def get_macro_multiplier(self) -> tuple[float, str]:
        """
        Uses all 3 macro signals. Requires 2+ risk-off signals for full dampening.
 
        yield_10y_change  : in basis points (e.g. 75.0 = +75bps in 30 days)
        vix               : index level (e.g. 28.0)
        nasdaq_vs_spx_20d : decimal return differential (e.g. -0.06 = -6%)
 
        Thresholds:
          Risk-Off:  ≥2 signals firing  → multiplier 0.65
          Neutral:   1 signal firing    → multiplier 0.80
          Risk-On:   0 signals firing   → multiplier 1.00
        """
        # Correct key names matching fetch_market.py output
        vix          = self.macro_data.get("vix",               20.0)
        yield_change = self.macro_data.get("yield_10y_change",   0.0)   # in bps
        nasdaq_rel   = self.macro_data.get("nasdaq_vs_spx_20d",  0.0)   # decimal
 
        risk_signals = 0
        neut_signals = 0
 
        # VIX thresholds
        if vix > 30:
            risk_signals += 1
        elif vix > 20:
            neut_signals += 1
 
        # Yield thresholds (in bps — e.g. 100 = +100bps = +1%)
        if yield_change > 100:
            risk_signals += 1
        elif yield_change > 50:
            neut_signals += 1
 
        # NASDAQ relative underperformance vs S&P 500
        if nasdaq_rel < -0.05:
            risk_signals += 1
        elif nasdaq_rel < -0.02:
            neut_signals += 1
 
        if risk_signals >= 2:
            return 0.65, "Risk-Off"
        elif risk_signals == 1 or neut_signals >= 2:
            return 0.80, "Neutral"
        return 1.00, "Risk-On"
 
    # ── Stage 6: Color assignment ─────────────────────────────────────────────
 
    def determine_color(self, score: float, delta: float | None,
                        hype: bool, hype_reasons: list) -> tuple[str, str]:
        """
        Color driven purely by score + fundamental delta.
        Hype is a separate warning flag shown in the dashboard, NOT a color override.
 
        Priority order:
          1. Negative growth delta  -> Blue    (fundamental override)
          2. Score > 70             -> Red     (true bottleneck)
          3. Score >= 45            -> Orange  (emerging / building momentum)
          4. Score < 20             -> Blue    (cooling)
          5. Otherwise              -> Green   (neutral/stable)
        """
        if delta is not None and delta < 0:
            return "Blue", f"Cooling — negative growth delta ({delta*100:.1f}%)"
 
        if score > 55:
            return "Red", "Bottleneck / Hot — all signals agree"
        elif score >= 40:
            return "Orange", "Emerging — building momentum"
        elif score < 20:
            return "Blue", "Cooling"
        else:
            return "Green", "Neutral"
 
    # ── Main entry point ──────────────────────────────────────────────────────
 
    def process_sector(self, sector_name: str, data: dict) -> dict:
        """
        Full scoring pipeline for one sector / layer.
        Never crashes — returns a safe default on any error.
        Always writes to audit_log (in-memory + JSON file).
        """
        missing_fields = []
 
        try:
            # Stage 1 — Fundamentals
            f_score, delta = self.calculate_fundamentals(data, missing_fields)
 
            # Stage 2 — Constraints
            c_score = self.calculate_constraints(data, missing_fields)
 
            # Stage 3 — Smart money
            s_score = self.calculate_smart_money(data, missing_fields)
 
            # Stage 4 — Hype detection
            is_hype, hype_reasons = self.calculate_hype(data, delta, missing_fields)
 
            # Stage 5 — Composite score
            raw = (
                f_score * self.weights["fundamentals"] +
                c_score * self.weights["constraints"] +
                s_score * self.weights["smart_money"]
            )
 
            # Stage 6 — Macro multiplier
            multiplier, regime = self.get_macro_multiplier()
            final_score = self._clamp(raw * multiplier)
 
            # Stage 7 — Color
            color, status = self.determine_color(final_score, delta, is_hype, hype_reasons)
 
            # Build result
            result = {
                "score":       round(final_score, 2),
                "color":       color,
                "status":      status,
                "regime":      regime,
                "multiplier":  multiplier,
                "sub_scores": {
                    "fundamentals":  round(f_score, 2),
                    "constraints":   round(c_score, 2),
                    "smart_money":   round(s_score, 2),
                    "raw_composite": round(raw, 2),
                },
                "fund_delta":   round(delta, 4) if delta is not None else None,
                "is_hype":      is_hype,
                "hype_reasons": hype_reasons,
                "data_status":  "Full" if not missing_fields else f"Partial — missing: {', '.join(missing_fields)}",
                "timestamp":    datetime.datetime.now().isoformat(),
            }
 
        except Exception as e:
            log.error(f"ScoreEngine error [{sector_name}]: {e}")
            result = {
                "score":      0.0,
                "color":      "Green",   # safe neutral default — not Gray
                "status":     f"Scoring error — defaulting to neutral ({e})",
                "regime":     "Unknown",
                "multiplier": 1.0,
                "sub_scores": {},
                "fund_delta": None,
                "is_hype":    False,
                "hype_reasons": [],
                "data_status": f"Error: {e}",
                "timestamp":  datetime.datetime.now().isoformat(),
            }
            missing_fields = ["pipeline_error"]
 
        # Save to in-memory audit log
        self.audit_log[sector_name] = {
            "score":         result["score"],
            "color":         result["color"],
            "regime":        result["regime"],
            "sub_scores":    result.get("sub_scores", {}),
            "missing_fields": missing_fields,
            "data_status":   result["data_status"],
            "timestamp":     result["timestamp"],
        }
 
        # Append to JSON audit file
        self._append_audit(sector_name, result, missing_fields)
 
        return result
 
    # ── Audit file ────────────────────────────────────────────────────────────
 
    def _append_audit(self, sector_name: str, result: dict, missing_fields: list):
        """Append this sector's result to the rolling audit_log.json file."""
        path = Path(AUDIT_LOG_FILE)
        log_entries = []
        if path.exists():
            try:
                log_entries = json.loads(path.read_text())
            except Exception:
                log_entries = []
 
        log_entries.append({
            "date":          datetime.datetime.now().strftime("%Y-%m-%d"),
            "time":          datetime.datetime.now().strftime("%H:%M:%S"),
            "sector":        sector_name,
            "score":         result["score"],
            "color":         result["color"],
            "regime":        result["regime"],
            "multiplier":    result.get("multiplier", 1.0),
            "sub_scores":    result.get("sub_scores", {}),
            "fund_delta":    result.get("fund_delta"),
            "is_hype":       result.get("is_hype", False),
            "missing_fields": missing_fields,
            "data_status":   result["data_status"],
        })
 
        # Keep last 500 entries (~3 months of daily 6-layer runs)
        log_entries = log_entries[-500:]
        path.write_text(json.dumps(log_entries, indent=2, ensure_ascii=False))
 
 
# ── Self-test ─────────────────────────────────────────────────────────────────
 
if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG, format="%(levelname)s: %(message)s")
 
    print("\nTest 1 — Power/Energy layer (should be Red, Risk-On)")
    engine = ScoreEngine({
        "vix":               17.4,
        "yield_10y_change":  15.0,    # mild, +15bps
        "nasdaq_vs_spx_20d": 0.02,    # NASDAQ outperforming — risk-on
    })
    result = engine.process_sector("power_energy", {
        "growth_curr":        0.38,
        "growth_prev":        0.22,   # delta = +0.16 — strong acceleration
        "gm_delta":           0.04,
        "news_velocity":      6.0,
        "capex_div":          0.7,
        "vol_spike":          1.8,
        "price_act":          0.04,
        "analyst_upgrades":   3,
        "short_int_change":  -0.05,
        "price_30d_return":   0.12,
        "price_momentum":     1.3,
        "peer_outperformance":0.08,
    })
    print(f"  Score: {result['score']} | Color: {result['color']} | Status: {result['status']}")
    print(f"  Regime: {result['regime']} × {result['multiplier']}")
    print(f"  Sub-scores: {result['sub_scores']}")
    print(f"  Data: {result['data_status']}")
 
    print("\nTest 2 — Software layer with negative delta (should be Blue)")
    result2 = engine.process_sector("software", {
        "growth_curr":  0.08,
        "growth_prev":  0.15,   # delta = -0.07 — decelerating
        "gm_delta":     0.01,
        "news_velocity":1.0,
        "capex_div":    0.1,
        "vol_spike":    1.1,
        "price_act":    0.01,
        "analyst_upgrades": 0,
        "short_int_change": 0.02,
    })
    print(f"  Score: {result2['score']} | Color: {result2['color']} | Status: {result2['status']}")
 
    print("\nTest 3 — Hype scenario (should be Orange)")
    result3 = engine.process_sector("compute_hype", {
        "growth_curr":        0.08,   # weak fundamentals
        "growth_prev":        0.06,
        "gm_delta":          -0.01,
        "news_velocity":      1.0,
        "capex_div":          0.1,
        "vol_spike":          1.2,
        "price_act":          0.02,
        "analyst_upgrades":   1,
        "short_int_change":   0.01,
        "price_30d_return":   0.22,   # +22% in 30 days — hype rule 1
        "price_momentum":     2.4,    # 2.4× own avg — hype rule 2
        "peer_outperformance":0.25,   # +25% vs peers — hype rule 3
    })
    print(f"  Score: {result3['score']} | Color: {result3['color']} | Status: {result3['status']}")
    print(f"  Hype reasons: {result3['hype_reasons']}")
 
    print("\nTest 4 — Risk-Off macro scenario (should dampen scores)")
    engine_riskoff = ScoreEngine({
        "vix":               35.0,    # fear elevated
        "yield_10y_change":  120.0,   # +120bps surge
        "nasdaq_vs_spx_20d":-0.07,    # tech selling off
    })
    result4 = engine_riskoff.process_sector("power_riskoff", {
        "growth_curr":  0.38,
        "growth_prev":  0.22,
        "gm_delta":     0.04,
        "news_velocity":6.0,
        "capex_div":    0.7,
        "vol_spike":    1.8,
        "price_act":    0.04,
        "analyst_upgrades": 3,
        "short_int_change": -0.05,
    })
    print(f"  Score: {result4['score']} | Color: {result4['color']}")
    print(f"  Regime: {result4['regime']} × {result4['multiplier']} (score dampened from high raw)")
 
    print("\nTest 5 — Missing data (should handle gracefully)")
    result5 = engine.process_sector("memory_incomplete", {
        "growth_curr": 0.25,
        # growth_prev missing — delta unavailable
        # most fields missing
    })
    print(f"  Score: {result5['score']} | Color: {result5['color']}")
    print(f"  Data: {result5['data_status']}")
 
    print(f"\n✅ All tests complete. Audit log saved to {AUDIT_LOG_FILE}")
    print(f"   In-memory audit_log keys: {list(engine.audit_log.keys())}")

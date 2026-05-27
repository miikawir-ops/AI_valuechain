"""
score_engine.py — Composite scoring engine for AI value chain layers.
 
BOTTLENECK-FOCUSED MODEL v3.1 (final)
=======================================
Core thesis: Revenue acceleration is the primary bottleneck signal.
When AI demand exceeds supply in a layer, revenue explodes first —
margins and analyst coverage follow 1-2 quarters later.
 
Scoring weights (final, agreed across both Claude instances):
  65% Revenue Acceleration   ← primary bottleneck detector
  20% Constraint Signal      ← layer supply pressure (news + capex)
  15% Smart Money            ← earliest leading indicator (positions before earnings)
 
Revenue Acceleration sub-components:
  50% Growth delta     — QoQ change in YoY growth (the inflection signal)
  30% Growth level     — absolute YoY growth (rewards sustained >50% growers)
  20% Margin delta     — gross margin expansion (pricing power forming)
  + High-margin bonus  — +8% multiplier if gross_margin > 55% (DDOG, MU boost)
                         No penalty for capital-intensive layers (CEG unaffected)
 
Constraint Signal (20%):
  60% News velocity    — ticker-specific RSS headlines
  40% Capex divergence — spending to meet demand
  Both-signal bonus: +15% when news AND capex both confirm
  Single-signal cap:  max 60pts if only news (no capex confirmation)
 
Smart Money (15%):
  Earliest pre-earnings signal — volume, upgrades, short covering, momentum
  Weighted lower than constraint to prevent noise-driven Red signals
 
Color thresholds (recalibrated for discrimination):
  Red    > 65  — confirmed structural bottleneck
  Orange  45-65 — emerging constraint / acceleration building
  Green   25-45 — healthy growth, no constraint pressure
  Blue    < 25  — cooling or decelerating
 
Macro multiplier: Risk-On 1.0 / Neutral 0.85 / Risk-Off 0.65
Full audit trail recorded to audit_log.json after every run.
 
v3.1 changes vs v3:
  - Weights: acceleration 60→65%, constraints 25→20%, smart 15% unchanged
  - High-margin multiplier added inside acceleration (+8% if GM > 55%)
  - Rationale: smart money moves before earnings; constraint signal is noisier
"""
 
import json
import logging
import datetime
from pathlib import Path
 
log = logging.getLogger(__name__)
 
AUDIT_LOG_FILE = "audit_log.json"
 
# Layer-type metadata — used for context-aware scoring
# Energy/Infra are physical constraint layers: lower baseline margins, higher capex weight
# Semicon/Memory/Software are margin-expansion layers: pricing power is the signal
LAYER_TYPES = {
    "energy":   "physical",
    "infra":    "physical",
    "compute":  "margin",
    "memory":   "margin",
    "cloud":    "scale",
    "software": "margin",
    "security": "margin",
}
 
 
class ScoreEngine:
    def __init__(self, macro_data: dict):
        self.macro_data = macro_data
        self.weights = {
            "acceleration": 0.65,   # revenue acceleration — primary bottleneck signal
            "constraints":  0.20,   # layer supply pressure
            "smart_money":  0.15,   # confirmation
        }
        self.audit_log = {}
 
    # ── Helpers ───────────────────────────────────────────────────────────────
 
    @staticmethod
    def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
        return max(lo, min(hi, float(value)))
 
    def _get(self, data: dict, key: str, default=None, missing_log: list = None):
        val = data.get(key)
        if val is None:
            if missing_log is not None:
                missing_log.append(key)
            return default
        return val
 
    # ── Stage 1: Revenue Acceleration (60%) ───────────────────────────────────
 
    def calculate_acceleration(self, data: dict, missing: list) -> tuple[float, float | None]:
        """
        Primary bottleneck signal: is revenue growth ACCELERATING?
 
        Three sub-components:
          50% Growth delta  — QoQ change in YoY growth rate
                              This is THE bottleneck formation signal
                              delta = +0.20 → score 90 (strong acceleration)
                              delta =  0.00 → score 50 (stable)
                              delta = -0.10 → score 30 (decelerating)
 
          30% Growth level  — absolute YoY revenue growth
                              Rewards sustained high growth (>50% is exceptional)
                              100% YoY → 100pts, 50% → 75pts, 20% → 50pts, 0% → 0pts
                              This catches MU (+111% YoY) even without multi-quarter data
 
          20% Margin bonus  — gross margin delta (pricing power forming)
                              Small bonus — not the primary signal but confirms bottleneck
                              +5pp expansion → +20pts bonus, flat → 0, contraction → 0
 
        Fundamental override: negative delta HARD CAPS score at 40.
        This prevents any decelerating company from scoring Red.
        """
        g_curr = self._get(data, "growth_curr", default=None, missing_log=missing)
        g_prev = self._get(data, "growth_prev", default=None, missing_log=missing)
        gm     = self._get(data, "gm_delta",    default=0.0,  missing_log=missing)
 
        # ── Sub-component 1: Growth delta (50% weight) ────────────────────────
        if g_curr is not None and g_prev is not None:
            delta = g_curr - g_prev
            # Scaled so:  delta +0.25 → 100, delta 0 → 50, delta -0.25 → 0
            delta_score = self._clamp(delta * 200 + 50)
        else:
            delta = None
            delta_score = 50.0
            log.debug("growth_delta: insufficient data — using neutral 50")
 
        # ── Sub-component 2: Growth level (30% weight) ────────────────────────
        if g_curr is not None:
            # 0% growth → 0pts, 50% growth → 75pts, 100%+ → 100pts
            # Logarithmic-ish: rewards high growth but diminishing returns
            if g_curr >= 1.0:        # 100%+ YoY — exceptional (MU, NVDA peak)
                level_score = 100.0
            elif g_curr >= 0.50:     # 50-100% YoY — very strong
                level_score = self._clamp(75 + (g_curr - 0.50) * 50)
            elif g_curr >= 0.20:     # 20-50% YoY — solid growth
                level_score = self._clamp(50 + (g_curr - 0.20) * 83)
            elif g_curr >= 0.10:     # 10-20% YoY — moderate
                level_score = self._clamp(25 + (g_curr - 0.10) * 250)
            elif g_curr > 0:         # 0-10% — slow
                level_score = self._clamp(g_curr * 250)
            else:                    # negative growth
                level_score = 0.0
        else:
            level_score = 40.0      # slightly below neutral if missing
 
        # ── Sub-component 3: Margin bonus (20% weight) ────────────────────────
        # Only reward margin expansion — contraction doesn't penalise separately
        # (already captured by fundamentals narrative)
        if gm and gm > 0:
            margin_bonus = self._clamp(gm * 500)   # +5pp = 25pts, +10pp = 50pts
        else:
            margin_bonus = 0.0
 
        # ── Combine ───────────────────────────────────────────────────────────
        raw = (
            delta_score  * 0.50 +
            level_score  * 0.30 +
            margin_bonus * 0.20
        )
 
        # ── High-margin multiplier (suggested by Claude instance 2) ───────────
        # +8% boost for high-margin accelerators (GM > 55%)
        # Rewards DDOG (80% GM), MU (58% GM) without penalising CEG (20% GM)
        # CEG's acceleration score stands on its own — no penalty applied
        gross_margin = self._get(data, "gross_margin", default=None, missing_log=missing)
        if gross_margin and gross_margin > 0.55 and (delta is None or delta >= 0):
            raw = min(raw * 1.08, 100.0)
 
        # ── Fundamental override ──────────────────────────────────────────────
        # Negative delta = decelerating revenue = CANNOT be a bottleneck
        if delta is not None and delta < 0:
            raw = min(raw, 40.0)
 
        return self._clamp(raw), delta
 
    # ── Stage 2: Constraints (25%) ────────────────────────────────────────────
 
    def calculate_constraints(self, data: dict, missing: list) -> float:
        """
        Supply pressure signal: is the layer unable to keep up with demand?
 
        news_velocity: 0–10 keyword hits (ticker-specific RSS feeds)
        capex_div:     0–1 capex divergence (high capex = expanding to meet demand)
 
        v3: Requires BOTH signals for high score — avoids pure news-driven inflation.
        If only one signal is present, max score is capped at 60.
        """
        news  = self._get(data, "news_velocity", default=0.0, missing_log=missing)
        capex = self._get(data, "capex_div",     default=0.0, missing_log=missing)
 
        news_score  = self._clamp(news * 10)     # 10 hits = 100pts
        capex_score = self._clamp(capex * 100)   # 1.0 = 100pts
 
        raw = news_score * 0.60 + capex_score * 0.40
 
        # Both-signal bonus: reward when news AND capex both confirm constraint
        if news >= 3 and capex >= 0.3:
            raw = min(raw * 1.15, 100)   # 15% boost when both signals confirm
 
        # Single-signal cap: pure news without capex confirmation → max 60
        if capex < 0.1 and news > 0:
            raw = min(raw, 60.0)
 
        return self._clamp(raw)
 
    # ── Stage 3: Smart Money (15%) ────────────────────────────────────────────
 
    def calculate_smart_money(self, data: dict, missing: list) -> float:
        """
        Confirmation signal only — weighted at 15% in v3 (down from 25%).
 
        Smart money confirms what fundamentals already show.
        It should never be the PRIMARY reason a layer scores Red.
 
        Signals:
          Vol spike + positive price  → institutional accumulation
          Analyst upgrades            → sell-side catching up
          Short covering              → bears capitulating
          Price momentum              → trend confirmation
          30d return                  → sustained strength
        """
        vol       = self._get(data, "vol_spike",         default=1.0, missing_log=missing)
        price     = self._get(data, "price_act",         default=0.0, missing_log=missing)
        upgrades  = self._get(data, "analyst_upgrades",  default=0,   missing_log=missing)
        short_ch  = self._get(data, "short_int_change",  default=0.0, missing_log=missing)
        momentum  = self._get(data, "price_momentum",    default=1.0, missing_log=missing)
        ret_30d   = self._get(data, "price_30d_return",  default=0.0, missing_log=missing)
 
        # Volume confirmation
        vol_score = self._clamp((vol - 1.0) * 50)
        # Partial dampener on institutional sell signals (don't zero — upgrades still valid)
        if price < -0.03 and vol > 1.5:
            vol_score *= 0.3
 
        # Analyst upgrades — 15pts each, capped at 2 upgrades
        upgrade_score = self._clamp(upgrades * 15, 0, 30)
 
        # Short covering
        short_score = self._clamp(-short_ch * 200)
 
        # Price momentum vs own history
        momentum_score = self._clamp((momentum - 1.0) * 20) if momentum > 1.0 else 0.0
 
        # 30d return (positive only — negative captured by fundamentals)
        ret_score = self._clamp(ret_30d * 100) if ret_30d > 0 else 0.0
 
        raw = (
            vol_score      * 0.35 +
            upgrade_score  * 0.25 +
            short_score    * 0.15 +
            momentum_score * 0.15 +
            ret_score      * 0.10
        )
 
        return self._clamp(raw)
 
    # ── Stage 4: Hype detection ───────────────────────────────────────────────
 
    def calculate_hype(self, data: dict, delta: float | None, missing: list) -> tuple[bool, list[str]]:
        """
        Hype warning: price running ahead of revenue fundamentals.
 
        Rule 1: Price +15% in 30d AND revenue growth <10%
        Rule 3: Outperforming peers by >20% with negative growth delta
 
        Note: Rule 2 (momentum) remains disabled — fires too broadly in bull markets.
        Hype is a WARNING FLAG on the card, NOT a color override.
        """
        price_30d  = self._get(data, "price_30d_return",    default=0.0, missing_log=missing)
        rev_growth = self._get(data, "growth_curr",         default=0.1, missing_log=missing)
        peer_outpf = self._get(data, "peer_outperformance", default=0.0, missing_log=missing)
 
        reasons = []
 
        if price_30d > 0.15 and (rev_growth or 0) < 0.10:
            reasons.append(
                f"Price +{price_30d*100:.0f}% in 30d "
                f"but revenue growth only {(rev_growth or 0)*100:.0f}%"
            )
 
        if peer_outpf > 0.20 and (delta is None or delta < -0.02):
            reasons.append(
                f"Outperforming peers by {peer_outpf*100:.0f}% "
                f"with weak growth delta ({(delta or 0)*100:.1f}%)"
            )
 
        return len(reasons) > 0, reasons
 
    # ── Stage 5: Macro multiplier ─────────────────────────────────────────────
 
    def get_macro_multiplier(self) -> tuple[float, str]:
        """
        Macro regime dampener.
 
        v3: Neutral multiplier raised from 0.80 → 0.85
        Rationale: 20% dampening was too aggressive for a single neutral signal —
        it was preventing valid bottleneck layers from reaching Red.
 
        Risk-Off still uses 0.65 — genuine fear environments warrant strong dampening.
        """
        vix          = self.macro_data.get("vix",               20.0)
        yield_change = self.macro_data.get("yield_10y_change",   0.0)
        nasdaq_rel   = self.macro_data.get("nasdaq_vs_spx_20d",  0.0)
 
        risk_signals = 0
        neut_signals = 0
 
        if vix > 30:          risk_signals += 1
        elif vix > 20:        neut_signals += 1
 
        if yield_change > 100:   risk_signals += 1
        elif yield_change > 50:  neut_signals += 1
 
        if nasdaq_rel < -0.05:   risk_signals += 1
        elif nasdaq_rel < -0.02: neut_signals += 1
 
        if risk_signals >= 2:
            return 0.65, "Risk-Off"
        elif risk_signals == 1 or neut_signals >= 2:
            return 0.85, "Neutral"    # v3: raised from 0.80
        return 1.00, "Risk-On"
 
    # ── Stage 6: Color assignment ─────────────────────────────────────────────
 
    def determine_color(self, score: float, delta: float | None,
                        hype: bool, hype_reasons: list) -> tuple[str, str]:
        """
        Bottleneck-calibrated color thresholds (v3).
 
        Recalibrated for discrimination — not everything should be Red/Orange:
          Red    > 65  — confirmed structural bottleneck
          Orange  45-65 — emerging constraint / acceleration building
          Green   25-45 — healthy growth, no constraint pressure
          Blue    < 25  — cooling or decelerating
 
        Additional Red requirement: fund_delta must be positive
        (can't be a bottleneck if revenue growth is slowing)
 
        Hype remains a WARNING FLAG only — never changes the color.
        """
        # Fundamental override always takes priority
        if delta is not None and delta < 0:
            return "Blue", f"Cooling — negative growth delta ({delta*100:.1f}%)"
 
        # Bottleneck confirmation: Red requires positive acceleration
        if score > 65:
            if delta is not None and delta < 0.05:
                # Score is high but acceleration is weak — downgrade to Orange
                return "Orange", f"Emerging — high score but acceleration slowing ({delta*100:.1f}%)"
            return "Red", "Confirmed bottleneck — revenue accelerating into supply constraint"
        elif score >= 45:
            return "Orange", "Emerging — acceleration building"
        elif score < 25:
            return "Blue", "Cooling — constraint easing"
        else:
            return "Green", "Neutral — healthy growth, no constraint pressure"
 
    # ── Main entry point ──────────────────────────────────────────────────────
 
    def process_sector(self, sector_name: str, data: dict) -> dict:
        """
        Full scoring pipeline for one ticker / sector.
        Never crashes. Always writes to audit_log.
        """
        missing_fields = []
 
        try:
            # Stage 1 — Revenue acceleration (primary bottleneck signal)
            accel_score, delta = self.calculate_acceleration(data, missing_fields)
 
            # Stage 2 — Constraint signal
            c_score = self.calculate_constraints(data, missing_fields)
 
            # Stage 3 — Smart money confirmation
            s_score = self.calculate_smart_money(data, missing_fields)
 
            # Stage 4 — Hype detection
            is_hype, hype_reasons = self.calculate_hype(data, delta, missing_fields)
 
            # Stage 5 — Composite
            raw = (
                accel_score * self.weights["acceleration"] +
                c_score     * self.weights["constraints"] +
                s_score     * self.weights["smart_money"]
            )
 
            # Stage 6 — Macro multiplier
            multiplier, regime = self.get_macro_multiplier()
            final_score = self._clamp(raw * multiplier)
 
            # Stage 7 — Color
            color, status = self.determine_color(final_score, delta, is_hype, hype_reasons)
 
            result = {
                "score":      round(final_score, 2),
                "color":      color,
                "status":     status,
                "regime":     regime,
                "multiplier": multiplier,
                "sub_scores": {
                    "acceleration": round(accel_score, 2),
                    "constraints":  round(c_score, 2),
                    "smart_money":  round(s_score, 2),
                    "raw_composite":round(raw, 2),
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
                "score":        0.0,
                "color":        "Green",
                "status":       f"Scoring error — defaulting to neutral ({e})",
                "regime":       "Unknown",
                "multiplier":   1.0,
                "sub_scores":   {},
                "fund_delta":   None,
                "is_hype":      False,
                "hype_reasons": [],
                "data_status":  f"Error: {e}",
                "timestamp":    datetime.datetime.now().isoformat(),
            }
            missing_fields = ["pipeline_error"]
 
        self.audit_log[sector_name] = {
            "score":          result["score"],
            "color":          result["color"],
            "regime":         result["regime"],
            "sub_scores":     result.get("sub_scores", {}),
            "missing_fields": missing_fields,
            "data_status":    result["data_status"],
            "timestamp":      result["timestamp"],
        }
 
        self._append_audit(sector_name, result, missing_fields)
        return result
 
    # ── Audit file ────────────────────────────────────────────────────────────
 
    def _append_audit(self, sector_name: str, result: dict, missing_fields: list):
        path = Path(AUDIT_LOG_FILE)
        log_entries = []
        if path.exists():
            try:
                log_entries = json.loads(path.read_text())
            except Exception:
                log_entries = []
 
        log_entries.append({
            "date":           datetime.datetime.now().strftime("%Y-%m-%d"),
            "time":           datetime.datetime.now().strftime("%H:%M:%S"),
            "sector":         sector_name,
            "score":          result["score"],
            "color":          result["color"],
            "regime":         result["regime"],
            "multiplier":     result.get("multiplier", 1.0),
            "sub_scores":     result.get("sub_scores", {}),
            "fund_delta":     result.get("fund_delta"),
            "is_hype":        result.get("is_hype", False),
            "missing_fields": missing_fields,
            "data_status":    result["data_status"],
        })
 
        log_entries = log_entries[-500:]
        path.write_text(json.dumps(log_entries, indent=2, ensure_ascii=False))
 
 
# ── Self-test ─────────────────────────────────────────────────────────────────
 
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
 
    engine = ScoreEngine({
        "vix":               16.6,
        "yield_10y_change":  24.8,
        "nasdaq_vs_spx_20d": 0.017,
    })
 
    print("\n" + "="*60)
    print("  ScoreEngine v3 — Bottleneck-focused self-test")
    print("="*60)
 
    # Test 1: MU (memory bottleneck — should be Red, ~75-85)
    print("\nTest 1 — MU / Memory (confirmed bottleneck)")
    r = engine.process_sector("memory_MU", {
        "growth_curr":         1.11,    # +111% YoY
        "growth_prev":         0.47,    # delta = +0.64 — massive acceleration
        "gm_delta":            0.06,
        "news_velocity":       8.0,
        "capex_div":           0.5,
        "vol_spike":           1.4,
        "price_act":           0.02,
        "analyst_upgrades":    2,
        "short_int_change":   -0.03,
        "price_30d_return":    0.79,
        "price_momentum":      1.8,
        "peer_outperformance": 0.35,
    })
    print(f"  Score: {r['score']} | Color: {r['color']}")
    print(f"  Sub-scores: accel={r['sub_scores']['acceleration']:.1f} "
          f"constr={r['sub_scores']['constraints']:.1f} "
          f"smart={r['sub_scores']['smart_money']:.1f}")
    print(f"  Expected: Red, ~75-85")
 
    # Test 2: CEG (energy bottleneck — should be Red, ~65-75)
    print("\nTest 2 — CEG / Energy (physical bottleneck, low GM)")
    r2 = engine.process_sector("energy_CEG", {
        "growth_curr":         0.69,    # +69% YoY
        "growth_prev":         0.00,    # delta = +0.69
        "gm_delta":            0.02,
        "news_velocity":       8.0,
        "capex_div":           0.6,
        "vol_spike":           1.3,
        "price_act":          -0.05,
        "analyst_upgrades":    1,
        "short_int_change":   -0.02,
        "price_30d_return":   -0.06,
        "price_momentum":      0.9,
        "peer_outperformance": 0.10,
    })
    print(f"  Score: {r2['score']} | Color: {r2['color']}")
    print(f"  Sub-scores: accel={r2['sub_scores']['acceleration']:.1f} "
          f"constr={r2['sub_scores']['constraints']:.1f} "
          f"smart={r2['sub_scores']['smart_money']:.1f}")
    print(f"  Expected: Red, ~65-75 (growth drives it despite low margin)")
 
    # Test 3: MSFT (cloud — healthy but not bottleneck, should be Green ~35-45)
    print("\nTest 3 — MSFT / Cloud (healthy, not a bottleneck)")
    r3 = engine.process_sector("cloud_MSFT", {
        "growth_curr":         0.13,
        "growth_prev":         0.12,    # delta = +0.01 — barely accelerating
        "gm_delta":            0.01,
        "news_velocity":       5.0,
        "capex_div":           0.3,
        "vol_spike":           1.1,
        "price_act":          -0.01,
        "analyst_upgrades":    1,
        "short_int_change":    0.00,
        "price_30d_return":   -0.01,
        "price_momentum":      1.0,
        "peer_outperformance": 0.00,
    })
    print(f"  Score: {r3['score']} | Color: {r3['color']}")
    print(f"  Expected: Green/Orange, ~35-45")
 
    # Test 4: AMD (decelerating — should be Blue)
    print("\nTest 4 — AMD / Compute (decelerating, hype)")
    r4 = engine.process_sector("compute_AMD", {
        "growth_curr":         0.36,
        "growth_prev":         0.58,    # delta = -0.22 — decelerating
        "gm_delta":           -0.03,
        "news_velocity":       4.0,
        "capex_div":           0.2,
        "vol_spike":           1.6,
        "price_act":           0.03,
        "analyst_upgrades":    1,
        "short_int_change":    0.01,
        "price_30d_return":    0.34,    # +34% — hype rule fires
        "price_momentum":      2.1,
        "peer_outperformance": 0.28,
    })
    print(f"  Score: {r4['score']} | Color: {r4['color']}")
    print(f"  Hype: {r4['is_hype']} — {r4['hype_reasons']}")
    print(f"  Expected: Blue (negative delta override)")
 
    # Test 5: Risk-Off scenario
    print("\nTest 5 — MU in Risk-Off environment")
    engine_riskoff = ScoreEngine({
        "vix": 35.0, "yield_10y_change": 120.0, "nasdaq_vs_spx_20d": -0.07
    })
    r5 = engine_riskoff.process_sector("memory_riskoff", {
        "growth_curr": 1.11, "growth_prev": 0.47, "gm_delta": 0.06,
        "news_velocity": 8.0, "capex_div": 0.5,
        "vol_spike": 1.4, "price_act": 0.02,
        "price_30d_return": 0.51, "price_momentum": 1.8,
    })
    print(f"  Score: {r5['score']} | Color: {r5['color']}")
    print(f"  Regime: {r5['regime']} × {r5['multiplier']} (dampened from {r5['sub_scores']['raw_composite']:.1f})")
    print(f"  Expected: Red or Orange (dampened but still strong fundamental)")
 
    print("\n" + "="*60)
    print("  Discrimination test — score spread")
    print("="*60)
    scores = [
        ("MU (bottleneck)",    r['score'],  r['color']),
        ("CEG (bottleneck)",   r2['score'], r2['color']),
        ("MSFT (healthy)",     r3['score'], r3['color']),
        ("AMD (decelerating)", r4['score'], r4['color']),
    ]
    for name, score, color in sorted(scores, key=lambda x: -x[1]):
        bar = "█" * int(score / 5)
        print(f"  {name:<22} {score:>5.1f}  {color:<8}  {bar}")
 
    spread = max(s for _,s,_ in scores) - min(s for _,s,_ in scores)
    print(f"\n  Score spread: {spread:.1f} points")
    print(f"  (Higher spread = better discrimination between bottleneck and healthy layers)")
    print(f"\n✅ Tests complete.")
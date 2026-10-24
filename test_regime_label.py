"""
test_regime_label.py — The page's "Market regime" label must be the regime
and multiplier the scores actually used (score_engine.get_macro_multiplier),
not a separate rule in render.py. The old render rule disagreed whenever one
signal crossed a threshold alone: VIX 25 showed "Neutral" while scores got
1.0x, VIX 35 showed "Risk-Off" while scores got 0.85x.

Renders end to end through main.stage_score() and render.generate_dashboard()
in a temporary directory, and reads the label off the rendered page, so it
runs against old and new render.py alike.

No pytest in this environment — plain script. Run: python test_regime_label.py
"""

import os
import re
import datetime
import tempfile

import main
import render
from score_engine import ScoreEngine

SESSIONS = [datetime.date(2026, 10, d) for d in (13, 14, 15, 16, 19, 20)]

CASES = [
    ("VIX 25 alone",            {"vix": 25.0, "yield_10y_change": None,  "nasdaq_vs_spx_20d": None},
     "Market regime: Risk-On · scores × 1.00"),
    ("VIX 35 alone",            {"vix": 35.0, "yield_10y_change": None,  "nasdaq_vs_spx_20d": None},
     "Market regime: Neutral · scores × 0.85"),
    ("VIX 35 + yield +120 bps", {"vix": 35.0, "yield_10y_change": 120.0, "nasdaq_vs_spx_20d": None},
     "Market regime: Risk-Off · scores × 0.65"),
    ("all three missing",       {"vix": None, "yield_10y_change": None,  "nasdaq_vs_spx_20d": None},
     "Market regime: Unknown (macro data unavailable)"),
]


def _ticker(sym: str) -> dict:
    return {
        "ticker": sym, "name": f"{sym} Corp", "price": 100.0, "market_cap": 1e11,
        "growth_curr": 0.30, "growth_prev": 0.20, "gm_delta": 0.01, "gross_margin": 0.5,
        "news_velocity": 4.0, "capex_div": 0.3, "vol_spike": 1.1, "price_act": 0.01,
        "analyst_upgrades": 1, "short_int_change": 0.0, "price_30d_return": 0.05,
        "price_momentum": 1.2, "peer_outperformance": 0.0, "price_history": [100.0] * 30,
        "week52_high": 120.0, "week52_low": 80.0, "revenue_quarterly": 0, "data_missing": [],
    }


def _rendered_label(macro: dict) -> tuple:
    market_data = {lid: [_ticker(lid[:4].upper())] for lid in render.LAYER_NAMES}
    scored = main.stage_score(market_data, macro, SESSIONS)
    render.generate_dashboard(scored, "Stub analysis.", macro, SESSIONS[-1], market_data, [])
    page = open("index.html", encoding="utf-8").read()
    m = re.search(r'<div class="reg-pill" tabindex="0">(.*?)\n\s*<div class="reg-tooltip">', page, re.S)
    used = {(t["regime"], t["multiplier"]) for l in scored.values() for t in l.get("all_tickers", [])}
    return (m.group(1).strip() if m else None), used, page


def test_rendered_regime_label_equals_the_engine_regime():
    cwd, failures = os.getcwd(), []
    with tempfile.TemporaryDirectory() as tmp:
        os.chdir(tmp)
        try:
            for name, macro, want in CASES:
                if os.path.exists("scores_history.json"):
                    os.remove("scores_history.json")
                macro = dict(macro, data_missing=[k for k, v in macro.items() if v is None])
                shown, used, page = _rendered_label(macro)
                mult, regime = ScoreEngine(macro).get_macro_multiplier([])
                ok = (shown == want and want.startswith("Market regime: " + regime)
                      and used == {(regime, mult)})
                print(f"{'PASS' if ok else 'FAIL'}: {name}: page {shown!r}; engine {regime} × {mult}")
                if not ok:
                    failures.append(name)
            rule = "<b>Rule applied:</b> Risk-Off (scores × 0.65) needs two risk signals"
            if rule in page and "5 percentage points behind" in page and "A missing signal doesn't vote." in page:
                print("PASS: the info text states the voting rule the engine applies")
            else:
                print("FAIL: the info text doesn't state the voting rule")
                failures.append("rule text")
        finally:
            os.chdir(cwd)
    assert not failures, f"regime label disagrees with the engine: {failures}"


def _render_page() -> str:
    cwd = os.getcwd()
    with tempfile.TemporaryDirectory() as tmp:
        os.chdir(tmp)
        try:
            _rendered_label({"vix": 17.0, "yield_10y_change": 10.0, "nasdaq_vs_spx_20d": 0.0, "data_missing": []})
            return open("index.html", encoding="utf-8").read()
        finally:
            os.chdir(cwd)


def test_easing_names_only_a_blue_layer():
    """The bottleneck strip's "Easing" must be a Blue (cooling) layer, or
    "none". The old code fell back to the lowest-scoring layer whatever its
    colour: every page from 2026-10-15 on said "Easing: Cloud", a Neutral
    layer. Runs the rendered page's own buildBottleneckStrip() in Node."""
    import json, shutil, subprocess
    if not shutil.which("node"):
        raise AssertionError("node is required for this check and wasn't found")
    page = _render_page()
    cc = re.search(r"const CC = \{.*?\n\};", page, re.S).group(0)
    fn = re.search(r"function buildBottleneckStrip\(\) \{.*?\n\}", page, re.S).group(0)

    def strip_text(layers):
        js = (cc + "\n" + fn + "\nconst strip = {innerHTML: ''};\n"
              "const document = {getElementById: () => strip};\n"
              f"const LAYERS = {json.dumps(layers)};\nbuildBottleneckStrip();\n"
              r"console.log(strip.innerHTML.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim());")
        out = subprocess.run(["node", "-e", js], capture_output=True, text=True, encoding="utf-8")
        assert out.returncode == 0, out.stderr
        return out.stdout.strip()

    lay = lambda n, s, c: {"n1": n, "score": s, "color": c}
    no_blue = strip_text([lay("HBM memory", 56, "Red"), lay("AI security", 39, "Orange"), lay("Cloud", 25, "Green")])
    blue    = strip_text([lay("HBM memory", 56, "Red"), lay("Cloud", 25, "Green"), lay("Energy", 22, "Blue")])
    assert no_blue.endswith("Easing: none"), f"no Blue layer, strip reads: {no_blue!r}"
    assert blue.endswith("Easing: Energy"), f"with a Blue layer, strip reads: {blue!r}"
    print("PASS: Easing names only a Blue layer, otherwise 'none'")
    no_red = strip_text([lay("HBM memory", 59, "Orange"), lay("Cloud", 37, "Green")])
    assert no_red == "Bottleneck: none → Emerging: HBM memory → Easing: none", f"no Red layer: {no_red!r}"
    print("PASS: no Red layer reads 'Bottleneck: none', no stray arrow")
    assert "of 45 companies" not in page and "count.textContent" not in page, \
        "buildRadar still overwrites the radar header"
    assert 'id="radar-count">Latest revenue growth</span>' in page
    print("PASS: radar header stays 'Latest revenue growth'")


def test_earnings_badge_shows_real_dates_within_14_days():
    """A layer card's earnings badge must come from the tickers' fetched
    next_earnings dates: shown only 0-14 days after the session, with the
    date. No date (failed fetch), a past date or one further out shows no
    badge. The old code read a hardcoded May-Aug 2026 table and showed
    "Earnings soon" on compute/infra/software/security every day."""
    import json, shutil, subprocess
    if not shutil.which("node"):
        raise AssertionError("node is required for this check and wasn't found")
    dates = {   # session is 2026-10-20
        "energy":   [("ENA", "2026-10-23"), ("ENB", "2026-10-21")],   # both inside → soonest + count
        "compute":  [("CMA", "2026-11-09")],                          # 20 days out → none
        "memory":   [("MEA", None)],                                  # failed fetch → none
        "infra":    [("INA", "2026-10-15")],                          # already past → none
        "cloud":    [("CLA", "2026-11-03")],                          # exactly 14 days → shown
        "software": [("SWA", "2026-10-20")],                          # session day → shown
        "security": [("SEA", None)],
    }
    market_data = {lid: [dict(_ticker(sym), next_earnings=d) for sym, d in tks] for lid, tks in dates.items()}
    macro = {"vix": 17.0, "yield_10y_change": 10.0, "nasdaq_vs_spx_20d": 0.0, "data_missing": []}
    cwd = os.getcwd()
    with tempfile.TemporaryDirectory() as tmp:
        os.chdir(tmp)
        try:
            scored = main.stage_score(market_data, macro, SESSIONS)
            render.generate_dashboard(scored, "Stub analysis.", macro, SESSIONS[-1], market_data, [])
            page = open("index.html", encoding="utf-8").read()
        finally:
            os.chdir(cwd)
    assert "EARNINGS_WATCH" not in page and "Earnings soon" not in page, "page still carries the hardcoded earnings table"
    layers = re.search(r"const LAYERS\s*= (\[.*?\]);\n", page).group(1)
    fn = re.search(r"(const EARNINGS_WATCH = \{.*?\n\};\s*)?(//[^\n]*\n)*function getEarningsBadge\(.*?\n\}", page, re.S).group(0)
    js = (fn + f"\nconst LAYERS = {layers};\nconst out = {{}};\n"
          "for (const l of LAYERS) { const h = getEarningsBadge(l);\n"
          "  out[l.id] = h ? h.replace(/<[^>]+>/g, ' ').replace(/\\s+/g, ' ').trim() + ' | ' + (h.match(/title=\"([^\"]*)\"/) || [, ''])[1] : ''; }\n"
          "console.log(JSON.stringify(out));")
    res = subprocess.run(["node", "-e", js], capture_output=True, text=True, encoding="utf-8")
    assert res.returncode == 0, res.stderr
    got = json.loads(res.stdout)
    want = {
        "energy":   "📅 ENB earnings Oct 21 +1 | Earnings: ENB Oct 21, ENA Oct 23",
        "compute":  "", "memory": "", "infra": "", "security": "",
        "cloud":    "📅 CLA earnings Nov 3 | Earnings: CLA Nov 3",
        "software": "📅 SWA earnings Oct 20 | Earnings: SWA Oct 20",
    }
    bad = {k: (got.get(k), v) for k, v in want.items() if got.get(k) != v}
    assert not bad, f"earnings badge (got, want): {bad}"
    print("PASS: earnings badge shows real dates 0-14 days out, none otherwise")


def test_missing_30d_return_shows_a_dash_not_zero():
    """A ticker with no 30d return must reach the page as null and show "—"
    in the top ticker band, not "+0.0%". The old render used `... or 0`."""
    import json, shutil, subprocess
    if not shutil.which("node"):
        raise AssertionError("node is required for this check and wasn't found")
    market_data = {lid: [_ticker(lid[:4].upper())] for lid in render.LAYER_NAMES}
    market_data["memory"] = [dict(_ticker("GAP"), price_30d_return=None)]
    macro = {"vix": 17.0, "yield_10y_change": 10.0, "nasdaq_vs_spx_20d": 0.0, "data_missing": []}
    cwd = os.getcwd()
    with tempfile.TemporaryDirectory() as tmp:
        os.chdir(tmp)
        try:
            scored = main.stage_score(market_data, macro, SESSIONS)
            render.generate_dashboard(scored, "Stub analysis.", macro, SESSIONS[-1], market_data, [])
            page = open("index.html", encoding="utf-8").read()
        finally:
            os.chdir(cwd)
    layers = json.loads(re.search(r"const LAYERS\s*= (\[.*?\]);\n", page).group(1))
    gap = next(t for l in layers for t in l["tickers"] if t["sym"] == "GAP")
    assert gap["ret30"] is None, f"GAP ret30 is {gap['ret30']!r} on the page, want null"
    fn = re.search(r"function buildTopTicker\(layers\) \{.*?\n\}", page, re.S).group(0)
    js = (fn + "\nconst el = {innerHTML: ''};\nconst document = {getElementById: () => el};\n"
          f"buildTopTicker({json.dumps(layers)});\n"
          r"console.log(el.innerHTML.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' '));")
    res = subprocess.run(["node", "-e", js], capture_output=True, text=True, encoding="utf-8")
    assert res.returncode == 0, res.stderr
    item = re.search(r"GAP .*?(—|[+-]?\d+\.\d%)", res.stdout)
    assert item and item.group(1) == "—", f"top ticker band shows GAP as {item.group(1) if item else res.stdout[:200]!r}"
    print("PASS: a missing 30d return reaches the page as null and shows '—'")


if __name__ == "__main__":
    import logging
    logging.disable(logging.CRITICAL)
    test_missing_30d_return_shows_a_dash_not_zero()
    test_earnings_badge_shows_real_dates_within_14_days()
    test_easing_names_only_a_blue_layer()
    test_rendered_regime_label_equals_the_engine_regime()
    print("\nAll regime-label tests passed.")

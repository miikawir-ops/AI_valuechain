"""tools/accel_distribution.py — historical distribution of true YoY revenue
acceleration (change in YoY growth between consecutive quarters) for the
pipeline's companies, from SEC EDGAR quarters cross-checked with yfinance.
Used to calibrate thresholds that read fund_delta (instant Red, leader boost,
the A rating, deceleration) against real data, not guesses.

Needs SEC_USER_AGENT ("Name email"). Usage (repo root):
    python tools/accel_distribution.py [since=2021-07-01] [threshold_pp ...]
"""
import os
import sys
import datetime
import logging

sys.path.insert(0, os.getcwd())
logging.disable(logging.CRITICAL)

import yfinance as yf
import edgar
from fetch_market import AI_CHAIN_LAYERS

since = datetime.date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else datetime.date(2021, 7, 1)
thresholds = [float(x) for x in sys.argv[2:]] or [5, 20, 40, 60]

rows = []
for layer, cfg in AI_CHAIN_LAYERS.items():
    for tk in cfg["tickers"]:
        yr = yf.Ticker(tk).quarterly_financials.loc["Total Revenue"]
        yq = {k.date(): float(v) for k, v in yr.items() if v == v}
        s, _ = edgar.quarterly_revenue(tk, yq)
        if not s:
            continue
        ends = sorted(s)

        def yoy(e):
            p = next((x for x in ends if 350 <= (e - x).days <= 380), None)
            return s[e] / s[p] - 1 if p and s[p] else None

        ser = [(e, yoy(e)) for e in ends if e >= since]
        ser = [(e, g) for e, g in ser if g is not None]
        rows += [(tk, e2, g2 - g1) for (e1, g1), (e2, g2) in zip(ser, ser[1:]) if 75 <= (e2 - e1).days <= 105]

vals = sorted(r[2] for r in rows)
pct = lambda p: vals[int(p * (len(vals) - 1))] * 100
print(f"{len(rows)} company-quarters since {since}, {len({r[0] for r in rows})} companies")
print("percentiles (pp): " + "  ".join(f"p{int(p*100)} {pct(p):+.1f}" for p in (.05, .25, .5, .75, .9, .95, .99)))
for t in thresholds:
    above = [r for r in rows if r[2] * 100 > t]
    below = sum(1 for r in rows if r[2] * 100 < -t)
    print(f"> +{t:g} pp: {len(above)} ({len(above)/len(rows):.1%})   < -{t:g} pp: {below} ({below/len(rows):.1%})")

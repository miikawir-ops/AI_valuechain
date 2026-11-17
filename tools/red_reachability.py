"""tools/red_reachability.py — is a Red layer reachable on fundamentals?

For each layer and calendar quarter since 2021, the market-cap weighted
acceleration sub-score (score_engine.calculate_acceleration, the real code)
from EDGAR quarterly revenue, and the layer score that gives with constraints
and smart money held at assumed values. Red needs a layer score > 65 (and > 55
on 2 of the 3 sessions before; quarterly fundamentals don't move between
sessions, so a quarter above 65 also passes that, other inputs unchanged).

Assumptions, all stated in the output:
- weights: each company's latest market cap (scores_detail.jsonl, errata runs excluded), held fixed;
- gross-margin change isn't in this history: margin bonus 0 ("low") or the
  maximum 100 ("high"), bracketing the real value;
- the high-margin x1.08 uses each company's latest gross margin;
- a fiscal quarter counts in the calendar quarter whose end is within 46 days;
- macro multiplier 1.0 (Risk-On).

Needs SEC_USER_AGENT ("Name email"). Usage (repo root):
    python tools/red_reachability.py [constraints=50] [smart_money=50]
"""
import os
import sys
import json
import datetime
import logging

sys.path.insert(0, os.getcwd())
logging.disable(logging.CRITICAL)

import yfinance as yf
import edgar
from fetch_market import AI_CHAIN_LAYERS
from score_engine import ScoreEngine

C = float(sys.argv[1]) if len(sys.argv) > 1 else 50.0
S = float(sys.argv[2]) if len(sys.argv) > 2 else 50.0
SINCE = datetime.date(2021, 1, 1)
eng = ScoreEngine({})
W = eng.weights

from tools.detail_runs import load_runs   # scores_detail.jsonl without the errata runs
last = load_runs()[-1]
latest = {c["ticker"]: c["inputs"] for c in last["companies"]}


def cal_q(d):
    """The calendar quarter-end within 46 days of a fiscal quarter-end."""
    for y in (d.year - 1, d.year, d.year + 1):
        for m, day in ((3, 31), (6, 30), (9, 30), (12, 31)):
            q = datetime.date(y, m, day)
            if abs((d - q).days) <= 46:
                return q


def accel(g, gp, gm_level, bonus):
    a, _ = eng.calculate_acceleration({"growth_curr": g, "growth_prev": gp, "gm_delta": bonus / 500,
                                       "gross_margin": gm_level}, [])
    return a


layers = {}
for lid, cfg in AI_CHAIN_LAYERS.items():
    per_q = {}
    for tk in cfg["tickers"]:
        try:
            yr = yf.Ticker(tk).quarterly_financials.loc["Total Revenue"]
            yq = {k.date(): float(v) for k, v in yr.items() if v == v}
        except Exception:
            yq = {}
        s, _ = edgar.quarterly_revenue(tk, yq)
        if not s:
            print(f"  {tk}: no revenue series")
            continue
        ends = sorted(s)

        def yoy(e):
            p = next((x for x in ends if 350 <= (e - x).days <= 380), None)
            return s[e] / s[p] - 1 if p and s[p] else None

        ys = [(e, yoy(e)) for e in ends]
        ys = [(e, g) for e, g in ys if g is not None]
        cap = latest.get(tk, {}).get("market_cap") or 0
        gml = latest.get(tk, {}).get("gross_margin")
        for (e0, g0), (e1, g1) in zip(ys, ys[1:]):
            if e1 < SINCE or not cap:
                continue
            per_q.setdefault(cal_q(e1), []).append((tk, cap, accel(g1, g0, gml, 0), accel(g1, g0, gml, 100), g1 - g0))
    layers[lid] = per_q

print(f"Constraints {C:.0f}, smart money {S:.0f}, multiplier 1.0: Red (> 65) needs weighted acceleration > "
      f"{(65 - W['constraints'] * C - W['smart_money'] * S) / W['acceleration']:.1f}")
print("Layer score per quarter: low (margin bonus 0) / high (margin bonus max); * = above 65")
summary = {}
for lid, per_q in layers.items():
    rows, hits_lo, hits_hi, best = [], 0, 0, None
    for q in sorted(per_q):
        cs = per_q[q]
        tot = sum(c[1] for c in cs)
        a_lo = sum(c[1] * c[2] for c in cs) / tot
        a_hi = sum(c[1] * c[3] for c in cs) / tot
        lo = W["acceleration"] * a_lo + W["constraints"] * C + W["smart_money"] * S
        hi = W["acceleration"] * a_hi + W["constraints"] * C + W["smart_money"] * S
        hits_lo += lo > 65; hits_hi += hi > 65
        if best is None or hi > best[1]:
            best = (q, hi, lo, len(cs))
        rows.append(f"{q} {lo:5.1f}{'*' if lo > 65 else ' '} / {hi:5.1f}{'*' if hi > 65 else ' '} ({len(cs)} cos)")
    summary[lid] = (len(per_q), hits_lo, hits_hi, best)
    print(f"\n{lid}: " + "; ".join(rows))
print("\nSummary: quarters above 65, low / high bound; best quarter (high bound)")
for lid, (n, lo, hi, b) in summary.items():
    print(f"  {lid:8} {lo:2d} / {hi:2d} of {n}   best {b[0]} {b[1]:.1f} (low {b[2]:.1f}, {b[3]} cos)")

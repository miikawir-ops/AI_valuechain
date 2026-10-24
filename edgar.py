"""
edgar.py — Quarterly revenue from SEC EDGAR XBRL companyfacts.

yfinance has at most 5-6 quarters of revenue, one short of what revenue
acceleration (the change in YoY growth between the latest two quarters)
needs, and sometimes leaves the newest quarter blank (MU's quarter ended
2026-09-03, AMZN's 2026-06-30, both reported). EDGAR has many years for
companies filing XBRL with the SEC: 10-Q/10-K, plus 6-K for some foreign
filers (ARM). ASML and TSM file no quarterly XBRL, so they get nothing here.

A company's revenue can sit under different XBRL concepts, and some of them
are partial (CEG's RevenueFromContractWithCustomer excludes ~$1.7bn of
non-contract revenue). So no concept is trusted by name: each candidate's
quarterly series is cross-checked against yfinance's quarters, and only one
that matches every overlapping quarter within 1% is used. No match, no
CIK or a failed request → None, and the caller treats acceleration as
unmeasured. Never an estimate.

SEC asks for a User-Agent naming the requester; set SEC_USER_AGENT (e.g.
"RayDar research you@example.com"). The default is accepted today but
isn't guaranteed.
"""

import os
import time
import logging
import datetime

import requests

log = logging.getLogger(__name__)

SEC_USER_AGENT = os.getenv("SEC_USER_AGENT") or "RayDar research"   # an unset Actions secret arrives as ""
TICKERS_URL    = "https://www.sec.gov/files/company_tickers.json"
FACTS_URL      = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"

REVENUE_CONCEPTS = [
    ("us-gaap",   "Revenues"),
    ("us-gaap",   "RevenueFromContractWithCustomerExcludingAssessedTax"),
    ("us-gaap",   "RevenueFromContractWithCustomerIncludingAssessedTax"),
    ("us-gaap",   "SalesRevenueNet"),
    ("ifrs-full", "Revenue"),
]
FORMS          = {"10-Q", "10-K", "10-Q/A", "10-K/A", "6-K", "20-F", "40-F"}
MATCH_TOL      = 0.01   # an overlapping quarter must agree with yfinance within 1%
MIN_OVERLAP    = 3      # ...on at least this many quarters
DATE_TOL_DAYS  = 10     # yfinance dates are month-ends; filings use the fiscal period end

MIN_INTERVAL_S = 0.15   # ≤ ~7 requests/s, under SEC's 10/s fair-access limit

_cik_cache: dict | None = None
_last_request = 0.0


def _get(url: str) -> dict:
    global _last_request
    wait = MIN_INTERVAL_S - (time.perf_counter() - _last_request)
    if wait > 0:
        time.sleep(wait)
    _last_request = time.perf_counter()
    r = requests.get(url, headers={"User-Agent": SEC_USER_AGENT}, timeout=30)
    r.raise_for_status()
    return r.json()


def _cik(ticker: str) -> int | None:
    global _cik_cache
    if _cik_cache is None:
        _cik_cache = {v["ticker"]: v["cik_str"] for v in _get(TICKERS_URL).values()}
    return _cik_cache.get(ticker)


def _quarters(rows: list) -> dict:
    """{quarter_end: revenue} from one concept's facts, Q4 derived as FY − Q1..Q3."""
    q, fy = {}, {}
    for f in rows:
        if not f.get("start") or (f.get("form") or "") not in FORMS:
            continue
        s, e = datetime.date.fromisoformat(f["start"]), datetime.date.fromisoformat(f["end"])
        days = (e - s).days
        bucket = q if 80 <= days <= 100 else fy if 350 <= days <= 380 else None
        if bucket is not None and ((s, e) not in bucket or f["filed"] > bucket[(s, e)][1]):
            bucket[(s, e)] = (float(f["val"]), f["filed"])   # latest filing (restatement) wins
    out = {}
    for (s, e), (v, _) in q.items():
        out[e] = v
    for (s, e), (v, _) in fy.items():
        if any(abs((e - qe).days) <= 7 for qe in out):
            continue
        inside = {qe: qv for (qs, qe), (qv, _) in q.items()
                  if qs >= s - datetime.timedelta(days=7) and qe <= e}
        if len(inside) == 3:
            out[e] = v - sum(inside.values())
    return out


def _matches(series: dict, yf_revenue: dict) -> bool:
    overlap = 0
    for yd, yv in yf_revenue.items():
        ed = next((d for d in series if abs((d - yd).days) <= DATE_TOL_DAYS), None)
        if ed is None:
            continue
        if not yv or abs(series[ed] / yv - 1) > MATCH_TOL:
            return False
        overlap += 1
    return overlap >= MIN_OVERLAP


def quarterly_revenue(ticker: str, yf_revenue: dict) -> tuple[dict | None, str]:
    """({quarter_end: revenue}, note) from EDGAR, or (None, reason).

    yf_revenue ({date: revenue}, yfinance's quarters) is the cross-check: the
    first concept whose series agrees with it is returned whole.
    """
    try:
        cik = _cik(ticker)
        if not cik:
            return None, "no SEC CIK"
        facts = _get(FACTS_URL.format(cik=cik)).get("facts", {})
    except Exception as e:
        log.warning(f"  {ticker} EDGAR fetch failed: {e}")
        return None, f"EDGAR fetch failed ({type(e).__name__})"
    found_any = False
    for ns, concept in REVENUE_CONCEPTS:
        for unit, rows in facts.get(ns, {}).get(concept, {}).get("units", {}).items():
            series = _quarters(rows)
            if not series:
                continue
            found_any = True
            if _matches(series, yf_revenue):
                return series, f"{ns}:{concept} [{unit}]"
    return None, ("no concept matches yfinance's quarters" if found_any else "no quarterly revenue filed")

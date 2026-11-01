"""
capex.py — Hyperscaler capex: the four hyperscalers' last reported quarters,
summed in dollars and compared with the same quarters a year earlier.
Display-only on both RayDar sites: it feeds no score, colour or rating.
Canonical here; RayDar Data Center copies this file unchanged, so both
sites show the same number (Ray, 2026-11-01).

Quarters come from SEC EDGAR, cross-checked against Yahoo Finance within 1%
on every overlapping quarter (edgar._matches, the revenue rule). Filings
report cash flow year-to-date, so a single quarter is a direct ~3-month
fact or the difference of two YTD facts with the same start (Q4 = full
year − 9 months). EDGAR also has quarters Yahoo leaves out: on 2026-11-01
Yahoo's newest AMZN quarter was 2026-03-31, two behind the other three.
EDGAR unavailable → Yahoo's quarters, and a company left behind is stale.

The aggregate uses the newest quarter-end (aligned within
edgar.DATE_TOL_DAYS) that at least MIN_COMPANIES companies have filed. Each
company is compared with its own quarter ending 350-380 days earlier, the
revenue YoY rule. A company without that quarter is named stale or missing,
never silently dropped or mixed in. Direction is the change in aggregate
YoY vs the previous quarter, the same companies on both sides, with the
±5 pp band of revenue acceleration. Reported capex is backward-looking;
company guidance isn't included.
"""

import datetime
import logging

import edgar

log = logging.getLogger(__name__)

TICKERS = ["MSFT", "GOOGL", "AMZN", "META"]
# Concepts tried in order; the first whose quarters match Yahoo's is used.
# AMZN files purchases of property and equipment as ProductiveAssets.
CONCEPTS = [("us-gaap", "PaymentsToAcquirePropertyPlantAndEquipment"),
            ("us-gaap", "PaymentsToAcquireProductiveAssets")]
FORMS = {"10-Q", "10-K", "10-Q/A", "10-K/A"}
MIN_COMPANIES = 3      # fewer: no aggregate, "not available" (never a 1-2 company "aggregate")
DIRECTION_BAND = 0.05  # ±5 pp change in aggregate YoY, the revenue-acceleration band
HISTORY_QUARTERS = 4   # aggregate YoY for the chart: the latest quarter and up to 3 before it
# What Yahoo's cash-flow table held per ticker in the last fetch: "row", "no
# row" or "no table". DataCenter fails its run when tables came back but none
# had the capex row, a data-format change (its ledger R44); nothing else reads it.
YAHOO_TABLES: dict = {}


def edgar_quarters(rows: list) -> dict:
    """{quarter_end: value} from one concept's cash-flow facts: a direct
    ~3-month fact, else YTD(n) − YTD(n−1) with the same fiscal-year start.
    The latest filing (a restatement) wins for each period."""
    facts = {}
    for f in rows:
        if not f.get("start") or (f.get("form") or "") not in FORMS:
            continue
        key = (datetime.date.fromisoformat(f["start"]), datetime.date.fromisoformat(f["end"]))
        if key not in facts or f["filed"] > facts[key][1]:
            facts[key] = (float(f["val"]), f["filed"])
    out = {e: v for (s, e), (v, _) in facts.items() if 80 <= (e - s).days <= 100}
    for (s, e), (v, _) in facts.items():
        if (e - s).days > 100 and e not in out:
            prior = [pv for (ps, pe), (pv, _) in facts.items() if ps == s and 75 <= (e - pe).days <= 105]
            if prior:
                out[e] = v - prior[0]
    return out


def yahoo_quarters(ticker: str) -> dict:
    """{quarter_end: value} from Yahoo's quarterly cash flow (capex is negative there)."""
    import yfinance as yf
    cf = yf.Ticker(ticker).quarterly_cashflow
    if cf is None or cf.empty or "Capital Expenditure" not in cf.index:
        YAHOO_TABLES[ticker] = "no table" if cf is None or cf.empty else "no row"
        return {}
    YAHOO_TABLES[ticker] = "row"
    return {k.date(): abs(float(v)) for k, v in cf.loc["Capital Expenditure"].items() if v == v}


def company_quarters(ticker: str) -> tuple[dict, str]:
    """({quarter_end: capex in $}, source). Yahoo's quarters plus the EDGAR
    quarters Yahoo lacks, from the first concept that matches Yahoo."""
    try:
        yq = yahoo_quarters(ticker)
    except Exception as e:
        log.warning(f"  {ticker} Yahoo capex fetch failed: {e}")
        yq = {}
    try:
        cik = edgar._cik(ticker)
        facts = edgar._get(edgar.FACTS_URL.format(cik=cik)).get("facts", {}) if cik else {}
    except Exception as e:
        log.warning(f"  {ticker} EDGAR capex fetch failed: {e}")
        return yq, f"Yahoo only (EDGAR fetch failed: {type(e).__name__})"
    for ns, concept in CONCEPTS:
        series = edgar_quarters(facts.get(ns, {}).get(concept, {}).get("units", {}).get("USD", []))
        if series and yq and edgar._matches(series, yq):
            merged = dict(yq)
            for d, v in series.items():
                if not any(abs((d - y).days) <= edgar.DATE_TOL_DAYS for y in yq):
                    merged[d] = v
            return merged, f"Yahoo + EDGAR {ns}:{concept}"
        if series and not yq:
            return series, f"EDGAR {ns}:{concept} (no Yahoo quarters to cross-check)"
    return yq, "Yahoo only (no EDGAR concept matches Yahoo's quarters)"


def _near(series: dict, target: datetime.date):
    return next((d for d in sorted(series, reverse=True) if abs((d - target).days) <= edgar.DATE_TOL_DAYS), None)


def _year_ago(series: dict, end: datetime.date):
    return next((d for d in sorted(series, reverse=True) if 350 <= (end - d).days <= 380), None)


def aggregate(quarters: dict, sources: dict = None) -> dict:
    """The aggregate from {ticker: {quarter_end: value}}. Pure: no fetching.

    available False (with reason) when fewer than MIN_COMPANIES have the
    newest common quarter and its year-earlier quarter. companies always
    lists all four, each "included", "stale" (latest quarter older than the
    aggregate's) or "missing" (no quarters, or no year-earlier quarter).
    """
    sources = sources or {}
    latest = {t: max(q) for t, q in quarters.items() if q}
    # Newest quarter-end that at least MIN_COMPANIES companies have filed.
    target = None
    for end in sorted({d for q in quarters.values() for d in q}, reverse=True):
        if sum(1 for q in quarters.values() if q and _near(q, end)) >= MIN_COMPANIES:
            target = end
            break
    companies, included = [], []
    for t in quarters:
        q = quarters[t]
        row = {"ticker": t, "latest_quarter": latest[t].isoformat() if t in latest else None,
               "source": sources.get(t, "")}
        end = _near(q, target) if (q and target) else None
        base = _year_ago(q, end) if end else None
        if end and base and q[base]:
            row.update(status="included", quarter=end.isoformat(), value=q[end],
                       year_ago_quarter=base.isoformat(), year_ago_value=q[base], yoy=round(q[end] / q[base] - 1, 4))
            included.append((t, end))
        elif not q:
            row.update(status="missing", note="no capex quarters")
        elif end:
            row.update(status="missing", note=f"no quarter a year before {end.isoformat()}")
        else:
            row.update(status="stale", note=f"latest quarter {latest[t].isoformat()}")
        companies.append(row)
    if target is None or len(included) < MIN_COMPANIES:
        return {"available": False, "companies": companies,
                "reason": f"{len(included)} of {len(quarters)} companies have a comparable quarter (minimum {MIN_COMPANIES})"}

    now  = sum(r["value"] for r in companies if r["status"] == "included")
    base = sum(r["year_ago_value"] for r in companies if r["status"] == "included")
    out = {"available": True, "quarter": target.isoformat(), "total": now, "total_year_ago": base,
           "yoy": round(now / base - 1, 4), "included": len(included), "companies": companies,
           "prev_quarter": None, "prev_yoy": None, "change_pp": None, "direction": None,
           "history": _history(quarters, dict(included))}

    # Previous quarter, the same companies: each needs the quarter before its
    # aggregate quarter and that quarter's year-earlier one.
    prev_now = prev_base = 0.0
    prev_ends = []
    for t, end in included:
        q = quarters[t]
        p = next((d for d in sorted(q, reverse=True) if 75 <= (end - d).days <= 105), None)
        pb = _year_ago(q, p) if p else None
        if not (p and pb and q[pb]):
            out["direction_note"] = f"{t} has no comparable previous quarter"
            return out
        prev_now += q[p]
        prev_base += q[pb]
        prev_ends.append(p)
    prev_yoy = round(prev_now / prev_base - 1, 4)
    change = round(out["yoy"] - prev_yoy, 4)
    out.update(prev_quarter=max(prev_ends).isoformat(), prev_yoy=prev_yoy, change_pp=round(change * 100, 1),
               direction="accelerating" if change > DIRECTION_BAND else
                         "decelerating" if change < -DIRECTION_BAND else "stable")
    return out


def _history(quarters: dict, ends: dict) -> list:
    """[{"quarter", "yoy"}], oldest first: aggregate YoY for up to
    HISTORY_QUARTERS consecutive quarters ending at the aggregate's, the same
    companies in each, each against its own year-earlier quarter. Stops at
    the first quarter a company can't supply, never mixing company sets."""
    out = []
    for _ in range(HISTORY_QUARTERS):
        now = base = 0.0
        for t, end in ends.items():
            b = _year_ago(quarters[t], end)
            if not (b and quarters[t][b]):
                return out[::-1]
            now += quarters[t][end]
            base += quarters[t][b]
        out.append({"quarter": max(ends.values()).isoformat(), "yoy": round(now / base - 1, 4)})
        prev = {t: next((d for d in sorted(quarters[t], reverse=True) if 75 <= (end - d).days <= 105), None)
                for t, end in ends.items()}
        if any(p is None for p in prev.values()):
            break
        ends = prev
    return out[::-1]


def fetch() -> dict:
    """The aggregate for TICKERS, fetched live. Never raises: a failure is
    "not available" with its reason, never a zero."""
    YAHOO_TABLES.clear()
    try:
        quarters, sources = {}, {}
        for t in TICKERS:
            quarters[t], sources[t] = company_quarters(t)
        result = aggregate(quarters, sources)
    except Exception as e:
        log.warning(f"  Capex aggregate failed: {e}")
        return {"available": False, "reason": f"fetch failed ({type(e).__name__})",
                "companies": [{"ticker": t, "status": "missing", "note": "fetch failed"} for t in TICKERS]}
    if result["available"]:
        log.info(f"  Capex: ${result['total'] / 1e9:.1f}bn in the quarter ending {result['quarter']}, "
                 f"{result['yoy']:+.1%} YoY, {result['direction'] or 'direction not measured'} "
                 f"({result['included']}/{len(TICKERS)} companies)")
    else:
        log.warning(f"  Capex not available: {result['reason']}")
    return result

"""
test_capex.py — the hyperscaler capex aggregate (capex.py): quarters from
YTD cash-flow facts, a dollar sum date-matched to the year-earlier
quarters (not an average of percentages), stale and missing companies
named, at least 3 of 4, and the direction from the change in aggregate YoY
with the same companies (±5 pp). Ray, 2026-11-01.

The figures are the real ones of 2026-11-01 ($bn): Yahoo's newest AMZN
quarter was 2026-03-31 while the others had 2026-09-30, and EDGAR had all
four. RayDar Data Center's old overlay averaged the four per-company YoY
percentages and mixed AMZN's Q1 into the Q3 reading (+93.6%).

No pytest in this environment — plain script. Run: python test_capex.py
"""

import datetime

import capex

D = datetime.date.fromisoformat
BN = 1e9

# EDGAR-derived quarters ($bn) on 2026-11-01, Q3 2025 .. Q3 2026 plus Q2 2025.
REAL = {
    "MSFT":  {"2025-06-30": 17.08, "2025-09-30": 19.394, "2025-12-31": 29.876, "2026-03-31": 30.876, "2026-06-30": 35.802, "2026-09-30": 39.367},
    "GOOGL": {"2025-06-30": 22.45, "2025-09-30": 23.953, "2025-12-31": 27.851, "2026-03-31": 35.674, "2026-06-30": 44.924, "2026-09-30": 51.355},
    "AMZN":  {"2025-06-30": 32.183, "2025-09-30": 35.095, "2025-12-31": 39.522, "2026-03-31": 44.203, "2026-06-30": 54.21, "2026-09-30": 77.26},
    "META":  {"2025-06-30": 16.54, "2025-09-30": 18.829, "2025-12-31": 21.383, "2026-03-31": 18.997, "2026-06-30": 30.116, "2026-09-30": 33.942},
}


def q(d):
    return {t: {D(k): v * BN for k, v in s.items()} for t, s in d.items()}


def test_ytd_facts_become_single_quarters():
    # GOOGL-style 10-Q facts: Q1 direct, then H1 and 9M year-to-date; MSFT-style Q4 = FY − 9M.
    rows = [
        {"start": "2026-01-01", "end": "2026-03-31", "val": 35.674e9, "form": "10-Q", "filed": "2026-04-30"},
        {"start": "2026-01-01", "end": "2026-06-30", "val": 80.598e9, "form": "10-Q", "filed": "2026-07-23"},
        {"start": "2026-01-01", "end": "2026-09-30", "val": 131.953e9, "form": "10-Q", "filed": "2026-10-29"},
        {"start": "2025-07-01", "end": "2026-03-31", "val": 80.146e9, "form": "10-Q", "filed": "2026-04-29"},
        {"start": "2025-07-01", "end": "2026-06-30", "val": 115.948e9, "form": "10-K", "filed": "2026-07-29"},
        {"start": "2026-01-01", "end": "2026-03-31", "val": 1.0, "form": "8-K", "filed": "2026-05-01"},   # not a 10-Q/10-K
    ]
    s = capex.edgar_quarters(rows)
    assert abs(s[D("2026-03-31")] - 35.674e9) < 1, s
    assert abs(s[D("2026-06-30")] - (80.598e9 - 35.674e9)) < 1, s
    assert abs(s[D("2026-09-30")] - (131.953e9 - 80.598e9)) < 1, s
    print("PASS: single quarters from direct and year-to-date cash-flow facts")
    s = capex.edgar_quarters(rows[3:5])
    assert abs(s[D("2026-06-30")] - (115.948e9 - 80.146e9)) < 1, s
    print("PASS: a fiscal Q4 is the full year minus nine months (MSFT's June quarter)")


def test_dollar_aggregate_all_four():
    r = capex.aggregate(q(REAL))
    assert r["available"] and r["quarter"] == "2026-09-30" and r["included"] == 4, r
    assert abs(r["total"] / BN - 201.924) < 0.01 and abs(r["total_year_ago"] / BN - 97.271) < 0.01, (r["total"], r["total_year_ago"])
    assert abs(r["yoy"] - 1.0759) < 0.0005, r["yoy"]
    avg = sum(c["yoy"] for c in r["companies"]) / 4
    assert abs(avg - r["yoy"]) > 0.02, "a dollar sum, not the average of the four percentages"
    # Q2 2026: $165.052bn vs $88.253bn = +87.02%; +107.59% − 87.02% = +20.6 pp.
    assert abs(r["prev_yoy"] - 0.8702) < 0.0005 and r["direction"] == "accelerating" and r["change_pp"] == 20.6, \
        (r["prev_yoy"], r["change_pp"], r["direction"])
    print(f"PASS: $201.9bn vs $97.3bn = {r['yoy']:+.1%}; previous quarter {r['prev_yoy']:+.1%}; accelerating {r['change_pp']:+.1f} pp")


def test_stale_company_named_not_mixed_in():
    # Yahoo-only AMZN on 2026-11-01: newest quarter 2026-03-31.
    d = q(REAL)
    d["AMZN"] = {k: v for k, v in d["AMZN"].items() if k <= D("2026-03-31")}
    r = capex.aggregate(d)
    amzn = next(c for c in r["companies"] if c["ticker"] == "AMZN")
    assert r["available"] and r["quarter"] == "2026-09-30" and r["included"] == 3, r
    assert amzn["status"] == "stale" and amzn["note"] == "latest quarter 2026-03-31", amzn
    assert abs(r["yoy"] - (124.664 / 62.176 - 1)) < 0.0005, r["yoy"]
    print(f"PASS: a stale AMZN is named and left out (3 of 4, {r['yoy']:+.1%}), not mixed into the Q3 sum")


def test_two_filed_falls_back_to_the_common_quarter():
    # Earnings season: only MSFT and GOOGL have filed Q3.
    d = q(REAL)
    for t in ("AMZN", "META"):
        d[t] = {k: v for k, v in d[t].items() if k <= D("2026-06-30")}
    r = capex.aggregate(d)
    assert r["available"] and r["quarter"] == "2026-06-30" and r["included"] == 4, (r["quarter"], r["included"])
    print("PASS: with 2 of 4 on a new quarter, the aggregate uses the newest quarter at least 3 have filed")


def test_fewer_than_three_is_not_available():
    d = q(REAL)
    d["AMZN"], d["META"] = {}, {D("2026-09-30"): 33.942 * BN}   # META: no year-earlier quarter
    r = capex.aggregate(d)
    st = {c["ticker"]: c["status"] for c in r["companies"]}
    assert r["available"] is False and "minimum 3" in r["reason"], r
    assert st == {"MSFT": "included", "GOOGL": "included", "AMZN": "missing", "META": "missing"}, st
    print("PASS: fewer than 3 comparable companies: not available, never a 2-company 'aggregate' or a zero")


def test_direction_bands():
    base = q(REAL)
    for t in base:   # flat YoY: each quarter 50% above its year-earlier quarter, both quarters
        base[t] = {D("2025-06-30"): 10 * BN, D("2025-09-30"): 10 * BN, D("2026-06-30"): 15 * BN, D("2026-09-30"): 15 * BN}
    assert capex.aggregate(base)["direction"] == "stable"
    for t in base:
        base[t][D("2026-09-30")] = 15.6 * BN   # +56% vs +50%: +6 pp
    assert capex.aggregate(base)["direction"] == "accelerating"
    for t in base:
        base[t][D("2026-09-30")] = 14.4 * BN   # +44%: −6 pp
    assert capex.aggregate(base)["direction"] == "decelerating"
    print("PASS: direction is the change in aggregate YoY, ±5 pp (stable / accelerating / decelerating)")


def test_no_previous_quarter_means_direction_not_measured():
    d = q(REAL)
    d["META"].pop(D("2025-06-30"))
    r = capex.aggregate(d)
    assert r["available"] and r["direction"] is None and "META" in r["direction_note"], r
    print("PASS: without the same companies' previous quarter, direction is not measured, not guessed")


def test_card_supply_side_names_a_recipient_layer_not_cloud():
    import render
    best = lambda score, color: {"best": {"score": score, "color": color}}
    scored = {"cloud": best(70.0, "Red"), "compute": best(59.1, "Orange"), "memory": best(56.3, "Orange"),
              "energy": best(39.5, "Green"), "infra": best(38.4, "Green"), "software": best(80.0, "Red")}
    html = render._capex_html(capex.aggregate(q(REAL)), scored)
    assert "Semicon</div>" in html and "score 59 · Orange" in html, "highest of Energy/Semicon/HBM memory/Data center"
    assert ">Cloud</div>" not in html and ">AI software</div>" not in html
    assert "$201.9bn" in html and "+107.6%" in html and "accelerating (+20.6 pp" in html
    print("PASS: card: supply side is the highest-scoring capex recipient (Semicon), never Cloud or software")


def test_card_names_stale_and_never_shows_zero():
    import render
    d = q(REAL)
    d["AMZN"] = {k: v for k, v in d["AMZN"].items() if k <= D("2026-03-31")}
    html = render._capex_html(capex.aggregate(d), {})
    assert "stale: latest quarter Mar 31, 2026" in html and "3 of 4 companies" in html, "stale company named"
    html = render._capex_html({"available": False, "reason": "fetch failed (RuntimeError)", "companies": []}, {})
    assert "Not available this run: fetch failed" in html and "$0" not in html and "0.0%" not in html
    html = render._capex_html(None, {})
    assert "Not available this run: not fetched this run" in html
    print("PASS: card: a stale company is named; a failed fetch says 'not available', never $0 or 0%")


if __name__ == "__main__":
    test_ytd_facts_become_single_quarters()
    test_dollar_aggregate_all_four()
    test_stale_company_named_not_mixed_in()
    test_two_filed_falls_back_to_the_common_quarter()
    test_fewer_than_three_is_not_available()
    test_direction_bands()
    test_no_previous_quarter_means_direction_not_measured()
    test_card_supply_side_names_a_recipient_layer_not_cloud()
    test_card_names_stale_and_never_shows_zero()
    print("\nAll capex tests passed.")

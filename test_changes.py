"""
test_changes.py — "What changed" (changes.py): layer colour and state
changes listed as they are; a company's rating or colour change only with a
score move of at least 2 points or a newly reported quarter (news noise is
1-2 points); new or cleared hype and divergence flags; new quarters;
nothing across a method change. Ray, 2026-11-02.

No pytest in this environment — plain script. Run: python test_changes.py
"""

import os
import json
import tempfile

import changes


def company(t, score, rating="B", color="Green", q="2026-06-30", hype=False, div=False):
    return {"ticker": t, "score": score, "rating": rating, "color": color, "is_hype": hype,
            "divergence": {"evaluated": True, "flag": div}, "inputs": {"growth_quarter": q}}


def record(session, layers, companies, method="2026-10-yoy-edgar"):
    return {"session": session, "method": method, "layers": layers, "companies": companies}


L0 = {"energy": {"color": "Green", "score": 44.1, "status": "Holding Green — borderline score, prev 3d dominant"},
      "compute": {"color": "Orange", "score": 58.0, "status": "Confirmed Orange — 2/3 recent days above 40"},
      "memory": {"color": "Orange", "score": 55.0, "status": "Confirmed Orange — 3/3 recent days above 40"}}
L1 = {"energy": {"color": "Orange", "score": 46.3, "status": "Confirmed Orange — 2/3 recent days above 40"},
      "compute": {"color": "Orange", "score": 58.4, "status": "Confirmed Orange — 3/3 recent days above 40"},
      "memory": {"color": "Orange", "score": 54.0, "status": "Holding Orange — borderline score, prev 3d dominant"}}
NAMES = {"energy": "Energy", "compute": "Semicon", "memory": "HBM memory"}


def test_layers_listed_as_they_are_without_day_counts():
    items = changes.compare(record("a", L0, []), record("b", L1, []), NAMES)
    assert items == ["Energy: Green → Orange (score 44.1 → 46.3)",
                     "HBM memory: Confirmed Orange → Holding Orange"], items
    print("PASS: layer colour and confirmation-state changes listed; '2/3 → 3/3 days' isn't a change")


def test_company_changes_need_a_two_point_move_or_a_new_quarter():
    prev = [company("NOISE", 50.0), company("MOVED", 50.0), company("NEWQ", 50.0), company("FLAT", 50.0)]
    cur = [company("NOISE", 51.2, rating="A", color="Orange"),            # 1.2 points: news noise, not listed
           company("MOVED", 52.5, rating="A", color="Orange"),            # 2.5 points: listed
           company("NEWQ", 50.4, rating="C", q="2026-09-30"),             # new quarter: listed despite 0.4
           company("FLAT", 50.1)]
    items = changes.compare(record("a", {}, prev), record("b", {}, cur))
    assert items == ["MOVED: rating B → A, Green → Orange (score 50.0 → 52.5)",
                     "NEWQ: rating B → C (score 50.0 → 50.4)",
                     "NEWQ: new quarter (Sep 30, 2026)"], items
    print("PASS: a rating/colour change needs a ≥2-point move or a new quarter; 1.2 points of noise isn't listed")


def test_flags_new_and_cleared():
    prev = [company("H", 50, hype=True), company("D", 50)]
    cur = [company("H", 50), company("D", 50, div=True)]
    items = changes.compare(record("a", {}, prev), record("b", {}, cur))
    assert items == ["H: hype flag cleared", "D: divergence flag new"], items
    print("PASS: new and cleared hype and divergence flags listed")


def test_compute_uses_each_sessions_last_run_and_not_across_methods():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "scores_detail.jsonl")
        recs = [record("2026-10-29", L0, [company("X", 50.0)]),
                record("2026-10-29", L0, [company("X", 40.0)]),           # same session, later run: this one counts
                record("2026-10-30", L1, [company("X", 43.0, rating="A")])]
        open(path, "w").write("\n".join(json.dumps(r) for r in recs) + "\n")
        r = changes.compute(path, "2026-10-30", NAMES)
        assert r["available"] and r["prev_session"] == "2026-10-29" and not r["method_break"], r
        assert "X: rating B → A (score 40.0 → 43.0)" in r["items"], r["items"]
        recs.append(record("2026-11-02", L0, [company("X", 60.0, rating="C")], method="new-method"))
        open(path, "w").write("\n".join(json.dumps(r) for r in recs) + "\n")
        r = changes.compute(path, "2026-11-02", NAMES)
        assert r["method_break"] and r["items"] == [], r
        assert changes.compute(path, "2026-10-29")["available"] is False
        assert changes.compute(os.path.join(tmp, "missing.jsonl"), "2026-10-30")["available"] is False
    print("PASS: each session's last run compared; nothing across a method change; missing data never raises")


def test_sections_render():
    import datetime
    import render
    items = [f"T{i}: hype flag new" for i in range(7)]
    html = render._changes_html({"available": True, "prev_session": "2026-10-29", "method_break": False, "items": items})
    assert "What changed since Thu Oct 29" in html and "Show all (7)" in html and html.count('class="wc-item"') == 7
    assert "How this list is made" in html and "at least 2 points" in html, "the rule is in the Details"
    assert "No changes since Thu Oct 29." in render._changes_html({"available": True, "prev_session": "2026-10-29", "method_break": False, "items": []})
    assert "scoring method changed" in render._changes_html({"available": True, "prev_session": "2026-10-29", "method_break": True, "items": []})
    assert render._changes_html({"available": False}) == ""
    best = lambda d: {"best": {}, "all_tickers": [{"ticker": "A", "fund_delta": d}]}
    ok = render._gaps_html({"x": best(0.1)}, {"x": [{"ticker": "A", "growth_quarter": "2026-09-30", "data_missing": []}]}, datetime.date(2026, 10, 30))
    assert ok == '<div class="gaps-ok">No data gaps this run · 1 of 1 measured</div>', ok
    bad = render._gaps_html({"x": best(None)}, {"x": [{"ticker": "A", "growth_curr": 0.2, "growth_quarter": "2026-03-31", "data_missing": ["growth_prev", "gross_margin"]}]}, datetime.date(2026, 10, 30))
    for want in ("A: acceleration not measured (no year-on-year figure for the previous quarter)",
                 "A: latest quarter Mar 31, 2026, 213 days old", "A: missing gross_margin", "0 of 1 companies"):
        assert want in bad, (want, bad)
    print("PASS: What changed (5 shown, Show all, the rule in Details) and Data gaps (one line, or each gap named; 150 days)")


if __name__ == "__main__":
    test_layers_listed_as_they_are_without_day_counts()
    test_company_changes_need_a_two_point_move_or_a_new_quarter()
    test_flags_new_and_cleared()
    test_compute_uses_each_sessions_last_run_and_not_across_methods()
    test_sections_render()
    print("\nAll changes tests passed.")

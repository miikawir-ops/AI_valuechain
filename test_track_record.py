"""
test_track_record.py — Item 8: the signal track record (Ray, 2026-10-24).

  - a pick starts when the signal names a new company and ends when another
    replaces it; a run without a signal doesn't end it;
  - a finished pick's return runs entry → exit (its last session), an active
    one entry → latest close; NASDAQ over the same sessions;
  - returns use split-adjusted closes fetched at evaluation time; the stored
    close is a cross-check only (a later split must not break the return);
  - old-method picks (scores_history.json, no price) are shown separately.

No pytest in this environment — plain script. Run: python test_track_record.py
"""

import os
import json
import tempfile
import datetime

import track_record

D = datetime.date
METHOD = "2026-10-yoy-edgar"


def _closes(table):
    def closes_for(ticker, start, end):
        return {d: v for d, v in table.get(ticker, {}).items() if start <= d <= end}
    return closes_for


def test_episodes_exits_benchmark_split_and_old_method():
    # A 4:1 split of "SPL" after its entry: the stored close is pre-split, the
    # fetched (split-adjusted) history isn't — the return must use the latter.
    prices = {
        "AVGO": {D(2026, 10, 23): 321.68, D(2026, 10, 26): 330.0, D(2026, 10, 27): 340.0},
        "SPL":  {D(2026, 10, 27): 100.0, D(2026, 10, 28): 110.0},
        "MU":   {D(2026, 9, 14): 400.0, D(2026, 9, 28): 440.0, D(2026, 9, 30): 450.0, D(2026, 10, 23): 877.55},
        "^IXIC":{D(2026, 9, 14): 100.0, D(2026, 9, 28): 101.0, D(2026, 9, 30): 102.0, D(2026, 10, 23): 104.0,
                 D(2026, 10, 26): 105.0, D(2026, 10, 27): 106.0, D(2026, 10, 28): 106.0},
    }
    runs = [("2026-10-23", "AVGO", 321.68, "2026-10-24T17:49:46Z"),
            ("2026-10-23", None,   None,   "2026-10-24T18:00:00Z"),      # withheld: doesn't end the pick
            ("2026-10-26", "AVGO", 330.0,  "2026-10-26T20:31:00Z"),
            ("2026-10-27", "SPL",  400.0,  "2026-10-27T20:31:00Z"),      # stored pre-split close
            ("2026-10-28", "SPL",  110.0,  "2026-10-28T20:31:00Z")]
    history = ([{"date": f"2026-09-{d}", "action_ticker": "MU"} for d in (14, 28)]
               + [{"date": "2026-09-29", "action_ticker": "AVGO"}, {"date": "2026-09-30", "action_ticker": "MU"},
                  {"date": "2026-10-21", "action_ticker": ""}, {"date": "2026-10-23", "action_ticker": "MU"},
                  {"date": "2026-10-26", "method": METHOD, "action_ticker": "AVGO"}])
    prices["AVGO"][D(2026, 9, 29)] = 300.0
    prices["^IXIC"][D(2026, 9, 29)] = 101.5
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "scores_detail.jsonl")
        with open(path, "w", encoding="utf-8") as fh:
            for s, t, c, r in runs:
                fh.write(json.dumps({"session": s, "run_utc": r, "signal": {"ticker": t, "close": c}}) + "\n")
        tr = track_record.build(history, path, D(2026, 10, 28), METHOD, closes_for=_closes(prices))
    cur = {r["ticker"]: r for r in tr["current"]}
    assert [r["ticker"] for r in tr["current"]] == ["SPL", "AVGO"], tr["current"]
    avgo, spl = cur["AVGO"], cur["SPL"]
    assert (avgo["first"], avgo["end"], avgo["active"]) == ("2026-10-23", "2026-10-26", False), avgo
    assert avgo["ret_pct"] == round((330.0 / 321.68 - 1) * 100, 1) and avgo["nasdaq_pct"] == round((105 / 104 - 1) * 100, 1), avgo
    assert spl["active"] and spl["end"] == "2026-10-28" and spl["ret_pct"] == 10.0, f"split broke the return: {spl}"
    old = tr["old"]
    assert [(r["ticker"], r["first"], r["end"]) for r in old] == [
        ("MU", "2026-09-30", "2026-10-23"), ("AVGO", "2026-09-29", "2026-09-29"), ("MU", "2026-09-14", "2026-09-28")], old
    assert old[0]["ret_pct"] == round((877.55 / 450 - 1) * 100, 1) and old[0]["entry"] == 450.0
    print("PASS: picks end at exit, active runs to latest, NASDAQ benchmark, split-safe, withheld/blank runs don't end a pick, old method separate")


def test_pre_fix_morning_entries_use_the_previous_session():
    """Before 48d0ada (2026-10-11) history was dated by run day: the 2026-09-30
    11:49 UTC run showed the Sep 29 close. Later entries are keyed by session."""
    e = track_record._effective_session
    assert e({"date": "2026-09-30", "time": "11:49"}) == D(2026, 9, 29)
    assert e({"date": "2026-09-28", "time": "12:44"}) == D(2026, 9, 25), "Monday morning → Friday's close"
    assert e({"date": "2026-09-29", "time": "23:50"}) == D(2026, 9, 29)
    assert e({"date": "2026-10-13", "time": "11:03"}) == D(2026, 10, 13), "post-fix entries are already sessions"
    print("PASS: pre-fix morning entries map to the session their page showed")


def test_missing_closes_are_missing_not_zero():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "scores_detail.jsonl")
        open(path, "w").write(json.dumps({"session": "2026-10-23", "run_utc": "x", "signal": {"ticker": "GONE", "close": 5.0}}) + "\n")
        tr = track_record.build([], path, D(2026, 10, 28), METHOD, closes_for=lambda *a: {})
    r = tr["current"][0]
    assert r["ret_pct"] is None and r["nasdaq_pct"] is None and r["entry"] is None, r
    print("PASS: a failed price fetch is a missing return (shown '—'), never 0%")


if __name__ == "__main__":
    import logging
    logging.disable(logging.CRITICAL)
    test_episodes_exits_benchmark_split_and_old_method()
    test_pre_fix_morning_entries_use_the_previous_session()
    test_missing_closes_are_missing_not_zero()
    print("\nAll track-record tests passed.")

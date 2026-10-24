"""
test_methodology_change.py — When a change alters what the scores mean
(METHOD_VERSION bumped), the first session under it must not read as a
market move (Ray, 2026-10-24):

  - its scores_history.json entry is flagged methodology_change (+ note);
  - the page compares it with no previous session: no "was Red" badges,
    score deltas or rating arrows;
  - the heat trail marks that day;
  - a dated methodology note shows for METHOD_NOTE_DAYS calendar days.

No pytest in this environment — plain script. Run: python test_methodology_change.py
"""

import os
import re
import json
import datetime
import tempfile

import main
import render
from test_regime_label import _ticker

MACRO = {"vix": 17.0, "yield_10y_change": 10.0, "nasdaq_vs_spx_20d": 0.0, "data_missing": []}
D = datetime.date


def _run(session, sessions):
    md = {lid: [_ticker(lid[:4].upper())] for lid in render.LAYER_NAMES}
    scored = main.stage_score(md, MACRO, sessions)
    render.generate_dashboard(scored, "Stub.", MACRO, session, md, [])
    page = open("index.html", encoding="utf-8").read()
    hist = json.load(open("scores_history.json"))
    return page, {e["date"]: e for e in hist}


def test_first_session_under_a_new_method_is_flagged_not_a_signal():
    cwd = os.getcwd()
    with tempfile.TemporaryDirectory() as tmp:
        os.chdir(tmp)
        try:
            # An old-method entry (no "method" key), Red memory, the session before.
            old = {lid: {"score": 30.0, "color": "Green", "ratings": {}} for lid in render.LAYER_NAMES}
            old["memory"] = {"score": 70.0, "color": "Red", "ratings": {}}
            json.dump([{"date": "2026-10-19", "scores": old}], open("scores_history.json", "w"))
            s1 = [D(2026, 10, d) for d in (13, 14, 15, 16, 19, 20)]

            page, hist = _run(s1[-1], s1)
            e = hist["2026-10-20"]
            assert e.get("method") == render.METHOD_VERSION and e.get("methodology_change") is True, e.keys()
            assert "methodology_note" in e
            assert "Methodology change, Oct 20:" in page, "dated note missing on the change day"
            assert "const HAS_YESTERDAY= false;" in page, "change day still compared with the old-method session"
            hist_js = json.loads(re.search(r"const HISTORY\s*= (\{.*?\});\n", page).group(1))
            day = next(d for d in hist_js["days"] if d["date"] == "2026-10-20")
            assert day["method_change"] is True, "heat trail doesn't mark the change day"
            print("PASS: change day — entry flagged, note shown, no previous-session comparison, heat trail marked")

            s2 = s1 + [D(2026, 10, 21)]
            page, hist = _run(s2[-1], s2)
            assert "methodology_change" not in hist["2026-10-21"], "a later session was flagged too"
            assert "Methodology change, Oct 20:" in page, "note gone the day after"
            assert "const HAS_YESTERDAY= true;" in page, "comparison not restored after the change day"
            print("PASS: next session — not flagged, comparison back, note still shown")

            s3 = s2 + [D(2026, 10, 29)]
            page, _ = _run(s3[-1], s3)
            assert "Methodology change" not in page, "note still shown 9 days later"
            print("PASS: note gone after 7 days")
        finally:
            os.chdir(cwd)


if __name__ == "__main__":
    import logging
    logging.disable(logging.CRITICAL)
    test_first_session_under_a_new_method_is_flagged_not_a_signal()
    print("\nAll methodology-change tests passed.")

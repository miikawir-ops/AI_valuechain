"""
test_methodology_change.py — When score_engine.METHOD_VERSION changes, the
switch must not read as a market move (Ray, 2026-10-24):

  - a session already stored under the old method is kept as published,
    never overwritten (a Saturday run re-scores Friday's close);
  - the first entry written under the new method is the next session, and
    it carries methodology_change: true (+ note) for backtests;
  - no comparison across methods: no "was Red" badges, score deltas or
    rating arrows, and the card says "no comparison: method changed";
  - old-method entries are not colour-confirmation history;
  - the heat trail marks the last old-method day, still in its old colour;
  - a dated note shows for METHOD_NOTE_DAYS days after that day;
  - no weekend column appears.

No pytest in this environment — plain script. Run: python test_methodology_change.py
"""

import os
import re
import json
import datetime
import tempfile

import main
import render
from score_engine import METHOD_VERSION
from test_regime_label import _ticker

MACRO = {"vix": 17.0, "yield_10y_change": 10.0, "nasdaq_vs_spx_20d": 0.0, "data_missing": []}
D = datetime.date
SESSIONS = [D(2026, 10, d) for d in (13, 14, 15, 16, 19, 20, 21, 22, 23, 26, 27, 28, 29)]


def _run(session):
    sessions = [s for s in SESSIONS if s <= session]
    md = {lid: [_ticker(lid[:4].upper())] for lid in render.LAYER_NAMES}
    scored = main.stage_score(md, MACRO, sessions)
    render.generate_dashboard(scored, "Stub.", MACRO, session, md, [])
    page = open("index.html", encoding="utf-8").read()
    hist = {e["date"]: e for e in json.load(open("scores_history.json"))}
    heat = json.loads(re.search(r"const HISTORY\s*= (\{.*?\});\n", page).group(1))
    return scored, page, hist, {d["date"]: d for d in heat["days"]}


def test_method_change_keeps_published_record_and_compares_nothing_across():
    cwd = os.getcwd()
    with tempfile.TemporaryDirectory() as tmp:
        os.chdir(tmp)
        try:
            # Old-method history (no "method" key): memory Red through Fri Oct 23.
            old = lambda c: {lid: {"score": 70.0 if lid == "memory" else 30.0,
                                   "color": c if lid == "memory" else "Green", "ratings": {}}
                             for lid in render.LAYER_NAMES}
            json.dump([{"date": f"2026-10-{d}", "scores": old("Red")} for d in (21, 22, 23)],
                      open("scores_history.json", "w"))

            # Saturday deploy: re-scores Friday Oct 23's close.
            scored, page, hist, heat = _run(D(2026, 10, 23))
            assert "method" not in hist["2026-10-23"] and hist["2026-10-23"]["scores"]["memory"]["color"] == "Red", \
                "the published old-method Oct 23 record was overwritten"
            assert "const METHOD_BREAK = true;" in page and "const HAS_YESTERDAY= false;" in page
            assert "no comparison: method changed" in page
            assert "Methodology change after the Oct 23 close:" in page
            assert heat["2026-10-23"]["method_boundary"] and heat["2026-10-23"]["layers"]["memory"] == "Red"
            assert not heat["2026-10-24"]["method_boundary"]
            assert all(d.weekday() < 5 for d in map(D.fromisoformat, hist)), f"weekend entry: {sorted(hist)}"
            print("PASS: Saturday re-score — Oct 23 kept Red as published, no comparison, note, marker on Oct 23")

            # Next session: first new-method entry, flagged; still no cross-method comparison.
            scored, page, hist, heat = _run(D(2026, 10, 26))
            e = hist["2026-10-26"]
            assert e.get("method") == METHOD_VERSION and e.get("methodology_change") is True and "methodology_note" in e
            assert "const METHOD_BREAK = true;" in page, "Oct 26 compared with old-method Oct 23"
            assert heat["2026-10-23"]["method_boundary"], "marker moved off the last old-method day"
            assert "insufficient history" in scored["memory"]["best"]["status"], \
                f"old-method entries used as confirmation history: {scored['memory']['best']['status']}"
            print("PASS: next session — first new-method entry flagged; old entries aren't confirmation history")

            # The session after: like-for-like comparison is back, no new flag.
            _, page, hist, _ = _run(D(2026, 10, 27))
            assert "methodology_change" not in hist["2026-10-27"]
            assert "const METHOD_BREAK = false;" in page and "const HAS_YESTERDAY= true;" in page
            assert "Methodology change after the Oct 23 close:" in page
            print("PASS: following session — comparison back, note still shown")

            _, page, _, _ = _run(D(2026, 10, 29))   # 6 days after Oct 23: inside the 7-day window
            assert "Methodology change after the Oct 23 close:" in page
            later = D(2026, 11, 2)
            SESSIONS.append(later)
            _, page, _, _ = _run(later)
            assert "Methodology change" not in page, "note still shown 10 days after the change"
            print("PASS: note shown through 7 days, gone after")
        finally:
            os.chdir(cwd)


if __name__ == "__main__":
    import logging
    logging.disable(logging.CRITICAL)
    test_method_change_keeps_published_record_and_compares_nothing_across()
    print("\nAll methodology-change tests passed.")

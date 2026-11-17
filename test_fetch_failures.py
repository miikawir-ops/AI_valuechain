"""
test_fetch_failures.py — a fetch failure is missing data or a failed run,
never an empty or silently shrunken page (Ray, 2026-11-17; CLAUDE.md:
missing is not zero, fail loudly when the page would be misleading).

  A. The company fetch stage raising fails the run (no commit, no deploy),
     and an empty scoring result is never saved over a stored session.
     Before: the run exited 0 and published a page with no layers under
     "Data: close of <session>", overwriting that session's stored entry.

No pytest in this environment — plain script. Run: python test_fetch_failures.py
"""

import json
import datetime
import tempfile
from pathlib import Path
from unittest.mock import patch

import main
import render

SESSION = datetime.date(2026, 11, 16)
STORED = [{"date": "2026-11-16", "method": "x", "time": "20:09", "scores": {"energy": {"score": 38.6, "color": "Green"}}}]


def _run(**patches):
    """run_full_pipeline with the calendar fixed and every later stage recorded."""
    with patch("fetch_market.fetch_trading_sessions", return_value=[datetime.date(2026, 11, 13), SESSION]), \
         patch("main.check_not_older_than_stored"), \
         patch("main.stage_render") as rend, patch("main.save_scores_detail") as detail, \
         patch("main.stage_analyze", return_value="(test)"), \
         patch("next_nvidia.load_cached_radar", return_value=[]), \
         patch("capex.fetch", return_value={"available": False}), \
         patch("track_record.build", return_value=None), patch("changes.compute", return_value=None):
        ctx = [patch(k, **v) for k, v in patches.items()]
        for c in ctx:
            c.start()
        try:
            main.run_full_pipeline()
            return None, rend, detail
        except Exception as e:
            return e, rend, detail
        finally:
            for c in ctx:
                c.stop()


def test_a_company_fetch_stage_failure_fails_the_run():
    err, rend, detail = _run(**{"fetch_market.run_pipeline": {"side_effect": ConnectionError("simulated outage")}})
    assert err is not None, "the run completed (old code: exit 0, an empty page under the session's date)"
    assert "simulated outage" in str(err), err
    assert not rend.called and not detail.called, "nothing rendered or logged after a failed fetch"
    print("PASS: A — a failing company fetch stage fails the run; nothing rendered, logged or deployed")


def test_a_empty_scoring_result_fails_the_run():
    err, rend, _ = _run(**{"fetch_market.run_pipeline": {"return_value": {}},
                           "fetch_market.fetch_macro": {"return_value": dict(main.MACRO_DEFAULTS)}})
    assert err is not None and "no layer" in str(err).lower(), err
    assert not rend.called
    print("PASS: A — an empty scoring result fails the run before anything is written")


def test_a_empty_result_is_never_saved_over_a_stored_session():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "scores_history.json"
        path.write_text(json.dumps(STORED))
        with patch("render.SCORES_HISTORY_FILE", str(path)):
            try:
                render.save_scores_history({}, SESSION)
                raise AssertionError("an empty result was saved")
            except ValueError as e:
                assert "empty" in str(e).lower(), e
        assert json.loads(path.read_text()) == STORED, "the stored session entry was changed"
    print("PASS: A — an empty scoring result is refused; the stored session entry is unchanged")


if __name__ == "__main__":
    import logging
    logging.disable(logging.CRITICAL)
    test_a_company_fetch_stage_failure_fails_the_run()
    test_a_empty_scoring_result_fails_the_run()
    test_a_empty_result_is_never_saved_over_a_stored_session()
    print("\nAll fetch-failure tests passed.")

"""
test_session_dating.py — Verifies history/score dating uses the last
completed US trading session (from ^GSPC's own calendar), not the UTC run
date, and that a session missing a layer's score is skipped rather than
read as 0.

Root cause this guards against: a run at e.g. 2026-10-10 00:08 UTC reflects
the 2026-10-09 session's close, not "today" in UTC-run-date terms. Dating
by run date produces weekend entries, duplicated/lost sessions, and (via
_load_recent_layer_scores' old `.get("score", 0)` default) can silently
treat a missing session as a real bearish 0 reading.

No pytest in this environment — plain script, matching test_gemini.py's
convention. Run: python test_session_dating.py
"""

import json
import datetime
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd

import fetch_market
import render
import main


def test_get_last_session_date_raises_on_empty_fetch():
    """^GSPC fetch failing must fail the run, never fall back silently —
    every date-keyed output depends on this being right."""
    mock_ticker = MagicMock()
    mock_ticker.history.return_value = pd.DataFrame()
    with patch("fetch_market.yf.Ticker", return_value=mock_ticker):
        try:
            fetch_market.get_last_session_date()
            raise AssertionError("should have raised on empty ^GSPC history")
        except RuntimeError as e:
            assert "GSPC" in str(e)
    print("PASS: get_last_session_date raises on empty fetch")


def test_get_last_session_date_returns_last_bar_date():
    dates = pd.date_range(end="2026-10-09", periods=5, freq="D")
    hist = pd.DataFrame({"Close": [1.0] * 5}, index=dates)
    mock_ticker = MagicMock()
    mock_ticker.history.return_value = hist
    with patch("fetch_market.yf.Ticker", return_value=mock_ticker):
        result = fetch_market.get_last_session_date()
    assert result == datetime.date(2026, 10, 9), f"expected 2026-10-09, got {result}"
    print("PASS: get_last_session_date returns the last bar's date")


def _fake_scored_data():
    """Minimal scored_data shaped like stage_score's real output."""
    return {
        "energy": {
            "best": {"score": 42.0, "color": "Green"},
            "all_tickers": [{"ticker": "CEG", "score": 42.0, "fund_delta": 0.0,
                              "color": "Green", "is_hype": False}],
        }
    }


def test_history_keyed_by_session_not_run_date():
    """A run that happens on UTC-calendar-day 2026-10-10 but reflects the
    2026-10-09 session must write a "2026-10-09" entry, not "2026-10-10" —
    the exact mismatch that produced weekend/duplicate history entries."""
    with tempfile.TemporaryDirectory() as tmp:
        hist_path = str(Path(tmp) / "scores_history.json")
        session_date = datetime.date(2026, 10, 9)  # the session, not "today"
        with patch("render.SCORES_HISTORY_FILE", hist_path):
            render.save_scores_history(_fake_scored_data(), session_date)
            written = json.loads(Path(hist_path).read_text())

    assert len(written) == 1
    assert written[0]["date"] == "2026-10-09", (
        f"expected the entry dated by the session (2026-10-09), "
        f"got {written[0]['date']!r} — looks like it used the UTC run date instead"
    )
    print("PASS: history entry is keyed by session date, not UTC run date")


def test_rerun_same_session_overwrites_not_duplicates():
    """The last run per session overwrites — reprocessing the same session
    (e.g. a manual re-run) must not create a second column for it."""
    with tempfile.TemporaryDirectory() as tmp:
        hist_path = str(Path(tmp) / "scores_history.json")
        session_date = datetime.date(2026, 10, 9)
        with patch("render.SCORES_HISTORY_FILE", hist_path):
            render.save_scores_history(_fake_scored_data(), session_date)
            render.save_scores_history(_fake_scored_data(), session_date)  # rerun
            written = json.loads(Path(hist_path).read_text())

    assert len(written) == 1, f"expected 1 entry for the same session after a rerun, got {len(written)}"
    print("PASS: rerunning the same session overwrites, does not duplicate")


def test_missing_session_score_is_skipped_not_zero():
    """A history entry that exists but has no recorded score for this
    layer (e.g. a partial-failure run) must be skipped and named in the
    log — never silently returned as a real 0.0 reading."""
    with tempfile.TemporaryDirectory() as tmp:
        hist_path = str(Path(tmp) / "scores_history.json")
        history = [
            {"date": "2026-10-07", "scores": {"energy": {"score": 40.0, "color": "Green"}}},
            {"date": "2026-10-08", "scores": {}},  # energy missing this session
            {"date": "2026-10-09", "scores": {"energy": {"score": 44.0, "color": "Green"}}},
        ]
        Path(hist_path).write_text(json.dumps(history))
        with patch("main.SCORES_HISTORY_FILE", hist_path):
            recent = main._load_recent_layer_scores(
                "energy", datetime.date(2026, 10, 10), days=3
            )

    assert 0.0 not in recent, f"a missing session leaked in as a 0.0 reading: {recent}"
    assert recent == [40.0, 44.0], f"expected the two real scores only, got {recent}"
    print("PASS: a session missing this layer's score is skipped, not read as 0")


if __name__ == "__main__":
    test_get_last_session_date_raises_on_empty_fetch()
    test_get_last_session_date_returns_last_bar_date()
    test_history_keyed_by_session_not_run_date()
    test_rerun_same_session_overwrites_not_duplicates()
    test_missing_session_score_is_skipped_not_zero()
    print("\nAll session-dating tests passed.")

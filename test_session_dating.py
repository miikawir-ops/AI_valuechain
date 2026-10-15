"""
test_session_dating.py — Verifies history/score dating uses the last
completed US trading session (from ^GSPC's own calendar), not the UTC run
date; that a bar for a session still in progress is never scored as a
close; that colour confirmation takes its expected sessions from the
calendar (a missing session is named, never read as 0 or silently replaced
by an older entry); and that the card delta is labelled with the session
it's compared against.

Root causes this guards against: a run at e.g. 2026-10-10 00:08 UTC
reflects the 2026-10-09 session's close, not "today" in UTC-run-date terms;
a run at 2026-10-12 14:13 UTC (run #273) happened during US trading hours
and published "close of Monday" mid-session; legacy entries keyed by run
date (e.g. Saturday 2026-10-10) are still in the history file.

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

UTC = datetime.timezone.utc
MID_SESSION_RUN = datetime.datetime(2026, 10, 12, 14, 13, tzinfo=UTC)  # Mon 10:13 ET, market open (run #273)
AFTER_CLOSE_RUN = datetime.datetime(2026, 10, 13, 2, 8, tzinfo=UTC)    # Mon 22:08 ET, session closed (run #274)


def _gspc(end: str, periods: int = 10) -> MagicMock:
    dates = pd.bdate_range(end=end, periods=periods)
    m = MagicMock()
    m.history.return_value = pd.DataFrame({"Close": [5000.0 + i for i in range(periods)]}, index=dates)
    return m


def test_trading_sessions_raises_on_empty_fetch():
    """^GSPC fetch failing must fail the run, never fall back silently —
    every date-keyed output depends on this being right."""
    mock_ticker = MagicMock()
    mock_ticker.history.return_value = pd.DataFrame()
    with patch("fetch_market.yf.Ticker", return_value=mock_ticker):
        try:
            fetch_market.fetch_trading_sessions(MID_SESSION_RUN)
            raise AssertionError("should have raised on empty ^GSPC history")
        except RuntimeError as e:
            assert "GSPC" in str(e)
    print("PASS: fetch_trading_sessions raises on empty fetch")


def test_mid_session_run_uses_previous_session():
    """A run during US trading hours must not treat today's in-progress bar
    as a completed session — run #273 published "close of Monday" at
    10:13 ET Monday. The last completed session at that moment is Friday."""
    with patch("fetch_market.yf.Ticker", return_value=_gspc("2026-10-12")):
        sessions = fetch_market.fetch_trading_sessions(MID_SESSION_RUN)
    assert sessions[-1] == datetime.date(2026, 10, 9), (
        f"expected Fri 2026-10-09 (Monday still in progress), got {sessions[-1]}"
    )
    print("PASS: a mid-session run scores the last completed session, not today's partial bar")


def test_after_close_run_includes_todays_session():
    """Control for the test above: once the session has closed (16:00 ET),
    today's bar is a real close and must be kept."""
    with patch("fetch_market.yf.Ticker", return_value=_gspc("2026-10-12")):
        sessions = fetch_market.fetch_trading_sessions(AFTER_CLOSE_RUN)
    assert sessions[-1] == datetime.date(2026, 10, 12), f"expected Mon 2026-10-12, got {sessions[-1]}"
    print("PASS: after the close, today's session counts")


def test_stale_calendar_fails_the_run():
    with patch("fetch_market.yf.Ticker", return_value=_gspc("2026-09-25")):
        try:
            fetch_market.fetch_trading_sessions(MID_SESSION_RUN)
            raise AssertionError("should have raised on a 17-day-old calendar")
        except RuntimeError as e:
            assert "stale" in str(e)
    print("PASS: a stale session calendar fails the run")


def test_drop_unfinished_bar_drops_trailing_nan_whatever_its_date():
    """The 00:00 UTC empty bar: a trailing NaN close is dropped even when
    it isn't dated today."""
    dates = pd.bdate_range(end="2026-10-09", periods=4)
    hist = pd.DataFrame({"Close": [1.0, 2.0, 3.0, float("nan")]}, index=dates)
    out = fetch_market.drop_unfinished_bar(hist, AFTER_CLOSE_RUN, "TEST")
    assert len(out) == 3 and out["Close"].iloc[-1] == 3.0
    print("PASS: a trailing NaN bar is dropped whatever its date")


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


def test_confirmation_window_comes_from_the_calendar():
    """Colour confirmation's expected sessions come from the trading
    calendar, not from whatever entries are stored: a session with no
    stored score is named and left out (never 0, never silently replaced
    by an older entry), and a legacy weekend-dated entry never counts."""
    sessions = list(pd.bdate_range("2026-10-05", "2026-10-14").date)   # Mon 10-05 .. Wed 10-14
    history = [
        {"date": "2026-10-08", "scores": {"energy": {"score": 40.0, "color": "Green"}}},
        {"date": "2026-10-09", "scores": {}},                                             # layer missing
        {"date": "2026-10-10", "scores": {"energy": {"score": 99.0, "color": "Red"}}},     # legacy Saturday entry
        {"date": "2026-10-12", "scores": {"energy": {"score": 44.0, "color": "Green"}}},
        # 2026-10-13: no entry at all (e.g. a failed run)
    ]
    with tempfile.TemporaryDirectory() as tmp:
        hist_path = str(Path(tmp) / "scores_history.json")
        Path(hist_path).write_text(json.dumps(history))
        with patch("main.SCORES_HISTORY_FILE", hist_path):
            stored, missing = main._confirmation_window("energy", sessions, datetime.date(2026, 10, 14))

    scores = [score for _, score, _ in stored]
    assert 99.0 not in scores, f"the legacy Saturday entry was counted as a session: {scores}"
    assert 0.0 not in scores, f"a missing session leaked in as a 0.0 reading: {scores}"
    assert scores == [44.0], f"expected only the stored in-window score, got {scores}"
    assert missing == [datetime.date(2026, 10, 9), datetime.date(2026, 10, 13)], (
        f"expected 10-09 (no layer score) and 10-13 (no entry) named as missing, got {missing}"
    )
    print("PASS: confirmation window comes from the calendar; missing sessions named, weekend entry ignored")


if __name__ == "__main__":
    test_trading_sessions_raises_on_empty_fetch()
    test_mid_session_run_uses_previous_session()
    test_after_close_run_includes_todays_session()
    test_stale_calendar_fails_the_run()
    test_drop_unfinished_bar_drops_trailing_nan_whatever_its_date()
    test_history_keyed_by_session_not_run_date()
    test_rerun_same_session_overwrites_not_duplicates()
    test_confirmation_window_comes_from_the_calendar()
    print("\nAll session-dating tests passed.")

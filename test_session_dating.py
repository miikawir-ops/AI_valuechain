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
from score_engine import METHOD_VERSION as M

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


def test_empty_row_marks_a_session_and_series_are_cut_there():
    """An empty (NaN close) row still marks a session that happened; a series
    is cut at the run's session, and without a usable bar for it the series
    is missing, never the bar before under the new date."""
    dates = pd.bdate_range(end="2026-10-09", periods=4)
    hist = pd.DataFrame({"Close": [1.0, 2.0, 3.0, float("nan")]}, index=dates)
    assert fetch_market.completed_dates(hist.index, AFTER_CLOSE_RUN)[-1] == datetime.date(2026, 10, 9)
    kept, ok = fetch_market.session_bars(hist, datetime.date(2026, 10, 9), "TEST")
    assert not ok and kept["Close"].iloc[-1] == 3.0, "an empty session bar is missing, not the day before"
    kept, ok = fetch_market.session_bars(hist, datetime.date(2026, 10, 8), "TEST")
    assert ok and len(kept) == 3, "bars after the session are cut"
    mid = pd.DataFrame({"Close": [1.0, float("nan"), 3.0]}, index=pd.bdate_range(end="2026-10-09", periods=3))
    kept, ok = fetch_market.session_bars(mid, datetime.date(2026, 10, 9))
    assert ok and len(kept) == 3, "a NaN in the middle is left for the value checks"
    print("PASS: an empty row still marks its session; series cut at the session; no bar for it = missing")


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
        {"date": "2026-10-08", "method": M, "scores": {"energy": {"score": 40.0, "color": "Green"}}},
        {"date": "2026-10-09", "method": M, "scores": {}},                                             # layer missing
        {"date": "2026-10-10", "method": M, "scores": {"energy": {"score": 99.0, "color": "Red"}}},     # legacy Saturday entry
        {"date": "2026-10-12", "method": M, "scores": {"energy": {"score": 44.0, "color": "Green"}}},
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


def test_delta_is_labelled_with_the_previous_session():
    """The card delta compares against the previous stored session, so its
    label must name that session — not the current one ("close of Mon Oct
    12 ... vs Mon Oct 12" was on the live page)."""
    history = [
        {"date": "2026-10-09", "method": M, "scores": {"energy": {"score": 40.0, "color": "Green"}}},
        {"date": "2026-10-12", "method": M, "scores": {"energy": {"score": 44.0, "color": "Green"}}},
    ]
    with tempfile.TemporaryDirectory() as tmp:
        hist_path = str(Path(tmp) / "scores_history.json")
        Path(hist_path).write_text(json.dumps(history))
        with patch("render.SCORES_HISTORY_FILE", hist_path):
            prev_date, prev_scores = render.get_yesterday_scores({}, datetime.date(2026, 10, 12))

    assert prev_date == "2026-10-09", f"expected the previous session 2026-10-09, got {prev_date!r}"
    assert prev_scores["energy"]["score"] == 40.0
    print("PASS: the delta is labelled with the previous session, not the current one")


def test_vix_is_the_last_completed_close_not_the_live_quote():
    """VIX comes from daily bars cut at the run's session, like every
    other daily series — not from .info's live quote (old code read
    regularMarketPrice, intraday during US trading hours)."""
    def bars(closes):
        return pd.DataFrame({"Close": closes}, index=pd.bdate_range(end="2026-10-09", periods=len(closes)))
    vix = MagicMock()
    vix.info = {"regularMarketPrice": 99.0, "previousClose": 98.0}   # live quote the old code used
    vix.history.return_value = bars([16.0, 16.5, 17.0, 17.25, float("nan")])  # trailing empty bar
    flat = MagicMock()
    flat.history.return_value = bars([100.0] * 30)
    tickers = {"^VIX": vix, "^TNX": flat, "^IXIC": flat, "^GSPC": flat}
    with patch("fetch_market.yf.Ticker", side_effect=lambda sym: tickers[sym]):
        macro = fetch_market.fetch_macro(session=datetime.date(2026, 10, 8))   # the empty 10-09 row is after it
    assert macro["vix"] == 17.25, f"expected the last completed close 17.25, got {macro['vix']}"
    print("PASS: VIX is the last completed session's close, not the live quote")


# ── Run-date fix (ported from RayDar-DataCenter e40e3e5, ledger R101) ────────
# 2026-11-13 01:17 UTC: Yahoo had emptied the 11-12 bar for every ticker, the
# empty bars were dropped, and 11-11 prices were published under 11-12.

def _at(s):
    return datetime.datetime.fromisoformat(s).replace(tzinfo=UTC)


def _cal(dates, empty=()):
    """^GSPC daily bars on these dates; `empty` rows have a NaN close."""
    idx = pd.DatetimeIndex([pd.Timestamp(d).tz_localize("America/New_York") for d in dates])
    m = MagicMock()
    m.history.return_value = pd.DataFrame(
        {"Close": [float("nan") if d in empty else 5000.0 + i for i, d in enumerate(dates)]}, index=idx)
    return m


def _price_bars(n, end, empty=()):
    """n daily bars ending `end`, then rows dated `empty` with a NaN close and volume."""
    dates = list(pd.bdate_range(end=end, periods=n)) + [pd.Timestamp(d) for d in empty]
    closes = [100.0 + i * 0.5 for i in range(n)] + [float("nan")] * len(empty)
    vols = [1_000_000.0] * n + [float("nan")] * len(empty)
    return pd.DataFrame({"Close": closes, "Volume": vols}, index=pd.DatetimeIndex(dates))


def test_session_for_each_start_time():
    """The session a run's data is keyed to, for start times before the close,
    just after it, just after midnight UTC (the empty-bar hour), Saturday,
    Monday morning and a holiday. Week of Mon 9 Nov 2026 (EST, ET = UTC-5)."""
    week = ["2026-11-03", "2026-11-04", "2026-11-05", "2026-11-06", "2026-11-09", "2026-11-10"]
    cases = [
        ("before the close: Tue 19:10 UTC (14:10 ET)", _cal(week), "2026-11-10T19:10", "2026-11-09"),
        ("the 20:30 UTC cron in winter is 15:30 ET: before the close", _cal(week), "2026-11-10T20:30", "2026-11-09"),
        ("just after the close: Tue 21:05 UTC (16:05 ET)", _cal(week), "2026-11-10T21:05", "2026-11-10"),
        ("the empty-bar hour: Wed 01:17 UTC, Tuesday's ^GSPC bar emptied too",
         _cal(week, empty=("2026-11-10",)), "2026-11-11T01:17", "2026-11-10"),
        ("the 06:00 UTC cron: Wed 01:00 ET", _cal(week), "2026-11-11T06:00", "2026-11-10"),
        ("Saturday 15:00 UTC", _cal(["2026-11-11", "2026-11-12", "2026-11-13"]), "2026-11-14T15:00", "2026-11-13"),
        ("Saturday 00:57 UTC, Friday's bar empty",
         _cal(["2026-11-11", "2026-11-12", "2026-11-13"], empty=("2026-11-13",)), "2026-11-14T00:57", "2026-11-13"),
        ("Monday 13:00 UTC (08:00 ET), an empty pre-market Monday row",
         _cal(["2026-11-12", "2026-11-13", "2026-11-16"], empty=("2026-11-16",)), "2026-11-16T13:00", "2026-11-13"),
        ("Thanksgiving Thu 26 Nov 22:00 UTC: no Thursday row", _cal(["2026-11-23", "2026-11-24", "2026-11-25"]),
         "2026-11-26T22:00", "2026-11-25"),
        ("summer time: Mon 12 Oct 20:05 UTC is 16:05 EDT", _cal(["2026-10-08", "2026-10-09", "2026-10-12"]),
         "2026-10-12T20:05", "2026-10-12"),
    ]
    for label, cal, t, want in cases:
        with patch("fetch_market.yf.Ticker", return_value=cal):
            got = fetch_market.fetch_trading_sessions(_at(t))[-1]
        assert got == datetime.date.fromisoformat(want), f"{label}: got {got}, want {want}"
    print(f"PASS: the session for {len(cases)} start times, incl. ^GSPC's own bar emptied at the empty-bar hour")


def test_gspc_row_absent_keys_the_run_a_session_behind_never_misdated():
    """If Yahoo has no ^GSPC row at all yet for the session that just closed
    (not merely an empty one), the run's session is the one before, and every
    series is cut there too: a session behind, never one session's prices
    under another's date. A run never writes a session older than the newest
    stored one."""
    with patch("fetch_market.yf.Ticker", return_value=_cal(["2026-11-06", "2026-11-09"])):
        session = fetch_market.fetch_trading_sessions(_at("2026-11-11T01:17"))[-1]
    assert session == datetime.date(2026, 11, 9), session
    hist = _price_bars(130, end="2026-11-10")            # a company that already has Tuesday's bar
    kept, ok = fetch_market.session_bars(hist, session)
    assert ok and kept.index[-1].date() == session, "Tuesday's bar is cut: the run is Monday's, consistently"
    with patch("render.load_scores_history", return_value=[{"date": "2026-11-10"}]):
        try:
            main.check_not_older_than_stored(session)
            raise AssertionError("a session older than the newest stored one was allowed")
        except RuntimeError as e:
            assert "older than the newest stored" in str(e)
    with patch("render.load_scores_history", return_value=[{"date": "2026-11-09"}]):
        main.check_not_older_than_stored(session)   # the same session again (a re-run) is fine
    print("PASS: no ^GSPC row yet -> the session before, every series cut there; older-than-stored refused")


def test_company_without_the_session_bar_is_missing_not_the_day_before():
    from test_nan_handling import _mock_ticker
    bad = _price_bars(130, end="2026-11-11", empty=("2026-11-12",))
    with patch("fetch_market.yf.Ticker", return_value=_mock_ticker(bad)):
        r = fetch_market.fetch_ticker_data("TST", [], ["x"], ["TST"], session=datetime.date(2026, 11, 12))
    for k in ("price", "price_act", "price_30d_return", "price_momentum", "vol_spike"):
        assert r[k] is None and k in r["data_missing"], (k, r[k], r["data_missing"])
    assert r["price_history"] == [] and r["data_date"] is None and r["_session_bar"] is False
    good = _price_bars(130, end="2026-11-12")
    with patch("fetch_market.yf.Ticker", return_value=_mock_ticker(good)):
        r = fetch_market.fetch_ticker_data("TST", [], ["x"], ["TST"], session=datetime.date(2026, 11, 11))
    assert r["data_date"] == "2026-11-11" and r["price"] == round(good["Close"].iloc[-2], 2), "a later bar is cut"
    print("PASS: a company without the session's bar has every price value missing; a later bar is cut")


def test_macro_and_nasdaq_benchmark_are_cut_at_the_session():
    sess = datetime.date(2026, 11, 12)
    stubs = {"^VIX": _price_bars(5, end="2026-11-12", empty=("2026-11-13",)),       # next day's empty row: cut
             "^TNX": _price_bars(35, end="2026-11-11", empty=("2026-11-12",)),      # session bar empty: missing
             "^GSPC": _price_bars(45, end="2026-11-12"),
             "^IXIC": _price_bars(45, end="2026-11-13")}                            # a later bar: cut

    def ticker(sym):
        m = MagicMock()
        m.history.return_value = stubs[sym]
        return m
    with patch("fetch_market.yf.Ticker", side_effect=ticker):
        m = fetch_market.fetch_macro(session=sess)
    assert m["vix"] == round(float(stubs["^VIX"]["Close"].iloc[-2]), 2), m["vix"]
    assert m["yield_10y_change"] is None and "yield_10y_change" in m["data_missing"], m
    nq = stubs["^IXIC"]["Close"][:-1]
    assert m["nasdaq_30d_return"] == round(float(nq.iloc[-1] / nq.iloc[-21] - 1), 4), "divergence benchmark cut at the session"
    assert m["nasdaq_vs_spx_20d"] is not None
    stubs["^IXIC"] = _price_bars(45, end="2026-11-11", empty=("2026-11-12",))
    with patch("fetch_market.yf.Ticker", side_effect=ticker):
        m = fetch_market.fetch_macro(session=sess)
    assert m["nasdaq_30d_return"] is None and m["nasdaq_vs_spx_20d"] is None, "no NASDAQ bar for the session: missing"
    print("PASS: VIX, 10Y, NASDAQ vs S&P and the NASDAQ 30d benchmark are cut at the session or missing")


def test_radar_and_track_record_are_cut_at_the_session():
    import next_nvidia
    import track_record
    from test_nan_handling import _mock_ticker
    sess = datetime.date(2026, 11, 12)
    with patch("next_nvidia.yf.Ticker", return_value=_mock_ticker(_price_bars(130, end="2026-11-11", empty=("2026-11-12",)))):
        r = next_nvidia.fetch_multi_quarter_data("RAD", sess)
    assert r["price"] is None and r["ret_1mo"] is None and r["price_history"] == [], r
    good = _price_bars(130, end="2026-11-13")
    with patch("next_nvidia.yf.Ticker", return_value=_mock_ticker(good)):
        r = next_nvidia.fetch_multi_quarter_data("RAD", sess)
    c = good["Close"][:-1]
    assert r["price"] == round(float(c.iloc[-1]), 2) and r["ret_1mo"] == round((c.iloc[-1] / c.iloc[-21] - 1) * 100, 1)
    with tempfile.TemporaryDirectory() as tmp:
        cache = str(Path(tmp) / "radar.json")
        Path(cache).write_text(json.dumps({"date": "2026-11-13", "session": "2026-11-11", "all": [{"ticker": "OLD"}]}))
        with patch("next_nvidia.RADAR_CACHE_FILE", cache):
            assert next_nvidia.load_cached_radar(sess) == [], "a cache from another session is not reused"
            assert next_nvidia.load_cached_radar(datetime.date(2026, 11, 11)) == [{"ticker": "OLD"}]
    with patch("yfinance.Ticker", return_value=_mock_ticker(_price_bars(30, end="2026-11-11", empty=("2026-11-12",)))):
        closes = track_record._closes("TST", datetime.date(2026, 10, 20), sess)
    assert sess not in closes and max(closes) == datetime.date(2026, 11, 11), "an empty session bar is missing"
    print("PASS: radar prices, 1-month return and sparkline cut at the session; cache keyed by session; track-record closes cut")


def _pipeline(empty_for, session=datetime.date(2026, 11, 12)):
    from test_nan_handling import _mock_ticker
    good = _price_bars(130, end="2026-11-12")
    bad = _price_bars(130, end="2026-11-11", empty=("2026-11-12",))
    with patch("fetch_market.fetch_all_headlines", return_value=([], set(), [])), \
         patch("fetch_market.apply_growth_history"), \
         patch("fetch_market.yf.Ticker", side_effect=lambda sym: _mock_ticker(bad if sym in empty_for else good)):
        return fetch_market.run_pipeline(session=session)


def test_most_companies_without_the_session_bar_fail_the_run():
    tickers = [t for layer in fetch_market.AI_CHAIN_LAYERS.values() for t in layer["tickers"]]
    try:
        _pipeline(set(tickers))
        raise AssertionError("the run went on with no session bars")
    except fetch_market.SessionDataError as e:
        assert f"{len(tickers)} of {len(tickers)} companies" in str(e), str(e)
    out = _pipeline({tickers[0]})
    one = [t for ts in out.values() for t in ts if t["ticker"] == tickers[0]][0]
    assert one["price"] is None and "_session_bar" not in one, "one company missing: its prices are missing, the run goes on"
    print("PASS: most companies without the session's bar fail the run; one company is just missing")


def test_push_run_at_the_empty_bar_hour_fails_without_deploying():
    """A push-triggered run at 01:17 UTC, Yahoo's empty-bar hour: the calendar
    says the session is the one that just closed, every company's bar for it
    is empty, so main fails before anything is scored, written or rendered.
    In the workflow a failed "Run agent" step skips the commit-back, the
    Pages artifact and the deploy job, so the live page stays on its last
    good build."""
    import yaml
    from test_nan_handling import _mock_ticker
    real_sessions = fetch_market.fetch_trading_sessions
    calendar = _cal(["2026-11-10", "2026-11-11", "2026-11-12"], empty=("2026-11-12",))
    bad = _price_bars(130, end="2026-11-11", empty=("2026-11-12",))
    with patch("fetch_market.yf.Ticker", side_effect=lambda sym: calendar if sym == "^GSPC" else _mock_ticker(bad)), \
         patch("fetch_market.fetch_trading_sessions", side_effect=lambda now=None: real_sessions(_at("2026-11-13T01:17"))), \
         patch("render.load_scores_history", return_value=[{"date": "2026-11-11"}]), \
         patch("fetch_market.fetch_all_headlines", return_value=([], set(), [])), \
         patch("fetch_market.apply_growth_history"), \
         patch("main.stage_score") as score, patch("main.stage_render") as rend, \
         patch("main.save_scores_detail") as detail:
        try:
            main.run_full_pipeline()
            raise AssertionError("the run went on at the empty-bar hour")
        except fetch_market.SessionDataError as e:
            assert "session 2026-11-12" in str(e), str(e)
        assert not score.called and not rend.called and not detail.called, "nothing scored, written or rendered"
    wf = yaml.safe_load(open(".github/workflows/daily_update.yml", encoding="utf-8"))
    steps = wf["jobs"]["update"]["steps"]
    run_at = next(i for i, s in enumerate(steps) if s.get("run") == "python main.py --now")
    assert [s.get("name") for s in steps[run_at + 1:]] == [
        "Commit and push dashboard", "Prepare Pages artifact", "Upload Pages artifact"]
    assert all("if" not in s and not s.get("continue-on-error") for s in steps), "a step runs after a failed pipeline"
    assert wf["jobs"]["deploy"]["needs"] == "update" and "if" not in wf["jobs"]["deploy"]
    print("PASS: a push-triggered run at the empty-bar hour fails before scoring; commit, artifact and deploy are skipped")


if __name__ == "__main__":
    test_trading_sessions_raises_on_empty_fetch()
    test_mid_session_run_uses_previous_session()
    test_after_close_run_includes_todays_session()
    test_stale_calendar_fails_the_run()
    test_empty_row_marks_a_session_and_series_are_cut_there()
    test_history_keyed_by_session_not_run_date()
    test_rerun_same_session_overwrites_not_duplicates()
    test_confirmation_window_comes_from_the_calendar()
    test_delta_is_labelled_with_the_previous_session()
    test_vix_is_the_last_completed_close_not_the_live_quote()
    test_session_for_each_start_time()
    test_gspc_row_absent_keys_the_run_a_session_behind_never_misdated()
    test_company_without_the_session_bar_is_missing_not_the_day_before()
    test_macro_and_nasdaq_benchmark_are_cut_at_the_session()
    test_radar_and_track_record_are_cut_at_the_session()
    test_most_companies_without_the_session_bar_fail_the_run()
    test_push_run_at_the_empty_bar_hour_fails_without_deploying()
    print("\nAll session-dating tests passed.")

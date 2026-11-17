"""
test_fetch_failures.py — a fetch failure is missing data or a failed run,
never an empty or silently shrunken page (Ray, 2026-11-17; CLAUDE.md:
missing is not zero, fail loudly when the page would be misleading).

  A. The company fetch stage raising fails the run (no commit, no deploy),
     and an empty scoring result is never saved over a stored session.
     Before: the run exited 0 and published a page with no layers under
     "Data: close of <session>", overwriting that session's stored entry.
  B. A macro fetch failure keeps the company data, every macro input
     missing and named. Before: the fetched company data was thrown away.
  C. A company whose fetch fails stays a named record (Data gaps,
     scores_detail), outside the layer score; more than half failing fails
     the run. Before: it vanished from its layer, and the layer was
     reweighted without a mark (no NVDA: Semicon 57.7 -> 59.1).
  D. A failed news feed is named in Data gaps; a company whose own feed
     failed has its news count missing (scored as before until the January
     review decides how); every feed failing fails the run. feedparser
     doesn't raise on a dead host or an HTTP 503 (bozo / status instead),
     so a feed with no entries and an error or non-200 status counts as
     failed too. Before: all feeds failing read as "no news" on every layer
     (-1.1 to -7.4 points, Energy 38.6 -> 32.0).

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


def test_b_macro_failure_keeps_the_company_data():
    import fetch_market
    market = {"energy": [{"ticker": "CEG", "price": 262.38}]}
    with patch("fetch_market.run_pipeline", return_value=market), \
         patch("fetch_market.fetch_macro", side_effect=ConnectionError("macro outage")):
        got_market, macro = main.stage_fetch(SESSION)
    assert got_market == market, "the company data was discarded"
    keys = ("vix", "yield_10y_change", "nasdaq_vs_spx_20d", "nasdaq_30d_return")
    assert all(macro[k] is None for k in keys) and sorted(macro["data_missing"]) == sorted(keys), macro
    macro["data_missing"].append("x")
    assert "x" not in main.MACRO_DEFAULTS["data_missing"], "the shared defaults were handed out, not a copy"
    print("PASS: B — a macro fetch failure keeps the company data; each macro input missing and named")


def _pipeline(fail):
    import fetch_market
    def fetch(ticker, *a, **k):
        if ticker in fail:
            return None
        return {"ticker": ticker, "price": 1.0, "price_30d_return": 0.01, "_session_bar": True, "data_missing": []}
    with patch("fetch_market.fetch_all_headlines", return_value=([], set(), [])), \
         patch("fetch_market.apply_growth_history"), \
         patch("fetch_market.fetch_ticker_data", side_effect=fetch):
        return fetch_market.run_pipeline(session=SESSION)


def test_c_failed_company_is_named_outside_the_layer_score():
    import fetch_market
    out = _pipeline({"NVDA"})
    assert "NVDA" not in [t["ticker"] for t in out["compute"]], "a failed company is not scored"
    assert fetch_market.FETCH_ISSUES["failed_companies"] == {"compute": ["NVDA"]}, fetch_market.FETCH_ISSUES
    gaps = render._gaps_html({}, {}, SESSION, fetch_market.FETCH_ISSUES)
    assert "NVDA: fetch failed this run — not in the Semicon layer score" in gaps, gaps
    rec = main.save_scores_detail({}, dict(main.MACRO_DEFAULTS), {}, SESSION, [], "", path=_tmp_detail(),
                                  fetch_issues=fetch_market.FETCH_ISSUES)
    assert rec["fetch_failures"]["failed_companies"] == {"compute": ["NVDA"]}, rec.get("fetch_failures")
    out = _pipeline(set())
    assert fetch_market.FETCH_ISSUES["failed_companies"] == {}, "issues are reset each run"
    print("PASS: C — a failed company is named in Data gaps and scores_detail, outside the layer score")


def test_c_most_companies_failing_fails_the_run():
    import fetch_market
    tickers = [t for layer in fetch_market.AI_CHAIN_LAYERS.values() for t in layer["tickers"]]
    try:
        _pipeline(set(tickers[: len(tickers) // 2 + 1]))
        raise AssertionError("the run went on with most companies failing")
    except fetch_market.FetchFailureError as e:
        assert f"{len(tickers) // 2 + 1} of {len(tickers)}" in str(e), e
    _pipeline(set(tickers[: len(tickers) // 2]))   # exactly half: the run goes on
    print("PASS: C — more than half the companies failing fails the run; half doesn't")


class _Feed(dict):
    def __init__(self, entries=(), bozo=False, status=200):
        super().__init__(status=status)
        self.entries, self.bozo = list(entries), bozo


def test_d_feed_failure_is_detected_without_an_exception():
    import fetch_market
    ok = _Feed([{"title": "Nvidia orders surge", "summary": ""}])
    dead = _Feed(bozo=True, status=None)          # what feedparser returns for a dead host
    down = _Feed(status=503)                      # ... and for an HTTP 503
    quiet = _Feed(status=200)                     # a working feed with no news: not a failure
    feeds = {"NVDA": ok, "AMD": dead, "AVGO": down, "MU": quiet}
    with patch("fetch_market.feedparser.parse", side_effect=lambda url: feeds[url.split("s=")[1].split("&")[0]]):
        heads, failed = fetch_market.fetch_ticker_headlines(list(feeds))
    assert failed == {"AMD", "AVGO"} and len(heads) == 1, (failed, heads)
    print("PASS: D — a dead host or HTTP 503 counts as a failed feed (feedparser doesn't raise); an empty 200 doesn't")


def test_d_failed_feeds_are_named_and_the_company_news_count_is_missing():
    import fetch_market
    from test_nan_handling import _mock_ticker
    import pandas as pd
    hist = pd.DataFrame({"Close": [100.0 + i for i in range(70)], "Volume": [1e6] * 70},
                        index=pd.bdate_range(end="2026-11-16", periods=70))
    with patch("fetch_market.yf.Ticker", return_value=_mock_ticker(hist)):
        r = fetch_market.fetch_ticker_data("CEG", [], ["power"], ["CEG"], session=SESSION, failed_ticker_feeds={"CEG"})
    assert r["news_velocity"] is None and "news_velocity" in r["data_missing"], (r["news_velocity"], r["data_missing"])
    from score_engine import ScoreEngine
    e = ScoreEngine({})
    assert e.calculate_constraints({"news_velocity": None, "capex_div": 0.4}, []) == \
        e.calculate_constraints({"news_velocity": 0.0, "capex_div": 0.4}, []), "scoring unchanged until January"
    issues = {"failed_companies": {}, "failed_ticker_feeds": ["CEG"], "failed_generic_feeds": ["https://www.cnbc.com/rss"]}
    gaps = render._gaps_html({}, {}, SESSION, issues)
    assert "CEG: news feed failed this run (news count missing)" in gaps, gaps
    assert "News feed failed this run: www.cnbc.com" in gaps, gaps
    print("PASS: D — a company's failed feed makes its news count missing (same score); failed feeds named in Data gaps")


def test_d_missing_news_count_renders():
    """A layer whose top company's news count is missing renders "News — hits"
    and its news-divergence check isn't evaluated (old code: int(None) raised)."""
    scored = {"energy": {"best": {"ticker": "CEG", "score": 40.0, "color": "Green", "news_velocity": None,
                                  "fund_delta": -0.1, "status": "x"},
                         "all_tickers": [{"ticker": "CEG", "score": 40.0, "color": "Green", "news_velocity": None,
                                          "fund_delta": -0.1, "market_cap": 1, "is_hype": False}]}}
    layers = json.loads(render._chain_js_data(scored, {}, {}, SESSION))
    assert layers[0]["news_vel"] is None and layers[0]["divergence"] is False, layers[0]
    assert 'l.news_vel ?? "—"' in open("render.py", encoding="utf-8").read()
    print("PASS: D — a missing news count renders as — and skips the news-divergence check")


def test_d_every_feed_failing_fails_the_run():
    import fetch_market
    tickers = [t for layer in fetch_market.AI_CHAIN_LAYERS.values() for t in layer["tickers"]]
    with patch("fetch_market.fetch_all_headlines", return_value=([], set(tickers), list(fetch_market.GENERIC_FEEDS))), \
         patch("fetch_market.apply_growth_history"), \
         patch("fetch_market.fetch_ticker_data", side_effect=lambda t, *a, **k: {"ticker": t, "_session_bar": True}):
        try:
            fetch_market.run_pipeline(session=SESSION)
            raise AssertionError("the run went on with every news feed failed")
        except fetch_market.FetchFailureError as e:
            assert "news feed" in str(e), e
    print("PASS: D — every news feed failing fails the run")


def _tmp_detail():
    return str(Path(tempfile.mkdtemp()) / "scores_detail.jsonl")


if __name__ == "__main__":
    import logging
    logging.disable(logging.CRITICAL)
    test_a_company_fetch_stage_failure_fails_the_run()
    test_a_empty_scoring_result_fails_the_run()
    test_a_empty_result_is_never_saved_over_a_stored_session()
    test_b_macro_failure_keeps_the_company_data()
    test_c_failed_company_is_named_outside_the_layer_score()
    test_c_most_companies_failing_fails_the_run()
    test_d_feed_failure_is_detected_without_an_exception()
    test_d_failed_feeds_are_named_and_the_company_news_count_is_missing()
    test_d_missing_news_count_renders()
    test_d_every_feed_failing_fails_the_run()
    print("\nAll fetch-failure tests passed.")

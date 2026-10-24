"""
track_record.py — the "One signal to watch" track record (item 8).

A pick starts when the signal names a different company from the previous
run's, and runs until another company replaces it. A run without a signal
(withheld or unparsed) doesn't end a pick. Entry = the close of the session
the pick was first shown; exit = the close of the last session it was shown
(an active pick runs to the latest completed close). Returns are computed at
evaluation time from yfinance's split-adjusted closes (auto_adjust=False
"Close": split- but not dividend-adjusted, i.e. price return), so a later
split can't break them; the close stored in scores_detail.jsonl is only a
cross-check. NASDAQ over the same sessions is the benchmark.

Two sources (Ray, 2026-10-24):
  - current method: scores_detail.jsonl lines (signal ticker + close);
  - old method: scores_history.json entries without the current METHOD_VERSION
    (action_ticker only, no price): shown separately, entry reconstructed
    from the historical close. They were published, so they're not omitted.
"""

import json
import logging
import datetime
from pathlib import Path

log = logging.getLogger(__name__)

CROSS_CHECK_TOL = 0.01   # stored vs fetched close; a bigger gap is logged (e.g. a later split)
END = "__END__"          # "no company qualifies" under a signal rule: ends the current pick


def _per_session(runs: list) -> list:
    """One signal per session: the last run's, i.e. the last run before the
    next open (the backtest rule, Ray 2026-10-24). runs are in run order."""
    last = {}
    for session, tk in runs:
        last[session] = tk
    return sorted(last.items())


def _episodes(runs: list) -> list:
    """runs: [(session_date, ticker or None)] in run order → [{ticker, first, last}]."""
    eps = []
    open_ep = True
    for session, tk in runs:
        if tk == END:
            open_ep = False                            # exit stays at the last qualifying session
            continue
        if not tk:
            continue                                   # withheld / unparsed: doesn't end the pick
        if eps and open_ep and eps[-1]["ticker"] == tk:
            eps[-1]["last"] = session
        else:
            eps.append({"ticker": tk, "first": session, "last": session})
        open_ep = True
    return eps


def current_method_runs(detail_path: str) -> list:
    """[(session, ticker, stored_close)] from scores_detail.jsonl, in run order."""
    p = Path(detail_path)
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        sig = d.get("signal") or {}
        # Under a signal rule, no ticker means no company qualified: that ends
        # the pick. Before the rule (no "rule" key), it meant a withheld or
        # unparsed reply, which doesn't (Ray, 2026-10-24).
        tk = sig.get("ticker") or (END if sig.get("rule") else None)
        out.append((datetime.date.fromisoformat(d["session"]), tk, sig.get("close"), d.get("run_utc", "")))
    out.sort(key=lambda x: x[3])
    return [(s, t, c) for s, t, c, _ in out]


SESSION_DATING_FIX = datetime.date(2026, 10, 11)   # 48d0ada: history keyed by session from here on


def _effective_session(entry: dict) -> datetime.date:
    """The session an old entry's page actually showed. Before 48d0ada, entries
    were dated by UTC run day: a run before the 13:30 UTC open showed the
    previous session's close (e.g. 2026-09-30 at 11:49 → the Sep 29 close).
    Previous weekday; no US market holiday falls in that period."""
    d = datetime.date.fromisoformat(entry["date"])
    if d < SESSION_DATING_FIX and (entry.get("time") or "23:59") < "13:30":
        d -= datetime.timedelta(days=3 if d.weekday() == 0 else 1)
    return d


def old_method_runs(history: list, method_version: str) -> list:
    """[(session, ticker)] from scores_history.json entries under an older method."""
    # Run order is date + time (scores_history keeps one entry per date: its
    # last run); a pre-open entry maps to the previous session, so it can be
    # the later run for that session (2026-09-30 11:49 after 09-29 23:50).
    rows = sorted((e for e in history if e.get("date") and e.get("method") != method_version),
                  key=lambda e: (e["date"], e.get("time") or ""))
    return [(_effective_session(e), (e.get("action_ticker") or "").strip() or None) for e in rows]


def _closes(ticker: str, start: datetime.date, end: datetime.date) -> dict:
    """{session: split-adjusted close} from yfinance; {} on failure."""
    import yfinance as yf
    from fetch_market import drop_unfinished_bar
    try:
        h = yf.Ticker(ticker).history(start=start - datetime.timedelta(days=5), end=end + datetime.timedelta(days=1),
                                      auto_adjust=False)
        h = drop_unfinished_bar(h, label=ticker)
        return {ts.date(): float(v) for ts, v in h["Close"].dropna().items()}
    except Exception as e:
        log.warning(f"  track record: {ticker} closes not fetched ({e})")
        return {}


def _evaluate(ep: dict, latest: datetime.date, active: bool, closes_for) -> dict:
    end = latest if active else ep["last"]
    px, nq = closes_for(ep["ticker"], ep["first"], end), closes_for("^IXIC", ep["first"], end)
    def ret(series):
        a, b = series.get(ep["first"]), series.get(end)
        return round((b / a - 1) * 100, 1) if a and b else None
    return {"ticker": ep["ticker"], "first": ep["first"].isoformat(), "end": end.isoformat(), "active": active,
            "entry": round(px[ep["first"]], 2) if px.get(ep["first"]) else None,
            "ret_pct": ret(px), "nasdaq_pct": ret(nq)}


def build(history: list, detail_path: str, latest_session: datetime.date, method_version: str,
          closes_for=_closes, keep: int = 5) -> dict:
    """{"current": [...], "old": [...]}, newest first. A return that can't be
    computed (fetch failure, session missing) is None, shown as "—"."""
    cur_runs = current_method_runs(detail_path)
    cur = _episodes(_per_session([(s, t) for s, t, _ in cur_runs]))
    rows_cur = []
    ended = bool(cur_runs) and _per_session([(s, t) for s, t, _ in cur_runs])[-1][1] == END
    for i, ep in enumerate(cur):
        row = _evaluate(ep, latest_session, active=(i == len(cur) - 1 and not ended), closes_for=closes_for)
        stored = next((c for s, t, c in cur_runs if s == ep["first"] and t == ep["ticker"] and c), None)
        if stored and row["entry"] and abs(row["entry"] / stored - 1) > CROSS_CHECK_TOL:
            log.warning(f"  track record: {ep['ticker']} entry {row['entry']} vs stored close {stored} "
                        f"on {ep['first']} differ > {CROSS_CHECK_TOL:.0%} (a split since?) — using the adjusted close")
        rows_cur.append(row)
    old = _episodes(_per_session(old_method_runs(history, method_version)))
    rows_old = [_evaluate(ep, latest_session, active=False, closes_for=closes_for) for ep in old]
    return {"current": rows_cur[::-1][:keep], "old": rows_old[::-1][:keep]}

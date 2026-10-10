# PLAN.md — AI_valuechain (RayDar)

Running record of in-progress work and a queue of known issues deliberately not yet fixed. Read alongside CLAUDE.md at the start of every session.

## Queue

Items here are reported, not yet diagnosed via decomposition against real data (per CLAUDE.md's diagnosis methodology) unless noted otherwise. Don't fix without confirming root cause first — that's the whole point of logging rather than fixing.

- **`_load_recent_layer_scores` reads a missing day as 0.** `main.py:53`, specifically the `.get("scores", {}).get(layer_id, {}).get("score", 0)` default at `main.py:71`. A day with no recorded score for a layer silently becomes a 0 in the recent-scores list used for momentum/trend comparisons, rather than being excluded or flagged missing — conflating "no data" with "score of zero," which `CLAUDE.md`'s data-honesty rule already prohibits elsewhere (fetch failures). Logged 2026-10-10.

- **History entries are dated by UTC run time, not market session.** Scheduled runs (currently `00:08`/`12:29` UTC-ish per the daily commit log) stamp entries by when the job ran, not by which trading session the data reflects. Reported symptoms: weekend entries appearing in history, lost sessions (09-28, 10-01, 10-05 missing), and duplicated sessions. Needs its own decomposition — likely in `render.py`'s `save_scores_history`/`load_scores_history` or wherever the history JSON's date keys get written. Logged 2026-10-10.

- **Momentum sign is wrong when 5-day and 90-day returns are both negative; near-zero 90-day return saturates.** `fetch_market.py:309-313` — `avg_5d_from_90d = ret_90d / 18 if ret_90d != 0 else 0.001` then `price_momentum = ret_5d / avg_5d_from_90d`, clamped to `[-5, 5]`. When both returns are negative the ratio of two negatives goes positive (wrong sign), and a near-zero `ret_90d` drives `avg_5d_from_90d` toward the `0.001` floor, blowing the ratio up to the clamp ceiling regardless of the actual 5-day move. This is the same clamp line implicated in the NaN bug below — worth re-examining together once the NaN fix lands, since the NaN fix will touch this exact function. Logged 2026-10-10.

- **Analyst-upgrade check reads a `yfinance` column that may no longer exist.** `fetch_market.py:380-381` — `if "To Grade" in recent.columns: up = recent[recent["To Grade"].isin([...])]`. Reported that current `yfinance` doesn't provide a `To Grade` column on `t.recommendations`, so this check likely always evaluates to 0 upgrades, silently. Needs confirming against the actual current `yfinance` schema before fixing. Logged 2026-10-10.

- **`yfinance`, `pandas`, `numpy` are unpinned in `requirements.txt`.** Confirmed by reading the file directly — all three use `>=` floors, not exact pins (`yfinance>=0.2.40`, `numpy>=1.26.0`, `pandas>=2.0.0`). A future `yfinance` release changing its data shape (column names, NaN handling, schema) could silently break the pipeline on the next scheduled run with no warning, same failure class as the NaN bug just fixed. Logged 2026-10-10.

- **`next_nvidia_cache.json` is a tracked file that should be untracked and gitignored.** It's a regenerated local cache (`next_nvidia.py`), last legitimately committed 2026-05-13, and has shown a large uncommitted diff in every session since — `.gitignore` already lists it, but it predates that entry so the ignore rule has no effect on an already-tracked file (would need `git rm --cached` to actually stop tracking it). Not changed now, per instruction. Logged 2026-10-10.

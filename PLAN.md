# PLAN.md — AI_valuechain (RayDar)

Running record of in-progress work and a queue of known issues deliberately not yet fixed. Read alongside CLAUDE.md at the start of every session.

## Queue

Items here are reported, not yet diagnosed via decomposition against real data (per CLAUDE.md's diagnosis methodology) unless noted otherwise. Don't fix without confirming root cause first — that's the whole point of logging rather than fixing.

- **Momentum sign is wrong when 5-day and 90-day returns are both negative; near-zero 90-day return saturates.** `fetch_market.py` — `avg_5d_from_90d = ret_90d / 18 if ret_90d != 0 else 0.001` then `price_momentum = ret_5d / avg_5d_from_90d`, clamped to `[-5, 5]`. When both returns are negative the ratio of two negatives goes positive (wrong sign), and a near-zero `ret_90d` drives `avg_5d_from_90d` toward the `0.001` floor, blowing the ratio up to the clamp ceiling regardless of the actual 5-day move. Same function the NaN fix (2026-10-10, `63e69d2`) already touched — ready to diagnose and fix next (Step 4). Logged 2026-10-10.

- **Analyst-upgrade check reads a `yfinance` column that may no longer exist.** `fetch_market.py` — `if "To Grade" in recent.columns: up = recent[recent["To Grade"].isin([...])]`. Reported that current `yfinance` doesn't provide a `To Grade` column on `t.recommendations`, so this check likely always evaluates to 0 upgrades, silently. Fix direction given: read `upgrades_downgrades` with a 30-day window instead, missing = `None` (not 0). Needs confirming against the actual current `yfinance` schema before fixing (Step 4). Logged 2026-10-10.

- **`next_nvidia_cache.json` is a tracked file that should be untracked and gitignored.** It's a regenerated local cache (`next_nvidia.py`), last legitimately committed 2026-05-13, and has shown a large uncommitted diff in every session since — `.gitignore` already lists it, but it predates that entry so the ignore rule has no effect on an already-tracked file (would need `git rm --cached` to actually stop tracking it). Not changed now, per instruction. Logged 2026-10-10.

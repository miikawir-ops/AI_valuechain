# PLAN.md — AI_valuechain (RayDar)

Running record of in-progress work and a queue of known issues deliberately not yet fixed. Read alongside CLAUDE.md at the start of every session.

## Queue

Items here are reported, not yet diagnosed via decomposition against real data (per CLAUDE.md's diagnosis methodology) unless noted otherwise. Don't fix without confirming root cause first — that's the whole point of logging rather than fixing.

- **The page's "Market regime" label uses different rules from the multiplier actually applied to scores.** `render.py` labels Risk-Off if VIX > 30 *or* yield change > 100, Neutral if VIX > 20 *or* yield change > 50, and never looks at NASDAQ vs S&P. `score_engine.get_macro_multiplier()` votes across all three: Risk-Off needs two risk signals, Neutral one risk or two neutral signals. Example: VIX 25 alone shows "Neutral" on the page while scores get 1.0x (Risk-On). RayDar-DataCenter has the same split. Logged 2026-10-15.

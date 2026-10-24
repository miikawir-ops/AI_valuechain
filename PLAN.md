# PLAN.md — AI_valuechain (RayDar)

Running record of in-progress work and a queue of known issues deliberately not yet fixed. Read alongside CLAUDE.md at the start of every session.

## Queue

Items here are reported, not yet diagnosed via decomposition against real data (per CLAUDE.md's diagnosis methodology) unless noted otherwise. Don't fix without confirming root cause first — that's the whole point of logging rather than fixing.

- **Missing 30d return shown and scored as 0 (found 2026-10-24, not yet decomposed).** render.py's card `ret30` uses `... or 0`, so a missing 30d return displays "0.0%". score_engine.calculate_hype defaults a missing price_30d_return to 0.0 and a missing growth_curr to 0.10, so hype rule 1 can't fire on missing data. Latent: no ticker lacks either field today. Fold into item 7, which reads the same 30d return.
- **Sibling follow-up, RayDar Data Center — note only, don't touch (Ray, 2026-10-24).** It copied fetch_market.py, so it likely has two bugs fixed or being fixed here: revenue growth compares the latest quarter with 2 quarters back instead of the same quarter a year earlier (item 1 of the 2026-10-24 batch), and the card price is the live intraday quote, not the last completed close (fixed here in dbc2812). Not diagnosed in the sibling. Per CLAUDE.md, a bug here doesn't prove the copy has it; test the sibling directly when this is picked up.

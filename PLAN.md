# PLAN.md — AI_valuechain (RayDar)

Running record of in-progress work and a queue of known issues deliberately not yet fixed. Read alongside CLAUDE.md at the start of every session.

## Queue

Items here are reported, not yet diagnosed via decomposition against real data (per CLAUDE.md's diagnosis methodology) unless noted otherwise. Don't fix without confirming root cause first — that's the whole point of logging rather than fixing.

- **`.gitattributes` has no effect in any form — awaiting Ray's decision.** Diagnosed 2026-10-15. It's UTF-16 (`file` reports "UTF-16, little-endian"), but the content is also malformed: each pattern and its attribute sit on separate lines (`ai_chain_report.html` / `merge=ours` / `scores_history.json` / `merge=union` / `scores_history.json` / `merge=ours`), so even re-saved as UTF-8 each line is a pattern with no attributes. Corrected to one-line rules it would still do nothing: tested in a throwaway repo, `scores_history.json` resolves to `merge=ours` (last line wins over `union`), and with no `ours` driver defined git falls back to the normal text merge (conflict markers) — identical to having no file. `ai_chain_report.html` is now a static redirect stub. Likely shared root cause with the old `.gitignore`: Windows PowerShell 5.1's `echo a b >> file` writes each argument on its own line, in UTF-16. Options: re-save literally (still inert), rewrite as valid one-line rules (valid but still inert — looks handled when it isn't), or delete the file (same effective behaviour, nothing misleading). Logged 2026-10-15.

- **`fetch_macro()` substitutes hardcoded defaults when a signal fails to fetch.** VIX falls back to 20.0, the yield change and NASDAQ-vs-S&P to 0.0, and one shared `try` means a failure on a later signal throws away the earlier successful ones. CLAUDE.md's fetch-failure rule says a failed fetch is a missing value, never a default. RayDar-DataCenter fetches each signal in its own `try` and reports a failure as `None` + `data_missing`. Logged 2026-10-15.

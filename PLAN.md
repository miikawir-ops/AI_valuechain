# PLAN.md — AI_valuechain (RayDar)

Running record of in-progress work and a queue of known issues deliberately not yet fixed. Read alongside CLAUDE.md at the start of every session.

## Queue

Items here are reported, not yet diagnosed via decomposition against real data (per CLAUDE.md's diagnosis methodology) unless noted otherwise. Don't fix without confirming root cause first — that's the whole point of logging rather than fixing.

- **`next_nvidia_cache.json` is a tracked file that should be untracked and gitignored.** It's a regenerated local cache (`next_nvidia.py`), last legitimately committed 2026-05-13, and has shown a large uncommitted diff in every session since — `.gitignore` already lists it, but it predates that entry so the ignore rule has no effect on an already-tracked file (would need `git rm --cached` to actually stop tracking it). Not changed now, per instruction. Logged 2026-10-10.

- **`.gitattributes` is UTF-16 encoded, so its `merge=` rules match nothing.** Same failure class as the `.gitignore` fixed 2026-09-16 (`8657ecf`): `file .gitattributes` reports "UTF-16, little-endian". Its `ai_chain_report.html merge=ours` entry also now names a redirect stub, and it lists `scores_history.json` twice with conflicting strategies (`union` and `ours`). Two files with the same encoding bug suggests a shared cause (likely a Windows tool writing UTF-16 — e.g. PowerShell 5.1's `>`/`Out-File` default) worth a look once this is fixed. Flagged 2026-09-23, logged 2026-10-15.

- **`render._print_console()` crashes on Windows when Gemini's analysis contains an emoji.** `print(analysis)` hits cp1252's `UnicodeEncodeError` (e.g. 🌍), and `stage_render()`'s fallback handler crashes on the identical `print`. `index.html` is already saved by then, and CI (Linux, UTF-8) is unaffected, so this only breaks local `main.py --now` runs (exit 1 after a successful render). Logged 2026-10-15.

- **`^VIX` is read from `.info` (intraday), not a completed daily bar.** Every other daily series now drops today's unfinished bar until 16:00 ET (2026-10-15); a run during US trading hours still takes VIX's live intraday value. RayDar-DataCenter reads `^VIX` daily bars through `drop_unfinished_bar()`. Logged 2026-10-15.

- **`main.py`'s auto-publish step is dead code.** `run_full_pipeline()` loads `publish.py` and calls `mod.publish()`, but `publish.py` has no `publish()` function (only `setup_repo()`), so every run logs "Publish skipped" via the surrounding `except`. Harmless — deploys happen via the workflow's own commit step — but misleading. Logged 2026-10-15.

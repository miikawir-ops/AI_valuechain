CLAUDE.md — Working rules for AI_valuechain (RayDar, the parent agent)

Read this at the start of every session. These rules override default behaviour. This is the CANONICAL RayDar implementation — sibling agents (RayDar Data Center, Quantum RayDar, etc.) copy patterns and reference code from here, not the reverse.

This is a live public site. A bug here isn't a local inconvenience — it's wrong output on a page real people read. Treat changes accordingly.

Core principles
Don't flatter — be honest. If an idea is bad, say so and explain why.
Diagnose, then plan, then code — in that order, never collapsed. Diagnosis means establishing what's actually true against real data, with no fix proposed yet. Planning means agreeing the fix. Neither should skip ahead of the other.
Don't over-engineer — keep it simple until proven to work. Add complexity only when justified by evidence, not by anticipation.
Language

All communication in English — explanations, code, comments, commit messages, names.

Planning & communication
Ask before architecture decisions, new dependencies, or multi-file changes. For small obvious fixes, just do them.
On this repo, "small and obvious" means no behavioral or output change — typos, comments, log lines, internal renames with no external effect. The moment a change could alter what a number means, what displays, or what deploys, it needs the plan-first treatment regardless of line count. A one-line diff can still be exactly the kind of change that needs a plan (the keyword-purity fix was one such line).
Show the plan before multi-step changes; wait for go-ahead.
When editing an existing file, view the current full file first. Never patch based on assumed content. (A stale assumed copy once silently dropped ~400 lines.)
After a multi-file change, state what changed and why, per file.
Challenge the direction. If there's a faster or more robust way to hit a goal, say so before implementing — don't silently execute a worse path because it's what was asked.
After correcting a wrong assumption, update this file, not just the code, so the same mistake can't recur silently on a later task.
Diagnosis methodology

These are specific techniques, not general caution — use them literally.

Identical values across things that should be independent is a bug signal, not reassurance. If every layer, ticker, or company shows the same number, decompose before accepting it as real.
A plausible composite output does not verify its inputs. A score built from three signals can look differentiated even when one input is silently maxed-out garbage — the other two can carry the appearance of health. Check each input.
Before fixing a bug, check whether other code silently depends on the broken behaviour to look correct. Fixing the root cause can break a downstream display or calculation that only worked by accident.
A bug found in a copied/reference version does not tell you whether the source has it, or vice versa. Don't infer either way — decompose and test the actual target directly against real, current data.
Before changing shared logic (a function, a keyword list, a config dict), enumerate every call site or usage with a search, not memory or assumption. Confirm the actual blast radius before editing.
Before deleting an entry from a shared list, check structurally whether other surviving entries contain it as a substring or otherwise depend on it — don't rely on spot-checking a sample.
Rule out formatting, rounding, or truncation before concluding a numeric mismatch is a logic bug. Cheap to check, easy to chase as a phantom otherwise.
Verification (don't claim done until checked)
Run it, or at minimum check syntax and imports, before saying it works.
A successful run is not a correct run. No errors / green CI proves the code didn't crash — it proves nothing about whether the output is meaningful. These are two separate claims; both need evidence.
Verify against the REAL production path (the actual pipeline, live data), not only a standalone or bypass script — a bypass script can hide the exact bug (pooling, aggregation, scope) that only appears at the real call sites.
If something can't be verified, say so explicitly rather than assuming.
When a fix has multiple independent parts, land them as separate commits, each verified against real output before the next starts. This is what makes a regression traceable to one change instead of a tangle of several.
Approval scope
Needs Ray's approval before committing: anything that changes how something looks (share screenshots first — a passing test run verifies function, not whether it looks right to Ray); any user-facing text; any change to rendered output on this live site; any change to scoring logic, weights or thresholds; any architecture decision, new dependency or multi-file change.
Proceed without approval, report afterwards: bug fixes with a reproduced and verified root cause; refactors with no behavior change; documentation; test and verification tooling — provided verification passes and the change is committed separately so it can be reverted on its own.
Always stop and ask regardless: when the fix would change agreed behavior; when the root cause isn't actually understood; when the change affects more than the reported problem.
Data & honesty
Never fabricate data, metrics, or milestones. Mark sparse or estimated data as such.
Financial data may be stale (quarterly financials up to 90 days old) — disclose it.
Distinguish "the signal is real" from "the price moved." Never conflate them.
Not financial advice — dashboards inform decisions, they don't make them.
On fetch failure, never substitute zero, a stale cached value, or an estimate silently. A failed fetch is a missing value, not a zero — treating it as zero is itself a form of fabrication. Mark it missing and surface that in the output.
A keyword-matching signal needs a topical requirement — a bare identifier match alone must never count. A company name or ticker symbol in a headline is not news about that company's fundamentals on its own. This can be satisfied by removing identifier-only keywords entirely (the simpler fix) or by requiring a separate topical match alongside a kept identifier — either is fine; the requirement is the outcome, not a specific mechanism.
RayDar family — this repo is the source, not a consumer
This is the canonical implementation. Siblings copy scoring philosophy, color system, visual language, and reference code FROM here.
When a bug is found and fixed here, check whether siblings' copied reference code has the same bug — their copy may be older than this repo's current state.
Core patterns siblings depend on, keep stable: three-signal scoring, market-cap weighted layers, layer-filtered news scoring (a ticker's headlines only count for its own layer — cross-contamination was a real, previously-shipped bug), multi-day color confirmation.
Files & deployment
Scratch/working output → ./Output/ (create if missing). The deployable dashboard HTML must live at repo ROOT (GitHub Pages serves from root or /docs) — never in ./Output/.
A one-off diagnostic script (used once, answer obtained, done) can stay in an external scratchpad — it doesn't need to live in the repo. But if a diagnostic technique becomes a named, reusable method (e.g. "the decomposition test"), that specific script belongs in the repo (./Output/ or a tools/ folder) so it can be reused, not rewritten from memory next time.
Every GitHub Pages repo needs index.html at root so the bare URL resolves without a filename. (Learned from a 404 on a sibling site.)
Keep secrets (.env, API keys) out of git. Check .gitignore before committing — and verify it's actually well-formed with git check-ignore <file> on a file it's supposed to exclude, not just by reading it. A malformed .gitignore line silently ignoring nothing is worse than no rule, because it looks handled.
Commit messages: type: what changed (e.g. fix: layer-filtered news scoring).
Don't force-push or rewrite history without asking.
Definition of done — for a fix intended for the live site

Local, pushed, and deployed are three different states. Don't claim "done" until all of these are true, in order:

At the start of a session, check how far local is from origin (git status, git fetch + compare) before starting work — don't discover a multi-month drift only when a push is rejected at the end.
Root cause confirmed via decomposition against real data — not inferred from a symptom, a screenshot, or a copy's state.
Fix scoped to the smallest correct change; plan agreed before code.
Each independent part committed and verified against the real production path before the next part starts.
Pushed to origin — confirm with git status (ahead/behind), not assumed.
Deploy triggered or confirmed — check the Actions run is green, not just that the push succeeded.
Live output re-checked against the original symptom, same view, before and after, side by side.
When stuck or uncertain
If a request is ambiguous, ask — don't guess and build the wrong thing.
Always surface an improvement when you notice one — a bug, a risk, a better pattern, a cheaper approach — even outside the current task. Never suppress it to stay narrowly in scope, and never act on it unasked; surfacing and doing are different steps. When there's more than one, tag by urgency so I can triage fast: must-fix-now (live-site correctness or security), real-but-not-urgent, and minor/cosmetic (your call, no action needed). Keep each item to a line or two unless asked to elaborate — a long unsolicited list is its own kind of noise.
If two of these rules conflict, ask which takes priority.
# Changelog

## 2026-10-10/11 — yfinance NaN, session dating, pinned deps, display fixes

### What was wrong

- **NaN on the live page.** Confirmed directly against the deployed site: every ticker's `ret30` was the literal value `NaN` on the runs at 2026-10-08 00:17, 10-09 00:32 and 10-10 00:08 UTC (28/28 tickers). Root cause: `yfinance` can append a trailing "today" bar with NaN `Close` right after 00:00 UTC, before the market has opened, poisoning `price_30d_return`/`price_momentum`. The existing clamp made it worse: `max(-5.0, min(5.0, nan))` resolves to exactly `5.0` in Python (confirmed in the REPL) — a plausible-looking "strong bullish momentum" reading, not an obviously-wrong value. Also silently corrupted `peer_outperformance` for every ticker in an affected layer, via `add_peer_outperformance()`'s `is not None` filter (already written assuming None-means-missing, which NaN doesn't satisfy).
- **History dated by UTC run time, not trading session.** A run shortly after midnight UTC reflects the *prior* day's session close, but `scores_history.json` keyed entries by `datetime.now()` (the run date) — producing weekend entries and lost/duplicated sessions.
- **A missing session read as 0.** `_load_recent_layer_scores`'s `.get("score", 0)` default made a partial-failure run's missing layer score indistinguishable from a real bearish 0 reading.
- **Unpinned dependencies.** `yfinance`/`pandas`/`numpy`/etc. all used `>=` floors — the exact failure class above (a library release silently changing data shape) had no guard against recurring.
- **Display**: the "AI value chain — signal scores" heading was inline `12px #C8D4E8` on a white card (a color meant for dark backgrounds) — nearly invisible. No "UTC" marker on displayed times. "vs yesterday" didn't say which day.

### Fix

- `fetch_market.py`: drop trailing NaN-Close rows before computing anything; check `isnan()` before clamping `price_momentum` (not after); any NaN still remaining → `None`, named in a new `data_missing` list; new `find_nan_paths()`/`assert_no_nan()` as a final backstop, scanning all fetched data.
- `main.py`: `assert_no_nan()` called right after `stage_fetch()`, deliberately outside its own try/except (which otherwise swallows exceptions into an empty-data fallback) — the point is to fail the whole run rather than publish degraded data.
- New `fetch_market.get_last_session_date()` (from `^GSPC`'s own daily-bar calendar, no try/except — a failed fetch must fail the run). Threaded through `save_scores_history`, `get_yesterday_scores`, `_load_recent_layer_scores`, `_confirmed_color` so history is keyed by session, not run date; a rerun of the same session now overwrites instead of duplicating.
- `_load_recent_layer_scores`: a session with no recorded score for a layer is skipped and logged, never defaulted to 0.
- `requirements.txt`: pinned to exact versions. **Correction, same session (`7161085`)**: the first pass (`8a43626`) used this session's local Python 3.14 environment's versions — wrong reference point, since CI runs Python 3.11 and numpy 2.4.x may not even support it. Redone properly: workflow's `python-version` bumped to `'3.12'` to match RayDar-DataCenter's own CI; `requirements.txt` rebuilt from DataCenter's CI-verified pins (run `38068453210`) for the overlapping packages plus their full transitive tree, with this repo's three extra direct dependencies (`schedule`, `python-dotenv`, `google-genai`) resolved by actually installing everything together in a fresh 3.12 venv — not a naive concatenation. That caught a real conflict: `google-genai` forces `websockets` down to `16.1.1` from DataCenter's `17.2` (yfinance's own range accepts it).
- `render.py`: new `.card-title` CSS class (20px bold navy `#0C1A3A`, reusing an existing color) replacing the inline near-invisible heading style; header and fetch-note now show "UTC" explicitly and "Data: close of \<session date\> · fetched HH:MM UTC"; "vs yesterday" → "vs \<session date\>" (e.g. "vs Fri Oct 9").

### Verified

- `test_nan_handling.py` and `test_session_dating.py` (plain scripts, no pytest in this environment): both confirmed to genuinely fail against the pre-fix code (temporarily stashed each fix, reran, got real failures — `AssertionError: price_30d_return is NaN: nan` and `AttributeError: module 'fetch_market' has no attribute 'get_last_session_date'` — restored, reran clean) rather than just asserted to fail.
- Whole-chain sanity checks against live data (`main.py --score` and a full `main.py --now`): exit 0, correct session date logged, plausible differentiated scores, `index.html` regenerated with all three display changes confirmed present (screenshots at 1400px/375px).
- Live deploy pending a successful Actions run — manual `workflow_dispatch` trigger requested, not yet confirmed as of this entry.

### Commits

`63e69d2` (NaN), `8a43626` (pinning, superseded — see correction above), `48d0ada` (session dating + display), `441c4e7` (PLAN.md queue cleared), `48fa058` (this entry), `7161085` (pinning correction).

## 2026-09-23 — RayDar Vice: fixed the 404 that this file's own index.html rule came from

### What

- `RayDar-Vice` (sibling repo, `github.com/miikawir-ops/RayDar-Vice`) had no `index.html` at repo root at all — its daily workflow (`.github/workflows/daily_brief.yml`) only ever committed `raydar_vice.html`, `seen_hashes.json`, `signal_history.json`. GitHub Pages had nothing to serve at the bare root URL. Confirmed live before the fix: root 404, `/raydar_vice.html` 200 directly — ruling out a Pages configuration problem, isolating the cause to the missing file.
- This is the same failure this repo's own `CLAUDE.md` rule ("Every GitHub Pages repo needs index.html at root... Learned from a 404 on a sibling site") was written from. That rule existed for months while the 404 it was learned from remained unfixed on the site that taught it.

### Fix

- Added a minimal `index.html` redirect at `RayDar-Vice` repo root (meta-refresh + `<link rel="canonical">` + a visible fallback link) pointing to `raydar_vice.html`. A redirect, not a rename: `raydar_vice.html` and its workflow are untouched, so existing direct links keep working — a smaller, lower-risk fix than the rename-to-canonical approach used on this repo's own `index.html`/`ai_chain_report.html` split, appropriate here since Vice's daily file was never split into two competing copies the way this repo's was.
- The file is deliberately excluded from the workflow's `git add` list, and carries an inline HTML comment warning against a future workflow change silently starting to write over it — the same orphaning failure mode this repo hit, forestalled at the point of risk rather than in a doc no one reads before touching that file.

### Verified

- Local clone was 123 commits behind origin — pulled fast-forward to match `origin/main` exactly, confirmed clean tree, before making the single-file commit (per this file's own "check ahead/behind at session start" rule, applied here to a sibling repo for the same reason).
- Live, after push: root returns 200 and serves the redirect content correctly (meta-refresh tag, canonical link verified byte-for-byte against what was committed). A real browser (Playwright) navigating to the bare root URL actually lands on `.../raydar_vice.html` with the correct title — not just inferred from a 200 status, the redirect was confirmed to execute. `raydar_vice.html` confirmed still reachable directly, unaffected.

## 2026-09-21 — Part B7: data-driven sub-agent deep-dive link (parent ↔ Data Center)

### What

- Added `LAYER_DEEP_DIVE_URLS` (`render.py`), a `layer_id -> {"url", "label"}` map. When a layer has an entry, its collapsed card renders a full-width "Enter <product name> ↗" button linking to that sub-agent's dashboard; layers without an entry are unchanged. Currently one entry: `infra -> RayDar Data Center`. A future sub-agent (e.g. Quantum RayDar) needs only a new dict entry here — no `buildChain()` template edit — to get its own named affordance ("Enter Quantum RayDar ↗").
- Removed the layer-level Claude "Deep dive ↗" button (`.ex-btn`, inside `buildExpand()`'s panel header) — dashboard-wide, all 7 layers, since it's one shared template with no way to scope removal to a single layer. It was redundant with the per-ticker "Deep dive with Claude" button, which offers the same Claude interaction at more useful (per-company) granularity, and removing it eliminated a "Deep dive" naming collision at its source. `sendPrompt()` itself is not dead code — 3 other call sites remain (per-ticker button, wildcard radar card, top-5 radar card).
- Data Center's own `render.py` was updated to link back (hero "← RayDar AI value chain", same tab; footer "Part of the RayDar family", new tab) — the cross-link is now two-way.

### Why two follow-up visibility fixes

The first version of the card-level link (9px inline text, translucent `rgba(0,0,0,0.15)` background) was visually indistinguishable from the pre-existing Claude button and separately nearly invisible against the card backgrounds — both found via live testing, not caught in local review. Iterated to a full-width button, opaque navy/purple background, static glow (no animation, per "no constant motion on a data dashboard").

### Fix

- `32984d7` — initial mechanism: `LAYER_DEEP_DIVE_URLS`, `_chain_js_data()` field, `.lyr-deepdive` CSS, `buildChain()` template line.
- `5430cc0` — visibility pass 1 (full-width button; dict upgraded to `{url, label}`; label renamed "Deep dive" → "Enter RayDar Data Center") + `.ex-btn` removal.
- `9af250d` — visibility pass 2: opaque navy/purple gradient background + static box-shadow glow, replacing the still-too-faint translucent fill from pass 1.

### Verified

- Each commit compiled and checked against a local `_chain_js_data()` call before pushing.
- Live page's embedded `LAYERS` JSON checked directly after each deploy to confirm `deep_dive` populated only for `infra`, not any other layer.
- Ray confirmed the final state in a browser: button clearly visible, click navigates to the Data Center dashboard correctly. An earlier click-test (before the naming-collision/`.ex-btn` fix) had landed on the wrong element — diagnosed via DOM/CSS structure (no shared ancestor, no z-index overlap between the two elements), not reproduced after the fix, then re-confirmed working.

## 2026-09-16 — news_velocity: two scoring bugs fixed, plus a downstream display fix

### What was wrong

- **Keyword purity** (`fetch_market.py`, `score_news_velocity()`): the per-layer keyword lists mixed bare company names ("nvidia", "crowdstrike", "micron", etc.) in with topical/technical/financial terms. A headline needed nothing but a brand mention — no chip, breach, cloud, or earnings content — to register as a full news match.
- **Layer pooling** (`fetch_market.py`, `run_pipeline()`): every ticker's news-velocity score was computed against its whole layer's ticker roster instead of its own headlines, so every ticker within a layer received an identical, pooled score instead of one reflecting its own coverage.
- **Downstream display** (`render.py`): the layer-level "news" badge and narrative text picked an arbitrary ticker's value via a scan with a fallback — a consequence of the pooling bug (it only looked coherent because pooling made every ticker's value identical), not an independent defect. Fixed alongside the pooling fix.

### Confirmed duration (via `git blame` on the affected lines, not inferred)

- **Keyword purity**: traced back to the repository's first commit (`1dc7fc4`, 2026-04-28). Present for the project's entire history until the fix — about 140 days.
- **Layer pooling**: introduced by commit `6435ffc7` (2026-05-30), which fixed a different, earlier bug (cross-layer news contamination between layers) and introduced this one as a side effect. Live for about 109 days until the fix.

### Fix

- `fetch_market.py`: removed the 36 bare brand/ticker-identifier keywords across all 7 layers (commit `af05f1f`); changed the two `fetch_ticker_data()` call sites to pass only the ticker being scored, not the whole layer roster (commit `c74acc1`).
- `render.py`: the layer's `news_vel` now reads its own best-scoring ticker's value directly instead of scanning for an arbitrary nonzero one (commit `9c546d2`).

### Verified

- Each fix committed and verified independently against a live `run_pipeline()` pull before the next one started.
- Deployed and confirmed via GitHub Actions run **#232** (`workflow_dispatch`, commit `7f708c8`, conclusion: success) — the first production run carrying all three fixes.
- Numbers seen during diagnosis and while these fixes were staged locally (e.g. the intermediate state after the pooling fix but before the keyword-purity fix) existed only in local, unpushed commits and manual diagnostic pulls. They were never shown on the live site and are not repeated here for that reason.

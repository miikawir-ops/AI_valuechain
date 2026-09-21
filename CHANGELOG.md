# Changelog

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

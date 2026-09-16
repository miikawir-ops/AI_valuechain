# Changelog

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

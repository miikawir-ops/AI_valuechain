# PLAN.md — AI_valuechain (RayDar)

Running record of in-progress work and a queue of known issues deliberately not yet fixed. Read alongside CLAUDE.md at the start of every session.

## Next (agreed order, 2026-10-24)

- **After item 3: save every run's per-company inputs and scores to a tracked file** (e.g. `scores_detail.json`, with history; Ray, 2026-10-24). It makes discrepancies traceable (compute 55.5 live vs 54.0 in the replay on the same prices, cause unverified), proves acceleration for companies the page doesn't show (ETN, CSCO, CIEN, DDOG), and gives a backtest point-in-time data. Needs the workflow's `git add` too (own line, like growth_history.json).
- **Item 7 (two-sided divergence flag): add it to the Gemini prompt too**, as a fact in analyze.build_facts with both readings stated, so the post-check covers it. Don't forget: the prompt was rebuilt in item 3 without it.

## Queue

- **Gemini post-check can't catch a figure swapped between two companies in one sentence (found 2026-10-24, logged by Ray's request).** analyze.check_section accepts any figure belonging to any company or layer named in the sentence (or the previous one), so "MU +54% and LRCX +379%" passes. Possible fix: match each figure to the nearest preceding company name.

Items here are reported, not yet diagnosed via decomposition against real data (per CLAUDE.md's diagnosis methodology) unless noted otherwise. Don't fix without confirming root cause first — that's the whole point of logging rather than fixing.

- **Leader boost has the same base-effect weakness as the removed instant Red (found 2026-10-24, no change by decision).** main.stage_score floors a layer at 45 (Orange) when one company's fund_delta > 0.40. On true YoY that fired in 17 of 600 company-quarters 2021-07 → 2026-10 (2.8%), many of them recoveries off a trough (WDC, VST, SMCI) rather than bottlenecks (tools/accel_distribution.py). Milder than instant Red (Orange floor, still confirmed), so left as is.
- **Rating fallback gives B where nothing supports it (found 2026-10-24, for items 6/7).** render.company_rating's final `else` returns B: a Blue company with a small dip (CRM, score 23.5, −2.4 pp) reads "B · Stable", and every company with unmeasured acceleration (ASML, TSM) gets B by default. Same fallback in both cases.
- **Hold rule keeps colours outside their score band (verified 2026-10-24, by design, flagged).** _confirmed_color holds a colour until the score is 10 points past its threshold. Compute was Orange on 30/30 stored sessions while scoring ≥45 once (2026-09-14). Documented on the page in cfb6e7f; whether the band is right is Ray's call. Separately, a "Confirmed Orange/Red" check can override the all-decelerating → Blue rule (not yet seen on live data).
- **Card run-rate never shows (found 2026-10-24, not decomposed).** fetch_market sets revenue_quarterly only on the revenueGrowth fallback path, so `run_rate` is None whenever growth computes normally. Fixing it adds a visible number to cards → needs approval.
- **gm_delta can refer to an older quarter than growth (found 2026-10-24).** Margin change still comes from yfinance's two newest quarters; when EDGAR supplies a newer quarter (MU, WDC, LRCX, META on 2026-10-24) growth and margin describe different quarters.
- **Smart-money "short covering" rewards a high short-interest level (found 2026-10-24, not decomposed).** short_int_change = −(shortPercentOfFloat − 0.05) and short_score = clamp(−short_int_change × 200), so more short interest scores higher. It's a level, not covering. Page text now says "Short interest level" (b73a5da).
- **Two of four generic news feeds are dead (verified 2026-10-24).** feeds.reuters.com doesn't resolve; finance.yahoo.com/news/rssindex returns 404. Both fail silently to zero headlines. CNBC, MarketWatch and per-ticker Yahoo feeds work.
- **Divergence rule 3 is mislabelled (found 2026-10-24).** "news_vel == 0, Green, delta < 0" shows "⚡ Narrative ahead of fundamentals" though there's no narrative. Fold into item 7.
- **Sibling follow-up, RayDar Data Center — note only, don't touch (Ray, 2026-10-24).** It copied fetch_market.py, so it likely has two bugs fixed or being fixed here: revenue growth compares the latest quarter with 2 quarters back instead of the same quarter a year earlier (item 1 of the 2026-10-24 batch), and the card price is the live intraday quote, not the last completed close (fixed here in dbc2812). Not diagnosed in the sibling. Per CLAUDE.md, a bug here doesn't prove the copy has it; test the sibling directly when this is picked up.

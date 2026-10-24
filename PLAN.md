# PLAN.md — AI_valuechain (RayDar)

Running record of in-progress work and a queue of known issues deliberately not yet fixed. Read alongside CLAUDE.md at the start of every session.

## Queue

Items here are reported, not yet diagnosed via decomposition against real data (per CLAUDE.md's diagnosis methodology) unless noted otherwise. Don't fix without confirming root cause first — that's the whole point of logging rather than fixing.

- **Red reality check never works (verified 2026-10-24, awaiting Ray).** main._red_reality_check reads each ticker's `rating`, but nothing in stage_score sets one, so every company reads as "C" and any Red layer with 2+ tickers is downgraded to Orange; _confirmed_color then re-derives Red from the score, which mostly hides it. Making it work would let hype (which lowers ratings to C) reach layer colour — a decision for Ray, not a silent fix.
- **Hold rule keeps colours outside their score band (verified 2026-10-24, by design, flagged).** _confirmed_color holds a colour until the score is 10 points past its threshold. Compute was Orange on 30/30 stored sessions while scoring ≥45 once (2026-09-14). Documented on the page in cfb6e7f; whether the band is right is Ray's call. Separately, a "Confirmed Orange/Red" check can override the all-decelerating → Blue rule (not yet seen on live data).
- **Card run-rate never shows (found 2026-10-24, not decomposed).** fetch_market sets revenue_quarterly only on the revenueGrowth fallback path, so `run_rate` is None whenever growth computes normally. Fixing it adds a visible number to cards → needs approval.
- **gm_delta can refer to an older quarter than growth (found 2026-10-24).** Margin change still comes from yfinance's two newest quarters; when EDGAR supplies a newer quarter (MU, WDC, LRCX, META on 2026-10-24) growth and margin describe different quarters.
- **Smart-money "short covering" rewards a high short-interest level (found 2026-10-24, not decomposed).** short_int_change = −(shortPercentOfFloat − 0.05) and short_score = clamp(−short_int_change × 200), so more short interest scores higher. It's a level, not covering. Page text now says "Short interest level" (b73a5da).
- **Two of four generic news feeds are dead (verified 2026-10-24).** feeds.reuters.com doesn't resolve; finance.yahoo.com/news/rssindex returns 404. Both fail silently to zero headlines. CNBC, MarketWatch and per-ticker Yahoo feeds work.
- **Divergence rule 3 is mislabelled (found 2026-10-24).** "news_vel == 0, Green, delta < 0" shows "⚡ Narrative ahead of fundamentals" though there's no narrative. Fold into item 7.
- **Sibling follow-up, RayDar Data Center — note only, don't touch (Ray, 2026-10-24).** It copied fetch_market.py, so it likely has two bugs fixed or being fixed here: revenue growth compares the latest quarter with 2 quarters back instead of the same quarter a year earlier (item 1 of the 2026-10-24 batch), and the card price is the live intraday quote, not the last completed close (fixed here in dbc2812). Not diagnosed in the sibling. Per CLAUDE.md, a bug here doesn't prove the copy has it; test the sibling directly when this is picked up.

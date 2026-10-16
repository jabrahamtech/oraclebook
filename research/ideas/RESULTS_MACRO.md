# Macro nowcasts vs Kalshi macro markets — results

Protocol: [PROTOCOL_MACRO.md](PROTOCOL_MACRO.md) (written before any nowcast was compared with an
outcome). Rules: [CANDIDATES_MACRO.md](CANDIDATES_MACRO.md). Kalshi markets settled before
2026-08-15 come from its `/historical/*` endpoints (`research/data/kalshi_api.py --historical`);
nowcasts from `research/data/macro_sources.py`. **Nothing here shows a profitable strategy.**

## Verdict

1. **CPI: the market is better than the Cleveland Fed nowcast.** 35 exploration events: Brier
   market 0.122, nowcast 0.206, 50/50 blend 0.132. The nowcast's error is the same size before and
   during the Kalshi era (SD 0.14–0.17 pp), so this is not miscalibration: the market knows more
   (consensus forecasts, component data). Nothing frozen.
2. **GDP: GDPNow was ahead of thin Kalshi GDP markets in exploration; the holdout neither
   confirms nor rejects it.** 12 exploration events: Brier market 0.175, GDPNow 0.120 (better in
   8 of 12); buying where GDPNow disagrees with the ask +21% [−6, +54]. Holdout (7 events, 2025 →
   2026-07): +30% [−11, +83], Brier tie (0.120 each). Same sign, not significant. The markets are
   thin (median 18 trades per market in the last 48 h; the rule bought $38 of one-contract
   positions over 19 events), so even a real edge has little capacity. A lead worth a forward
   paper test on the next releases (Q3 2026 advance: late October), not a result.
3. Fed decisions were not tested: free probability trackers cover 3-month SOFR windows rather than
   single meetings, and a replication of Diercks, Katz & Wright (2026) finds Kalshi and futures
   disagree only in a handful of "mixed" meetings.

## Data sources checked (2026-10-15)

| Source | Use | As-of history? |
|---|---|---|
| Cleveland Fed inflation nowcast (`nowcast_month.json`) | CPI MoM nowcast per target month, daily, with the actual print | yes, daily from 2013 |
| Atlanta Fed GDPNow (`GDPTrackingModelDataAndForecasts.xlsx`) | daily GDP nowcast, track record vs BEA advance | yes, daily from 2014 |
| ALFRED (`alfredgraph.csv?id=…&vintage_date=…`, no key) | vintage macro data | yes |
| Atlanta Fed Market Probability Tracker, Treasury bill CSV, NY Fed SOFR/EFFR API | Fed path | daily; 3-month windows, not meetings |
| Consensus forecasts | — | paywalled (Bloomberg); none free |
| BLS release calendar | — | blocked (403) from here |

Repos worth knowing: `jdkatz21/Prediction_Markets_Public` (code for "Kalshi and the Rise of Macro
Markets", NBER w34702), `YichengYang-Ethan/fed-pricing-db` (Kalshi vs CME vs Polymarket on FOMC),
`t-e-lawrence/kalshi_regime_replication`, `rmmomin/kalshi-urate-pmf`.

## Reproduce

```bash
cd research/data
python -I macro_sources.py raw
python -I kalshi_api.py raw/macro_hist --series KXCPI,KXGDP --start 2022-01-01 --end 2026-08-16 \
    --close-window 48,0 --historical
python -I kalshi_api.py raw/macro --series KXCPI,KXGDP --start 2026-08-01 --end 2026-10-15 \
    --close-window 48,0
cd ../ideas
python -I macro_cpi.py explore
python -I macro_gdp.py explore --window 24
python -I macro_gdp.py holdout --edge 0 --window 24
```

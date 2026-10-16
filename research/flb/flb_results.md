# Favorite–longshot bias & calibration on public resolved prediction-market data

Generated 2026-10-12 (A–C); D–F added 2026-10-15 from Kaggle. Scripts: `load_pancake.py`, `load_kalshi.py`,
`load_marketlens.py`, `load_kachoio.py`, `load_polyorderbooks.py`, `load_worldcup.py` (build observation tables),
`flb_lib.py` (bins, fees, cluster bootstrap, logistic calibration), `run_flb.py` (tables). All read data with
`python -I`; seed 20261012; 5000 bootstrap reps for every CI. Raw data: each source at the pinned commit / Kaggle version (see README.md).
A–C were built when Kaggle and HuggingFace were blocked; Kaggle was reachable on 2026-10-15 and D–F come from it. A–C reproduce
byte-identically in the rerun (A–C run first on the same seed). Polymarket / Kalshi APIs were not used. **No data was simulated.**

## Datasets obtained (all real; none play-money)

| # | Source (commit) | Venue | License | Coverage | Size used | Price field | Key selection biases |
|---|---|---|---|---|---|---|---|
| A | github.com/usepancake/polymarket-history @356b59b | Polymarket | CC BY 4.0 data (README); manifest says `commercial_use_allowed: false` — check before commercial use | resolutions 2023-03 → 2026-05 (bars Nov 2022 → Jun 2026) | 24,304 markets catalogued, 7,788 resolved, 851,131 daily bars | daily 00:00 UTC price sample (Gamma/CLOB price history; **not** bid/ask, not volume) | resolved-only; Pancake's own catalog subset (crypto/sports heavy); no event id; 2-outcome markets only; voids = none in prod |
| B | github.com/J0shusmc/Kalshi-BTC @d5a55bf (`data/raw/kalshi_btc15/`) | Kalshi | MIT | KXBTC15M markets closing 2026-06-15 → 2026-08-20 | 6,336 markets (6 without candles), 1-min candles with YES bid/ask/last | minute-close YES bid & ask (executable top of book) | one series only (BTC up/down in 15 min); 67 days |
| C | github.com/marketlenstrade/polymarket-historical-data @6eb9b8e (markets.csv + snapshots.csv only) | Polymarket | CC BY 4.0 (attribute "Marketlens, https://marketlens.trade") | 7 single-day events, Jun–Sep 2026 | 2,278 markets; 274k top-of-book snapshots | best bid / best ask | vendor sample of 7 events; mostly in-play sports; 29 CS markets void (50-50) excluded |
| D | kaggle.com/datasets/kachoio/polymarket-5-minute-crypto-updown-markets v1 | Polymarket | CC0 | 5-minute Up/Down windows for 7 coins, closing 2026-03-24 → 2026-05-18 | 89,233 markets, ~26.8M per-second ticks | best bid / best ask of the Up and Down books, every second | one product type; **outcome inferred by the publisher from the final tick, null for 11,139 non-decisive (close-call) markets** — not the official resolution |
| E | kaggle.com/datasets/polyorderbooks/polymarket-crypto-orderbooks-1s-l2 v1 | Polymarket | CC BY 4.0 (cite PolyOrderbooks 2026, doi 10.5281/zenodo.22084114) | crypto Up/Down 5m / 15m / 4h, closing 2026-08-21 → 08-24 | 805 markets, 897k L2 snapshots | per-token best bid / best ask (~1 s) | 4 days only; small; many one-sided books near the close |
| F | kaggle.com/datasets/madrading/polymarket-2026-fifa-world-cup-order-book v2 | Polymarket | CC BY-NC 4.0 (`tickfoundry` rows — all but 6 used) / CC BY 4.0 (`pmxt` rows); non-commercial | every market attached to the 2026 World Cup, end dates 2026-03-12 → 08-31 | 39,497 markets in 1,085 events; 105.5M 1-minute bars | close-of-minute best bid / best ask of the first outcome's book | one tournament; correlated markets within each event; official resolutions |

Not used: Jon-Becker/prediction-market-analysis (data is a 36 GiB archive on s3.jbecker.dev, outside budget/allowed hosts);
SII-WANGZJ/Polymarket_data, TrevorJS/kalshi-trades, marketlens resolved-markets (HuggingFace-hosted, blocked);
manja316/polymarket-historical-data (free part has no outcomes; full set is paid); tanaerao/polymarket-midterms (39 markets only).
No Manifold/PredictIt/IEM resolved price-history dump was found on GitHub within the time budget.
Kaggle datasets screened and not used: luciferforge/polymarket-engineered-features (README promises labels, but all 12,518 markets
have `resolved = 0` and no outcome); pdpdpa/pdata-prediction-markets (resolved file keeps only the *final* probability);
simplefunctions/settled-markets (price at resolution − 24h, i.e. anchored on actual resolution; licence unknown);
gavinalfaro/kalshi-financeseconomics-18-months-history (one end-state row per market, no time series);
abhishekt0607/kalshi-nba-markets-2026 (price at open only); rngrng/polymarket-crypto-updown-markets-trade-tape (trade prints
only; E covers the same products with quotes). The Kaggle CLI needs an API token, which was not available; the public REST
endpoints (search, file list, single-file download) were used instead.

### Exclusions
- A: 8 markets with non-0/1 payoff (void/50-50); 98 with no candles; 97 whose price never moved (placeholder series); 96 with no planned end date (excluded from primary spec). 7,331 markets enter at ≥1 horizon. A market enters a horizon only if it was still unresolved at the sample time and its last bar is ≤2.5 days stale.
- B: 6 markets without candles; 1-minute rows with no ask (ask=1) or no bid (bid=0) drop that side.
- C: 29 void (50-50) markets; any snapshot older than 15 min, one-sided, or spread > 5¢ is dropped (ex-ante liquidity filter).
- D: 11,139 markets with a null (inferred) outcome are kept separately for the bounds below; a side enters only if its book is
  ≤ 5 s stale, uncrossed, has an ask < 1 and a spread ≤ 5¢. 83,159 markets enter at ≥1 horizon (74,283 with an outcome).
- E: crossed or one-sided snapshots dropped; a side enters only if its snapshot is ≤ 15 s stale with spread ≤ 5¢. Label check:
  the last tight-book midpoint agrees with `is_winning_outcome` for 93.8% (5m), 99.6% (15m), 100% (4h) of tokens. 686 markets used.
- F: 56 unresolved/void/non-binary markets; a horizon needs the market still open, a bar ≤ 6 h old, a two-sided book and
  spread ≤ 5¢ (6,960 samples dropped). Mapping check: the first token's last pre-close midpoint agrees with the official result
  for 98.5% of 25,577 markets. 15,333 markets in 923 events used.

### Look-ahead control (important)
Horizons are measured back from an **ex-ante** time: the planned end date (A), the scheduled close (B), a fixed offset after
market open (C), the scheduled window end (D, E) or the scheduled `end_date` (F; ≈ kick-off for match markets). Measuring back from the *realised* resolution time instead (a common shortcut) leaks the outcome: "Will X happen by D?" markets
that resolve Yes early get sampled just before the event. In A that shortcut moves the 7d intercept from +0.02 to +0.23–0.25 and makes the 7d longshot bin look
under-priced; rows labelled "biased anchor" below are kept only to show the size of this effect.

### Fees
Polymarket taker fee = rate·p·(1−p) per share, rate ∈ {0, 0.04 (pm4), 0.07 (pm7)}; Kalshi = ceil-to-cent(0.07·p·(1−p)) per
1-contract order (kx). After-fee return = (y − p − fee)/(p + fee). The Kalshi per-contract ceiling is ≥1¢ for any 0<p<1, so it dominates for
p<0.10; on a 100-lot order the per-contract fee is smaller. The real fee schedule for KXBTC15M may differ from the general formula.

## Headline table (primary spec)

EW = contract-equal-weighted mean return per $1, with a 95% CI from resampling markets. "Clustered" = mean of per-event means,
with a 95% CI from resampling events (A: category × planned-end date proxy; B, D: UTC day; C: game/event; E: UTC hour;
F: Polymarket event). "After fees" = EW (D–F: pm7 with its market-resample CI). D rows drop null-outcome markets — see the bounds.
Longshot = buy price in (0, 0.10]; favorite = [0.90, 1).

| dataset / horizon | bin | n mkts | mean price | win rate | return/$ EW [CI] | clustered [CI] | after fees (EW) |
|---|---|---|---|---|---|---|---|
| A Polymarket 30d | longshot | 1211 | 0.019 | 0.023 | −32.6% [−66.5, +13.4] | +92.4% [−26.4, +271.9] | pm4 −35.1%, pm7 −36.8% |
| A Polymarket 30d | favorite | 55 | 0.954 | 0.964 | +1.0% [−5.1, +5.2] | −0.7% [−8.6, +4.8] | pm4 +0.8%, pm7 +0.7% |
| A Polymarket 7d | longshot | 1659 | 0.019 | 0.033 | +25.5% [−22.4, +82.7] | +72.4% [+11.6, +147.6] | pm4 +20.8%, pm7 +17.5% |
| A Polymarket 7d | favorite | 108 | 0.958 | 0.954 | −0.7% [−5.4, +3.1] | −2.3% [−8.7, +2.8] | pm4 −0.8%, pm7 −0.9% |
| A Polymarket 1d | longshot | 1696 | 0.014 | 0.022 | −7.7% [−49.2, +40.2] | +35.0% [−38.8, +129.2] | pm4 −11.2%, pm7 −13.6% |
| A Polymarket 1d | favorite | 172 | 0.964 | 0.965 | +0.2% [−3.1, +2.9] | −0.7% [−4.9, +2.8] | pm4 +0.1%, pm7 −0.0% |
| B Kalshi BTC15 10m | longshot | 441 | 0.072 | 0.066 | −10.5% [−41.8, +23.9] | −25.5% [−54.7, +8.7] | kx −22.1% [−49.8, +7.4] |
| B Kalshi BTC15 10m | favorite | 556 | 0.924 | 0.932 | +0.9% [−1.5, +3.0] | +1.0% [−1.5, +3.3] | kx −0.2% [−2.5, +1.9] |
| B Kalshi BTC15 5m | longshot | 2892 | 0.043 | 0.036 | −11.7% [−31.7, +10.8] | −9.4% [−30.7, +15.4] | kx −33.5% [−47.0, −18.9] |
| B Kalshi BTC15 5m | favorite | 3045 | 0.956 | 0.959 | +0.4% [−0.4, +1.1] | +0.3% [−0.5, +1.1] | kx −0.7% [−1.4, +0.1] |
| B Kalshi BTC15 1m | longshot | 5555 | 0.009 | 0.008 | −59.4% [−81.9, −28.2] | −57.5% [−81.9, −25.3] | kx −81.8% [−88.3, −74.2] |
| B Kalshi BTC15 1m | favorite | 3144 | 0.985 | 0.985 | +0.0% [−0.4, +0.5] | +0.1% [−0.4, +0.5] | kx −1.0% [−1.4, −0.5] |
| C Polymarket book, open+5m | longshot | 163 | 0.049 | 0.037 | −26.2% [−81.6, +47.7] | −51.3% [−90.5, +5.2] | pm7 −30.8% |
| C Polymarket book, open+5m | favorite | 178 | 0.962 | 0.949 | −1.3% [−5.0, +1.8] | −1.1% [−5.4, +2.2] | pm7 −1.6% |
| D Polymarket 5m crypto 4m | longshot | 1049 | 0.075 | 0.036 | −53.2% [−68.1, −36.4] | −55.6% [−72.6, −36.2] | pm7 −56.0% [−69.8, −40.0] |
| D Polymarket 5m crypto 4m | favorite | 1450 | 0.930 | 0.952 | +2.3% [+1.1, +3.5] | +3.1% [+2.0, +4.3] | pm7 +1.8% [+0.6, +3.0] |
| D Polymarket 5m crypto 2m | longshot | 21505 | 0.055 | 0.030 | −47.5% [−52.3, −42.4] | −48.4% [−55.4, −41.2] | pm7 −50.8% [−55.6, −45.8] |
| D Polymarket 5m crypto 2m | favorite | 22506 | 0.952 | 0.964 | +1.3% [+1.0, +1.6] | +1.5% [+1.0, +1.9] | pm7 +1.0% [+0.7, +1.2] |
| D Polymarket 5m crypto 1m | longshot | 40770 | 0.038 | 0.017 | −63.6% [−67.0, −60.0] | −61.4% [−67.4, −54.9] | pm7 −65.9% [−69.1, −62.6] |
| D Polymarket 5m crypto 1m | favorite | 33307 | 0.964 | 0.977 | +1.3% [+1.2, +1.5] | +1.3% [+1.0, +1.6] | pm7 +1.1% [+0.9, +1.2] |
| D Polymarket 5m crypto 30s | longshot | 54107 | 0.028 | 0.010 | −72.3% [−75.3, −69.1] | −70.6% [−75.6, −65.2] | pm7 −74.0% [−76.9, −71.0] |
| D Polymarket 5m crypto 30s | favorite | 32519 | 0.971 | 0.982 | +1.2% [+1.0, +1.3] | +0.8% [+0.0, +1.4] | pm7 +1.0% [+0.8, +1.1] |
| E Polymarket crypto 5m:4m | longshot | 16 | 0.085 | 0.000 | −100.0% [−100.0, −100.0] | −100.0% [−100.0, −100.0] | pm7 −100.0% [−100.0, −100.0] |
| E Polymarket crypto 5m:4m | favorite | 21 | 0.930 | 0.952 | +2.3% [−8.3, +8.1] | +1.5% [−10.9, +8.2] | pm7 +1.8% [−8.8, +7.5] |
| E Polymarket crypto 5m:2m | longshot | 148 | 0.053 | 0.047 | −16.3% [−75.6, +55.8] | −26.6% [−74.3, +33.9] | pm7 −21.5% [−77.1, +48.9] |
| E Polymarket crypto 5m:2m | favorite | 157 | 0.961 | 0.962 | +0.2% [−3.3, +3.0] | −0.2% [−4.4, +3.2] | pm7 −0.1% [−3.5, +2.7] |
| E Polymarket crypto 5m:1m | longshot | 146 | 0.038 | 0.068 | +28.7% [−50.3, +115.6] | +46.1% [−47.8, +174.6] | pm7 +20.7% [−50.0, +109.5] |
| E Polymarket crypto 5m:1m | favorite | 145 | 0.979 | 0.945 | −3.5% [−7.7, +0.1] | −4.7% [−10.6, +0.3] | pm7 −3.6% [−7.9, −0.1] |
| E Polymarket crypto 5m:30s | longshot | 56 | 0.035 | 0.036 | −42.0% [−100.0, +51.8] | −14.5% [−100.0, +123.7] | pm7 −45.5% [−100.0, +42.4] |
| E Polymarket crypto 5m:30s | favorite | 56 | 0.982 | 0.964 | −1.8% [−7.4, +2.1] | −3.2% [−10.9, +2.1] | pm7 −1.9% [−7.5, +1.9] |
| E Polymarket crypto 15m:10m | longshot | 26 | 0.058 | 0.000 | −100.0% [−100.0, −100.0] | −100.0% [−100.0, −100.0] | pm7 −100.0% [−100.0, −100.0] |
| E Polymarket crypto 15m:10m | favorite | 33 | 0.949 | 0.939 | −1.3% [−10.9, +5.5] | −2.7% [−15.1, +5.9] | pm7 −1.6% [−11.2, +5.2] |
| E Polymarket crypto 15m:5m | longshot | 84 | 0.044 | 0.024 | −66.9% [−100.0, −14.0] | −58.1% [−100.0, +15.3] | pm7 −68.9% [−100.0, −19.3] |
| E Polymarket crypto 15m:5m | favorite | 93 | 0.968 | 0.978 | +1.1% [−2.4, +3.6] | +0.4% [−4.5, +3.5] | pm7 +0.8% [−2.7, +3.3] |
| E Polymarket crypto 15m:1m | longshot | 69 | 0.024 | 0.000 | −100.0% [−100.0, −100.0] | −100.0% [−100.0, −100.0] | pm7 −100.0% [−100.0, −100.0] |
| E Polymarket crypto 15m:1m | favorite | 71 | 0.988 | 1.000 | +1.2% [+0.9, +1.6] | +1.2% [+0.9, +1.5] | pm7 +1.1% [+0.8, +1.5] |
| E Polymarket crypto 4h:3h | longshot | 2 | 0.035 | 0.000 | −100.0% [−100.0, −100.0] | −100.0% [–, –] | pm7 −100.0% [−100.0, −100.0] |
| E Polymarket crypto 4h:3h | favorite | 5 | 0.930 | 0.800 | −14.5% [−57.6, +9.7] | −4.9% [−20.9, +11.1] | pm7 −14.9% [−57.7, +9.0] |
| E Polymarket crypto 4h:1h | longshot | 24 | 0.042 | 0.000 | −100.0% [−100.0, −100.0] | −100.0% [−100.0, −100.0] | pm7 −100.0% [−100.0, −100.0] |
| E Polymarket crypto 4h:1h | favorite | 24 | 0.974 | 1.000 | +2.7% [+1.9, +3.8] | +2.6% [+1.8, +3.6] | pm7 +2.5% [+1.7, +3.5] |
| E Polymarket crypto 4h:30m | longshot | 29 | 0.034 | 0.000 | −100.0% [−100.0, −100.0] | −100.0% [−100.0, −100.0] | pm7 −100.0% [−100.0, −100.0] |
| E Polymarket crypto 4h:30m | favorite | 31 | 0.978 | 1.000 | +2.3% [+1.4, +3.3] | +2.7% [+1.4, +4.1] | pm7 +2.1% [+1.3, +3.1] |
| E Polymarket crypto 4h:5m | longshot | 26 | 0.022 | 0.000 | −100.0% [−100.0, −100.0] | −100.0% [−100.0, −100.0] | pm7 −100.0% [−100.0, −100.0] |
| E Polymarket crypto 4h:5m | favorite | 26 | 0.989 | 1.000 | +1.1% [+0.6, +1.8] | +1.1% [+0.6, +1.6] | pm7 +1.0% [+0.5, +1.7] |
| F World Cup 7d | longshot | 949 | 0.046 | 0.031 | −32.4% [−62.8, +6.6] | −48.2% [−70.9, −20.3] | pm7 −36.6% [−65.2, +0.1] |
| F World Cup 7d | favorite | 1077 | 0.967 | 0.960 | −0.7% [−1.9, +0.4] | +0.2% [−1.4, +1.6] | pm7 −1.0% [−2.2, +0.2] |
| F World Cup 1d | longshot | 4482 | 0.039 | 0.026 | −22.2% [−55.6, +34.7] | +21.3% [−41.8, +104.7] | pm7 −27.2% [−57.5, +22.1] |
| F World Cup 1d | favorite | 4828 | 0.970 | 0.968 | −0.2% [−0.7, +0.3] | −1.1% [−2.7, +0.4] | pm7 −0.4% [−1.0, +0.1] |
| F World Cup 1h | longshot | 6932 | 0.038 | 0.023 | −49.8% [−59.7, −39.0] | −22.7% [−45.8, +3.0] | pm7 −52.9% [−62.4, −42.8] |
| F World Cup 1h | favorite | 7290 | 0.974 | 0.972 | −0.3% [−0.7, +0.1] | −0.6% [−2.2, +0.8] | pm7 −0.5% [−0.9, −0.1] |

A uses reported prices (no spread); B–F use the ask actually payable. E's 15m:14m bins are empty after the spread filter.

D null-outcome bounds (gross EW, market resample; every null-outcome contract set to lose / win):

| horizon | bin | null share | outcome known | nulls lose | nulls win |
|---|---|---|---|---|---|
| 4m | longshot | 0.037 | −53.2 [−67.8, −37.0] | −54.9 [−69.4, −39.2] | −9.2 [−29.0, +11.8] |
| 4m | favorite | 0.042 | +2.3 [+1.1, +3.5] | −2.0 [−3.6, −0.5] | +2.6 [+1.4, +3.7] |
| 2m | longshot | 0.035 | −47.5 [−52.5, −42.5] | −49.3 [−54.0, −44.3] | +15.1 [+8.0, +22.5] |
| 2m | favorite | 0.041 | +1.3 [+1.1, +1.6] | −2.8 [−3.2, −2.5] | +1.5 [+1.3, +1.8] |
| 1m | longshot | 0.037 | −63.6 [−67.0, −60.1] | −64.9 [−68.2, −61.4] | +28.6 [+20.8, +37.2] |
| 1m | favorite | 0.049 | +1.3 [+1.2, +1.5] | −3.6 [−3.9, −3.3] | +1.5 [+1.4, +1.7] |
| 30s | longshot | 0.037 | −72.3 [−75.3, −69.1] | −73.3 [−76.3, −70.3] | +57.6 [+47.4, +68.6] |
| 30s | favorite | 0.059 | +1.2 [+1.0, +1.3] | −4.8 [−5.1, −4.5] | +1.4 [+1.2, +1.5] |

The D favourite gain survives only if the null-outcome close calls mostly went the favourite's way; the bounds straddle zero. Full decile + fine-tail tables follow.

### Calibration slope (logit(P(win)) = a + b·logit(price); b > 1 = classic favorite-longshot bias)

| sample | n | Brier | b [95% CI, event resample] |
|---|---|---|---|
| A Polymarket 30d | 1835 | 0.0785 | 0.92 [0.82, 1.03] |
| A Polymarket 7d | 3757 | 0.1341 | 0.84 [0.77, 0.92] |
| A Polymarket 1d | 4795 | 0.1484 | 0.82 [0.76, 0.89] |
| B Kalshi BTC15 10m (YES mid) | 6330 | 0.1852 | 1.12 [1.05, 1.19] |
| B Kalshi BTC15 5m | 6329 | 0.1141 | 1.05 [1.00, 1.10] |
| B Kalshi BTC15 1m | 3875 | 0.0439 | 1.08 [1.00, 1.19] |
| C Polymarket book open+5m (mid) | 752 | 0.1775 | 0.98 [0.82, 1.21] |
| D Polymarket 5m crypto 4m | 54241 | 0.2046 | 1.16 [1.11, 1.20] |
| D Polymarket 5m crypto 2m | 57540 | 0.1222 | 1.18 [1.15, 1.21] |
| D Polymarket 5m crypto 1m | 54701 | 0.0806 | 1.21 [1.18, 1.24] |
| D Polymarket 5m crypto 30s | 45218 | 0.0567 | 1.27 [1.24, 1.31] |
| E Polymarket crypto 5m:4m | 303 | 0.1898 | 1.19 [0.88, 1.63] |
| E Polymarket crypto 5m:2m | 280 | 0.1029 | 1.01 [0.83, 1.30] |
| E Polymarket crypto 5m:1m | 185 | 0.0830 | 0.81 [0.66, 1.06] |
| E Polymarket crypto 5m:30s | 70 | 0.0718 | 0.99 [0.73, 1.86] |
| E Polymarket crypto 15m:14m | 198 | 0.2413 | 0.80 [0.10, 1.63] |
| E Polymarket crypto 15m:10m | 183 | 0.1872 | 0.91 [0.63, 1.31] |
| E Polymarket crypto 15m:5m | 178 | 0.0931 | 1.28 [0.99, 1.94] |
| E Polymarket crypto 15m:1m | 83 | 0.0163 | 3.24 [1.99, 3.82] |
| E Polymarket crypto 4h:3h | 51 | 0.2346 | 0.38 [−0.44, 1.74] |
| E Polymarket crypto 4h:1h | 46 | 0.1071 | 1.07 [0.75, 2.80] |
| E Polymarket crypto 4h:30m | 41 | 0.0436 | 1.57 [1.19, 8.27] |
| E Polymarket crypto 4h:5m | 28 | 0.0294 | 1.55 [1.08, 2.03] |
| F World Cup 7d | 2052 | 0.1159 | 1.03 [0.92, 1.17] |
| F World Cup 1d | 10074 | 0.1171 | 1.05 [0.98, 1.12] |
| F World Cup 1h | 14157 | 0.1080 | 1.03 [0.96, 1.09] |
| F World Cup 1h, Yes/No markets | 8780 | 0.0768 | 0.98 [0.92, 1.05] |
| F World Cup 1h, other two-way (Over/Under, team v team, …) | 5377 | 0.1589 | 1.03 [0.91, 1.15] |

## Interpretation (honest)
- **Favorites (p ≥ 0.90): no edge anywhere.** All datasets and horizons give returns within about ±1–2% of zero, with CIs spanning zero;
  after Kalshi's per-contract fee the 1m and 5m favorites are slightly negative. "Buy near-certain contracts" does not survive the data here.
- **Longshots (p ≤ 0.10): too noisy to sign on Polymarket; negative at short horizons on Kalshi.** On Kalshi BTC15, buying at the ask
  loses money (−59% at 1m, CI excludes 0; −10% to −12% at 5–10m, CIs include 0). Kalshi's ceil-to-cent fee makes every
  longshot bin clearly negative after fees. On Polymarket (A) the longshot EW mean flips sign across horizons and every CI is
  60–100+ points wide, because a few sub-2¢ winners dominate.
- **Calibration differs by venue/data type.** Kalshi BTC15 midpoints show mild classic FLB (b ≈ 1.05–1.12). Polymarket
  daily prices show b < 1 at 7d/1d (mid-priced 0.10–0.30 contracts won more than their price at 7d), which is the
  opposite. That may be an artifact of the price field: midpoint/last trade in thin books, with no spread available to check it.
  Treat it as a hypothesis to re-test on executable quotes, not a signal.
- **Kaggle additions (D–F).** On the World Cup set (F: official results, executable quotes, ~900 events) favourites are
  flat: −0.7% / −0.2% / −0.3% gross at 7d / 1d / 1h with CIs spanning zero, and slightly negative after a pm7 fee. Slopes are
  1.03–1.05 with CIs that include 1, i.e. no detectable bias either way. 1h longshots lose −50% [−60, −39] with a market
  resample but only −23% [−46, +3] with an event resample, because outcomes within a match are correlated.
  In 5-minute crypto (D) favourites show +1.2–2.3% gross with tight CIs and the slope is 1.16–1.27, but D's labels are
  inferred from the last tick and the dropped null-outcome markets are exactly the close calls. With every null-outcome
  contract set to lose, the favourite bins go to −2.0% to −4.8%. D cannot sign the favourite return, and its slope is
  probably inflated by the same selection. E (official winners, 4 days, ≤157 contracts per cell) is too small to
  sign anything; its cells with 100% win rates rest on 24–71 contracts.
- **Look-ahead matters more than fees.** Anchoring horizons on the realised resolution time inflates the apparent longshot edge.
  An agent backtesting FLB in oraclebook should anchor on scheduled close times only.

## Limitations
A has no bid/ask, no volume, daily granularity (no 1h horizon), no event ids (the event clusters are a proxy), and only
markets that resolved and that Pancake catalogued. Planned end dates are read from a 2026 snapshot and may have been edited after the fact. B is a single 67-day crypto series in
which adjacent markets are correlated (only 67 day-clusters). C is 7 vendor-chosen days. D's outcomes are the publisher's
inference from the final tick (not the official resolution) and miss 12% of markets non-randomly. E covers 4 days and fewer than
700 usable markets. F is a single tournament whose markets are mostly from one vendor's non-commercial capture; its bars print
only on quote changes, so a quote can be up to 6 h old. Many bins and horizons were tested, so expect some nominal
"significant" cells by chance. Nothing here shows a profitable strategy.

---
## A. Polymarket — usepancake/polymarket-history (daily 00:00 UTC price samples)

Primary spec: one contract per market (first-listed outcome, i.e. `Yes` for Yes/No markets), price = last daily bar released at or before `T − horizon`, where T = the market's planned end date (ex-ante), and the market must still be unresolved at `T − horizon`. Buy price = reported price (no spread available). Bars staler than 2.5 days at the target time are dropped. Rows labelled 'biased anchor' instead measure the horizon back from the realised resolution time (or min(resolution, planned end)); that conditions on when the outcome happened and is shown only to quantify the look-ahead it introduces.

### Calibration (logistic regression of outcome on logit(price); b>1 ⇒ prices too compressed toward 0.5, i.e. favorite-longshot bias; b<1 ⇒ prices too extreme)

| sample | n | markets | events | mean price | outcome rate | Brier | slope b [95% CI event resample] | slope CI (market resample) | intercept a [95% CI] |
|---|---|---|---|---|---|---|---|---|---|
| Polymarket 30d | 1835 | 1835 | 428 | 0.165 | 0.169 | 0.0785 | 0.916 [0.816, 1.029] | [0.834, 1.022] | -0.016 [-0.224, +0.195] |
| Polymarket 7d | 3757 | 3757 | 873 | 0.284 | 0.294 | 0.1341 | 0.841 [0.770, 0.920] | [0.783, 0.910] | +0.024 [-0.067, +0.110] |
| Polymarket 1d | 4795 | 4795 | 940 | 0.327 | 0.333 | 0.1484 | 0.822 [0.756, 0.892] | [0.769, 0.882] | +0.007 [-0.060, +0.077] |
| Polymarket 7d (biased anchor = min(resolved, planned)) | 4332 | 4332 | 1003 | 0.228 | 0.255 | 0.1049 | 0.889 [0.828, 0.959] | [0.837, 0.949] | +0.226 [+0.120, +0.336] |
| Polymarket 7d (biased anchor = resolved_at) | 4451 | 4451 | 1032 | 0.226 | 0.255 | 0.1009 | 0.887 [0.830, 0.949] | [0.835, 0.946] | +0.247 [+0.143, +0.356] |
| Polymarket 1d (biased anchor = min(resolved, planned)) | 6920 | 6920 | 1408 | 0.315 | 0.329 | 0.1348 | 0.867 [0.807, 0.933] | [0.822, 0.919] | +0.072 [+0.007, +0.136] |
| Polymarket 1d (biased anchor = resolved_at) | 7056 | 7056 | 1435 | 0.318 | 0.331 | 0.1270 | 0.895 [0.839, 0.961] | [0.849, 0.948] | +0.086 [+0.019, +0.155] |
| Polymarket 7d sports | 1323 | 1323 | 378 | 0.469 | 0.461 | 0.2201 | 0.797 [0.669, 0.963] | [0.661, 0.958] | -0.043 [-0.159, +0.069] |
| Polymarket 7d non-sports | 2434 | 2434 | 495 | 0.183 | 0.203 | 0.0874 | 0.879 [0.793, 0.976] | [0.807, 0.960] | +0.150 [+0.000, +0.294] |

### Polymarket horizon 30d: bins (deciles, then fine tails)

| bin | n contracts | n markets | n events | mean buy price | win rate | return/$ EW [95% CI, market resample] | EW CI (event resample) | event-clustered mean [95% CI] | EW after pm4 | EW after pm7 |
|---|---|---|---|---|---|---|---|---|---|---|
| [0.00,0.10) | 1205 | 1205 | 276 | 0.019 | 0.022 | -33.1% [-67.7%, +11.4%] | [-67.0%, +15.5%] | +92.0% [-29.8%, +267.7%] | -35.5% [-67.7%, +8.6%] | -37.3% [-69.7%, +4.9%] |
| [0.10,0.20) | 164 | 164 | 109 | 0.144 | 0.207 | +44.0% [-0.5%, +90.7%] | [+0.6%, +95.3%] | +80.5% [+24.4%, +139.2%] | +39.3% [-3.0%, +84.8%] | +35.9% [-4.2%, +78.2%] |
| [0.20,0.30) | 99 | 99 | 80 | 0.252 | 0.273 | +5.1% [-27.6%, +39.9%] | [-31.2%, +41.7%] | +11.7% [-24.4%, +51.6%] | +2.1% [-31.1%, +37.3%] | -0.1% [-31.8%, +33.5%] |
| [0.30,0.40) | 72 | 72 | 58 | 0.350 | 0.361 | +3.5% [-27.9%, +35.3%] | [-30.8%, +39.7%] | +6.1% [-28.1%, +41.2%] | +0.9% [-30.0%, +31.9%] | -1.0% [-30.8%, +29.4%] |
| [0.40,0.50) | 50 | 50 | 35 | 0.445 | 0.400 | -9.1% [-40.4%, +22.1%] | [-39.2%, +21.2%] | -3.3% [-38.0%, +32.6%] | -11.1% [-42.2%, +20.3%] | -12.6% [-41.9%, +18.1%] |
| [0.50,0.60) | 53 | 53 | 45 | 0.541 | 0.453 | -16.4% [-40.6%, +9.3%] | [-39.7%, +7.2%] | -19.5% [-44.4%, +7.4%] | -17.9% [-42.0%, +6.4%] | -19.0% [-42.6%, +4.6%] |
| [0.60,0.70) | 43 | 43 | 36 | 0.644 | 0.767 | +19.5% [-2.1%, +38.0%] | [-0.9%, +37.5%] | +15.3% [-7.2%, +35.9%] | +17.8% [-3.1%, +36.2%] | +16.6% [-2.1%, +34.8%] |
| [0.70,0.80) | 44 | 44 | 40 | 0.738 | 0.636 | -14.2% [-33.0%, +4.1%] | [-35.5%, +6.1%] | -16.0% [-36.4%, +4.1%] | -15.1% [-34.0%, +3.4%] | -15.7% [-34.0%, +2.7%] |
| [0.80,0.90) | 50 | 50 | 41 | 0.850 | 0.760 | -10.7% [-24.8%, +2.9%] | [-25.5%, +1.8%] | -12.1% [-27.9%, +2.8%] | -11.2% [-25.3%, +2.3%] | -11.6% [-25.9%, +2.0%] |
| [0.90,1.00) | 55 | 55 | 41 | 0.954 | 0.964 | +1.0% [-5.0%, +5.2%] | [-5.5%, +5.2%] | -0.7% [-8.5%, +4.8%] | +0.8% [-5.1%, +5.0%] | +0.7% [-5.2%, +4.9%] |
| [0.00,0.01) | 653 | 653 | 145 | 0.003 | 0.002 | -69.4% [-100.0%, -8.1%] | [-100.0%, +8.9%] | +37.9% [-100.0%, +313.8%] | -70.5% [-100.0%, -11.6%] | -71.4% [-100.0%, -14.1%] |
| [0.01,0.02) | 174 | 174 | 93 | 0.014 | 0.006 | -45.3% [-100.0%, +64.2%] | [-100.0%, +74.2%] | -74.4% [-100.0%, -23.2%] | -47.3% [-100.0%, +58.0%] | -48.8% [-100.0%, +53.6%] |
| [0.02,0.05) | 211 | 211 | 107 | 0.031 | 0.043 | +42.4% [-40.0%, +145.5%] | [-43.2%, +156.0%] | +165.8% [+7.8%, +357.8%] | +37.1% [-42.0%, +135.7%] | +33.4% [-47.2%, +126.5%] |
| [0.05,0.10) | 167 | 167 | 102 | 0.071 | 0.096 | +26.3% [-29.1%, +87.8%] | [-36.2%, +103.2%] | +53.6% [-22.5%, +138.8%] | +21.8% [-32.8%, +81.7%] | +18.6% [-33.1%, +75.6%] |
| [0.90,0.95) | 22 | 22 | 17 | 0.919 | 0.909 | -1.0% [-15.9%, +9.2%] | [-17.5%, +9.1%] | -4.0% [-23.3%, +9.1%] | -1.3% [-16.1%, +8.9%] | -1.5% [-16.3%, +8.6%] |
| [0.95,0.98) | 17 | 17 | 14 | 0.968 | 1.000 | +3.4% [+3.0%, +3.7%] | [+3.0%, +3.8%] | +3.4% [+2.9%, +3.8%] | +3.2% [+2.9%, +3.6%] | +3.1% [+2.8%, +3.5%] |
| [0.98,0.99) | 9 | 9 | 8 | 0.985 | 1.000 | +1.6% [+1.4%, +1.7%] | [+1.4%, +1.7%] | +1.5% [+1.4%, +1.7%] | +1.5% [+1.3%, +1.7%] | +1.5% [+1.3%, +1.6%] |
| [0.99,1.00) | 7 | 7 | 7 | 0.995 | 1.000 | +0.6% [+0.3%, +0.8%] | [+0.3%, +0.8%] | +0.6% [+0.3%, +0.8%] | +0.5% [+0.3%, +0.8%] | +0.5% [+0.3%, +0.8%] |

Headline (longshot p≤0.10, favorite p≥0.90), horizon 30d:

| bin | n contracts | n markets | n events | mean buy price | win rate | return/$ EW [95% CI, market resample] | EW CI (event resample) | event-clustered mean [95% CI] | EW after pm4 | EW after pm7 |
|---|---|---|---|---|---|---|---|---|---|---|
| (0.00,0.10] | 1211 | 1211 | 278 | 0.019 | 0.023 | -32.6% [-66.5%, +13.4%] | [-65.5%, +15.9%] | +92.4% [-26.4%, +271.9%] | -35.1% [-67.1%, +9.9%] | -36.8% [-68.7%, +5.9%] |
| [0.90,1.00] | 55 | 55 | 41 | 0.954 | 0.964 | +1.0% [-5.1%, +5.2%] | [-5.5%, +5.2%] | -0.7% [-8.6%, +4.8%] | +0.8% [-5.2%, +5.0%] | +0.7% [-5.3%, +4.8%] |

Robustness — both legs of every market (each market contributes p and 1−p), 30d:

| bin | n contracts | n markets | n events | mean buy price | win rate | return/$ EW [95% CI, market resample] | EW CI (event resample) | event-clustered mean [95% CI] | EW after pm4 | EW after pm7 |
|---|---|---|---|---|---|---|---|---|---|---|
| (0.00,0.10] | 1267 | 1267 | 291 | 0.020 | 0.024 | -33.4% [-66.2%, +9.9%] | [-67.0%, +14.0%] | +88.8% [-27.5%, +263.0%] | -35.9% [-67.2%, +7.1%] | -37.6% [-68.3%, +2.4%] |
| [0.90,1.00] | 1263 | 1263 | 289 | 0.980 | 0.977 | -0.3% [-1.2%, +0.6%] | [-1.4%, +0.6%] | -3.3% [-6.1%, -0.8%] | -0.4% [-1.3%, +0.5%] | -0.4% [-1.3%, +0.4%] |

Biased comparison — anchor = min(resolved_at, planned end) (look-ahead via resolution timing), 30d:

| bin | n contracts | n markets | n events | mean buy price | win rate | return/$ EW [95% CI, market resample] | EW CI (event resample) | event-clustered mean [95% CI] | EW after pm4 | EW after pm7 |
|---|---|---|---|---|---|---|---|---|---|---|
| [0.00,0.10) | 1699 | 1699 | 421 | 0.016 | 0.016 | -57.5% [-75.8%, -35.3%] | [-75.7%, -34.4%] | -7.9% [-52.0%, +43.4%] | -59.1% [-76.8%, -36.9%] | -60.2% [-77.8%, -38.8%] |
| [0.10,0.20) | 184 | 184 | 125 | 0.143 | 0.190 | +31.2% [-8.1%, +73.3%] | [-7.2%, +75.9%] | +51.4% [+4.4%, +103.2%] | +26.8% [-11.3%, +67.8%] | +23.8% [-11.7%, +63.0%] |
| [0.20,0.30) | 106 | 106 | 86 | 0.251 | 0.283 | +10.7% [-20.9%, +43.8%] | [-25.1%, +47.5%] | +19.3% [-17.6%, +57.9%] | +7.5% [-23.4%, +40.5%] | +5.2% [-26.1%, +38.7%] |
| [0.30,0.40) | 85 | 85 | 67 | 0.351 | 0.388 | +10.6% [-19.6%, +41.4%] | [-21.0%, +45.2%] | +18.4% [-14.0%, +52.2%] | +7.8% [-21.0%, +37.1%] | +5.8% [-21.2%, +34.9%] |
| [0.40,0.50) | 65 | 65 | 48 | 0.445 | 0.400 | -9.9% [-37.0%, +17.3%] | [-37.2%, +20.3%] | -3.8% [-35.2%, +28.4%] | -11.9% [-38.1%, +15.6%] | -13.3% [-38.2%, +12.2%] |
| [0.50,0.60) | 70 | 70 | 59 | 0.547 | 0.529 | -3.6% [-24.7%, +18.2%] | [-22.7%, +15.6%] | -3.8% [-25.8%, +18.0%] | -5.3% [-26.1%, +15.2%] | -6.5% [-26.8%, +13.8%] |
| [0.60,0.70) | 55 | 55 | 45 | 0.643 | 0.782 | +22.4% [+4.9%, +39.3%] | [+3.6%, +38.0%] | +16.9% [-3.6%, +35.0%] | +20.6% [+3.1%, +37.0%] | +19.4% [+2.3%, +34.4%] |
| [0.70,0.80) | 53 | 53 | 47 | 0.744 | 0.698 | -6.8% [-23.4%, +8.9%] | [-25.2%, +10.2%] | -7.9% [-26.0%, +8.9%] | -7.7% [-24.8%, +7.8%] | -8.4% [-25.6%, +6.8%] |
| [0.80,0.90) | 57 | 57 | 47 | 0.850 | 0.789 | -7.1% [-19.9%, +4.9%] | [-20.3%, +3.9%] | -8.2% [-23.2%, +4.7%] | -7.6% [-20.1%, +4.3%] | -8.0% [-20.5%, +4.1%] |
| [0.90,1.00) | 76 | 76 | 53 | 0.952 | 0.974 | +2.3% [-2.1%, +5.4%] | [-2.3%, +5.4%] | +1.3% [-4.8%, +5.6%] | +2.1% [-2.2%, +5.2%] | +2.0% [-2.3%, +5.1%] |
| [0.00,0.01) | 1041 | 1041 | 277 | 0.003 | 0.001 | -89.9% [-100.0%, -69.7%] | [-100.0%, -66.1%] | -90.5% [-100.0%, -71.5%] | -90.3% [-100.0%, -70.8%] | -90.5% [-100.0%, -71.6%] |
| [0.01,0.02) | 218 | 218 | 114 | 0.015 | 0.009 | -36.9% [-100.0%, +61.5%] | [-100.0%, +60.4%] | -35.6% [-100.0%, +93.2%] | -39.3% [-100.0%, +55.3%] | -41.0% [-100.0%, +58.0%] |
| [0.02,0.05) | 243 | 243 | 132 | 0.032 | 0.037 | +7.6% [-58.6%, +83.0%] | [-57.8%, +92.6%] | +76.3% [-31.5%, +213.8%] | +3.6% [-58.2%, +77.4%] | +0.8% [-59.0%, +73.0%] |
| [0.05,0.10) | 197 | 197 | 126 | 0.071 | 0.076 | +10.8% [-41.9%, +67.8%] | [-43.3%, +69.7%] | +37.5% [-32.0%, +111.8%] | +6.8% [-42.2%, +61.7%] | +4.0% [-43.4%, +59.8%] |
| [0.90,0.95) | 31 | 31 | 26 | 0.920 | 0.968 | +5.3% [-2.0%, +9.1%] | [-2.8%, +9.1%] | +4.5% [-4.2%, +9.1%] | +4.9% [-2.4%, +8.8%] | +4.7% [-2.5%, +8.5%] |
| [0.95,0.98) | 26 | 26 | 22 | 0.967 | 0.962 | -0.5% [-8.6%, +3.7%] | [-9.8%, +3.7%] | -1.2% [-10.8%, +3.8%] | -0.7% [-8.8%, +3.5%] | -0.8% [-8.8%, +3.4%] |
| [0.98,0.99) | 16 | 16 | 11 | 0.983 | 1.000 | +1.7% [+1.6%, +1.8%] | [+1.6%, +1.8%] | +1.7% [+1.5%, +1.8%] | +1.6% [+1.5%, +1.7%] | +1.6% [+1.5%, +1.7%] |
| [0.99,1.00) | 3 | 3 | 3 | 0.993 | 1.000 | +0.7% [+0.5%, +1.0%] | [+0.5%, +1.0%] | +0.7% [+0.5%, +1.0%] | +0.6% [+0.4%, +0.9%] | +0.6% [+0.4%, +0.9%] |

By category — sports, 30d:

| bin | n contracts | n markets | n events | mean buy price | win rate | return/$ EW [95% CI, market resample] | EW CI (event resample) | event-clustered mean [95% CI] | EW after pm4 | EW after pm7 |
|---|---|---|---|---|---|---|---|---|---|---|
| (0.00,0.10] | 154 | 154 | 53 | 0.019 | 0.019 | -74.3% [-100.0%, -41.4%] | [-100.0%, -34.7%] | -59.6% [-100.0%, +4.4%] | -75.2% [-100.0%, -43.0%] | -75.9% [-100.0%, -45.2%] |
| [0.90,1.00] | 5 | 5 | 5 | 0.990 | 1.000 | +1.0% [+0.5%, +1.5%] | [+0.5%, +1.5%] | +1.0% [+0.5%, +1.5%] | +1.0% [+0.4%, +1.5%] | +0.9% [+0.4%, +1.4%] |

By category — non-sports, 30d:

| bin | n contracts | n markets | n events | mean buy price | win rate | return/$ EW [95% CI, market resample] | EW CI (event resample) | event-clustered mean [95% CI] | EW after pm4 | EW after pm7 |
|---|---|---|---|---|---|---|---|---|---|---|
| (0.00,0.10] | 1057 | 1057 | 225 | 0.019 | 0.024 | -26.5% [-64.7%, +25.5%] | [-64.6%, +30.6%] | +128.3% [-18.6%, +351.8%] | -29.2% [-66.0%, +21.2%] | -31.1% [-66.5%, +18.5%] |
| [0.90,1.00] | 50 | 50 | 36 | 0.951 | 0.960 | +1.0% [-5.7%, +5.6%] | [-6.1%, +5.6%] | -1.0% [-9.8%, +5.4%] | +0.8% [-5.8%, +5.3%] | +0.6% [-5.9%, +5.2%] |

### Polymarket horizon 7d: bins (deciles, then fine tails)

| bin | n contracts | n markets | n events | mean buy price | win rate | return/$ EW [95% CI, market resample] | EW CI (event resample) | event-clustered mean [95% CI] | EW after pm4 | EW after pm7 |
|---|---|---|---|---|---|---|---|---|---|---|
| [0.00,0.10) | 1650 | 1650 | 339 | 0.019 | 0.032 | +25.6% [-21.9%, +81.9%] | [-23.0%, +89.9%] | +71.3% [+8.3%, +148.6%] | +20.9% [-25.5%, +76.1%] | +17.6% [-27.9%, +71.3%] |
| [0.10,0.20) | 230 | 230 | 146 | 0.142 | 0.209 | +48.8% [+12.3%, +88.7%] | [+9.6%, +91.6%] | +57.8% [+13.7%, +103.5%] | +43.8% [+7.9%, +81.0%] | +40.3% [+5.8%, +78.2%] |
| [0.20,0.30) | 185 | 185 | 147 | 0.250 | 0.314 | +27.8% [+1.5%, +56.1%] | [+0.2%, +57.9%] | +36.5% [+8.0%, +67.4%] | +24.1% [-2.9%, +52.3%] | +21.4% [-4.3%, +47.5%] |
| [0.30,0.40) | 270 | 270 | 207 | 0.356 | 0.385 | +8.6% [-7.5%, +24.8%] | [-7.5%, +24.6%] | +12.6% [-5.3%, +30.4%] | +5.9% [-9.7%, +22.1%] | +3.9% [-11.9%, +19.8%] |
| [0.40,0.50) | 413 | 413 | 277 | 0.450 | 0.465 | +3.4% [-7.5%, +14.2%] | [-7.4%, +14.4%] | +6.2% [-5.6%, +18.2%] | +1.2% [-9.1%, +11.9%] | -0.4% [-10.6%, +9.8%] |
| [0.50,0.60) | 428 | 428 | 266 | 0.542 | 0.507 | -6.2% [-14.9%, +2.8%] | [-14.7%, +2.6%] | -9.6% [-19.1%, +0.3%] | -7.9% [-16.7%, +0.7%] | -9.1% [-17.6%, -0.7%] |
| [0.60,0.70) | 227 | 227 | 177 | 0.645 | 0.608 | -6.0% [-16.3%, +3.8%] | [-16.1%, +3.5%] | -7.1% [-17.5%, +3.3%] | -7.3% [-17.2%, +2.6%] | -8.3% [-17.9%, +1.1%] |
| [0.70,0.80) | 159 | 159 | 130 | 0.748 | 0.748 | -0.1% [-9.1%, +8.7%] | [-9.9%, +9.1%] | +1.1% [-8.4%, +10.2%] | -1.1% [-10.5%, +7.4%] | -1.8% [-10.8%, +6.9%] |
| [0.80,0.90) | 87 | 87 | 77 | 0.847 | 0.828 | -2.3% [-12.1%, +6.9%] | [-11.9%, +6.3%] | -3.1% [-13.3%, +6.1%] | -2.9% [-12.6%, +6.3%] | -3.3% [-12.7%, +5.5%] |
| [0.90,1.00) | 108 | 108 | 79 | 0.958 | 0.954 | -0.7% [-5.1%, +3.1%] | [-5.5%, +3.1%] | -2.3% [-8.5%, +2.8%] | -0.8% [-5.5%, +3.0%] | -0.9% [-5.3%, +2.9%] |
| [0.00,0.01) | 954 | 954 | 200 | 0.003 | 0.006 | +1.4% [-73.8%, +92.0%] | [-72.8%, +101.0%] | +46.4% [-68.8%, +225.6%] | -2.5% [-74.0%, +85.2%] | -5.2% [-73.9%, +80.1%] |
| [0.01,0.02) | 195 | 195 | 114 | 0.015 | 0.010 | -31.2% [-100.0%, +77.0%] | [-100.0%, +80.7%] | +17.6% [-100.0%, +202.7%] | -33.9% [-100.0%, +70.2%] | -35.7% [-100.0%, +65.5%] |
| [0.02,0.05) | 254 | 254 | 145 | 0.032 | 0.067 | +123.0% [+25.8%, +231.6%] | [+22.0%, +241.9%] | +113.7% [+6.4%, +243.0%] | +114.7% [+19.1%, +223.5%] | +108.8% [+17.8%, +215.2%] |
| [0.05,0.10) | 247 | 247 | 143 | 0.071 | 0.113 | +63.8% [+7.0%, +124.2%] | [+13.9%, +118.9%] | +52.5% [-5.2%, +120.4%] | +57.9% [+4.2%, +115.0%] | +53.8% [+0.0%, +111.1%] |
| [0.90,0.95) | 41 | 41 | 34 | 0.925 | 0.878 | -5.2% [-16.2%, +5.2%] | [-17.8%, +5.0%] | -7.9% [-20.9%, +4.5%] | -5.5% [-16.3%, +4.9%] | -5.7% [-16.5%, +4.5%] |
| [0.95,0.98) | 30 | 30 | 28 | 0.966 | 1.000 | +3.5% [+3.2%, +3.9%] | [+3.2%, +3.9%] | +3.5% [+3.2%, +3.9%] | +3.4% [+3.1%, +3.7%] | +3.3% [+3.0%, +3.6%] |
| [0.98,0.99) | 17 | 17 | 16 | 0.985 | 1.000 | +1.5% [+1.4%, +1.7%] | [+1.4%, +1.7%] | +1.5% [+1.4%, +1.7%] | +1.5% [+1.3%, +1.6%] | +1.4% [+1.3%, +1.5%] |
| [0.99,1.00) | 20 | 20 | 15 | 0.994 | 1.000 | +0.6% [+0.5%, +0.7%] | [+0.5%, +0.7%] | +0.6% [+0.5%, +0.8%] | +0.6% [+0.5%, +0.7%] | +0.6% [+0.5%, +0.7%] |

Headline (longshot p≤0.10, favorite p≥0.90), horizon 7d:

| bin | n contracts | n markets | n events | mean buy price | win rate | return/$ EW [95% CI, market resample] | EW CI (event resample) | event-clustered mean [95% CI] | EW after pm4 | EW after pm7 |
|---|---|---|---|---|---|---|---|---|---|---|
| (0.00,0.10] | 1659 | 1659 | 342 | 0.019 | 0.033 | +25.5% [-22.4%, +82.7%] | [-23.7%, +89.5%] | +72.4% [+11.6%, +147.6%] | +20.8% [-24.7%, +77.5%] | +17.5% [-27.3%, +70.6%] |
| [0.90,1.00] | 108 | 108 | 79 | 0.958 | 0.954 | -0.7% [-5.4%, +3.1%] | [-5.7%, +3.0%] | -2.3% [-8.7%, +2.8%] | -0.8% [-5.5%, +2.9%] | -0.9% [-5.4%, +2.8%] |

Robustness — both legs of every market (each market contributes p and 1−p), 7d:

| bin | n contracts | n markets | n events | mean buy price | win rate | return/$ EW [95% CI, market resample] | EW CI (event resample) | event-clustered mean [95% CI] | EW after pm4 | EW after pm7 |
|---|---|---|---|---|---|---|---|---|---|---|
| (0.00,0.10] | 1769 | 1769 | 372 | 0.021 | 0.033 | +20.8% [-24.5%, +74.8%] | [-25.2%, +76.4%] | +66.6% [+8.9%, +135.5%] | +16.3% [-27.7%, +70.3%] | +13.1% [-28.4%, +61.7%] |
| [0.90,1.00] | 1765 | 1765 | 366 | 0.979 | 0.967 | -1.4% [-2.2%, -0.5%] | [-2.4%, -0.5%] | -3.6% [-5.8%, -1.5%] | -1.4% [-2.3%, -0.6%] | -1.5% [-2.4%, -0.7%] |

Biased comparison — anchor = min(resolved_at, planned end) (look-ahead via resolution timing), 7d:

| bin | n contracts | n markets | n events | mean buy price | win rate | return/$ EW [95% CI, market resample] | EW CI (event resample) | event-clustered mean [95% CI] | EW after pm4 | EW after pm7 |
|---|---|---|---|---|---|---|---|---|---|---|
| [0.00,0.10) | 2432 | 2432 | 568 | 0.016 | 0.026 | +5.1% [-32.7%, +49.9%] | [-31.6%, +51.2%] | +60.6% [-8.2%, +151.3%] | +1.2% [-36.1%, +43.8%] | -1.6% [-35.9%, +40.5%] |
| [0.10,0.20) | 274 | 274 | 183 | 0.141 | 0.237 | +69.3% [+32.8%, +107.3%] | [+31.3%, +110.0%] | +84.7% [+42.7%, +128.5%] | +63.7% [+29.4%, +100.8%] | +59.7% [+25.8%, +95.0%] |
| [0.20,0.30) | 183 | 183 | 149 | 0.249 | 0.372 | +51.0% [+22.8%, +80.8%] | [+20.8%, +85.0%] | +63.4% [+34.0%, +96.1%] | +46.6% [+18.4%, +74.3%] | +43.4% [+15.7%, +70.8%] |
| [0.30,0.40) | 215 | 215 | 165 | 0.350 | 0.447 | +28.9% [+10.0%, +48.2%] | [+9.3%, +48.5%] | +33.2% [+12.6%, +53.9%] | +25.7% [+6.9%, +45.1%] | +23.3% [+4.9%, +41.8%] |
| [0.40,0.50) | 296 | 296 | 210 | 0.451 | 0.503 | +12.0% [-0.9%, +24.5%] | [-1.3%, +25.0%] | +16.8% [+2.7%, +30.6%] | +9.6% [-3.0%, +22.4%] | +7.8% [-4.5%, +19.9%] |
| [0.50,0.60) | 323 | 323 | 220 | 0.542 | 0.526 | -2.6% [-12.4%, +7.2%] | [-12.5%, +6.8%] | -4.0% [-15.4%, +6.9%] | -4.4% [-14.5%, +5.6%] | -5.7% [-15.4%, +4.0%] |
| [0.60,0.70) | 194 | 194 | 153 | 0.645 | 0.686 | +6.0% [-4.5%, +15.8%] | [-4.2%, +16.1%] | +4.9% [-6.0%, +15.8%] | +4.5% [-5.3%, +14.0%] | +3.4% [-6.2%, +13.1%] |
| [0.70,0.80) | 159 | 159 | 133 | 0.747 | 0.761 | +1.9% [-6.9%, +10.6%] | [-8.2%, +11.1%] | +3.7% [-6.0%, +12.5%] | +0.8% [-7.8%, +9.4%] | +0.1% [-9.3%, +8.8%] |
| [0.80,0.90) | 105 | 105 | 90 | 0.848 | 0.876 | +3.4% [-4.5%, +10.3%] | [-4.6%, +10.3%] | +2.6% [-6.0%, +10.3%] | +2.8% [-5.0%, +9.6%] | +2.3% [-5.4%, +9.3%] |
| [0.90,1.00) | 151 | 151 | 110 | 0.963 | 0.967 | +0.4% [-2.8%, +3.0%] | [-3.0%, +3.1%] | -0.9% [-5.3%, +2.8%] | +0.2% [-3.0%, +2.9%] | +0.1% [-3.2%, +2.8%] |
| [0.00,0.01) | 1569 | 1569 | 382 | 0.003 | 0.005 | -16.9% [-70.6%, +46.5%] | [-69.5%, +48.9%] | +23.7% [-69.2%, +154.1%] | -20.1% [-70.6%, +41.6%] | -22.3% [-70.9%, +37.6%] |
| [0.01,0.02) | 250 | 250 | 158 | 0.015 | 0.012 | -23.5% [-100.0%, +75.8%] | [-100.0%, +76.5%] | +21.0% [-100.0%, +178.2%] | -26.4% [-100.0%, +69.2%] | -28.4% [-100.0%, +64.5%] |
| [0.02,0.05) | 314 | 314 | 185 | 0.032 | 0.057 | +74.5% [-2.3%, +161.7%] | [-3.3%, +158.7%] | +43.6% [-27.7%, +125.5%] | +68.0% [-5.3%, +146.8%] | +63.4% [-6.7%, +141.3%] |
| [0.05,0.10) | 299 | 299 | 177 | 0.072 | 0.117 | +72.0% [+20.0%, +129.0%] | [+23.1%, +127.2%] | +72.1% [+11.6%, +139.7%] | +65.9% [+17.3%, +120.0%] | +61.5% [+12.1%, +113.2%] |
| [0.90,0.95) | 50 | 50 | 41 | 0.925 | 0.920 | -0.7% [-9.3%, +6.0%] | [-10.3%, +6.0%] | -2.6% [-13.2%, +5.5%] | -1.0% [-9.7%, +5.7%] | -1.2% [-9.8%, +5.4%] |
| [0.95,0.98) | 39 | 39 | 36 | 0.966 | 1.000 | +3.5% [+3.2%, +3.8%] | [+3.2%, +3.8%] | +3.5% [+3.2%, +3.8%] | +3.3% [+3.1%, +3.6%] | +3.2% [+3.0%, +3.5%] |
| [0.98,0.99) | 23 | 23 | 22 | 0.986 | 1.000 | +1.5% [+1.3%, +1.6%] | [+1.3%, +1.6%] | +1.5% [+1.3%, +1.6%] | +1.4% [+1.3%, +1.5%] | +1.4% [+1.3%, +1.5%] |
| [0.99,1.00) | 39 | 39 | 31 | 0.994 | 0.974 | -2.0% [-7.2%, +0.6%] | [-8.2%, +0.7%] | -2.6% [-9.2%, +0.7%] | -2.0% [-7.2%, +0.6%] | -2.0% [-7.2%, +0.6%] |

By category — sports, 7d:

| bin | n contracts | n markets | n events | mean buy price | win rate | return/$ EW [95% CI, market resample] | EW CI (event resample) | event-clustered mean [95% CI] | EW after pm4 | EW after pm7 |
|---|---|---|---|---|---|---|---|---|---|---|
| (0.00,0.10] | 87 | 87 | 27 | 0.015 | 0.023 | -69.3% [-100.0%, -20.8%] | [-100.0%, -11.7%] | -50.8% [-100.0%, +40.5%] | -70.4% [-100.0%, -23.7%] | -71.1% [-100.0%, -25.7%] |
| [0.90,1.00] | 15 | 15 | 15 | 0.939 | 0.867 | -8.1% [-29.4%, +6.7%] | [-29.1%, +6.8%] | -8.1% [-29.1%, +6.8%] | -8.3% [-29.6%, +6.4%] | -8.5% [-29.6%, +6.2%] |

By category — non-sports, 7d:

| bin | n contracts | n markets | n events | mean buy price | win rate | return/$ EW [95% CI, market resample] | EW CI (event resample) | event-clustered mean [95% CI] | EW after pm4 | EW after pm7 |
|---|---|---|---|---|---|---|---|---|---|---|
| (0.00,0.10] | 1572 | 1572 | 315 | 0.020 | 0.033 | +30.8% [-20.3%, +93.6%] | [-21.1%, +94.7%] | +83.0% [+16.2%, +164.8%] | +25.9% [-23.9%, +84.7%] | +22.4% [-26.0%, +78.0%] |
| [0.90,1.00] | 93 | 93 | 64 | 0.962 | 0.968 | +0.6% [-3.7%, +3.9%] | [-4.2%, +3.9%] | -0.9% [-7.2%, +3.9%] | +0.4% [-4.0%, +3.7%] | +0.3% [-4.0%, +3.6%] |

### Polymarket horizon 1d: bins (deciles, then fine tails)

| bin | n contracts | n markets | n events | mean buy price | win rate | return/$ EW [95% CI, market resample] | EW CI (event resample) | event-clustered mean [95% CI] | EW after pm4 | EW after pm7 |
|---|---|---|---|---|---|---|---|---|---|---|
| [0.00,0.10) | 1689 | 1689 | 368 | 0.014 | 0.022 | -7.3% [-47.5%, +40.8%] | [-51.1%, +46.9%] | +36.1% [-36.8%, +135.3%] | -10.8% [-49.7%, +34.6%] | -13.3% [-51.0%, +31.2%] |
| [0.10,0.20) | 302 | 302 | 214 | 0.149 | 0.215 | +45.9% [+14.3%, +78.2%] | [+13.9%, +80.1%] | +48.9% [+13.4%, +85.9%] | +41.1% [+10.8%, +73.0%] | +37.7% [+6.6%, +68.3%] |
| [0.20,0.30) | 334 | 334 | 234 | 0.251 | 0.290 | +18.6% [-1.2%, +39.0%] | [-3.6%, +41.2%] | +15.0% [-6.6%, +37.6%] | +15.1% [-3.9%, +34.8%] | +12.6% [-5.5%, +31.9%] |
| [0.30,0.40) | 391 | 391 | 250 | 0.353 | 0.394 | +12.6% [-1.2%, +26.4%] | [-0.7%, +25.9%] | +13.0% [-2.1%, +28.5%] | +9.8% [-4.1%, +23.6%] | +7.7% [-5.9%, +20.9%] |
| [0.40,0.50) | 578 | 578 | 315 | 0.453 | 0.483 | +6.5% [-2.5%, +15.4%] | [-2.7%, +15.5%] | +5.0% [-5.6%, +14.8%] | +4.2% [-4.5%, +12.7%] | +2.5% [-6.3%, +11.3%] |
| [0.50,0.60) | 597 | 597 | 339 | 0.539 | 0.526 | -2.3% [-9.7%, +5.3%] | [-9.3%, +4.8%] | -4.5% [-13.0%, +4.4%] | -4.1% [-11.5%, +3.5%] | -5.3% [-12.7%, +1.9%] |
| [0.60,0.70) | 328 | 328 | 203 | 0.645 | 0.604 | -6.4% [-14.8%, +2.0%] | [-14.4%, +1.4%] | -3.4% [-12.5%, +5.5%] | -7.7% [-15.7%, +0.3%] | -8.7% [-16.7%, -0.7%] |
| [0.70,0.80) | 267 | 267 | 196 | 0.748 | 0.670 | -10.4% [-18.1%, -3.0%] | [-17.8%, -3.0%] | -9.1% [-17.2%, -1.1%] | -11.3% [-18.7%, -3.9%] | -12.0% [-19.4%, -4.5%] |
| [0.80,0.90) | 137 | 137 | 117 | 0.846 | 0.796 | -5.9% [-13.9%, +1.8%] | [-14.2%, +1.8%] | -7.5% [-16.3%, +0.9%] | -6.5% [-14.6%, +1.4%] | -6.9% [-15.0%, +0.9%] |
| [0.90,1.00) | 172 | 172 | 128 | 0.964 | 0.965 | +0.2% [-2.9%, +2.8%] | [-3.1%, +2.9%] | -0.7% [-5.0%, +2.8%] | +0.1% [-3.0%, +2.6%] | -0.0% [-3.3%, +2.6%] |
| [0.00,0.01) | 1199 | 1199 | 235 | 0.002 | 0.003 | -47.2% [-89.6%, +11.8%] | [-89.5%, +14.2%] | +23.6% [-83.6%, +171.7%] | -49.2% [-90.0%, +7.5%] | -50.7% [-90.3%, +4.5%] |
| [0.01,0.02) | 132 | 132 | 93 | 0.014 | 0.030 | +164.3% [-45.9%, +458.4%] | [-44.6%, +447.8%] | +108.9% [-65.9%, +351.6%] | +154.2% [-47.9%, +419.8%] | +147.2% [-49.4%, +411.2%] |
| [0.02,0.05) | 175 | 175 | 125 | 0.032 | 0.040 | +55.0% [-43.1%, +179.0%] | [-77.5%, +235.4%] | +18.6% [-77.5%, +136.1%] | +49.1% [-51.8%, +170.9%] | +45.1% [-54.0%, +158.4%] |
| [0.05,0.10) | 183 | 183 | 125 | 0.071 | 0.120 | +70.6% [+5.1%, +140.7%] | [-8.0%, +169.1%] | +107.7% [+23.0%, +200.4%] | +64.5% [+1.5%, +129.7%] | +60.2% [-0.2%, +128.6%] |
| [0.90,0.95) | 57 | 57 | 51 | 0.925 | 0.947 | +2.5% [-5.0%, +8.2%] | [-4.6%, +8.2%] | +1.8% [-6.3%, +8.2%] | +2.2% [-5.2%, +7.7%] | +1.9% [-5.5%, +7.6%] |
| [0.95,0.98) | 39 | 39 | 34 | 0.965 | 0.974 | +1.0% [-4.5%, +3.9%] | [-5.2%, +3.9%] | +0.5% [-5.7%, +3.8%] | +0.9% [-4.6%, +3.7%] | +0.8% [-4.7%, +3.6%] |
| [0.98,0.99) | 23 | 23 | 22 | 0.984 | 0.913 | -7.2% [-20.5%, +1.7%] | [-21.5%, +1.7%] | -7.6% [-21.5%, +1.7%] | -7.3% [-20.6%, +1.6%] | -7.3% [-20.6%, +1.5%] |
| [0.99,1.00) | 53 | 53 | 35 | 0.996 | 1.000 | +0.4% [+0.4%, +0.5%] | [+0.4%, +0.5%] | +0.5% [+0.4%, +0.5%] | +0.4% [+0.4%, +0.5%] | +0.4% [+0.4%, +0.5%] |

Headline (longshot p≤0.10, favorite p≥0.90), horizon 1d:

| bin | n contracts | n markets | n events | mean buy price | win rate | return/$ EW [95% CI, market resample] | EW CI (event resample) | event-clustered mean [95% CI] | EW after pm4 | EW after pm7 |
|---|---|---|---|---|---|---|---|---|---|---|
| (0.00,0.10] | 1696 | 1696 | 371 | 0.014 | 0.022 | -7.7% [-49.2%, +40.2%] | [-51.2%, +47.9%] | +35.0% [-38.8%, +129.2%] | -11.2% [-50.1%, +34.9%] | -13.6% [-51.5%, +30.9%] |
| [0.90,1.00] | 172 | 172 | 128 | 0.964 | 0.965 | +0.2% [-3.1%, +2.9%] | [-3.1%, +2.8%] | -0.7% [-4.9%, +2.8%] | +0.1% [-3.1%, +2.6%] | -0.0% [-3.2%, +2.6%] |

Robustness — both legs of every market (each market contributes p and 1−p), 1d:

| bin | n contracts | n markets | n events | mean buy price | win rate | return/$ EW [95% CI, market resample] | EW CI (event resample) | event-clustered mean [95% CI] | EW after pm4 | EW after pm7 |
|---|---|---|---|---|---|---|---|---|---|---|
| (0.00,0.10] | 1867 | 1867 | 425 | 0.016 | 0.023 | -4.9% [-43.5%, +42.9%] | [-45.7%, +44.9%] | +44.7% [-28.3%, +135.1%] | -8.5% [-44.1%, +34.5%] | -11.0% [-47.5%, +31.4%] |
| [0.90,1.00] | 1879 | 1879 | 428 | 0.984 | 0.976 | -0.9% [-1.6%, -0.2%] | [-1.9%, +0.0%] | -1.2% [-3.0%, +0.5%] | -0.9% [-1.7%, -0.3%] | -1.0% [-1.7%, -0.3%] |

Biased comparison — anchor = min(resolved_at, planned end) (look-ahead via resolution timing), 1d:

| bin | n contracts | n markets | n events | mean buy price | win rate | return/$ EW [95% CI, market resample] | EW CI (event resample) | event-clustered mean [95% CI] | EW after pm4 | EW after pm7 |
|---|---|---|---|---|---|---|---|---|---|---|
| [0.00,0.10) | 2708 | 2708 | 702 | 0.012 | 0.018 | -22.6% [-54.6%, +14.0%] | [-56.1%, +20.0%] | +5.0% [-56.2%, +95.2%] | -25.5% [-56.4%, +8.8%] | -27.6% [-57.7%, +7.7%] |
| [0.10,0.20) | 406 | 406 | 291 | 0.149 | 0.234 | +55.8% [+28.2%, +85.5%] | [+27.1%, +86.2%] | +60.3% [+29.0%, +92.8%] | +50.7% [+23.6%, +78.3%] | +47.1% [+20.1%, +73.4%] |
| [0.20,0.30) | 412 | 412 | 291 | 0.252 | 0.299 | +22.1% [+3.9%, +40.8%] | [+2.3%, +42.5%] | +17.5% [-2.5%, +37.3%] | +18.5% [+1.1%, +36.7%] | +15.9% [-1.5%, +33.1%] |
| [0.30,0.40) | 543 | 543 | 358 | 0.354 | 0.407 | +15.7% [+3.7%, +27.3%] | [+4.4%, +27.0%] | +18.8% [+5.9%, +31.2%] | +12.8% [+1.4%, +24.2%] | +10.7% [-0.5%, +21.8%] |
| [0.40,0.50) | 772 | 772 | 432 | 0.451 | 0.484 | +7.4% [-0.6%, +15.2%] | [-0.1%, +15.0%] | +7.6% [-1.1%, +16.8%] | +5.1% [-2.4%, +12.6%] | +3.4% [-4.2%, +10.7%] |
| [0.50,0.60) | 768 | 768 | 432 | 0.540 | 0.536 | -0.6% [-7.3%, +5.8%] | [-6.9%, +5.6%] | -0.8% [-8.7%, +6.6%] | -2.4% [-9.0%, +4.0%] | -3.7% [-10.1%, +2.8%] |
| [0.60,0.70) | 439 | 439 | 281 | 0.645 | 0.610 | -5.3% [-12.5%, +1.6%] | [-12.1%, +1.5%] | -3.2% [-10.9%, +4.7%] | -6.7% [-13.8%, +0.2%] | -7.6% [-14.3%, -0.8%] |
| [0.70,0.80) | 347 | 347 | 263 | 0.748 | 0.712 | -4.9% [-11.1%, +1.2%] | [-11.2%, +1.4%] | -3.5% [-10.2%, +3.2%] | -5.8% [-12.0%, +0.5%] | -6.5% [-12.7%, -0.1%] |
| [0.80,0.90) | 176 | 176 | 150 | 0.846 | 0.824 | -2.7% [-9.5%, +3.8%] | [-9.8%, +3.7%] | -3.8% [-11.4%, +3.2%] | -3.3% [-10.1%, +3.2%] | -3.7% [-10.6%, +2.7%] |
| [0.90,1.00) | 349 | 349 | 266 | 0.971 | 0.980 | +1.0% [-0.6%, +2.5%] | [-0.7%, +2.5%] | +0.5% [-1.7%, +2.4%] | +0.9% [-0.7%, +2.3%] | +0.8% [-0.9%, +2.3%] |
| [0.00,0.01) | 2004 | 2004 | 504 | 0.002 | 0.003 | -49.7% [-87.5%, -3.4%] | [-87.1%, -3.0%] | +7.2% [-80.0%, +134.7%] | -51.6% [-86.6%, -8.4%] | -53.0% [-87.0%, -10.4%] |
| [0.01,0.02) | 192 | 192 | 144 | 0.014 | 0.026 | +121.8% [-54.7%, +340.6%] | [-53.3%, +330.3%] | +88.3% [-55.9%, +280.0%] | +113.3% [-56.4%, +319.8%] | +107.4% [-53.6%, +304.6%] |
| [0.02,0.05) | 258 | 258 | 184 | 0.032 | 0.023 | -16.1% [-76.6%, +60.7%] | [-77.5%, +59.8%] | -11.2% [-78.5%, +71.0%] | -19.3% [-78.2%, +53.6%] | -21.5% [-78.1%, +49.2%] |
| [0.05,0.10) | 254 | 254 | 179 | 0.071 | 0.122 | +75.6% [+19.2%, +134.0%] | [-0.7%, +164.6%] | +70.4% [+5.4%, +140.6%] | +69.3% [+13.8%, +127.6%] | +64.8% [+12.4%, +121.2%] |
| [0.90,0.95) | 89 | 89 | 82 | 0.926 | 0.966 | +4.4% [-0.2%, +8.0%] | [-0.3%, +8.1%] | +4.1% [-1.0%, +8.1%] | +4.1% [-0.6%, +7.7%] | +3.9% [-0.8%, +7.5%] |
| [0.95,0.98) | 72 | 72 | 63 | 0.966 | 0.972 | +0.7% [-3.7%, +3.7%] | [-3.9%, +3.7%] | +0.3% [-4.7%, +3.7%] | +0.6% [-3.9%, +3.5%] | +0.5% [-3.9%, +3.4%] |
| [0.98,0.99) | 39 | 39 | 38 | 0.985 | 0.949 | -3.7% [-11.5%, +1.6%] | [-11.8%, +1.6%] | -3.8% [-11.9%, +1.6%] | -3.7% [-11.6%, +1.5%] | -3.8% [-11.6%, +1.5%] |
| [0.99,1.00) | 149 | 149 | 117 | 0.996 | 1.000 | +0.4% [+0.3%, +0.4%] | [+0.3%, +0.4%] | +0.4% [+0.3%, +0.4%] | +0.4% [+0.3%, +0.4%] | +0.4% [+0.3%, +0.4%] |

By category — sports, 1d:

| bin | n contracts | n markets | n events | mean buy price | win rate | return/$ EW [95% CI, market resample] | EW CI (event resample) | event-clustered mean [95% CI] | EW after pm4 | EW after pm7 |
|---|---|---|---|---|---|---|---|---|---|---|
| (0.00,0.10] | 72 | 72 | 23 | 0.018 | 0.028 | -66.9% [-100.0%, -17.1%] | [-100.0%, +26.1%] | +3.5% [-100.0%, +159.5%] | -68.1% [-100.0%, -20.0%] | -68.9% [-100.0%, -22.1%] |
| [0.90,1.00] | 12 | 12 | 11 | 0.940 | 0.917 | -2.6% [-21.6%, +8.1%] | [-22.1%, +8.5%] | -2.9% [-23.2%, +8.7%] | -2.9% [-21.3%, +7.8%] | -3.0% [-21.8%, +7.5%] |

By category — non-sports, 1d:

| bin | n contracts | n markets | n events | mean buy price | win rate | return/$ EW [95% CI, market resample] | EW CI (event resample) | event-clustered mean [95% CI] | EW after pm4 | EW after pm7 |
|---|---|---|---|---|---|---|---|---|---|---|
| (0.00,0.10] | 1624 | 1624 | 348 | 0.014 | 0.022 | -5.1% [-47.2%, +43.5%] | [-49.3%, +50.8%] | +37.0% [-42.4%, +140.4%] | -8.7% [-48.7%, +40.0%] | -11.2% [-51.6%, +34.7%] |
| [0.90,1.00] | 160 | 160 | 117 | 0.965 | 0.969 | +0.4% [-2.7%, +3.0%] | [-2.9%, +3.0%] | -0.5% [-4.8%, +3.0%] | +0.3% [-2.9%, +2.8%] | +0.2% [-3.0%, +2.7%] |


## B. Kalshi — KXBTC15M (BTC up/down in 15 min), J0shusmc/Kalshi-BTC raw 1-minute candles

Both sides per market: buy YES at the YES ask, buy NO at 1 − YES bid (minute-close quotes), at h minutes before close. Calibration uses the YES quote midpoint (one row per market). Event cluster = UTC calendar day (adjacent 15-min BTC markets share price paths).

### Calibration (YES midpoint)

| sample | n | markets | events | mean price | outcome rate | Brier | slope b [95% CI event resample] | slope CI (market resample) | intercept a [95% CI] |
|---|---|---|---|---|---|---|---|---|---|
| Kalshi BTC15 14m before close | 6330 | 6330 | 67 | 0.498 | 0.498 | 0.2336 | 1.019 [0.917, 1.125] | [0.916, 1.123] | +0.003 [-0.041, +0.048] |
| Kalshi BTC15 10m before close | 6330 | 6330 | 67 | 0.499 | 0.498 | 0.1852 | 1.120 [1.051, 1.189] | [1.059, 1.186] | +0.000 [-0.052, +0.053] |
| Kalshi BTC15 5m before close | 6329 | 6329 | 67 | 0.494 | 0.498 | 0.1141 | 1.049 [1.003, 1.102] | [0.999, 1.102] | +0.042 [-0.022, +0.102] |
| Kalshi BTC15 2m before close | 5920 | 5920 | 67 | 0.491 | 0.494 | 0.0609 | 1.000 [0.952, 1.057] | [0.950, 1.059] | +0.051 [-0.051, +0.157] |
| Kalshi BTC15 1m before close | 3875 | 3875 | 67 | 0.483 | 0.488 | 0.0439 | 1.081 [1.002, 1.185] | [1.004, 1.176] | +0.107 [-0.047, +0.266] |

### Kalshi horizon 10m: bins by ask price paid

| bin | n contracts | n markets | n events | mean buy price | win rate | return/$ EW [95% CI, market resample] | EW CI (event resample) | event-clustered mean [95% CI] | EW after kx | EW after pm7 |
|---|---|---|---|---|---|---|---|---|---|---|
| [0.00,0.10) | 438 | 438 | 65 | 0.072 | 0.066 | -9.9% [-41.5%, +24.8%] | [-43.6%, +24.6%] | -24.0% [-54.6%, +10.1%] | -21.5% [-48.7%, +9.3%] | -15.4% [-44.6%, +19.0%] |
| [0.10,0.20) | 1113 | 1113 | 67 | 0.155 | 0.119 | -24.7% [-37.1%, -12.3%] | [-37.1%, -11.4%] | -24.5% [-38.1%, -10.6%] | -30.6% [-41.6%, -19.4%] | -28.9% [-40.3%, -17.4%] |
| [0.20,0.30) | 1313 | 1313 | 67 | 0.248 | 0.221 | -11.9% [-21.0%, -2.4%] | [-21.1%, -2.5%] | -10.6% [-20.1%, -1.2%] | -18.5% [-26.6%, -10.2%] | -16.3% [-24.9%, -7.5%] |
| [0.30,0.40) | 1621 | 1621 | 67 | 0.344 | 0.320 | -7.2% [-13.8%, -0.6%] | [-14.6%, +0.3%] | -6.2% [-13.6%, +1.3%] | -12.3% [-18.5%, -6.1%] | -11.3% [-17.6%, -5.0%] |
| [0.40,0.50) | 1671 | 1671 | 67 | 0.446 | 0.431 | -3.2% [-8.7%, +2.3%] | [-8.8%, +2.5%] | -3.1% [-9.0%, +2.9%] | -7.4% [-12.6%, -2.1%] | -6.8% [-11.9%, -1.5%] |
| [0.50,0.60) | 1695 | 1521 | 67 | 0.544 | 0.552 | +1.5% [-2.3%, +5.3%] | [-2.6%, +5.4%] | +1.4% [-2.7%, +5.5%] | -2.1% [-5.8%, +1.4%] | -1.7% [-5.3%, +2.0%] |
| [0.60,0.70) | 1603 | 1603 | 67 | 0.645 | 0.656 | +1.5% [-2.0%, +5.0%] | [-1.8%, +4.9%] | +0.9% [-2.2%, +4.2%] | -1.6% [-5.1%, +1.8%] | -1.0% [-4.6%, +2.5%] |
| [0.70,0.80) | 1430 | 1430 | 67 | 0.743 | 0.752 | +1.2% [-1.9%, +4.2%] | [-2.1%, +4.6%] | +1.4% [-1.9%, +4.6%] | -1.4% [-4.3%, +1.4%] | -0.6% [-3.6%, +2.4%] |
| [0.80,0.90) | 1220 | 1220 | 67 | 0.842 | 0.867 | +3.0% [+0.7%, +5.2%] | [+0.5%, +5.4%] | +2.4% [-0.3%, +5.0%] | +1.4% [-0.9%, +3.5%] | +1.9% [-0.4%, +4.0%] |
| [0.90,1.00) | 556 | 556 | 65 | 0.924 | 0.932 | +0.9% [-1.5%, +3.1%] | [-1.3%, +3.0%] | +1.0% [-1.5%, +3.4%] | -0.2% [-2.5%, +2.0%] | +0.3% [-2.0%, +2.5%] |
| [0.00,0.01) | 0 | | | | | | | | | | |
| [0.01,0.02) | 2 | 2 | 2 | 0.016 | 0.000 | -100.0% [-100.0%, -100.0%] | [-100.0%, -100.0%] | -100.0% [-100.0%, -100.0%] | -100.0% [-100.0%, -100.0%] | -100.0% [-100.0%, -100.0%] |
| [0.02,0.05) | 63 | 63 | 32 | 0.040 | 0.048 | +18.7% [-100.0%, +163.0%] | [-100.0%, +164.9%] | +39.0% [-100.0%, +222.2%] | -5.3% [-100.0%, +115.6%] | +11.2% [-100.0%, +146.4%] |
| [0.05,0.10) | 373 | 373 | 64 | 0.077 | 0.070 | -14.3% [-44.9%, +18.9%] | [-42.4%, +15.3%] | -28.9% [-55.4%, +1.8%] | -23.8% [-51.7%, +6.2%] | -19.5% [-48.5%, +12.3%] |
| [0.90,0.95) | 469 | 469 | 65 | 0.917 | 0.925 | +0.9% [-1.7%, +3.4%] | [-1.4%, +3.1%] | +1.3% [-1.3%, +3.6%] | -0.2% [-2.8%, +2.3%] | +0.3% [-2.4%, +2.8%] |
| [0.95,0.98) | 84 | 84 | 42 | 0.958 | 0.964 | +0.6% [-4.3%, +4.3%] | [-4.1%, +4.3%] | +0.2% [-5.7%, +4.3%] | -0.4% [-5.2%, +3.3%] | +0.3% [-4.6%, +4.0%] |
| [0.98,0.99) | 2 | 2 | 2 | 0.983 | 1.000 | +1.7% [+1.5%, +1.9%] | [+1.5%, +1.9%] | +1.7% [+1.5%, +1.9%] | +0.7% [+0.5%, +0.9%] | +1.6% [+1.4%, +1.8%] |
| [0.99,1.00) | 1 | 1 | 1 | 0.990 | 1.000 | +1.0% [–, –] | [–, –] | +1.0% [–, –] | +0.0% [–, –] | +0.9% [–, –] |

Headline, 10m:

| bin | n contracts | n markets | n events | mean buy price | win rate | return/$ EW [95% CI, market resample] | EW CI (event resample) | event-clustered mean [95% CI] | EW after kx | EW after pm7 |
|---|---|---|---|---|---|---|---|---|---|---|
| (0.00,0.10] | 441 | 441 | 65 | 0.072 | 0.066 | -10.5% [-41.8%, +23.9%] | [-42.5%, +24.9%] | -25.5% [-54.7%, +8.7%] | -22.1% [-49.8%, +7.4%] | -16.0% [-44.8%, +16.1%] |
| [0.90,1.00] | 556 | 556 | 65 | 0.924 | 0.932 | +0.9% [-1.5%, +3.0%] | [-1.3%, +2.9%] | +1.0% [-1.5%, +3.3%] | -0.2% [-2.5%, +1.9%] | +0.3% [-2.0%, +2.5%] |

### Kalshi horizon 5m: bins by ask price paid

| bin | n contracts | n markets | n events | mean buy price | win rate | return/$ EW [95% CI, market resample] | EW CI (event resample) | event-clustered mean [95% CI] | EW after kx | EW after pm7 |
|---|---|---|---|---|---|---|---|---|---|---|
| [0.00,0.10) | 2878 | 2878 | 67 | 0.043 | 0.036 | -11.6% [-31.4%, +11.5%] | [-31.7%, +11.2%] | -9.3% [-30.6%, +14.9%] | -33.5% [-47.0%, -19.1%] | -17.2% [-36.3%, +3.2%] |
| [0.10,0.20) | 1150 | 1150 | 67 | 0.149 | 0.134 | -9.3% [-22.7%, +4.6%] | [-22.0%, +3.2%] | -12.5% [-25.3%, -0.0%] | -16.1% [-28.4%, -3.0%] | -14.4% [-26.9%, -1.4%] |
| [0.20,0.30) | 820 | 820 | 67 | 0.247 | 0.226 | -8.2% [-19.4%, +3.6%] | [-19.6%, +3.6%] | -4.7% [-17.8%, +9.4%] | -15.2% [-26.1%, -4.1%] | -12.8% [-24.0%, -1.5%] |
| [0.30,0.40) | 699 | 699 | 67 | 0.347 | 0.343 | -2.1% [-12.4%, +7.9%] | [-12.2%, +8.4%] | -2.5% [-12.7%, +8.0%] | -7.4% [-16.9%, +2.0%] | -6.4% [-16.0%, +3.3%] |
| [0.40,0.50) | 703 | 703 | 67 | 0.445 | 0.420 | -5.8% [-14.0%, +2.4%] | [-13.5%, +1.8%] | -5.3% [-14.1%, +3.6%] | -9.9% [-17.8%, -2.1%] | -9.3% [-17.1%, -1.7%] |
| [0.50,0.60) | 711 | 632 | 67 | 0.544 | 0.551 | +1.4% [-4.5%, +7.1%] | [-3.8%, +6.7%] | +2.3% [-3.5%, +8.3%] | -2.2% [-7.9%, +3.5%] | -1.7% [-7.4%, +3.8%] |
| [0.60,0.70) | 729 | 729 | 67 | 0.645 | 0.623 | -3.3% [-8.8%, +2.1%] | [-8.9%, +2.2%] | -2.9% [-9.0%, +3.2%] | -6.2% [-11.5%, -0.8%] | -5.6% [-11.1%, -0.1%] |
| [0.70,0.80) | 799 | 799 | 67 | 0.748 | 0.780 | +4.4% [+0.5%, +8.3%] | [+0.9%, +8.1%] | +4.4% [+0.6%, +8.2%] | +1.7% [-2.0%, +5.4%] | +2.6% [-1.3%, +6.4%] |
| [0.80,0.90) | 1124 | 1124 | 67 | 0.848 | 0.856 | +0.9% [-1.6%, +3.3%] | [-1.3%, +3.1%] | +1.4% [-1.0%, +3.7%] | -0.6% [-2.9%, +1.8%] | -0.2% [-2.6%, +2.2%] |
| [0.90,1.00) | 3045 | 3045 | 67 | 0.956 | 0.959 | +0.4% [-0.4%, +1.1%] | [-0.4%, +1.2%] | +0.3% [-0.5%, +1.2%] | -0.7% [-1.4%, +0.0%] | +0.1% [-0.7%, +0.8%] |
| [0.00,0.01) | 169 | 169 | 56 | 0.007 | 0.006 | -1.4% [-100.0%, +195.9%] | [-100.0%, +212.5%] | -50.4% [-100.0%, +48.8%] | -63.0% [-100.0%, +10.9%] | -7.8% [-100.0%, +176.6%] |
| [0.01,0.02) | 527 | 527 | 66 | 0.014 | 0.008 | -51.6% [-90.0%, +1.7%] | [-90.0%, +0.4%] | -52.9% [-93.6%, +0.8%] | -70.6% [-93.5%, -39.4%] | -54.7% [-90.7%, -5.1%] |
| [0.02,0.05) | 1101 | 1101 | 67 | 0.034 | 0.039 | +20.1% [-16.2%, +58.1%] | [-15.5%, +59.5%] | +19.0% [-18.4%, +61.6%] | -9.1% [-36.0%, +19.2%] | +12.5% [-20.3%, +46.9%] |
| [0.05,0.10) | 1081 | 1081 | 67 | 0.072 | 0.052 | -26.1% [-45.1%, -6.2%] | [-45.5%, -5.1%] | -26.5% [-46.9%, -3.5%] | -35.7% [-51.8%, -17.9%] | -30.6% [-48.6%, -11.8%] |
| [0.90,0.95) | 1155 | 1155 | 67 | 0.924 | 0.939 | +1.6% [+0.0%, +3.0%] | [-0.0%, +3.2%] | +1.7% [+0.1%, +3.3%] | +0.5% [-1.1%, +2.0%] | +1.0% [-0.4%, +2.5%] |
| [0.95,0.98) | 1090 | 1090 | 67 | 0.966 | 0.958 | -0.8% [-2.1%, +0.4%] | [-2.0%, +0.3%] | -0.9% [-2.2%, +0.3%] | -1.8% [-3.1%, -0.7%] | -1.0% [-2.3%, +0.1%] |
| [0.98,0.99) | 520 | 520 | 67 | 0.985 | 0.987 | +0.2% [-0.8%, +1.1%] | [-0.8%, +1.0%] | +0.3% [-0.8%, +1.1%] | -0.9% [-1.8%, +0.1%] | +0.0% [-1.1%, +1.0%] |
| [0.99,1.00) | 280 | 280 | 62 | 0.993 | 0.996 | +0.4% [-0.4%, +0.7%] | [-0.5%, +0.7%] | +0.5% [-0.1%, +0.8%] | -0.6% [-1.4%, -0.3%] | +0.3% [-0.4%, +0.7%] |

Headline, 5m:

| bin | n contracts | n markets | n events | mean buy price | win rate | return/$ EW [95% CI, market resample] | EW CI (event resample) | event-clustered mean [95% CI] | EW after kx | EW after pm7 |
|---|---|---|---|---|---|---|---|---|---|---|
| (0.00,0.10] | 2892 | 2892 | 67 | 0.043 | 0.036 | -11.7% [-31.7%, +10.8%] | [-32.2%, +11.6%] | -9.4% [-30.7%, +15.4%] | -33.5% [-47.0%, -18.9%] | -17.3% [-35.5%, +3.3%] |
| [0.90,1.00] | 3045 | 3045 | 67 | 0.956 | 0.959 | +0.4% [-0.4%, +1.1%] | [-0.4%, +1.1%] | +0.3% [-0.5%, +1.1%] | -0.7% [-1.4%, +0.1%] | +0.1% [-0.7%, +0.8%] |

### Kalshi horizon 1m: bins by ask price paid

| bin | n contracts | n markets | n events | mean buy price | win rate | return/$ EW [95% CI, market resample] | EW CI (event resample) | event-clustered mean [95% CI] | EW after kx | EW after pm7 |
|---|---|---|---|---|---|---|---|---|---|---|
| [0.00,0.10) | 5549 | 5549 | 67 | 0.009 | 0.008 | -59.5% [-82.0%, -28.6%] | [-81.7%, -30.5%] | -57.6% [-81.8%, -26.4%] | -81.9% [-88.3%, -74.7%] | -62.1% [-83.0%, -33.2%] |
| [0.10,0.20) | 245 | 245 | 65 | 0.148 | 0.090 | -36.1% [-60.8%, -7.9%] | [-60.4%, -12.2%] | -38.2% [-64.6%, -7.4%] | -41.2% [-64.0%, -16.5%] | -39.7% [-63.1%, -14.6%] |
| [0.20,0.30) | 199 | 199 | 63 | 0.242 | 0.191 | -18.7% [-41.4%, +6.4%] | [-41.2%, +3.9%] | -13.7% [-41.7%, +16.2%] | -25.1% [-46.0%, -2.7%] | -22.8% [-43.8%, -0.3%] |
| [0.30,0.40) | 159 | 159 | 60 | 0.345 | 0.289 | -14.8% [-34.9%, +6.1%] | [-37.6%, +8.3%] | -10.0% [-35.9%, +16.1%] | -19.5% [-38.5%, +0.5%] | -18.5% [-38.2%, +2.3%] |
| [0.40,0.50) | 158 | 158 | 58 | 0.447 | 0.475 | +6.0% [-11.9%, +23.4%] | [-11.7%, +23.4%] | +6.8% [-13.6%, +26.8%] | +1.5% [-15.4%, +18.0%] | +2.1% [-14.7%, +18.7%] |
| [0.50,0.60) | 167 | 148 | 55 | 0.547 | 0.527 | -3.8% [-15.6%, +8.0%] | [-16.9%, +9.2%] | -2.5% [-17.5%, +12.2%] | -7.2% [-19.0%, +4.1%] | -6.7% [-18.3%, +4.6%] |
| [0.60,0.70) | 147 | 147 | 60 | 0.647 | 0.680 | +4.9% [-6.6%, +16.3%] | [-8.9%, +18.6%] | +6.2% [-8.5%, +20.0%] | +1.8% [-10.0%, +13.0%] | +2.4% [-9.5%, +13.4%] |
| [0.70,0.80) | 187 | 187 | 63 | 0.749 | 0.765 | +2.3% [-5.9%, +10.5%] | [-5.9%, +10.6%] | +1.7% [-8.4%, +11.1%] | -0.4% [-8.5%, +7.4%] | +0.5% [-8.1%, +8.3%] |
| [0.80,0.90) | 249 | 249 | 67 | 0.845 | 0.896 | +6.0% [+1.3%, +10.3%] | [+1.6%, +10.1%] | +6.6% [+1.7%, +11.1%] | +4.4% [-0.2%, +8.6%] | +4.9% [+0.1%, +9.3%] |
| [0.90,1.00) | 3144 | 3144 | 67 | 0.985 | 0.985 | +0.0% [-0.4%, +0.4%] | [-0.5%, +0.5%] | +0.1% [-0.4%, +0.5%] | -1.0% [-1.4%, -0.6%] | -0.1% [-0.5%, +0.3%] |
| [0.00,0.01) | 4314 | 4314 | 67 | 0.002 | 0.001 | -68.8% [-97.1%, -31.4%] | [-97.1%, -31.7%] | -66.6% [-96.8%, -27.1%] | -92.4% [-98.7%, -84.7%] | -70.8% [-95.2%, -35.0%] |
| [0.01,0.02) | 524 | 524 | 67 | 0.014 | 0.008 | -48.0% [-90.0%, +8.6%] | [-90.0%, +8.8%] | -46.5% [-91.3%, +11.8%] | -69.2% [-93.4%, -36.7%] | -51.4% [-90.6%, +1.6%] |
| [0.02,0.05) | 434 | 434 | 67 | 0.032 | 0.030 | -16.3% [-59.2%, +32.3%] | [-59.9%, +33.3%] | -26.0% [-65.5%, +19.2%] | -35.3% [-68.9%, +4.3%] | -21.5% [-62.2%, +23.4%] |
| [0.05,0.10) | 277 | 277 | 65 | 0.073 | 0.072 | -4.3% [-43.3%, +39.5%] | [-48.0%, +45.2%] | -8.2% [-50.7%, +38.2%] | -15.8% [-50.5%, +22.5%] | -10.1% [-47.3%, +31.2%] |
| [0.90,0.95) | 286 | 286 | 65 | 0.923 | 0.920 | -0.4% [-3.9%, +2.9%] | [-5.1%, +3.8%] | +1.1% [-2.6%, +4.4%] | -1.4% [-4.9%, +1.9%] | -0.9% [-4.7%, +2.4%] |
| [0.95,0.98) | 382 | 382 | 67 | 0.967 | 0.966 | -0.1% [-2.2%, +1.6%] | [-2.2%, +1.7%] | +0.4% [-1.4%, +2.0%] | -1.1% [-3.0%, +0.5%] | -0.3% [-2.3%, +1.4%] |
| [0.98,0.99) | 396 | 396 | 66 | 0.985 | 0.987 | +0.2% [-1.1%, +1.2%] | [-1.0%, +1.2%] | +0.3% [-0.9%, +1.2%] | -0.8% [-2.1%, +0.2%] | +0.1% [-1.2%, +1.1%] |
| [0.99,1.00) | 2080 | 2080 | 67 | 0.997 | 0.998 | +0.1% [-0.2%, +0.3%] | [-0.1%, +0.3%] | +0.1% [-0.1%, +0.3%] | -0.9% [-1.1%, -0.7%] | +0.1% [-0.2%, +0.3%] |

Headline, 1m:

| bin | n contracts | n markets | n events | mean buy price | win rate | return/$ EW [95% CI, market resample] | EW CI (event resample) | event-clustered mean [95% CI] | EW after kx | EW after pm7 |
|---|---|---|---|---|---|---|---|---|---|---|
| (0.00,0.10] | 5555 | 5555 | 67 | 0.009 | 0.008 | -59.4% [-81.9%, -28.2%] | [-81.8%, -29.9%] | -57.5% [-81.9%, -25.3%] | -81.8% [-88.3%, -74.2%] | -62.0% [-82.6%, -32.8%] |
| [0.90,1.00] | 3144 | 3144 | 67 | 0.985 | 0.985 | +0.0% [-0.4%, +0.5%] | [-0.5%, +0.5%] | +0.1% [-0.4%, +0.5%] | -1.0% [-1.4%, -0.5%] | -0.1% [-0.5%, +0.4%] |


## C. Polymarket — marketlenstrade/polymarket-historical-data top-of-book snapshots (7 sample events)

Mostly in-play sports/esports plus BTC up/down, weather and AAPL buckets, all within single days. Price sampled at fixed offsets after recording start (≈ market/game open), only for markets still unresolved then. Buy first outcome at best ask, second at 1 − best bid; snapshot must be ≤15 min old at target and the book two-sided with spread ≤ 0.05 (ex-ante liquidity filter). Event cluster = game/event within folder. Very small number of independent events — treat as anecdotal.

| sample | n | markets | events | mean price | outcome rate | Brier | slope b [95% CI event resample] | slope CI (market resample) | intercept a [95% CI] |
|---|---|---|---|---|---|---|---|---|---|
| Marketlens open+5m | 752 | 752 | 338 | 0.410 | 0.420 | 0.1775 | 0.980 [0.817, 1.210] | [0.833, 1.178] | +0.055 [-0.203, +0.298] |
| Marketlens open+30m | 430 | 430 | 116 | 0.382 | 0.395 | 0.1676 | 1.002 [0.762, 1.437] | [0.784, 1.320] | +0.077 [-0.249, +0.425] |
| Marketlens open+90m | 306 | 306 | 87 | 0.393 | 0.405 | 0.1378 | 1.116 [0.811, 1.624] | [0.876, 1.508] | +0.122 [-0.269, +0.587] |

Headline, open+5m:

| bin | n contracts | n markets | n events | mean buy price | win rate | return/$ EW [95% CI, market resample] | EW CI (event resample) | event-clustered mean [95% CI] | EW after pm4 | EW after pm7 |
|---|---|---|---|---|---|---|---|---|---|---|
| (0.00,0.10] | 163 | 163 | 102 | 0.049 | 0.037 | -26.2% [-81.6%, +47.7%] | [-89.1%, +61.4%] | -51.3% [-90.5%, +5.2%] | -28.9% [-83.1%, +43.9%] | -30.8% [-83.5%, +40.9%] |
| [0.90,1.00] | 178 | 178 | 108 | 0.962 | 0.949 | -1.3% [-5.0%, +1.8%] | [-5.0%, +1.7%] | -1.1% [-5.4%, +2.2%] | -1.5% [-5.1%, +1.6%] | -1.6% [-5.1%, +1.5%] |

Deciles, open+5m:

| bin | n contracts | n markets | n events | mean buy price | win rate | return/$ EW [95% CI, market resample] | EW CI (event resample) | event-clustered mean [95% CI] | EW after pm4 | EW after pm7 |
|---|---|---|---|---|---|---|---|---|---|---|
| [0.00,0.10) | 158 | 158 | 102 | 0.047 | 0.038 | -23.8% [-84.6%, +51.7%] | [-88.8%, +68.9%] | -50.4% [-90.2%, +3.3%] | -26.6% [-81.7%, +46.0%] | -28.6% [-85.5%, +43.7%] |
| [0.10,0.20) | 92 | 92 | 68 | 0.150 | 0.130 | -11.4% [-57.6%, +38.4%] | [-53.5%, +33.9%] | -17.4% [-62.0%, +32.2%] | -14.3% [-58.7%, +34.3%] | -16.4% [-57.2%, +32.4%] |
| [0.20,0.30) | 93 | 93 | 67 | 0.248 | 0.247 | +0.7% [-33.3%, +38.3%] | [-33.6%, +37.2%] | -2.0% [-39.7%, +38.3%] | -2.3% [-36.8%, +33.4%] | -4.4% [-37.3%, +30.3%] |
| [0.30,0.40) | 140 | 140 | 89 | 0.348 | 0.386 | +9.1% [-13.3%, +32.4%] | [-13.1%, +30.6%] | +6.9% [-18.8%, +33.5%] | +6.4% [-16.1%, +28.6%] | +4.4% [-17.2%, +26.1%] |
| [0.40,0.50) | 232 | 232 | 103 | 0.450 | 0.487 | +7.9% [-6.1%, +22.1%] | [-6.2%, +22.5%] | +9.2% [-9.3%, +27.4%] | +5.6% [-8.5%, +19.6%] | +3.9% [-9.9%, +17.0%] |
| [0.50,0.60) | 253 | 216 | 103 | 0.545 | 0.482 | -11.1% [-20.6%, -1.8%] | [-20.8%, -1.9%] | -15.5% [-29.4%, -1.6%] | -12.7% [-21.9%, -3.7%] | -13.9% [-23.3%, -4.8%] |
| [0.60,0.70) | 151 | 151 | 86 | 0.641 | 0.603 | -6.0% [-18.2%, +5.9%] | [-17.3%, +5.7%] | -7.7% [-21.4%, +6.1%] | -7.4% [-19.1%, +4.5%] | -8.3% [-20.5%, +3.5%] |
| [0.70,0.80) | 108 | 108 | 78 | 0.743 | 0.731 | -1.6% [-12.9%, +9.4%] | [-12.3%, +9.9%] | +3.6% [-7.9%, +15.1%] | -2.6% [-14.5%, +7.9%] | -3.3% [-14.4%, +7.3%] |
| [0.80,0.90) | 99 | 99 | 76 | 0.844 | 0.838 | -0.8% [-9.8%, +7.5%] | [-9.3%, +7.2%] | +0.3% [-9.1%, +8.7%] | -1.4% [-10.0%, +6.8%] | -1.8% [-10.7%, +6.5%] |
| [0.90,1.00) | 178 | 178 | 108 | 0.962 | 0.949 | -1.3% [-4.8%, +1.8%] | [-4.8%, +1.8%] | -1.1% [-5.4%, +2.3%] | -1.5% [-5.2%, +1.7%] | -1.6% [-5.2%, +1.5%] |

Headline, open+30m:

| bin | n contracts | n markets | n events | mean buy price | win rate | return/$ EW [95% CI, market resample] | EW CI (event resample) | event-clustered mean [95% CI] | EW after pm4 | EW after pm7 |
|---|---|---|---|---|---|---|---|---|---|---|
| (0.00,0.10] | 100 | 100 | 32 | 0.046 | 0.050 | +16.9% [-80.0%, +144.3%] | [-100.0%, +162.5%] | -29.8% [-100.0%, +56.7%] | +12.6% [-83.7%, +140.3%] | +9.6% [-81.2%, +128.7%] |
| [0.90,1.00] | 114 | 114 | 36 | 0.962 | 0.947 | -1.4% [-6.0%, +2.5%] | [-7.3%, +3.4%] | -2.5% [-11.6%, +4.2%] | -1.6% [-6.2%, +2.3%] | -1.7% [-6.4%, +2.2%] |

Deciles, open+30m:

| bin | n contracts | n markets | n events | mean buy price | win rate | return/$ EW [95% CI, market resample] | EW CI (event resample) | event-clustered mean [95% CI] | EW after pm4 | EW after pm7 |
|---|---|---|---|---|---|---|---|---|---|---|
| [0.00,0.10) | 95 | 95 | 31 | 0.043 | 0.053 | +23.1% [-78.9%, +160.2%] | [-100.0%, +180.7%] | -23.5% [-100.0%, +72.8%] | +18.6% [-82.8%, +148.7%] | +15.4% [-80.2%, +139.3%] |
| [0.10,0.20) | 60 | 60 | 32 | 0.147 | 0.167 | +2.6% [-53.3%, +64.1%] | [-50.9%, +72.1%] | +39.9% [-39.6%, +130.8%] | -0.7% [-53.9%, +60.8%] | -3.1% [-54.5%, +55.0%] |
| [0.20,0.30) | 56 | 56 | 43 | 0.250 | 0.196 | -21.4% [-60.8%, +22.7%] | [-64.4%, +22.8%] | -27.3% [-67.9%, +19.7%] | -23.7% [-61.7%, +20.7%] | -25.3% [-62.1%, +16.9%] |
| [0.30,0.40) | 87 | 87 | 55 | 0.348 | 0.287 | -18.8% [-45.3%, +8.1%] | [-46.5%, +11.2%] | -17.5% [-47.5%, +13.5%] | -20.8% [-45.6%, +5.7%] | -22.3% [-47.6%, +3.9%] |
| [0.40,0.50) | 111 | 111 | 57 | 0.450 | 0.423 | -5.3% [-26.1%, +15.4%] | [-26.0%, +13.9%] | -10.9% [-35.5%, +13.2%] | -7.3% [-26.6%, +13.3%] | -8.8% [-28.3%, +11.2%] |
| [0.50,0.60) | 131 | 110 | 53 | 0.543 | 0.565 | +4.3% [-8.4%, +17.2%] | [-9.3%, +18.7%] | +15.0% [-3.8%, +33.4%] | +2.4% [-9.8%, +14.8%] | +1.1% [-11.2%, +13.6%] |
| [0.60,0.70) | 84 | 84 | 54 | 0.643 | 0.643 | -0.8% [-17.0%, +14.3%] | [-16.7%, +14.8%] | -1.3% [-18.4%, +15.6%] | -2.1% [-17.8%, +13.4%] | -3.2% [-18.9%, +12.1%] |
| [0.70,0.80) | 67 | 67 | 47 | 0.740 | 0.776 | +4.8% [-9.3%, +17.7%] | [-9.8%, +18.6%] | +6.0% [-9.5%, +20.5%] | +3.8% [-10.2%, +16.4%] | +3.0% [-11.0%, +16.0%] |
| [0.80,0.90) | 55 | 55 | 35 | 0.843 | 0.800 | -5.0% [-18.1%, +6.6%] | [-17.3%, +6.4%] | -5.4% [-20.4%, +7.9%] | -5.6% [-18.9%, +6.6%] | -6.1% [-19.1%, +5.5%] |
| [0.90,1.00) | 114 | 114 | 36 | 0.962 | 0.947 | -1.4% [-6.1%, +2.5%] | [-7.4%, +3.4%] | -2.5% [-11.5%, +4.1%] | -1.6% [-6.2%, +2.3%] | -1.7% [-6.2%, +2.1%] |

Headline, open+90m:

| bin | n contracts | n markets | n events | mean buy price | win rate | return/$ EW [95% CI, market resample] | EW CI (event resample) | event-clustered mean [95% CI] | EW after pm4 | EW after pm7 |
|---|---|---|---|---|---|---|---|---|---|---|
| (0.00,0.10] | 94 | 94 | 38 | 0.028 | 0.021 | -74.9% [-100.0%, -36.5%] | [-100.0%, -24.8%] | -37.9% [-100.0%, +57.2%] | -75.8% [-100.0%, -38.7%] | -76.4% [-100.0%, -40.3%] |
| [0.90,1.00] | 105 | 105 | 41 | 0.978 | 0.962 | -1.8% [-5.9%, +1.5%] | [-8.3%, +2.0%] | -3.5% [-11.2%, +2.3%] | -1.9% [-6.1%, +1.3%] | -2.0% [-6.1%, +1.4%] |

Deciles, open+90m:

| bin | n contracts | n markets | n events | mean buy price | win rate | return/$ EW [95% CI, market resample] | EW CI (event resample) | event-clustered mean [95% CI] | EW after pm4 | EW after pm7 |
|---|---|---|---|---|---|---|---|---|---|---|
| [0.00,0.10) | 94 | 94 | 38 | 0.028 | 0.021 | -74.9% [-100.0%, -36.5%] | [-100.0%, -22.8%] | -37.9% [-100.0%, +57.2%] | -75.8% [-100.0%, -38.7%] | -76.4% [-100.0%, -40.3%] |
| [0.10,0.20) | 49 | 49 | 30 | 0.152 | 0.122 | -15.6% [-72.7%, +53.3%] | [-81.7%, +69.9%] | -11.1% [-79.2%, +75.7%] | -18.4% [-73.7%, +48.8%] | -20.4% [-75.0%, +43.3%] |
| [0.20,0.30) | 49 | 49 | 34 | 0.256 | 0.204 | -17.9% [-60.9%, +30.9%] | [-61.0%, +30.2%] | -14.2% [-63.6%, +43.4%] | -20.3% [-62.5%, +26.1%] | -22.0% [-61.6%, +23.8%] |
| [0.30,0.40) | 49 | 49 | 36 | 0.354 | 0.347 | +0.0% [-38.1%, +39.4%] | [-37.3%, +39.6%] | -2.4% [-41.0%, +39.4%] | -2.5% [-38.7%, +34.3%] | -4.3% [-39.2%, +34.0%] |
| [0.40,0.50) | 53 | 53 | 37 | 0.449 | 0.415 | -9.8% [-38.5%, +19.3%] | [-35.0%, +17.5%] | -8.9% [-39.7%, +23.6%] | -11.7% [-39.5%, +16.5%] | -13.0% [-40.5%, +15.1%] |
| [0.50,0.60) | 64 | 52 | 35 | 0.540 | 0.516 | -4.6% [-22.6%, +12.6%] | [-21.4%, +12.3%] | -1.4% [-25.3%, +21.8%] | -6.3% [-23.4%, +9.9%] | -7.6% [-24.2%, +8.9%] |
| [0.60,0.70) | 53 | 53 | 36 | 0.644 | 0.736 | +14.3% [-5.6%, +32.3%] | [-3.3%, +30.9%] | +15.0% [-5.6%, +33.4%] | +12.7% [-5.3%, +30.5%] | +11.5% [-7.0%, +29.1%] |
| [0.70,0.80) | 46 | 46 | 32 | 0.743 | 0.717 | -4.0% [-21.7%, +13.3%] | [-22.7%, +13.9%] | +1.0% [-18.0%, +18.2%] | -5.0% [-22.5%, +12.2%] | -5.7% [-23.1%, +11.1%] |
| [0.80,0.90) | 50 | 50 | 32 | 0.847 | 0.860 | +1.6% [-10.7%, +11.6%] | [-10.6%, +11.6%] | -1.3% [-16.2%, +11.5%] | +0.9% [-10.8%, +11.1%] | +0.5% [-11.5%, +10.8%] |
| [0.90,1.00) | 105 | 105 | 41 | 0.978 | 0.962 | -1.8% [-5.9%, +1.6%] | [-7.9%, +2.0%] | -3.5% [-11.2%, +2.4%] | -1.9% [-6.0%, +1.3%] | -2.0% [-6.0%, +1.3%] |


## D. Polymarket — 5-minute crypto Up/Down, Kaggle kachoio/polymarket-5-minute-crypto-updown-markets

Seven coins, windows closing 2026-03-24 → 2026-05-18, per-second best bid/ask for the Up and Down books. Buy Up at the Up ask and Down at the Down ask, h before the scheduled window end, when that book's spread is ≤ 0.05. Calibration uses the Up midpoint (one row per market). Market cluster = market; event cluster = UTC day (all coins pooled). The publisher infers `outcome` from the final tick and leaves it null when that tick was not decisive (final Up mid between ≈0.09 and ≈0.91): 8,876 of 83,159 markets sampled. The primary spec drops them, which removes close calls and flatters short-horizon favourites; the bounds table sets every null-outcome contract to lose / win.

### Calibration (Up midpoint)

| sample | n | markets | events | mean price | outcome rate | Brier | slope b [95% CI event resample] | slope CI (market resample) | intercept a [95% CI] |
|---|---|---|---|---|---|---|---|---|---|
| Polymarket 5m crypto 4m before end | 54241 | 54241 | 56 | 0.502 | 0.504 | 0.2046 | 1.156 [1.111, 1.202] | [1.130, 1.181] | +0.007 [-0.017, +0.032] |
| Polymarket 5m crypto 2m before end | 57540 | 57540 | 56 | 0.503 | 0.504 | 0.1222 | 1.176 [1.149, 1.205] | [1.158, 1.195] | +0.011 [-0.026, +0.047] |
| Polymarket 5m crypto 1m before end | 54701 | 54701 | 56 | 0.505 | 0.506 | 0.0806 | 1.212 [1.181, 1.243] | [1.193, 1.232] | +0.016 [-0.027, +0.058] |
| Polymarket 5m crypto 30s before end | 45218 | 45218 | 56 | 0.503 | 0.503 | 0.0567 | 1.271 [1.238, 1.308] | [1.248, 1.296] | -0.010 [-0.065, +0.045] |

### 5m crypto horizon 4m: bins by ask price paid

| bin | n contracts | n markets | n events | mean buy price | win rate | return/$ EW [95% CI, market resample] | EW CI (event resample) | event-clustered mean [95% CI] | EW after pm4 | EW after pm7 |
|---|---|---|---|---|---|---|---|---|---|---|
| [0.00,0.10) | 820 | 820 | 53 | 0.069 | 0.030 | -56.0% [-72.6%, -36.1%] | [-72.6%, -37.9%] | -58.4% [-77.8%, -36.5%] | -57.6% [-73.7%, -38.6%] | -58.7% [-74.8%, -41.4%] |
| [0.10,0.20) | 4968 | 4968 | 56 | 0.154 | 0.112 | -28.4% [-34.1%, -22.6%] | [-35.9%, -20.8%] | -33.8% [-41.7%, -25.9%] | -30.8% [-36.0%, -24.9%] | -32.4% [-37.8%, -27.0%] |
| [0.20,0.30) | 11034 | 11034 | 56 | 0.249 | 0.209 | -16.5% [-19.5%, -13.5%] | [-20.9%, -11.9%] | -20.8% [-26.3%, -15.2%] | -18.9% [-21.8%, -16.0%] | -20.6% [-23.4%, -17.8%] |
| [0.30,0.40) | 15919 | 15919 | 56 | 0.346 | 0.323 | -6.8% [-8.9%, -4.7%] | [-9.6%, -3.7%] | -5.7% [-8.8%, -2.3%] | -9.2% [-11.2%, -7.2%] | -10.9% [-12.9%, -8.9%] |
| [0.40,0.50) | 18945 | 18945 | 56 | 0.447 | 0.431 | -3.7% [-5.3%, -2.1%] | [-5.7%, -1.9%] | -4.0% [-6.5%, -1.5%] | -5.8% [-7.3%, -4.2%] | -7.3% [-8.8%, -5.8%] |
| [0.50,0.60) | 20204 | 17383 | 56 | 0.542 | 0.538 | -0.8% [-1.8%, +0.3%] | [-1.9%, +0.5%] | -0.4% [-2.0%, +1.1%] | -2.5% [-3.6%, -1.5%] | -3.8% [-4.8%, -2.8%] |
| [0.60,0.70) | 16107 | 16107 | 56 | 0.643 | 0.649 | +1.0% [-0.2%, +2.1%] | [-0.5%, +2.5%] | +0.6% [-1.2%, +2.4%] | -0.4% [-1.5%, +0.7%] | -1.5% [-2.6%, -0.4%] |
| [0.70,0.80) | 12461 | 12461 | 56 | 0.741 | 0.762 | +2.9% [+1.8%, +3.9%] | [+1.4%, +4.3%] | +3.9% [+2.1%, +5.8%] | +1.8% [+0.8%, +2.8%] | +1.0% [+0.0%, +2.0%] |
| [0.80,0.90) | 6526 | 6526 | 56 | 0.839 | 0.866 | +3.2% [+2.3%, +4.2%] | [+1.9%, +4.6%] | +4.4% [+3.0%, +5.9%] | +2.6% [+1.6%, +3.6%] | +2.1% [+1.1%, +3.0%] |
| [0.90,1.00) | 1450 | 1450 | 54 | 0.930 | 0.952 | +2.3% [+1.1%, +3.5%] | [+1.2%, +3.5%] | +3.1% [+2.0%, +4.2%] | +2.0% [+0.9%, +3.2%] | +1.8% [+0.6%, +3.0%] |
| [0.00,0.01) | 0 | | | | | | | | | | |
| [0.01,0.02) | 0 | | | | | | | | | | |
| [0.02,0.05) | 99 | 99 | 36 | 0.034 | 0.020 | -41.1% [-100.0%, +51.5%] | [-100.0%, +56.3%] | -36.3% [-100.0%, +73.6%] | -43.3% [-100.0%, +54.0%] | -44.8% [-100.0%, +41.9%] |
| [0.05,0.10) | 721 | 721 | 53 | 0.073 | 0.032 | -58.0% [-74.0%, -39.7%] | [-74.0%, -41.2%] | -61.0% [-79.3%, -35.4%] | -59.5% [-75.7%, -41.6%] | -60.6% [-75.7%, -43.8%] |
| [0.90,0.95) | 1041 | 1041 | 54 | 0.917 | 0.939 | +2.4% [+0.8%, +4.0%] | [+0.9%, +4.0%] | +3.7% [+2.3%, +5.1%] | +2.1% [+0.5%, +3.6%] | +1.8% [+0.2%, +3.4%] |
| [0.95,0.98) | 338 | 338 | 47 | 0.958 | 0.982 | +2.5% [+0.9%, +3.8%] | [+0.8%, +4.0%] | +1.3% [-3.7%, +4.2%] | +2.4% [+0.8%, +3.6%] | +2.2% [+0.7%, +3.5%] |
| [0.98,0.99) | 55 | 55 | 26 | 0.980 | 0.982 | +0.2% [-3.5%, +2.0%] | [-4.5%, +2.0%] | -1.9% [-9.7%, +2.0%] | +0.1% [-3.6%, +2.0%] | +0.0% [-3.7%, +1.9%] |
| [0.99,1.00) | 16 | 16 | 10 | 0.990 | 1.000 | +1.0% [+1.0%, +1.0%] | [+1.0%, +1.0%] | +1.0% [+1.0%, +1.0%] | +1.0% [+1.0%, +1.0%] | +0.9% [+0.9%, +0.9%] |

Headline, 4m:

| bin | n contracts | n markets | n events | mean buy price | win rate | return/$ EW [95% CI, market resample] | EW CI (event resample) | event-clustered mean [95% CI] | EW after pm4 | EW after pm7 |
|---|---|---|---|---|---|---|---|---|---|---|
| (0.00,0.10] | 1049 | 1049 | 53 | 0.075 | 0.036 | -53.2% [-68.1%, -36.4%] | [-68.5%, -36.4%] | -55.6% [-72.6%, -36.2%] | -54.9% [-69.5%, -39.5%] | -56.0% [-69.8%, -40.0%] |
| [0.90,1.00] | 1450 | 1450 | 54 | 0.930 | 0.952 | +2.3% [+1.1%, +3.5%] | [+1.2%, +3.5%] | +3.1% [+2.0%, +4.3%] | +2.0% [+0.9%, +3.2%] | +1.8% [+0.6%, +3.0%] |

Null-outcome bounds, 4m (gross, market resample):

| bin | n with outcome | n outcome null | null share | EW, outcome known [95% CI] | EW, nulls lose [95% CI] | EW, nulls win [95% CI] |
|---|---|---|---|---|---|---|
| (0.00,0.10] | 1049 | 40 | 0.037 | -53.2% [-67.8%, -37.0%] | -54.9% [-69.4%, -39.2%] | -9.2% [-29.0%, +11.8%] |
| [0.90,1.00] | 1450 | 64 | 0.042 | +2.3% [+1.1%, +3.5%] | -2.0% [-3.6%, -0.5%] | +2.6% [+1.4%, +3.7%] |

Headline, 2m:

| bin | n contracts | n markets | n events | mean buy price | win rate | return/$ EW [95% CI, market resample] | EW CI (event resample) | event-clustered mean [95% CI] | EW after pm4 | EW after pm7 |
|---|---|---|---|---|---|---|---|---|---|---|
| (0.00,0.10] | 21505 | 21505 | 56 | 0.055 | 0.030 | -47.5% [-52.3%, -42.4%] | [-54.5%, -40.3%] | -48.4% [-55.4%, -41.2%] | -49.4% [-54.1%, -44.6%] | -50.8% [-55.6%, -45.8%] |
| [0.90,1.00] | 22506 | 22506 | 56 | 0.952 | 0.964 | +1.3% [+1.0%, +1.6%] | [+0.9%, +1.7%] | +1.5% [+1.0%, +1.9%] | +1.1% [+0.9%, +1.4%] | +1.0% [+0.7%, +1.2%] |

Null-outcome bounds, 2m (gross, market resample):

| bin | n with outcome | n outcome null | null share | EW, outcome known [95% CI] | EW, nulls lose [95% CI] | EW, nulls win [95% CI] |
|---|---|---|---|---|---|---|
| (0.00,0.10] | 21505 | 777 | 0.035 | -47.5% [-52.5%, -42.5%] | -49.3% [-54.0%, -44.3%] | +15.1% [+8.0%, +22.5%] |
| [0.90,1.00] | 22506 | 952 | 0.041 | +1.3% [+1.1%, +1.6%] | -2.8% [-3.2%, -2.5%] | +1.5% [+1.3%, +1.8%] |

### 5m crypto horizon 1m: bins by ask price paid

| bin | n contracts | n markets | n events | mean buy price | win rate | return/$ EW [95% CI, market resample] | EW CI (event resample) | event-clustered mean [95% CI] | EW after pm4 | EW after pm7 |
|---|---|---|---|---|---|---|---|---|---|---|
| [0.00,0.10) | 39135 | 39135 | 56 | 0.036 | 0.015 | -64.3% [-67.8%, -60.6%] | [-68.7%, -59.7%] | -62.0% [-68.1%, -55.2%] | -65.6% [-69.1%, -62.0%] | -66.6% [-69.9%, -63.1%] |
| [0.10,0.20) | 10289 | 10289 | 56 | 0.137 | 0.094 | -33.5% [-37.7%, -29.5%] | [-38.6%, -28.2%] | -28.7% [-35.3%, -21.2%] | -35.8% [-39.6%, -31.8%] | -37.3% [-41.1%, -33.4%] |
| [0.20,0.30) | 5805 | 5805 | 56 | 0.241 | 0.195 | -19.1% [-23.5%, -14.7%] | [-24.0%, -14.1%] | -20.1% [-25.3%, -14.8%] | -21.5% [-25.6%, -17.3%] | -23.2% [-27.2%, -19.2%] |
| [0.30,0.40) | 4523 | 4523 | 56 | 0.343 | 0.293 | -14.8% [-18.7%, -11.0%] | [-19.2%, -10.1%] | -9.6% [-16.9%, -0.1%] | -17.0% [-20.8%, -13.3%] | -18.6% [-22.3%, -14.9%] |
| [0.40,0.50) | 4225 | 4225 | 56 | 0.444 | 0.405 | -8.9% [-12.4%, -5.6%] | [-12.4%, -5.6%] | -10.8% [-15.8%, -6.5%] | -10.9% [-14.1%, -7.7%] | -12.3% [-15.6%, -9.1%] |
| [0.50,0.60) | 4061 | 3597 | 56 | 0.545 | 0.560 | +2.5% [+0.2%, +4.8%] | [-0.2%, +5.2%] | +3.3% [-0.3%, +7.4%] | +0.7% [-1.7%, +3.0%] | -0.7% [-3.0%, +1.7%] |
| [0.60,0.70) | 4361 | 4361 | 56 | 0.646 | 0.673 | +4.3% [+2.1%, +6.4%] | [+1.8%, +6.8%] | +3.7% [+0.5%, +6.9%] | +2.8% [+0.7%, +4.9%] | +1.8% [-0.4%, +3.9%] |
| [0.70,0.80) | 5237 | 5237 | 56 | 0.747 | 0.787 | +5.3% [+3.8%, +6.8%] | [+3.8%, +6.8%] | +5.0% [+2.9%, +6.9%] | +4.3% [+2.7%, +5.7%] | +3.5% [+2.0%, +4.9%] |
| [0.80,0.90) | 7893 | 7893 | 56 | 0.849 | 0.872 | +2.6% [+1.8%, +3.5%] | [+1.6%, +3.7%] | +2.1% [+0.8%, +3.3%] | +2.0% [+1.1%, +2.9%] | +1.6% [+0.7%, +2.4%] |
| [0.90,1.00) | 33307 | 33307 | 56 | 0.964 | 0.977 | +1.3% [+1.2%, +1.5%] | [+1.1%, +1.6%] | +1.3% [+1.0%, +1.6%] | +1.2% [+1.0%, +1.4%] | +1.1% [+0.9%, +1.2%] |
| [0.00,0.01) | 609 | 609 | 54 | 0.005 | 0.000 | -100.0% [-100.0%, -100.0%] | [-100.0%, -100.0%] | -100.0% [-100.0%, -100.0%] | -100.0% [-100.0%, -100.0%] | -100.0% [-100.0%, -100.0%] |
| [0.01,0.02) | 9466 | 9466 | 56 | 0.010 | 0.002 | -76.8% [-86.3%, -66.2%] | [-85.6%, -67.6%] | -79.7% [-89.4%, -67.9%] | -77.6% [-86.8%, -67.5%] | -78.3% [-86.2%, -68.4%] |
| [0.02,0.05) | 16769 | 16769 | 56 | 0.028 | 0.010 | -64.4% [-69.7%, -58.9%] | [-71.2%, -56.4%] | -56.2% [-67.3%, -42.8%] | -65.7% [-70.9%, -60.4%] | -66.7% [-71.6%, -61.5%] |
| [0.05,0.10) | 12291 | 12291 | 56 | 0.068 | 0.032 | -52.8% [-57.5%, -48.1%] | [-57.9%, -47.6%] | -50.5% [-57.5%, -42.7%] | -54.5% [-59.1%, -49.9%] | -55.7% [-60.0%, -51.2%] |
| [0.90,0.95) | 7573 | 7573 | 55 | 0.922 | 0.948 | +2.8% [+2.3%, +3.3%] | [+2.0%, +3.5%] | +2.5% [+1.6%, +3.4%] | +2.5% [+1.9%, +3.0%] | +2.3% [+1.7%, +2.8%] |
| [0.95,0.98) | 10522 | 10522 | 56 | 0.962 | 0.976 | +1.5% [+1.2%, +1.8%] | [+1.1%, +1.8%] | +1.6% [+1.2%, +2.0%] | +1.3% [+1.0%, +1.6%] | +1.2% [+0.9%, +1.5%] |
| [0.98,0.99) | 5850 | 5850 | 56 | 0.980 | 0.988 | +0.8% [+0.5%, +1.1%] | [+0.4%, +1.1%] | +0.9% [+0.5%, +1.2%] | +0.7% [+0.4%, +1.0%] | +0.6% [+0.3%, +0.9%] |
| [0.99,1.00) | 9362 | 9362 | 55 | 0.991 | 0.994 | +0.3% [+0.1%, +0.5%] | [+0.1%, +0.5%] | -0.3% [-1.1%, +0.3%] | +0.3% [+0.1%, +0.4%] | +0.2% [+0.1%, +0.4%] |

Headline, 1m:

| bin | n contracts | n markets | n events | mean buy price | win rate | return/$ EW [95% CI, market resample] | EW CI (event resample) | event-clustered mean [95% CI] | EW after pm4 | EW after pm7 |
|---|---|---|---|---|---|---|---|---|---|---|
| (0.00,0.10] | 40770 | 40770 | 56 | 0.038 | 0.017 | -63.6% [-67.0%, -60.0%] | [-67.9%, -59.0%] | -61.4% [-67.4%, -54.9%] | -64.9% [-68.2%, -61.5%] | -65.9% [-69.1%, -62.6%] |
| [0.90,1.00] | 33307 | 33307 | 56 | 0.964 | 0.977 | +1.3% [+1.2%, +1.5%] | [+1.1%, +1.6%] | +1.3% [+1.0%, +1.6%] | +1.2% [+1.0%, +1.4%] | +1.1% [+0.9%, +1.2%] |

Null-outcome bounds, 1m (gross, market resample):

| bin | n with outcome | n outcome null | null share | EW, outcome known [95% CI] | EW, nulls lose [95% CI] | EW, nulls win [95% CI] |
|---|---|---|---|---|---|---|
| (0.00,0.10] | 40770 | 1553 | 0.037 | -63.6% [-67.0%, -60.1%] | -64.9% [-68.2%, -61.4%] | +28.6% [+20.8%, +37.2%] |
| [0.90,1.00] | 33307 | 1698 | 0.049 | +1.3% [+1.2%, +1.5%] | -3.6% [-3.9%, -3.3%] | +1.5% [+1.4%, +1.7%] |

### 5m crypto horizon 30s: bins by ask price paid

| bin | n contracts | n markets | n events | mean buy price | win rate | return/$ EW [95% CI, market resample] | EW CI (event resample) | event-clustered mean [95% CI] | EW after pm4 | EW after pm7 |
|---|---|---|---|---|---|---|---|---|---|---|
| [0.00,0.10) | 53059 | 53059 | 56 | 0.026 | 0.010 | -72.5% [-75.6%, -69.5%] | [-76.3%, -68.5%] | -72.8% [-77.5%, -68.0%] | -73.6% [-76.5%, -70.3%] | -74.3% [-77.1%, -71.2%] |
| [0.10,0.20) | 6429 | 6429 | 56 | 0.137 | 0.082 | -41.4% [-46.2%, -36.5%] | [-47.1%, -35.3%] | -38.4% [-45.2%, -31.1%] | -43.3% [-48.2%, -38.4%] | -44.7% [-49.1%, -40.1%] |
| [0.20,0.30) | 3364 | 3364 | 56 | 0.241 | 0.166 | -31.4% [-36.5%, -26.0%] | [-39.0%, -24.0%] | -29.4% [-38.7%, -18.8%] | -33.4% [-38.3%, -28.2%] | -34.8% [-39.8%, -29.8%] |
| [0.30,0.40) | 2667 | 2667 | 56 | 0.343 | 0.262 | -23.6% [-28.6%, -18.8%] | [-29.0%, -18.0%] | -23.5% [-29.5%, -17.3%] | -25.6% [-30.3%, -20.9%] | -27.0% [-31.7%, -22.4%] |
| [0.40,0.50) | 2280 | 2280 | 55 | 0.444 | 0.371 | -16.1% [-20.7%, -11.7%] | [-21.0%, -11.2%] | -15.7% [-20.9%, -10.5%] | -18.0% [-22.3%, -13.6%] | -19.3% [-23.5%, -14.9%] |
| [0.50,0.60) | 2231 | 1958 | 55 | 0.545 | 0.593 | +8.6% [+5.5%, +11.8%] | [+5.2%, +12.1%] | +8.1% [+4.5%, +11.8%] | +6.7% [+3.6%, +9.8%] | +5.2% [+2.1%, +8.4%] |
| [0.60,0.70) | 2491 | 2491 | 56 | 0.646 | 0.707 | +9.5% [+6.7%, +12.2%] | [+6.6%, +12.2%] | +9.3% [+5.7%, +12.7%] | +8.0% [+5.1%, +10.7%] | +6.8% [+4.2%, +9.5%] |
| [0.70,0.80) | 3085 | 3085 | 55 | 0.747 | 0.804 | +7.6% [+5.7%, +9.4%] | [+5.3%, +9.8%] | +8.0% [+5.5%, +10.5%] | +6.5% [+4.6%, +8.3%] | +5.7% [+3.9%, +7.5%] |
| [0.80,0.90) | 4936 | 4936 | 56 | 0.850 | 0.892 | +5.0% [+4.0%, +6.0%] | [+3.8%, +6.2%] | +4.7% [+3.2%, +6.0%] | +4.4% [+3.4%, +5.4%] | +3.9% [+2.9%, +5.0%] |
| [0.90,1.00) | 32519 | 32519 | 56 | 0.971 | 0.982 | +1.2% [+1.0%, +1.3%] | [+1.0%, +1.4%] | +0.8% [+0.0%, +1.4%] | +1.1% [+0.9%, +1.2%] | +1.0% [+0.8%, +1.1%] |
| [0.00,0.01) | 1758 | 1758 | 56 | 0.005 | 0.001 | -93.7% [-100.0%, -81.0%] | [-100.0%, -78.3%] | -91.4% [-100.0%, -74.1%] | -93.9% [-100.0%, -81.8%] | -94.1% [-100.0%, -82.3%] |
| [0.01,0.02) | 22250 | 22250 | 56 | 0.010 | 0.002 | -80.2% [-85.6%, -73.9%] | [-85.9%, -74.2%] | -83.3% [-88.4%, -77.8%] | -81.0% [-86.2%, -74.9%] | -81.5% [-87.0%, -76.0%] |
| [0.02,0.05) | 19252 | 19252 | 56 | 0.027 | 0.008 | -71.5% [-76.1%, -66.8%] | [-77.3%, -65.2%] | -69.0% [-76.9%, -59.7%] | -72.6% [-76.9%, -67.9%] | -73.3% [-77.7%, -68.9%] |
| [0.05,0.10) | 9799 | 9799 | 56 | 0.066 | 0.032 | -53.3% [-58.5%, -48.1%] | [-59.3%, -47.2%] | -52.0% [-60.8%, -40.9%] | -55.0% [-59.8%, -49.9%] | -56.1% [-61.1%, -51.2%] |
| [0.90,0.95) | 4945 | 4945 | 56 | 0.923 | 0.948 | +2.8% [+2.1%, +3.4%] | [+2.1%, +3.4%] | +2.0% [+0.4%, +3.2%] | +2.5% [+1.8%, +3.1%] | +2.2% [+1.5%, +2.9%] |
| [0.95,0.98) | 8827 | 8827 | 56 | 0.962 | 0.977 | +1.6% [+1.3%, +1.9%] | [+1.3%, +1.9%] | +1.8% [+1.3%, +2.2%] | +1.5% [+1.1%, +1.8%] | +1.3% [+1.0%, +1.7%] |
| [0.98,0.99) | 5692 | 5692 | 56 | 0.980 | 0.989 | +0.9% [+0.6%, +1.1%] | [+0.6%, +1.1%] | +0.6% [+0.1%, +1.1%] | +0.8% [+0.5%, +1.1%] | +0.7% [+0.4%, +1.0%] |
| [0.99,1.00) | 13055 | 13055 | 55 | 0.991 | 0.995 | +0.4% [+0.3%, +0.5%] | [+0.2%, +0.6%] | +0.4% [+0.2%, +0.6%] | +0.4% [+0.3%, +0.5%] | +0.4% [+0.2%, +0.5%] |

Headline, 30s:

| bin | n contracts | n markets | n events | mean buy price | win rate | return/$ EW [95% CI, market resample] | EW CI (event resample) | event-clustered mean [95% CI] | EW after pm4 | EW after pm7 |
|---|---|---|---|---|---|---|---|---|---|---|
| (0.00,0.10] | 54107 | 54107 | 56 | 0.028 | 0.010 | -72.3% [-75.3%, -69.1%] | [-76.0%, -68.3%] | -70.6% [-75.6%, -65.2%] | -73.3% [-76.3%, -70.3%] | -74.0% [-76.9%, -71.0%] |
| [0.90,1.00] | 32519 | 32519 | 56 | 0.971 | 0.982 | +1.2% [+1.0%, +1.3%] | [+1.0%, +1.4%] | +0.8% [+0.0%, +1.4%] | +1.1% [+0.9%, +1.2%] | +1.0% [+0.8%, +1.1%] |

Null-outcome bounds, 30s (gross, market resample):

| bin | n with outcome | n outcome null | null share | EW, outcome known [95% CI] | EW, nulls lose [95% CI] | EW, nulls win [95% CI] |
|---|---|---|---|---|---|---|
| (0.00,0.10] | 54107 | 2090 | 0.037 | -72.3% [-75.3%, -69.1%] | -73.3% [-76.3%, -70.3%] | +57.6% [+47.4%, +68.6%] |
| [0.90,1.00] | 32519 | 2051 | 0.059 | +1.2% [+1.0%, +1.3%] | -4.8% [-5.1%, -4.5%] | +1.4% [+1.2%, +1.5%] |


## E. Polymarket — crypto Up/Down 5m / 15m / 4h, Kaggle polyorderbooks/polymarket-crypto-orderbooks-1s-l2

686 markets closing 2026-08-21 → 08-24 (4 days), ~1-second L2 snapshots per outcome token, winner on every row. Buy each outcome at its own best ask h before the scheduled end; the snapshot must be ≤15 s old, two-sided, uncrossed and ≤ 0.05 wide (without that filter, 0.01/0.99 books fill the ≥ 0.90 bin). Near the close many books are one-sided (the likely winner has no asks), so short-horizon rows drop the most decided markets. Market cluster = market; event cluster = UTC hour.

### Calibration (Up midpoint)

| sample | n | markets | events | mean price | outcome rate | Brier | slope b [95% CI event resample] | slope CI (market resample) | intercept a [95% CI] |
|---|---|---|---|---|---|---|---|---|---|
| Polymarket crypto 5m:4m | 303 | 303 | 80 | 0.489 | 0.505 | 0.1898 | 1.192 [0.883, 1.626] | [0.889, 1.580] | +0.095 [-0.231, +0.440] |
| Polymarket crypto 5m:2m | 280 | 280 | 81 | 0.491 | 0.496 | 0.1029 | 1.010 [0.825, 1.303] | [0.827, 1.286] | +0.052 [-0.298, +0.414] |
| Polymarket crypto 5m:1m | 185 | 185 | 75 | 0.485 | 0.492 | 0.0830 | 0.805 [0.656, 1.061] | [0.654, 1.077] | +0.043 [-0.435, +0.567] |
| Polymarket crypto 5m:30s | 70 | 70 | 42 | 0.483 | 0.529 | 0.0718 | 0.989 [0.732, 1.865] | [0.739, 2.019] | +0.775 [-0.251, +2.592] |
| Polymarket crypto 15m:14m | 198 | 198 | 77 | 0.496 | 0.561 | 0.2413 | 0.803 [0.097, 1.631] | [0.237, 1.498] | +0.266 [-0.061, +0.614] |
| Polymarket crypto 15m:10m | 183 | 183 | 77 | 0.514 | 0.546 | 0.1872 | 0.910 [0.632, 1.306] | [0.675, 1.252] | +0.177 [-0.190, +0.551] |
| Polymarket crypto 15m:5m | 178 | 178 | 77 | 0.554 | 0.556 | 0.0931 | 1.281 [0.987, 1.935] | [0.989, 1.884] | -0.013 [-0.569, +0.463] |
| Polymarket crypto 15m:1m | 83 | 83 | 54 | 0.532 | 0.542 | 0.0163 | 3.237 [1.987, 3.825] | [2.058, 3.785] | +1.355 [+0.010, +2.506] |
| Polymarket crypto 4h:3h | 51 | 51 | 19 | 0.602 | 0.667 | 0.2346 | 0.378 [-0.438, 1.744] | [-0.193, 1.219] | +0.516 [-0.538, +1.604] |
| Polymarket crypto 4h:1h | 46 | 46 | 19 | 0.604 | 0.652 | 0.1071 | 1.066 [0.748, 2.800] | [0.730, 2.439] | +0.453 [-0.985, +3.004] |
| Polymarket crypto 4h:30m | 41 | 41 | 18 | 0.589 | 0.610 | 0.0436 | 1.570 [1.190, 8.273] | [1.095, 8.272] | +0.528 [-0.861, +4.214] |
| Polymarket crypto 4h:5m | 28 | 28 | 17 | 0.694 | 0.679 | 0.0294 | 1.547 [1.075, 2.025] | [1.070, 2.012] | -1.716 [-3.970, -0.104] |

Headline, 5m:4m:

| bin | n contracts | n markets | n events | mean buy price | win rate | return/$ EW [95% CI, market resample] | EW CI (event resample) | event-clustered mean [95% CI] | EW after pm4 | EW after pm7 |
|---|---|---|---|---|---|---|---|---|---|---|
| (0.00,0.10] | 16 | 16 | 14 | 0.085 | 0.000 | -100.0% [-100.0%, -100.0%] | [-100.0%, -100.0%] | -100.0% [-100.0%, -100.0%] | -100.0% [-100.0%, -100.0%] | -100.0% [-100.0%, -100.0%] |
| [0.90,1.00] | 21 | 21 | 18 | 0.930 | 0.952 | +2.3% [-8.3%, +8.1%] | [-9.4%, +8.1%] | +1.5% [-10.9%, +8.2%] | +2.0% [-8.6%, +7.8%] | +1.8% [-8.8%, +7.5%] |

Headline, 5m:2m:

| bin | n contracts | n markets | n events | mean buy price | win rate | return/$ EW [95% CI, market resample] | EW CI (event resample) | event-clustered mean [95% CI] | EW after pm4 | EW after pm7 |
|---|---|---|---|---|---|---|---|---|---|---|
| (0.00,0.10] | 148 | 148 | 67 | 0.053 | 0.047 | -16.3% [-75.6%, +55.8%] | [-72.6%, +54.3%] | -26.6% [-74.3%, +33.9%] | -19.3% [-75.4%, +53.4%] | -21.5% [-77.1%, +48.9%] |
| [0.90,1.00] | 157 | 157 | 67 | 0.961 | 0.962 | +0.2% [-3.3%, +3.0%] | [-3.1%, +2.9%] | -0.2% [-4.4%, +3.2%] | -0.0% [-3.4%, +2.9%] | -0.1% [-3.5%, +2.7%] |

Headline, 5m:1m:

| bin | n contracts | n markets | n events | mean buy price | win rate | return/$ EW [95% CI, market resample] | EW CI (event resample) | event-clustered mean [95% CI] | EW after pm4 | EW after pm7 |
|---|---|---|---|---|---|---|---|---|---|---|
| (0.00,0.10] | 146 | 146 | 67 | 0.038 | 0.068 | +28.7% [-50.3%, +115.6%] | [-45.3%, +114.9%] | +46.1% [-47.8%, +174.6%] | +24.0% [-49.9%, +110.3%] | +20.7% [-50.0%, +109.5%] |
| [0.90,1.00] | 145 | 145 | 67 | 0.979 | 0.945 | -3.5% [-7.7%, +0.1%] | [-7.4%, -0.0%] | -4.7% [-10.6%, +0.3%] | -3.6% [-7.7%, -0.0%] | -3.6% [-7.9%, -0.1%] |

Headline, 5m:30s:

| bin | n contracts | n markets | n events | mean buy price | win rate | return/$ EW [95% CI, market resample] | EW CI (event resample) | event-clustered mean [95% CI] | EW after pm4 | EW after pm7 |
|---|---|---|---|---|---|---|---|---|---|---|
| (0.00,0.10] | 56 | 56 | 38 | 0.035 | 0.036 | -42.0% [-100.0%, +51.8%] | [-100.0%, +54.8%] | -14.5% [-100.0%, +123.7%] | -44.1% [-100.0%, +46.3%] | -45.5% [-100.0%, +42.4%] |
| [0.90,1.00] | 56 | 56 | 40 | 0.982 | 0.964 | -1.8% [-7.4%, +2.1%] | [-7.7%, +2.0%] | -3.2% [-10.9%, +2.1%] | -1.9% [-7.4%, +2.0%] | -1.9% [-7.5%, +1.9%] |

Headline, 15m:14m:

| bin | n contracts | n markets | n events | mean buy price | win rate | return/$ EW [95% CI, market resample] | EW CI (event resample) | event-clustered mean [95% CI] | EW after pm4 | EW after pm7 |
|---|---|---|---|---|---|---|---|---|---|---|
| (0.00,0.10] | 0 | | | | | | | | | | |
| [0.90,1.00] | 0 | | | | | | | | | | |

Headline, 15m:10m:

| bin | n contracts | n markets | n events | mean buy price | win rate | return/$ EW [95% CI, market resample] | EW CI (event resample) | event-clustered mean [95% CI] | EW after pm4 | EW after pm7 |
|---|---|---|---|---|---|---|---|---|---|---|
| (0.00,0.10] | 26 | 26 | 19 | 0.058 | 0.000 | -100.0% [-100.0%, -100.0%] | [-100.0%, -100.0%] | -100.0% [-100.0%, -100.0%] | -100.0% [-100.0%, -100.0%] | -100.0% [-100.0%, -100.0%] |
| [0.90,1.00] | 33 | 33 | 26 | 0.949 | 0.939 | -1.3% [-10.9%, +5.5%] | [-12.0%, +5.6%] | -2.7% [-15.1%, +5.9%] | -1.5% [-11.2%, +5.4%] | -1.6% [-11.2%, +5.2%] |

Headline, 15m:5m:

| bin | n contracts | n markets | n events | mean buy price | win rate | return/$ EW [95% CI, market resample] | EW CI (event resample) | event-clustered mean [95% CI] | EW after pm4 | EW after pm7 |
|---|---|---|---|---|---|---|---|---|---|---|
| (0.00,0.10] | 84 | 84 | 53 | 0.044 | 0.024 | -66.9% [-100.0%, -14.0%] | [-100.0%, -13.4%] | -58.1% [-100.0%, +15.3%] | -68.1% [-100.0%, -17.1%] | -68.9% [-100.0%, -19.3%] |
| [0.90,1.00] | 93 | 93 | 55 | 0.968 | 0.978 | +1.1% [-2.4%, +3.6%] | [-2.6%, +3.5%] | +0.4% [-4.5%, +3.5%] | +0.9% [-2.6%, +3.4%] | +0.8% [-2.7%, +3.3%] |

Headline, 15m:1m:

| bin | n contracts | n markets | n events | mean buy price | win rate | return/$ EW [95% CI, market resample] | EW CI (event resample) | event-clustered mean [95% CI] | EW after pm4 | EW after pm7 |
|---|---|---|---|---|---|---|---|---|---|---|
| (0.00,0.10] | 69 | 69 | 49 | 0.024 | 0.000 | -100.0% [-100.0%, -100.0%] | [-100.0%, -100.0%] | -100.0% [-100.0%, -100.0%] | -100.0% [-100.0%, -100.0%] | -100.0% [-100.0%, -100.0%] |
| [0.90,1.00] | 71 | 71 | 49 | 0.988 | 1.000 | +1.2% [+0.9%, +1.6%] | [+0.8%, +1.8%] | +1.2% [+0.9%, +1.5%] | +1.2% [+0.9%, +1.6%] | +1.1% [+0.8%, +1.5%] |

Headline, 4h:3h:

| bin | n contracts | n markets | n events | mean buy price | win rate | return/$ EW [95% CI, market resample] | EW CI (event resample) | event-clustered mean [95% CI] | EW after pm4 | EW after pm7 |
|---|---|---|---|---|---|---|---|---|---|---|
| (0.00,0.10] | 2 | 2 | 1 | 0.035 | 0.000 | -100.0% [-100.0%, -100.0%] | [–, –] | -100.0% [–, –] | -100.0% [-100.0%, -100.0%] | -100.0% [-100.0%, -100.0%] |
| [0.90,1.00] | 5 | 5 | 2 | 0.930 | 0.800 | -14.5% [-57.6%, +9.7%] | [-20.9%, +11.1%] | -4.9% [-20.9%, +11.1%] | -14.7% [-57.7%, +9.3%] | -14.9% [-57.7%, +9.0%] |

Headline, 4h:1h:

| bin | n contracts | n markets | n events | mean buy price | win rate | return/$ EW [95% CI, market resample] | EW CI (event resample) | event-clustered mean [95% CI] | EW after pm4 | EW after pm7 |
|---|---|---|---|---|---|---|---|---|---|---|
| (0.00,0.10] | 24 | 24 | 14 | 0.042 | 0.000 | -100.0% [-100.0%, -100.0%] | [-100.0%, -100.0%] | -100.0% [-100.0%, -100.0%] | -100.0% [-100.0%, -100.0%] | -100.0% [-100.0%, -100.0%] |
| [0.90,1.00] | 24 | 24 | 14 | 0.974 | 1.000 | +2.7% [+1.9%, +3.8%] | [+1.7%, +3.8%] | +2.6% [+1.8%, +3.6%] | +2.6% [+1.7%, +3.6%] | +2.5% [+1.7%, +3.5%] |

Headline, 4h:30m:

| bin | n contracts | n markets | n events | mean buy price | win rate | return/$ EW [95% CI, market resample] | EW CI (event resample) | event-clustered mean [95% CI] | EW after pm4 | EW after pm7 |
|---|---|---|---|---|---|---|---|---|---|---|
| (0.00,0.10] | 29 | 29 | 14 | 0.034 | 0.000 | -100.0% [-100.0%, -100.0%] | [-100.0%, -100.0%] | -100.0% [-100.0%, -100.0%] | -100.0% [-100.0%, -100.0%] | -100.0% [-100.0%, -100.0%] |
| [0.90,1.00] | 31 | 31 | 15 | 0.978 | 1.000 | +2.3% [+1.4%, +3.3%] | [+1.4%, +3.6%] | +2.7% [+1.4%, +4.1%] | +2.2% [+1.4%, +3.1%] | +2.1% [+1.3%, +3.1%] |

Headline, 4h:5m:

| bin | n contracts | n markets | n events | mean buy price | win rate | return/$ EW [95% CI, market resample] | EW CI (event resample) | event-clustered mean [95% CI] | EW after pm4 | EW after pm7 |
|---|---|---|---|---|---|---|---|---|---|---|
| (0.00,0.10] | 26 | 26 | 16 | 0.022 | 0.000 | -100.0% [-100.0%, -100.0%] | [-100.0%, -100.0%] | -100.0% [-100.0%, -100.0%] | -100.0% [-100.0%, -100.0%] | -100.0% [-100.0%, -100.0%] |
| [0.90,1.00] | 26 | 26 | 16 | 0.989 | 1.000 | +1.1% [+0.6%, +1.8%] | [+0.6%, +1.8%] | +1.1% [+0.6%, +1.6%] | +1.1% [+0.6%, +1.7%] | +1.0% [+0.5%, +1.7%] |


## F. Polymarket — 2026 FIFA World Cup, Kaggle madrading/polymarket-2026-fifa-world-cup-order-book

Every Polymarket market attached to the tournament (futures, group/match winners, totals, props), official resolutions, end dates 2026-03-12 → 08-31. Price = the first outcome's book at the close of the last 1-minute bar at or before `end_date − h` (bars print only on quote events; ≤6 h old), market still open then, two-sided with spread ≤ 0.05. Buy first outcome at the ask, second at 1 − bid. For match markets `end_date` is about kick-off, so 1h is pre-match. Event cluster = Polymarket event (e.g. one match's win/draw/win markets).

### Calibration (first-outcome midpoint)

| sample | n | markets | events | mean price | outcome rate | Brier | slope b [95% CI event resample] | slope CI (market resample) | intercept a [95% CI] |
|---|---|---|---|---|---|---|---|---|---|
| World Cup 7d | 2052 | 2052 | 296 | 0.261 | 0.270 | 0.1159 | 1.035 [0.923, 1.173] | [0.942, 1.145] | +0.105 [-0.158, +0.386] |
| World Cup 1d | 10074 | 10074 | 877 | 0.252 | 0.249 | 0.1171 | 1.049 [0.980, 1.119] | [1.004, 1.096] | +0.004 [-0.142, +0.149] |
| World Cup 1h | 14157 | 14157 | 859 | 0.230 | 0.216 | 0.1080 | 1.026 [0.963, 1.089] | [0.988, 1.067] | -0.120 [-0.253, +0.013] |
| World Cup 1h, Yes/No markets | 8780 | 8780 | 757 | 0.146 | 0.127 | 0.0768 | 0.980 [0.916, 1.052] | [0.927, 1.037] | -0.264 [-0.388, -0.133] |
| World Cup 1h, other two-way (Over/Under, team v team, …) | 5377 | 5377 | 204 | 0.369 | 0.361 | 0.1589 | 1.027 [0.914, 1.150] | [0.964, 1.095] | -0.046 [-0.225, +0.134] |

### World Cup horizon 7d: bins by buy price

| bin | n contracts | n markets | n events | mean buy price | win rate | return/$ EW [95% CI, market resample] | EW CI (event resample) | event-clustered mean [95% CI] | EW after pm4 | EW after pm7 |
|---|---|---|---|---|---|---|---|---|---|---|
| [0.00,0.10) | 891 | 891 | 203 | 0.042 | 0.027 | -33.6% [-65.8%, +6.9%] | [-65.3%, +7.1%] | -50.9% [-74.1%, -22.9%] | -36.0% [-68.0%, +4.4%] | -37.8% [-69.4%, +2.3%] |
| [0.10,0.20) | 378 | 378 | 171 | 0.138 | 0.124 | -9.2% [-33.1%, +15.6%] | [-36.0%, +20.5%] | -12.2% [-41.7%, +19.7%] | -12.3% [-35.0%, +12.4%] | -14.4% [-38.1%, +9.9%] |
| [0.20,0.30) | 297 | 297 | 154 | 0.248 | 0.269 | +8.8% [-11.3%, +29.5%] | [-12.1%, +29.7%] | +14.8% [-9.3%, +39.4%] | +5.6% [-13.8%, +26.5%] | +3.4% [-15.9%, +22.7%] |
| [0.30,0.40) | 208 | 208 | 112 | 0.342 | 0.327 | -4.1% [-22.5%, +15.1%] | [-22.7%, +14.7%] | -17.3% [-36.9%, +3.4%] | -6.5% [-24.8%, +11.7%] | -8.3% [-25.7%, +9.1%] |
| [0.40,0.50) | 244 | 244 | 133 | 0.448 | 0.475 | +6.2% [-7.6%, +20.2%] | [-9.4%, +22.1%] | +0.3% [-15.4%, +17.1%] | +4.0% [-9.7%, +18.1%] | +2.3% [-11.0%, +16.1%] |
| [0.50,0.60) | 247 | 213 | 125 | 0.545 | 0.510 | -6.5% [-16.3%, +3.0%] | [-16.7%, +3.8%] | -7.2% [-19.4%, +4.9%] | -8.2% [-17.8%, +1.3%] | -9.4% [-18.8%, +0.1%] |
| [0.60,0.70) | 191 | 191 | 107 | 0.643 | 0.613 | -4.6% [-15.2%, +6.0%] | [-15.6%, +6.1%] | +0.0% [-12.2%, +12.0%] | -6.0% [-16.7%, +4.6%] | -6.9% [-17.6%, +3.1%] |
| [0.70,0.80) | 294 | 294 | 149 | 0.745 | 0.735 | -1.4% [-8.3%, +5.3%] | [-8.3%, +5.4%] | +1.4% [-6.0%, +8.7%] | -2.4% [-9.3%, +4.2%] | -3.1% [-9.9%, +3.5%] |
| [0.80,0.90) | 277 | 277 | 160 | 0.845 | 0.809 | -4.4% [-9.9%, +1.0%] | [-10.5%, +1.4%] | -4.3% [-10.9%, +2.0%] | -5.0% [-10.5%, +0.5%] | -5.4% [-10.9%, -0.1%] |
| [0.90,1.00) | 1077 | 1077 | 217 | 0.967 | 0.960 | -0.7% [-2.0%, +0.4%] | [-2.1%, +0.5%] | +0.2% [-1.6%, +1.6%] | -0.9% [-2.1%, +0.3%] | -1.0% [-2.2%, +0.2%] |
| [0.00,0.01) | 130 | 130 | 58 | 0.005 | 0.008 | -14.5% [-100.0%, +156.4%] | [-100.0%, +154.5%] | -80.8% [-100.0%, -42.5%] | -17.8% [-100.0%, +146.6%] | -20.1% [-100.0%, +139.8%] |
| [0.01,0.02) | 89 | 89 | 61 | 0.014 | 0.011 | +12.4% [-100.0%, +237.1%] | [-100.0%, +265.9%] | -45.4% [-100.0%, +63.9%] | +8.1% [-100.0%, +224.2%] | +5.1% [-100.0%, +215.2%] |
| [0.02,0.05) | 308 | 308 | 121 | 0.032 | 0.016 | -54.6% [-93.1%, -5.8%] | [-90.2%, -9.4%] | -73.4% [-94.4%, -46.5%] | -56.2% [-93.3%, -9.1%] | -57.4% [-93.5%, -13.1%] |
| [0.05,0.10) | 364 | 364 | 158 | 0.071 | 0.047 | -33.9% [-63.5%, -1.2%] | [-63.2%, +0.6%] | -32.1% [-67.4%, +12.3%] | -36.3% [-64.4%, -3.6%] | -37.9% [-66.5%, -7.5%] |
| [0.90,0.95) | 277 | 277 | 128 | 0.923 | 0.913 | -1.0% [-4.7%, +2.4%] | [-5.0%, +2.6%] | +0.4% [-3.9%, +4.0%] | -1.3% [-5.0%, +2.1%] | -1.6% [-5.1%, +1.9%] |
| [0.95,0.98) | 279 | 279 | 139 | 0.962 | 0.953 | -0.9% [-3.6%, +1.4%] | [-3.5%, +1.3%] | -0.9% [-4.2%, +1.8%] | -1.1% [-3.7%, +1.4%] | -1.2% [-3.8%, +1.2%] |
| [0.98,0.99) | 131 | 131 | 85 | 0.986 | 0.977 | -0.9% [-3.9%, +1.5%] | [-3.7%, +1.5%] | -1.2% [-4.9%, +1.5%] | -0.9% [-4.0%, +1.4%] | -1.0% [-3.3%, +1.4%] |
| [0.99,1.00) | 390 | 390 | 136 | 0.995 | 0.992 | -0.3% [-1.3%, +0.5%] | [-1.3%, +0.5%] | -0.3% [-1.5%, +0.5%] | -0.3% [-1.3%, +0.5%] | -0.3% [-1.4%, +0.4%] |

Headline, 7d:

| bin | n contracts | n markets | n events | mean buy price | win rate | return/$ EW [95% CI, market resample] | EW CI (event resample) | event-clustered mean [95% CI] | EW after pm4 | EW after pm7 |
|---|---|---|---|---|---|---|---|---|---|---|
| (0.00,0.10] | 949 | 949 | 210 | 0.046 | 0.031 | -32.4% [-62.8%, +6.6%] | [-62.5%, +4.9%] | -48.2% [-70.9%, -20.3%] | -34.9% [-65.6%, +3.9%] | -36.6% [-65.2%, +0.1%] |
| [0.90,1.00] | 1077 | 1077 | 217 | 0.967 | 0.960 | -0.7% [-1.9%, +0.4%] | [-2.0%, +0.5%] | +0.2% [-1.4%, +1.6%] | -0.9% [-2.1%, +0.4%] | -1.0% [-2.2%, +0.2%] |

### World Cup horizon 1d: bins by buy price

| bin | n contracts | n markets | n events | mean buy price | win rate | return/$ EW [95% CI, market resample] | EW CI (event resample) | event-clustered mean [95% CI] | EW after pm4 | EW after pm7 |
|---|---|---|---|---|---|---|---|---|---|---|
| [0.00,0.10) | 4282 | 4282 | 498 | 0.037 | 0.023 | -22.8% [-57.0%, +36.7%] | [-60.5%, +38.7%] | +20.3% [-45.9%, +111.2%] | -25.7% [-58.9%, +28.4%] | -27.7% [-59.8%, +26.5%] |
| [0.10,0.20) | 1650 | 1650 | 532 | 0.142 | 0.144 | +1.1% [-10.9%, +13.5%] | [-13.1%, +16.2%] | +0.9% [-15.7%, +18.6%] | -2.3% [-14.5%, +9.9%] | -4.7% [-16.3%, +7.3%] |
| [0.20,0.30) | 1275 | 1275 | 466 | 0.246 | 0.226 | -7.8% [-17.4%, +2.0%] | [-17.9%, +2.6%] | -14.2% [-26.1%, -2.0%] | -10.5% [-19.6%, -1.4%] | -12.4% [-21.4%, -3.6%] |
| [0.30,0.40) | 1226 | 1226 | 464 | 0.342 | 0.308 | -9.7% [-17.2%, -2.0%] | [-16.9%, -2.3%] | -15.3% [-24.7%, -5.6%] | -12.0% [-19.2%, -4.5%] | -13.7% [-20.8%, -6.2%] |
| [0.40,0.50) | 1383 | 1383 | 461 | 0.446 | 0.430 | -3.8% [-9.6%, +2.2%] | [-9.0%, +1.4%] | -8.5% [-15.8%, -0.9%] | -5.9% [-11.6%, -0.2%] | -7.4% [-13.0%, -1.8%] |
| [0.50,0.60) | 1482 | 1224 | 428 | 0.543 | 0.526 | -3.1% [-6.7%, +0.7%] | [-6.4%, +0.3%] | +0.4% [-5.2%, +6.2%] | -4.8% [-8.5%, -1.2%] | -6.1% [-9.6%, -2.5%] |
| [0.60,0.70) | 1226 | 1226 | 466 | 0.645 | 0.661 | +2.6% [-1.5%, +6.8%] | [-1.2%, +6.4%] | +5.7% [+0.6%, +10.9%] | +1.1% [-3.0%, +5.3%] | +0.1% [-3.9%, +4.3%] |
| [0.70,0.80) | 1316 | 1316 | 470 | 0.744 | 0.751 | +1.0% [-2.0%, +4.2%] | [-2.4%, +4.4%] | +1.0% [-3.4%, +5.1%] | +0.0% [-3.1%, +3.1%] | -0.7% [-3.9%, +2.3%] |
| [0.80,0.90) | 1480 | 1480 | 537 | 0.847 | 0.830 | -2.0% [-4.3%, +0.3%] | [-4.6%, +0.6%] | -1.0% [-4.1%, +1.9%] | -2.6% [-4.8%, -0.4%] | -3.0% [-5.3%, -0.9%] |
| [0.90,1.00) | 4828 | 4828 | 566 | 0.970 | 0.968 | -0.2% [-0.7%, +0.3%] | [-0.8%, +0.3%] | -1.1% [-2.7%, +0.4%] | -0.3% [-0.9%, +0.2%] | -0.4% [-1.0%, +0.1%] |
| [0.00,0.01) | 461 | 461 | 161 | 0.006 | 0.004 | +147.9% [-100.0%, +612.7%] | [-100.0%, +661.0%] | +195.8% [-100.0%, +698.6%] | +138.4% [-100.0%, +615.2%] | +131.7% [-100.0%, +566.2%] |
| [0.01,0.02) | 993 | 993 | 267 | 0.012 | 0.007 | -37.2% [-79.9%, +13.2%] | [-94.6%, +57.8%] | -40.5% [-94.6%, +55.2%] | -39.5% [-80.6%, +9.4%] | -41.2% [-81.2%, +5.9%] |
| [0.02,0.05) | 1430 | 1430 | 335 | 0.032 | 0.015 | -58.3% [-75.7%, -38.1%] | [-76.5%, -37.5%] | -50.3% [-74.8%, -21.7%] | -59.9% [-76.6%, -40.7%] | -60.9% [-76.8%, -41.9%] |
| [0.05,0.10) | 1398 | 1398 | 421 | 0.068 | 0.048 | -32.6% [-47.9%, -15.7%] | [-49.5%, -14.7%] | -21.6% [-45.1%, +4.0%] | -35.0% [-49.4%, -18.6%] | -36.7% [-51.4%, -21.2%] |
| [0.90,0.95) | 1117 | 1117 | 390 | 0.922 | 0.915 | -0.8% [-2.5%, +0.9%] | [-2.8%, +1.0%] | -1.1% [-3.6%, +1.2%] | -1.1% [-3.0%, +0.7%] | -1.3% [-3.1%, +0.4%] |
| [0.95,0.98) | 1062 | 1062 | 345 | 0.964 | 0.960 | -0.4% [-1.7%, +0.7%] | [-1.8%, +0.8%] | -1.0% [-3.0%, +0.7%] | -0.6% [-1.8%, +0.6%] | -0.7% [-2.0%, +0.5%] |
| [0.98,0.99) | 651 | 651 | 267 | 0.985 | 0.988 | +0.3% [-0.7%, +1.1%] | [-0.7%, +1.1%] | -0.4% [-2.1%, +0.9%] | +0.2% [-0.7%, +1.0%] | +0.2% [-0.8%, +1.0%] |
| [0.99,1.00) | 1998 | 1998 | 327 | 0.995 | 0.995 | +0.0% [-0.3%, +0.3%] | [-0.5%, +0.4%] | -0.2% [-1.0%, +0.4%] | +0.0% [-0.3%, +0.3%] | +0.0% [-0.3%, +0.3%] |

Headline, 1d:

| bin | n contracts | n markets | n events | mean buy price | win rate | return/$ EW [95% CI, market resample] | EW CI (event resample) | event-clustered mean [95% CI] | EW after pm4 | EW after pm7 |
|---|---|---|---|---|---|---|---|---|---|---|
| (0.00,0.10] | 4482 | 4482 | 524 | 0.039 | 0.026 | -22.2% [-55.6%, +34.7%] | [-59.1%, +34.0%] | +21.3% [-41.8%, +104.7%] | -25.1% [-56.3%, +27.1%] | -27.2% [-57.5%, +22.1%] |
| [0.90,1.00] | 4828 | 4828 | 566 | 0.970 | 0.968 | -0.2% [-0.7%, +0.3%] | [-0.8%, +0.4%] | -1.1% [-2.7%, +0.4%] | -0.3% [-0.9%, +0.2%] | -0.4% [-1.0%, +0.1%] |

### World Cup horizon 1h: bins by buy price

| bin | n contracts | n markets | n events | mean buy price | win rate | return/$ EW [95% CI, market resample] | EW CI (event resample) | event-clustered mean [95% CI] | EW after pm4 | EW after pm7 |
|---|---|---|---|---|---|---|---|---|---|---|
| [0.00,0.10) | 6677 | 6677 | 500 | 0.036 | 0.021 | -51.6% [-61.8%, -40.6%] | [-66.7%, -30.9%] | -21.3% [-49.7%, +13.8%] | -53.4% [-62.9%, -43.0%] | -54.6% [-64.3%, -44.3%] |
| [0.10,0.20) | 2131 | 2131 | 617 | 0.143 | 0.145 | +1.3% [-9.4%, +12.1%] | [-11.2%, +14.1%] | -0.9% [-15.2%, +13.4%] | -2.0% [-12.2%, +8.4%] | -4.4% [-14.2%, +6.1%] |
| [0.20,0.30) | 1681 | 1681 | 524 | 0.245 | 0.220 | -9.6% [-17.8%, -1.4%] | [-18.5%, -1.0%] | -13.7% [-24.3%, -2.2%] | -12.3% [-20.2%, -4.0%] | -14.2% [-21.6%, -6.3%] |
| [0.30,0.40) | 1662 | 1662 | 532 | 0.343 | 0.309 | -10.1% [-16.3%, -3.7%] | [-16.3%, -4.0%] | -13.1% [-21.7%, -4.4%] | -12.4% [-18.7%, -6.2%] | -14.0% [-20.4%, -7.7%] |
| [0.40,0.50) | 1728 | 1728 | 512 | 0.445 | 0.431 | -3.4% [-8.7%, +1.8%] | [-8.2%, +1.4%] | -5.5% [-12.5%, +1.6%] | -5.5% [-10.7%, -0.4%] | -7.0% [-12.0%, -2.0%] |
| [0.50,0.60) | 1842 | 1564 | 476 | 0.543 | 0.545 | +0.2% [-3.3%, +3.7%] | [-3.2%, +3.5%] | -0.5% [-6.2%, +5.2%] | -1.6% [-4.9%, +1.8%] | -2.9% [-6.2%, +0.3%] |
| [0.60,0.70) | 1623 | 1623 | 522 | 0.645 | 0.654 | +1.2% [-2.4%, +4.8%] | [-2.1%, +4.6%] | +6.0% [+1.5%, +10.5%] | -0.2% [-3.7%, +3.4%] | -1.2% [-4.7%, +2.2%] |
| [0.70,0.80) | 1731 | 1731 | 524 | 0.744 | 0.761 | +2.2% [-0.5%, +4.8%] | [-0.8%, +5.1%] | +1.7% [-2.2%, +5.6%] | +1.1% [-1.5%, +3.8%] | +0.4% [-2.2%, +3.1%] |
| [0.80,0.90) | 1949 | 1949 | 612 | 0.847 | 0.832 | -1.8% [-3.8%, +0.1%] | [-4.1%, +0.4%] | -2.0% [-4.8%, +0.6%] | -2.4% [-4.4%, -0.4%] | -2.8% [-4.8%, -0.9%] |
| [0.90,1.00) | 7290 | 7290 | 566 | 0.974 | 0.972 | -0.3% [-0.7%, +0.1%] | [-0.8%, +0.2%] | -0.6% [-2.2%, +0.8%] | -0.4% [-0.8%, -0.0%] | -0.5% [-0.9%, -0.1%] |
| [0.00,0.01) | 823 | 823 | 238 | 0.005 | 0.001 | -82.6% [-100.0%, -47.9%] | [-100.0%, -43.5%] | -40.0% [-100.0%, +80.1%] | -83.3% [-100.0%, -49.9%] | -83.8% [-100.0%, -51.3%] |
| [0.01,0.02) | 1363 | 1363 | 313 | 0.012 | 0.007 | -45.1% [-76.7%, -7.1%] | [-91.9%, +39.1%] | -37.9% [-84.4%, +23.9%] | -47.1% [-77.7%, -10.8%] | -48.6% [-78.4%, -13.0%] |
| [0.02,0.05) | 2314 | 2314 | 363 | 0.034 | 0.013 | -61.6% [-75.2%, -46.2%] | [-75.3%, -45.7%] | -28.4% [-59.4%, +6.3%] | -63.1% [-76.4%, -48.9%] | -64.1% [-77.1%, -50.1%] |
| [0.05,0.10) | 2177 | 2177 | 436 | 0.065 | 0.045 | -33.3% [-46.1%, -19.5%] | [-46.9%, -19.2%] | -15.7% [-37.5%, +9.0%] | -35.7% [-48.1%, -22.3%] | -37.4% [-49.6%, -24.6%] |
| [0.90,0.95) | 1361 | 1361 | 454 | 0.921 | 0.916 | -0.6% [-2.2%, +1.0%] | [-2.3%, +1.1%] | -0.2% [-2.3%, +1.8%] | -0.9% [-2.4%, +0.7%] | -1.1% [-2.7%, +0.4%] |
| [0.95,0.98) | 1331 | 1331 | 363 | 0.963 | 0.959 | -0.5% [-1.6%, +0.6%] | [-1.7%, +0.7%] | -1.8% [-3.9%, -0.0%] | -0.6% [-1.7%, +0.4%] | -0.7% [-1.9%, +0.4%] |
| [0.98,0.99) | 739 | 739 | 277 | 0.984 | 0.981 | -0.3% [-1.4%, +0.6%] | [-1.4%, +0.6%] | -0.4% [-1.8%, +0.7%] | -0.4% [-1.5%, +0.6%] | -0.4% [-1.5%, +0.5%] |
| [0.99,1.00) | 3859 | 3859 | 326 | 0.995 | 0.994 | -0.1% [-0.4%, +0.1%] | [-0.6%, +0.2%] | +0.1% [-0.2%, +0.3%] | -0.1% [-0.4%, +0.1%] | -0.2% [-0.4%, +0.1%] |

Headline, 1h:

| bin | n contracts | n markets | n events | mean buy price | win rate | return/$ EW [95% CI, market resample] | EW CI (event resample) | event-clustered mean [95% CI] | EW after pm4 | EW after pm7 |
|---|---|---|---|---|---|---|---|---|---|---|
| (0.00,0.10] | 6932 | 6932 | 533 | 0.038 | 0.023 | -49.8% [-59.7%, -39.0%] | [-64.1%, -29.0%] | -22.7% [-45.8%, +3.0%] | -51.6% [-61.1%, -41.5%] | -52.9% [-62.4%, -42.8%] |
| [0.90,1.00] | 7290 | 7290 | 566 | 0.974 | 0.972 | -0.3% [-0.7%, +0.1%] | [-0.8%, +0.2%] | -0.6% [-2.2%, +0.8%] | -0.4% [-0.8%, -0.0%] | -0.5% [-0.9%, -0.1%] |

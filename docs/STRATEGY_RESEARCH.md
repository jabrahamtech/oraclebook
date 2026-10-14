# Strategy research map

Which edges are worth testing with oraclebook, what the evidence says, and what result would
kill each idea. Compiled 2026-10-12. **Nothing here is a profitability claim** — each idea is a
hypothesis with a pre-registered falsification test.

How this was produced: one pass collected candidate edges and their sources; a separate
validation pass tried to reject each claim (checking citations, re-deriving the fee
arithmetic, looking for survivorship, weighting and pre-fee-era traps). Sources were read
through search-engine abstracts and snippets — full texts were not reachable from the research
environment — so treat quoted magnitudes as one notch less certain than their citations imply.

## Costs first

Every idea below lives or dies on fees, so check these per market rather than hard-coding them.

| Venue | Taker fee per contract | Maker |
|---|---|---|
| Polymarket | `rate · p(1−p)` in USDC (pUSD); `rate` per market from Gamma `feeSchedule` (crypto ≈ 0.07, lower in other categories; geopolitics exempt) | 0, plus a share of taker fees as rebates in some categories |
| Kalshi | `ceil_to_cent(0.07 · M · C · P(1−P))` | 0, or 0.0175·C·P(1−P) on series with maker fees |

Break-even true probability for buying a favourite at `p` is `p + fee/share`:

| p | Polymarket r=0.04 | r=0.07 | Kalshi, 1 contract | Kalshi, 100 contracts |
|---|---|---|---|---|
| 0.90 | +0.36pp | +0.63pp | **+1.00pp** | +0.63pp |
| 0.95 | +0.19pp | +0.33pp | **+1.00pp** | +0.34pp |
| 0.97 | +0.12pp | +0.20pp | **+1.00pp** | +0.21pp |

Kalshi's round-up to the cent erases a 1pp favourite edge on single-contract orders. Batch.

## Ideas that survived validation (ranked)

### 1. Maker-side favourite carry (buy 90–97¢ outcomes passively, hold to resolution)
- **Status.** The taker version shows no edge on real data (see *Empirical check*); the maker
  version is untested.
- **Evidence.** The favourite–longshot bias is well documented: cheap contracts lose heavily and
  expensive ones earn a small positive return. Kalshi: Bürgi, Deng & Whelan (CEPR DP20631), 300k+
  contracts, <10¢ contracts lose >60%, >50¢ earn a small positive return; makers lose ≈9.6% on
  average vs ≈31.5% for takers. Polymarket: Cardozo & Rivero-Wildemauwe (arXiv 2609.12878,
  preprint), ≥90¢ buys earn ≈ +0.83¢/$. Page & Clemen (EJ 2013): the bias grows with time to
  expiry and is largely explained by time preference.
- **Why it might not work.** The Polymarket sign flips with weighting (−6.3¢ contract-equal vs
  +4.1¢ event-clustered) and the effect is absent in Sports, the largest category. The bias is
  reported to be shrinking. At 95¢ one loss erases 19 wins: simulated 100 bets at 5% stake with a
  true 1pp edge give a 4.9% chance of a >20% drawdown (12.1% with no edge), and ≈1,500 bets are
  needed to detect a 1pp edge at 2σ. Capital is locked until resolution.
- **Falsified if** the event-clustered mean return of *filled* maker buys at 0.90–0.97, net of
  fees, has a 95% bootstrap CI (resampling events, `analysis.bootstrap_by_market`) that includes
  ≤ 0, or the win rate conditional on being filled is ≤ the price paid. Run on a held-out, recent
  window — conditional on fill, because a resting favourite bid fills mostly when news turns.
- **Build.** A `FavoriteCarry` strategy (price band + time-to-expiry window, GTD bids) with a
  `TimeToExpiryGate` and per-event caps; evaluate with Replay + `settlements` + the report.

### 2. Reward-aware two-sided quoting (Polymarket liquidity rewards)
- **Evidence.** Rewards score `((v−s)/v)²·b` (two-sided quoting required outside [0.10, 0.90]).
  Bartlett & O'Hara (SSRN 6615739, 41.6M Kalshi trades): one-sided order flow predicts maker
  losses in single-name markets — use it to pull quotes. Any edge is mostly rewards and rebates.
- **Falsified if** spread capture + estimated rewards − 60s markouts − fees ≤ 0 over ≥ 4 weeks
  or ≥ 50 markets of recorded L2 replayed through the conservative paper executor.
- **Build.** Inventory-skewed quoting (`on_fill`), an `InventoryGate`-style cap, a
  resolution-proximity pull, and markouts per fill from `oraclebook.analysis`.

### 3. negRisk basket arbitrage (taker, all legs or none)
- **Evidence.** Saguillo et al. (arXiv 2508.03474, AFT 2025): ≈$40M of realised arbitrage on
  Polymarket, Apr 2024–Apr 2025 — but largely before taker fees, and practitioner scans now
  report 1–2¢ gaps that close within minutes.
- **Falsified if** on recorded L2, events where Σ best asks + per-leg taker fees < 1 at
  executable depth yield ≈ $0 median capturable per event, or gaps last < ~1s.
- **Build.** A `NegRiskBasketArb` sized to the thinnest leg, FOK legs, a basket-completeness gate.

### 4. Crypto threshold markets vs options-implied probability
- **Evidence.** Portnaya (arXiv 2606.19517): Polymarket BTC thresholds ≈5.6pp rich vs Binance
  options-implied, ≈11pp vs Deribit — but 3 contracts, 2023 (pre-fee), precision "marginal".
- **Falsified if** on 2026 data across > 30 contracts, with r = 0.07 taker fees, executable asks
  and delta-hedge cost, the net gap is not significantly > 0.
- **Build.** Deribit/Binance `PriceTick` feeds, a fair-value strategy on `on_price`.

### 5. Time-to-expiry structure of the favourite–longshot bias
- **Falsified if** long-dated buckets don't beat the T-bill rate after costs, annualised.

## Rejected

| Idea | Why |
|---|---|
| 15-minute crypto up/down direction models | OpenMarket (arXiv 2607.26245, 727M rows): 43 features fail to beat the book-implied probability; simulated trading loses. Taker fees were introduced to stop latency arbitrage. |
| Cross-venue Polymarket–Kalshi convergence arb | Gebele & Matthes (arXiv 2601.01706): persistent 2–4% gaps due to differing resolution rules — basis risk, not convergence. |
| Single-market YES+NO maker (`CompleteSetMaker`) | One-leg adverse selection; reproduced in `tests/test_complete_set_maker_replay.py`. |
| Kalshi single-contract taker favourite buys | Cent round-up costs 1pp — larger than the documented edge. |
| Exploiting reverted ("ghost") fills | Not acceptable. Reverted fills are an infrastructure risk to model, not an edge (Shen et al., arXiv 2606.16852). |
| "Logit Avellaneda–Stoikov" citing arXiv 2510.15205 | Misattributed — that paper is a logit jump-diffusion pricing kernel. The quoting idea folds into #2 as a heuristic. |

## Methodology checklist for any test above

- Use **executable** prices (asks for buys), never mids or last trades.
- Resample **markets/events**, not trades (`bootstrap_by_market`); report both contract-equal
  and event-clustered means — they can disagree in sign.
- Deflate for the number of variants tried (`deflated_sharpe`), and split by time with purging
  (`purged_walk_forward`).
- Don't select on resolved-only markets; keep voided/disputed ones.
- Use the current fee regime; pre-2026 Polymarket data understates costs for takers.
- Score probability models with Brier / log loss and `calibration_bins`; size with fractional
  Kelly (`kelly_fraction_binary`), never full Kelly.

## Data

Public sources to run the tests on:

- `Jon-Becker/prediction-market-analysis`: Kalshi and Polymarket trades plus market outcomes, Parquet.
- pmxt archive: hourly Polymarket L2 Parquet from 2026-04-13. This is the best input for Replay.
- Polymarket-v1 Database: arXiv 2606.04217.
- Kaggle Polymarket and Kalshi snapshots: their schemas are unverified.


## Empirical check on real resolved markets

The favourite–longshot bias was tested directly on six public datasets of real resolved
markets: three on GitHub (A–C) and three on Kaggle (D–F, added 2026-10-15). Scripts and full
tables are in [`research/flb/`](../research/flb/).
Kaggle datasets that lacked pre-close prices or outcomes, or anchored prices on the actual
resolution time, were screened out (listed in `flb_results.md`).

| | Source | Coverage | Price used |
|---|---|---|---|
| A | `usepancake/polymarket-history` (CC BY 4.0, non-commercial) | 7,331 resolved Polymarket binaries, 2023-03 → 2026-05 | daily 00:00 UTC price (no bid/ask) |
| B | `J0shusmc/Kalshi-BTC` (MIT) | 6,330 Kalshi `KXBTC15M` markets, 2026-06-15 → 08-20 | 1-minute YES bid/ask; buys at the ask |
| C | `marketlenstrade/polymarket-historical-data` (CC BY 4.0) | 2,249 Polymarket markets on 7 days in 2026 | best bid/ask |
| D | Kaggle `kachoio/polymarket-5-minute-crypto-updown-markets` (CC0) | 83,159 Polymarket 5-minute crypto Up/Down markets, 2026-03 → 05 | per-second bid/ask; outcome *inferred* from the final tick |
| E | Kaggle `polyorderbooks/polymarket-crypto-orderbooks-1s-l2` (CC BY 4.0) | 686 Polymarket crypto 5m/15m/4h markets, 2026-08-21 → 24 | ~1-second bid/ask |
| F | Kaggle `madrading/polymarket-2026-fifa-world-cup-order-book` (CC BY-NC 4.0) | 15,333 World Cup markets in 923 events, 2026-03 → 08 | 1-minute bid/ask; official results |

Every horizon is measured back from the **scheduled** end or close time, not the actual
resolution. Return per $1 from buying at price `p` (EW = equal weight per contract). The 95%
CIs come from a market-clustered bootstrap (5,000 replications, fixed seed) for EW, and from
resampling events for the clustered column. Books wider than 5¢ are skipped in C–F.

| Data, horizon | Bin | n | Win rate vs price | Gross EW [CI] | Clustered [CI] | After fees |
|---|---|---|---|---|---|---|
| A, 1d to planned end | ≥0.90 | 172 | .965 vs .964 | +0.2% [−3.1, +2.9] | −0.7% [−4.9, +2.8] | −0.0% (r=0.07) |
| A, 7d | ≥0.90 | 108 | .954 vs .958 | −0.7% [−5.4, +3.1] | −2.3% [−8.7, +2.8] | −0.9% |
| A, 7d | ≤0.10 | 1,659 | .033 vs .019 | +26% [−22, +83] | +72% [+12, +148] | +18% |
| B, 5m to close | ≥0.90 | 3,045 | .959 vs .956 | +0.4% [−0.4, +1.1] | +0.3% [−0.5, +1.1] | −0.7% [−1.4, +0.1] (Kalshi) |
| B, 5m | ≤0.10 | 2,892 | .036 vs .043 | −12% [−32, +11] | −9% [−31, +15] | −34% [−47, −19] |
| B, 1m | ≤0.10 | 5,555 | .008 vs .009 | −59% [−82, −28] | −58% [−82, −25] | −82% [−88, −74] |
| C, open+5m | ≥0.90 | 178 | .949 vs .962 | −1.3% [−5.0, +1.8] | −1.1% [−5.4, +2.2] | −1.6% (r=0.07) |
| D, 1m to window end | ≥0.90 | 33,307 | .977 vs .964 | +1.3% [+1.2, +1.5]\* | +1.3% [+1.0, +1.6] | +1.1% (r=0.07) |
| D, 30s | ≥0.90 | 32,519 | .982 vs .971 | +1.2% [+1.0, +1.3]\* | +0.8% [+0.0, +1.4] | +1.0% |
| D, 1m | ≤0.10 | 40,770 | .017 vs .038 | −64% [−67, −60] | −61% [−67, −55] | −66% |
| E, 15m contract, 5m to end | ≥0.90 | 93 | .978 vs .968 | +1.1% [−2.4, +3.6] | +0.4% [−4.5, +3.5] | +0.8% |
| E, 5m contract, 1m to end | ≥0.90 | 145 | .945 vs .979 | −3.5% [−7.7, +0.1] | −4.7% [−10.6, +0.3] | −3.6% |
| F, 1d to scheduled end | ≥0.90 | 4,828 | .968 vs .970 | −0.2% [−0.7, +0.3] | −1.1% [−2.7, +0.4] | −0.4% [−1.0, +0.1] |
| F, 1h | ≥0.90 | 7,290 | .972 vs .974 | −0.3% [−0.7, +0.1] | −0.6% [−2.2, +0.8] | −0.5% [−0.9, −0.1] |
| F, 1h | ≤0.10 | 6,932 | .023 vs .038 | −50% [−60, −39] | −23% [−46, +3] | −53% [−62, −43] |

\* D excludes the 8,876 markets whose inferred outcome is null — the close calls. Setting every
null-outcome contract to lose turns the D favourite rows into −3.6% (1m) and −4.8% (30s);
setting them to win gives +1.5% and +1.4%. The sign of the D favourite return is not identified.

Calibration slope, from a logistic regression of the outcome on logit(price); a slope above 1
is the classic bias. CIs come from resampling events.

| Data, horizon | Slope [CI] |
|---|---|
| A, 1d | 0.82 [0.76, 0.89] |
| A, 7d | 0.84 [0.77, 0.92] |
| A, 30d | 0.92 [0.82, 1.03] |
| B, 10m | 1.12 [1.05, 1.19] |
| B, 5m | 1.05 [1.00, 1.10] |
| B, 1m | 1.08 [1.00, 1.19] |
| C, open+5m | 0.98 [0.82, 1.21] |
| D, 4m | 1.16 [1.11, 1.20]\* |
| D, 30s | 1.27 [1.24, 1.31]\* |
| E, 15m contract, 5m | 1.28 [0.99, 1.94] |
| F, 7d | 1.03 [0.92, 1.17] |
| F, 1d | 1.05 [0.98, 1.12] |
| F, 1h | 1.03 [0.96, 1.09] |

\* Probably inflated: dropping close calls removes the mid-priced contracts whose outcome stayed
uncertain, which pushes the slope above 1.

**What this says.**
- **Favourites:** taking 90¢+ favourites at executable prices shows **no demonstrated edge**.
  In A–C and F gross returns are within ±1–2% with CIs spanning zero, and slightly negative
  after fees. The best new sample (F: about 900 World Cup events with official results) is flat
  at every horizon. D's nominal +1.2–1.3% depends on how its unlabelled close calls resolved,
  so it is not evidence either way. E is too small to sign.
- **Longshots:** short-horizon longshots lose heavily at the ask on Kalshi (B) and Polymarket
  crypto (D). In F, 1h longshots lose −50% when markets are resampled, but the CI reaches zero
  when events are resampled (outcomes within a match move together).
- **Polymarket's daily prices lean the other way** (slope < 1 in A). On executable World Cup
  quotes (F) the slope is about 1 with CIs that include 1, which fits the price-field-artefact
  explanation for A, though F covers different market types.
- **Revision to idea #1:** unchanged. The *taker* version of favourite carry is falsified on
  these samples. D's positive favourite cells do not reverse that, because their labels are not
  official resolutions. Re-testing D would need the official resolution for each market. Only
  the *maker* version remains untested, and it needs fill-conditional data — recorded L2
  replayed through the conservative paper executor.

**Look-ahead trap found in the process.** Measuring "7 days before resolution" from the
*actual* resolution time leaks the outcome. "Will X happen by D?" markets that resolve Yes early
get sampled just before the event. In dataset A that moved the calibration intercept from about
+0.02 to about +0.24 and made longshots look underpriced. Anchor horizons on the **scheduled**
end/close time.

**Limitations.**
- All six datasets are resolved-only.
- A has no event ids (category + end date stands in for the event) and no executable prices.
- B is a single crypto series with 67 day-clusters.
- C is a 7-day vendor sample.
- D's outcomes are inferred from the final tick, not the official resolution, and 12% of its
  markets have no outcome.
- E covers 4 days and fewer than 700 markets.
- F is one tournament. Most of its data is non-commercial (CC BY-NC), and quotes can be up to
  6 h old because bars print only on quote changes.
- Many bins and horizons were tested, so expect some cells to look significant by chance.

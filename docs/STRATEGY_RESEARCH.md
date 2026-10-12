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

The empirical check run for this document is in the section below.

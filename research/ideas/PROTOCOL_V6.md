# Protocol v6: five untested ideas (written before any of their data was looked at)

Rounds 1–5 and the earlier production bot covered taker favourites and longshots, spot and
model fair value (random walk, gradient boosting, Chronos), momentum and reversal, order-flow
follow and fade on Kalshi, complete-set and event-sum arbitrage, makers on quotes and on tapes,
weather and macro nowcasts, soccer against Betfair, and LLM forecasters. This round tests the five
ideas that none of them touched. Each is a simple mechanical rule; none is expected to be
profitable, and nothing here would show that one is.

## P1. Pre-open lean (Polymarket 5-minute crypto Up/Down)

Before a window opens its strike is not yet set, so P(Up) is 0.5 whatever has happened (ties
resolve Up, which Chainlink's precision makes negligible). Any pre-open lean is mispricing, unless
it is too small to trade.

- **Data:** `kachoio` panel (BTC, ETH, SOL, XRP), with outcomes rebuilt from Binance as in
  `crypto_favourites.load()`. Only quotes with `ts < open_ts` are used.
- **Rule P1(τ, d):** at `open_ts − τ`, take the latest Up quote known within the previous 5 s
  with spread ≤ 5¢, and its mid m. If |m − 0.5| ≥ d, buy the side whose mid is below 0.5 at
  its ask (Down's ask from the Down book), pay the fee 0.07·p·(1−p), hold to resolution. One
  trade per market.
- **Grid:** τ ∈ {60, 30, 10} s; d ∈ {0.02, 0.03, 0.05}.
- **Also reported:** how many markets have a pre-open quote, and the distribution of m.
- **Not testable if:** fewer than 200 exploration markets have a usable pre-open quote. Then P1
  is reported as not testable on this data, and nothing is frozen.

## P2. BTC leads the alt markets (Polymarket 5-minute crypto)

The BTC, ETH, SOL and XRP markets share each window, and the alts' spot follows BTC within
seconds. Does the alt market lag BTC's?

- **Data:** `kachoio` panel. Pair each alt market with the BTC market that has the same
  `open_ts` and `close_ts`.
- **Rule P2(L, k):** scan the window each second from `open_ts + 30 s` to `close_ts − 30 s`.
  Signal at t when both hold:
  - BTC's Up mid moved by at least k over [t − L, t];
  - the alt's Up mid moved less than k/4 in the same direction over the same interval.

  Then buy the alt's side in BTC's direction at the ask of the first alt quote known after t,
  at most 3 s later, with spread ≤ 5¢. Pay the fee and hold to resolution. Only the first
  signal per alt market is traded.
- **Grid:** L ∈ {5, 10} s; k ∈ {0.05, 0.10}.
- **Also reported:** at h ∈ {120, 60} s before the close, the out-of-sample log-loss gain from
  adding BTC's L-second logit change to the alt's logit(mid). Fit on the first half of
  exploration and score on the second half, as `momentum.py` does.

## P3. Wallet skill persists (Polymarket 5-minute crypto, public trade tape)

Kalshi's tape is anonymous, so no earlier round could test whether some traders keep winning.
Polymarket's tape names the wallet behind every trade.

- **Data:** Gamma outcomes, and every taker trade from data-api `/trades?market=…&takerOnly=true`.
  This covers BTC and ETH 5-minute markets closing 2026-09-24 → 2026-10-14, fetched with
  `research/data/polymarket_api.py`.
- **Wallet edge:** for each trade, direction × (y_Up − p_Up), averaged per market and then over
  markets. A wallet is eligible with at least 20 markets in the window being scored. Its
  t-statistic is taken across markets.
- **Persistence (reported):** Spearman correlation of edge between the first and second halves
  of exploration days, for wallets eligible in both. The CI comes from resampling wallets.
- **Rule P3(t\*):**
  - Select wallets with t ≥ t\* in the scoring window.
  - In the evaluation window, copy each selected wallet's first trade per market that comes at
    least 10 s before the close. The fill is the next taker BUY of the same economic side by
    another wallet within 5 s, at the price that wallet paid. Pay the fee and hold to
    resolution.
  - Windows: in exploration, score on the first half of exploration days and evaluate on the
    second half. In the holdout, score on all exploration days and evaluate on the holdout.
- **Grid:** t\* ∈ {2, 3}.

## P4. Nested expiries (Polymarket crypto Up/Down)

A 15-minute market and its last 5-minute market settle on the same close, as do a 4-hour market
and its last 15-minute and 5-minute markets, each with its own strike. If the shorter market's
strike is higher, Up(short) implies Up(long). Up(long) + Down(short) then pays at least $1, and
$2 when the close lands between the two strikes. In the mirror case the pair is Up(short) +
Down(long). Round 3 checked this on Kalshi only, where both markets share one settlement number.

- **Data:** `polyorderbooks` panel (5m, 15m and 4h books, official outcomes). Strikes are the
  Binance 1-minute open at each window start; pairs whose strikes differ by less than 3 bp are
  skipped.
- **Rule P4(e):**
  - At each second where both books are known within 1 s and each has a spread ≤ 5¢:
    cost = ask_a + ask_b + both fees.
  - At the first second with cost ≤ 1 − e, buy the pair at the asks of the next quotes, known
    after that second. Hold to resolution. The payoff is y_a + y_b.
- **Grid:** e ∈ {0, 0.01}.
- **Also reported:** the share of seconds with cost < 1, and how long each episode lasts.
- **Sample size:** this sample is 4 days. P4 is descriptive unless the holdout reaches the trade
  and cluster minimums below.

## P5. Implied volatility instead of realised (Kalshi daily BTC / ETH ladders)

Fair value from a random walk on spot failed in rounds 1, 2 and 4, but always with realised
volatility. Literature that prices crypto threshold markets off options finds them rich. This
round swaps in the options market's volatility.

- **Data:** the `cx.py` dataset (744 events, decision times 60/30/15/5 min before the 17:00 ET
  close). Add Deribit's DVOL index (30-day implied volatility, hourly, from public
  `get_volatility_index_data`), using the last value known before t.
- **Fair value:** fair = Φ(ln(S_t/K) / (σ·√τ)), where:
  - σ = DVOL/100 in annual units;
  - τ is the time to close in years;
  - S_t is the last Binance close known at t, shifted by the median Kalshi strike basis from
    round 1.
- **Rule P5(e):** buy the side where fair − ask − fee > e. The ask is the last taker-buy print
  at most 60 s old; one contract; clusters are events.
- **Grid:** e ∈ {0, 0.03, 0.05}.
- **Also reported:** Brier score of the market, the DVOL model, the realised-vol model
  (60-minute σ, as in round 1) and a 50/50 blend.
- **Split:** the same as G1 (exploration Nov 2024 → Dec 2025, holdout 2026), so that the two
  results are comparable.

## Split, costs and decision rule (as PROTOCOL.md unless stated)

- **Split:** P1, P2 and P4 use the earliest 60% of each dataset by scheduled close for
  exploration; P3 and P5 use the windows above. The holdout is touched once, for frozen rules
  only.
- **Freezing:** one rule per idea, chosen on exploration. A rule is frozen only if its
  exploration return is positive with a 95% interval above zero.
- **Costs:** Polymarket taker fee 0.07·p·(1−p); Kalshi fee ceil-to-cent of 0.07·p·(1−p). Buys
  are filled at asks or at tape prices, never at mids.
- **Clusters:**
  - UTC day for P1 and P2, as in rounds 1–4 on kachoio;
  - UTC hour for P3 and P4, because there are too few days for 20 day-clusters; the
    day-clustered interval is also reported;
  - event for P5.
- **Holdout pass:** the return per $1 net of costs has a CI above zero at α = 0.05 / 5 = 0.01.
  This α is fixed now for the five ideas, whatever number of rules ends up frozen. The holdout
  also needs at least 200 trades over at least 20 clusters, with the same sign as exploration.

## Caveats fixed in advance

- kachoio outcomes are rebuilt from Binance, not official. Round 1 measured 99.3% agreement,
  falling to 77.8% for moves under 0.5 bp.
- data-api timestamps are on-chain seconds. "Next taker BUY within 5 s" is a price someone
  paid, not a guaranteed fill for us.
- P2 and P4 are latency questions measured on one-second snapshots. A pass would still need a
  live recording to confirm it, because round 3 found that crossings like these close within
  seconds.
- DVOL is 30-day implied volatility, not same-day. The term structure is not modelled.
- Nothing here implies a profitable strategy.

## Not in this round (no usable data here)

- Retrieval-augmented LLM forecasters: RESULTS_LLM.md calls them the remaining untested variant.
- A forward paper test of an active weather market maker.
- Reward-aware Polymarket quoting, which needs weeks of recorded L2.
- A live Polymarket negRisk basket scan.
- GDP1's forward test, due at the Q3 2026 advance release (late October).

## Amendment 1 (2026-10-18, before any data): crypto the bot can trade, bull regimes

**Scope.** Only strategies this bot can execute: Polymarket crypto markets (`oraclebook`'s
adapter) and Hyperliquid perps (feed already wired; an executor would be new). Kalshi is
geoblocked for the operator, so **P5 is withdrawn** and `cx_iv.py` removed.

**Bull regime (fixed).** BTC's 30-day log return > ln(1.10), from the last Binance daily close
known at the decision time — the threshold round 4 used.

**B1. Bull drift in hourly / 4-hour Up/Down.** In a bull regime, does Up win more often than its
price? Break-even arithmetic, fixed now: pure drift shifts P(Up) by about φ(0)·μΔt/(σ√Δt). With
+10% per 30 days and 50% annual volatility that is ≈ 1.1pp per hour and ≈ 2.3pp per 4 hours,
against a fee of ≈ 1.8¢ near 0.50 plus half the spread. So B1 can pass only if the market prices
less than the drift.
- Data: Gamma + data-api tape for hourly (`{coin}-up-or-down-{month}-{day}-{h}{am|pm}-et`, the
  bot's slug) and 4-hour (`{coin}-updown-4h-{ts}`; tested only if those markets are found), BTC
  and ETH, 2024-11-01 → 2026-10-14.
- Rule B1(Δt): in a bull regime, at the open + 2 min (hourly) or + 5 min (4 h), buy Up at the
  next taker BUY of Up within 60 s; hold to resolution. The same rule outside bull regimes is
  reported as a control, not tested.

**B2. Upside longshots in bull runs.** Monthly "What price will <coin> hit in <month>?" events.
- Rule B2(k, regime): one day after the month starts, for every upside strike at least k above
  the Binance close known then whose last YES trade in the previous 24 h was ≤ 0.20, buy NO at
  the next taker BUY of NO within 6 h; hold to resolution.
- Grid: k ∈ {15%, 30%}; regime ∈ {bull, all}. Clusters: event (coin-month). Expect ≤ ~70 events:
  probably underpowered, said now.

**B3. Hyperliquid funding carry.** BTC, ETH, SOL perps, hourly funding and premium from
`fundingHistory`, 2024-01-01 → 2026-10-14.
- Rule B3(F): at each UTC day start, if the trailing 7-day mean funding annualises to ≥ F, short
  the perp and hold spot for 7 days. Return per $1 of spot = Σ funding + (premium at entry −
  premium at exit) − 0.20% round-trip costs (four taker legs at 0.05%).
- Grid: F ∈ {10%, 20%, 30%} APR. Clusters: entry week; overlapping holds share clusters.
- Also reported: the annualised return, next to a 5% cash yield. B3 is a yield, not a
  prediction edge.

**Split.** 60/40 by time (B2: by event close), as for P1–P4.

**Holdout α = 0.05 / 7** for P1–P4 and B1–B3, replacing 0.05 / 5 (no data had been seen).

## Amendment 2 (2026-10-18, before any data): B1/B2 prices

data-api returns a market's tape newest-first and stops paginating at a depth cap, so busy
hourly and monthly markets would lose exactly the opening trades B1 and B2 need. B1 and B2
therefore price entries from CLOB `prices-history` instead:
- B1: the last point at or before the decision time, at most 2 min old, at 1-minute fidelity.
- B2: the last YES point in the previous 24 h, at hourly fidelity; the NO price is 1 − YES.

Either way, the entry pays that price + 1¢ (spread allowance) + the 0.07·p·(1−p) fee. P3 keeps
the tape, which only it needs.

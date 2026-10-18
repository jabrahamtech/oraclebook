# Protocol v7: reverse the best current crypto accounts (written before any of their data)

Polymarket's public tape names the wallet behind every fill. This round reconstructs the
trading rules of the most profitable current wallets in crypto Up/Down markets from that
tape, turns each reconstructed behaviour into a rule this bot can execute, and tests the rule,
not the wallet, out of sample. The source is public behaviour only: no private code,
credentials or systems. A third-party tool (e.g. "REA") may assist only after its code has been
read; nothing below depends on it. Nothing here implies a profitable strategy.

## Data

- **Polymarket.** BTC and ETH Up/Down markets at 5m, 15m and 1h, closing in the 30 days before
  the run date (fixed at fetch time and recorded in `_source.json`). Gamma supplies outcomes.
  data-api `/trades` supplies every fill on **both** sides (`takerOnly=false`), with wallet, side,
  price, size and time. Fetched with `research/data/polymarket_api.py`.
- **Spot.** Binance 1-second klines for BTC and ETH (data.binance.vision), to time fills against
  spot moves.
- **Leaderboard.** lb-api profit is a cross-check only. Selection uses the tape, so it cannot pick
  wallets on profits made elsewhere.

## Split

The earliest 60% of days (by market close) are exploration; the latest 40% are the holdout.
Wallet selection, fingerprints and rule parameters come from exploration only.

## 1. Select the wallets (exploration)

- **PnL** per wallet, held to resolution: Σ over fills of size × (payout − price) for buys, and
  size × (price − payout) for sells. Taker fills pay 0.07·p·(1−p); maker fills pay nothing.
- **Eligibility:** ≥ 200 markets traded, and a t ≥ 3 on per-market PnL.
- **Selection:** the top 10 eligible wallets by PnL.

## 2. Fingerprint each selected wallet (exploration, reported per wallet)

- Maker share of fills, and the share of markets where it bought both outcomes.
- When it enters:
  - seconds after the open and before the close (quartiles);
  - seconds after the last ≥ 2σ one-second spot move, and whether it took the side that move
    favoured.
- Price paid against the prevailing mid; median size; net inventory at the close.

Each wallet is assigned one archetype, by fixed cut-offs:
- **two-sided maker:** maker share ≥ 0.7 and two-sided markets ≥ 0.5;
- **latency taker:** taker share ≥ 0.7, ≥ 60% of fills within 3 s of a 2σ spot move, and
  ≥ 70% of those on the favoured side;
- **late favourite:** ≥ 60% of fills in the last 60 s at prices ≥ 0.80;
- **other:** anything else, which is reported but not tested.

## 3. Freeze one rule per archetype present (at most 3), with no tuning on PnL

Parameters are the median of the selected wallets' own behaviour in that archetype.

- **R-lat:**
  - Signal: at a ≥ kσ one-second spot move (k = the wallets' median move size).
  - Entry: buy the favoured side at the first ask that arrives at least 1 s later. The 1 s
    stands for this bot's latency and the tape's resolution.
  - Taker fee; hold to resolution.
- **R-mm:**
  - Quotes: bid both sides at mid − s, where s is the wallets' median distance below mid.
  - Fills: trade-through only.
  - Inventory: cancel the remaining leg once one side fills, if the wallets' median inventory
    at the close is near zero.
  - Maker; hold to resolution.
- **R-fav:** in the last T seconds (the wallets' median), buy the side whose ask is ≥ the
  wallets' median price. Taker fee; hold to resolution.

## 4. Test (holdout, each frozen rule once)

- **Pass:** return per $1 net of costs with a CI above zero at α = 0.05 / (number of frozen
  rules), ≥ 200 trades over ≥ 20 hour-clusters, and the same sign as exploration.
- **Also reported, not tested:** did the 10 selected wallets stay profitable in the holdout
  (skill persistence, as in P3)?

## Caveats fixed in advance

- data-api times are on-chain seconds. Sub-second latency behaviour is invisible, and R-lat at
  ≥ 1 s may be too slow to capture what the wallets capture. A fail there says "not at our
  speed", not "no edge".
- Selecting the top 10 by exploration PnL picks some lucky wallets. That is why the rules, not
  the wallets, are judged on a holdout they never saw.
- A maker's edge depends on queue position, which the tape does not show; R-mm's trade-through
  fill is conservative.
- Two-sided wallets may also earn maker rebates and liquidity rewards, which the tape omits.

## Amendment 1 (2026-10-19, before any data): Hyperliquid wallets

Polymarket does not resolve from the operator's machine (Australia blocks it), so this round
reverses **Hyperliquid** perp traders instead. Hyperliquid is a venue the bot can reach and
trade, and every account's fills are public. Steps 1–4 above apply with these substitutions.

- **Data:**
  - Leaderboard: `stats-data.hyperliquid.xyz/Mainnet/leaderboard` (month and all-time PnL,
    volume).
  - Fills: `info` → `userFillsByTime`, giving coin, price, size, side, direction, closed PnL,
    fee, `crossed` (taker) and time. Hyperliquid serves at most each account's latest 10,000
    fills, so truncation is reported per wallet.
  - Funding: `info` → `fundingHistory`.
  - Candles: `info` → `candleSnapshot` (1h).
- **Window:** the 90 days before the run date; exploration is the first 60% of days.
- **Wallet selection:** the 200 accounts with the highest month PnL. Eligible means account
  value ≥ $10k, ≥ 100 closed trades and ≥ 20 active days in exploration, and not a vault. The
  top 10 by exploration closed PnL net of fees are selected.
- **Fingerprint:**
  - maker share and median holding time;
  - median leverage, where clearinghouse data allows;
  - coins traded;
  - direction at entry against the coin's prior 4h and 24h return (follows or fades);
  - direction against the sign of funding;
  - entry hour (UTC).
- **Archetypes, by fixed cut-offs:**
  - **trend:** ≥ 65% of entries follow the prior 24h move, and median hold ≥ 1 h;
  - **fade:** ≥ 65% of entries go against the prior 4h move;
  - **funding-contrarian:** ≥ 65% of entries are opposite to funding's sign;
  - **market maker:** maker share ≥ 0.7 and median hold < 10 min. Reported only; not
    reproducible at our latency.
  - **other**.
- **Frozen rules (at most 3), with parameters from the selected wallets' medians and no tuning on
  PnL:**
  - **R-trend:** enter in the direction of a ≥ kσ prior-24h move and hold H;
  - **R-fade:** enter against a ≥ kσ prior-4h move and hold H;
  - **R-fund:** go short when funding APR ≥ f, long when ≤ −f, and hold H.

  Each pays taker fees of 0.045% per side plus 1 bp slippage, and pays or receives funding.
  It runs on BTC, ETH, SOL and the coins the selected wallets traded most.
- **Test, per frozen rule:**
  - on 1h candles from 2024-01-01 to the start of the 90-day window, data the rules were not
    inferred from;
  - and on the 90-day holdout.

  A rule passes if its per-trade return net of costs has a CI above zero on the earlier data at
  α = 0.05 / (number of frozen rules), with ≥ 200 trades over ≥ 20 week-clusters, and the
  holdout has the same sign. The same top-10 wallets' holdout PnL is also reported (skill
  persistence).
- **Caveats:**
  - A month-PnL leaderboard is mostly luck plus leverage, which is why the rules, not the
    wallets, are judged.
  - A 10,000-fill cap truncates high-frequency accounts.
  - Inferring discretionary traders' rules from fills may find no stable rule ("other").

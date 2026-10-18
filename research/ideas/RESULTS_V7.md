# Round 7: reverse the best Hyperliquid wallets — exploration and frozen rules

Protocol: [PROTOCOL_V7.md](PROTOCOL_V7.md), Amendments 1–3. The Polymarket version was not run,
because `*.polymarket.com` does not resolve from the operator's machine; Amendment 1 moved the
round to Hyperliquid. Data is public: the leaderboard was fetched 2026-10-18 17:48 UTC, with
fills, roles, positions, 1h candles and funding from the `info` API. The window is
2026-07-20 → 2026-10-18 and exploration runs to 2026-09-12. **The rules below were frozen here,
before any backtest or holdout data was used. Nothing here shows a profitable strategy.**

## Selection (exploration)

| step | accounts |
|---|---|
| top 200 by month PnL | 200 |
| account value ≥ $10k | 192 |
| not a vault | 191 |
| exploration fills served (not truncated, not late) | 91 |
| ≥ 100 closing orders (Amendment 3) | 19 |
| ≥ 20 active days | **12** |

Under Amendment 2's round-trip definition only 2 accounts qualify. The large accounts scale in
and out of standing positions and are rarely flat. Of the 109 accounts dropped at the "fills
served" step:
- 45 had lost their oldest fills, or had more than 200,000 of them;
- 55 had no fill served before the exploration end;
- the rest had no perp fills in the window.

Only 12 accounts are eligible, so the "top 10" includes two with negative exploration PnL.

## The 10 selected wallets (exploration, 2026-07-20 → 2026-09-12)

| wallet | PnL net of fees | closing orders | maker share | median hold | archetype |
|---|---|---|---|---|---|
| 0x9c062c…0560 | +$2.86M | 287 | 0.08 | 86.5 h | fade |
| 0x288ed4…b336 | +$0.60M | 684 | 0.95 | 454.7 h | fade |
| 0x9751ce…a97e | +$0.51M | 451 | 0.77 | 786.6 h | funding-contrarian |
| 0xfc98b6…8777 | +$0.46M | 253 | 0.19 | 7.3 h | fade |
| 0xf9faba…3275 | +$0.42M | 799 | 0.10 | 8.9 h | other |
| 0xb1b365…ae8f | +$0.39M | 434 | 0.88 | 7.7 h | fade |
| 0x07423f…57c2 | +$0.30M | 1,003 | 0.13 | 45.3 h | trend |
| 0x051748…4d23 | +$0.02M | 1,096 | 0.51 | 100.0 h | other |
| 0x4fe260…ac59 | −$0.11M | 701 | 0.76 | 240.5 h | fade |
| 0xd6e1a6…f256 | −$0.36M | 1,041 | 0.92 | 36.4 h | fade |

Shares of entries, in wallet order:

| measure | values |
|---|---|
| follows the prior 24h move | 0.15, 0.28, 0.63, 0.19, 0.58, 0.30, 0.68, 0.55, 0.13, 0.27 |
| fades the prior 4h move | 0.88, 0.70, 0.34, 0.72, 0.54, 0.69, 0.45, 0.46, 0.72, 0.70 |
| opposite to funding's sign | 0.45, 0.50, 0.86, 0.62, 0.30, 0.55, 0.76, 0.42, 0.67, 0.76 |

- **Fading is the common behaviour.** Six of the ten fade the prior 4h move on ≥ 65% of their
  entries.
- **Few wallets for the other two rules.** One wallet is trend and one is funding-contrarian.
  No wallet is a market maker under the cut-off: three have a maker share ≥ 0.7, but they hold
  for hours to weeks.
- **Coins.** Most are HIP-3 stock perps (`xyz:` SK Hynix, SanDisk, Micron, XYZ100) alongside
  BTC, ETH and ZEC.
- **Other columns in `research/data/out/hl_wallets.csv`.** That file has, per wallet, leverage
  (current positions only), entry hour and candle coverage (87–100% of entries).

## Frozen rules (α = 0.05 / 3 ≈ 0.0167)

Parameters are the medians of the wallets' own medians (Amendment 2). They involve no PnL.

| rule | from | parameters | backtest command |
|---|---|---|---|
| R-fade | 6 wallets | enter against a ≥ **1.064σ** prior-4h move, hold **61 h** | `hl_wallets.py backtest --rule fade --k 1.064 --hold 61 --coins BTC,ETH,SOL,xyz:SKHX,xyz:SNDK,xyz:XYZ100 --alpha 0.0167` |
| R-trend | 1 wallet | enter with a ≥ **0.611σ** prior-24h move, hold **45 h** | `… --rule trend --k 0.611 --hold 45 …` |
| R-fund | 1 wallet | short when funding APR ≥ **0.110**, long when ≤ −0.110, hold **787 h** | `… --rule fund --k 0.110 --hold 787 …` |

Universe: BTC, ETH and SOL, plus the three coins the selected wallets traded most: `xyz:SKHX`,
`xyz:SNDK` and `xyz:XYZ100`. Those three are HIP-3 stock perps with no Binance USDⓈ-M perp, so the
backtest covers BTC, ETH and SOL only; the holdout covers all six.

Two things about the frozen rules, stated before the backtest:
- **R-fund cannot meet the trade minimum.** Its 787 h hold allows at most about 28
  non-overlapping trades per coin from 2024-01 to 2026-07, so it cannot reach 200 trades.
- **R-fund is close to "short whenever funding is at its baseline".** The funding-contrarian
  threshold is Hyperliquid's resting funding (10.95% APR), as Amendment 2 expected.

## Backtest (2024-01-01 → 2026-07-20, Binance USDⓈ-M 1h, each rule run once)

```
BACKTEST R-fade k=1.064 H=61h coins=BTC,ETH,SOL alpha=0.0167: n=912 clusters=133 ret=-0.31% [-0.79, +0.18]
BACKTEST R-trend k=0.611 H=45h coins=BTC,ETH,SOL alpha=0.0167: n=1,284 clusters=133 ret=-0.15% [-0.51, +0.21]
BACKTEST R-fund k=0.11 H=787h coins=BTC,ETH,SOL alpha=0.0167: n=78 clusters=48 ret=+1.90% [-4.43, +8.10]
```

**All three rules fail.** A rule passes only if its backtest CI is above zero, so the holdout
cannot change these verdicts. The holdout has not been run; it would now only report whether
the 10 wallets stayed profitable.

- **R-fade and R-trend.** Each has enough trades and week clusters, and both lose a little per
  trade after the 0.11% round trip and funding. The selected wallets' common habit, buying
  dips and selling rips over 4 hours and holding for days, does not show up as an edge on BTC,
  ETH and SOL over 2024–2026.
- **R-fund.** It has 78 trades, short of the 200 minimum, and its interval spans zero.
- **What the test leaves out.** The wallets traded mostly HIP-3 stock perps and timed entries in
  ways a fixed σ threshold does not capture. The rules test the habit, not the wallets.

## Reproduce

```bash
cd research/data
python -I hl_api.py raw leaderboard
python -I hl_api.py raw wallets --top 200 --start 2026-07-20 --need-before 2026-09-12
python -I hl_api.py raw binance --coins BTC,ETH,SOL --start 2023-12-01 --end 2026-07-20
cd ../ideas
python -I hl_wallets.py explore          # fetches the candles and funding it needs
python -I hl_wallets.py backtest --rule fade --k 1.064 --hold 61 \
    --coins BTC,ETH,SOL,xyz:SKHX,xyz:SNDK,xyz:XYZ100 --alpha 0.0167   # and trend / fund
```

The leaderboard and the served fills change daily, so a re-fetch selects a different set of
wallets.

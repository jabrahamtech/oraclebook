# Round 6: untried ideas and bull-regime crypto — exploration results

Protocol: [PROTOCOL_V6.md](PROTOCOL_V6.md) with Amendments 1–2 (committed before any data).
One holdout has been run: B3 at F ≥ 10% (Amendment 3), which **failed**. Data fetched 2026-10-19 from a laptop on which no
`*.polymarket.com` host resolves (Gamma, data-api, CLOB), so the three ideas that need those APIs
(P3, B1, B2) have not been run yet. **Nothing here shows a profitable strategy.**

## Verdict (exploration)

| id | idea | data | exploration | would freeze |
|---|---|---|---|---|
| P1 | pre-open lean | kachoio | not testable: 0 markets with a pre-open quote | nothing (protocol: not testable) |
| P2 | BTC leads the alt markets | kachoio | every cell ≤ 0; BTC's move adds no log-loss gain | nothing |
| P3 | wallet skill persists | data-api tape | not run (host unreachable) | – |
| P4 | nested expiries | polyorderbooks | 31 pairs, 980 quoted seconds, none under $1 | nothing (0 trades) |
| B1 | bull drift, hourly / 4 h | Gamma + CLOB | not run (host unreachable) | – |
| B2 | upside longshots in bull runs | Gamma + CLOB | not run (host unreachable) | – |
| B3 | Hyperliquid funding carry | Hyperliquid | all three cells' CIs above 0 | F ≥ 10% (Amendment 3) → holdout **fail** |

1. **P1 cannot be tested on kachoio.** The publisher records each market from its open: exactly
   300 one-second ticks, the first at `open_ts` (BTC and ETH checked in the raw files), so no
   quote exists before the strike is set.
2. **The alt markets do not lag BTC's at one-second resolution, after costs.** Buying the alt in
   BTC's direction loses in all four cells (−1.2% to −4.2%), and BTC's recent logit move adds no
   out-of-sample log-loss gain to the alt's own price (|gain| ≤ 0.0001).
3. **No nested pair ever cost under $1 after fees** in the 4-day polyorderbooks sample, which
   holds only 31 nested pairs with a strike gap ≥ 3 bp. P4 stays descriptive, as the protocol says.
4. **Funding carry beat cash in exploration but not in the holdout.** Frozen at F ≥ 10% and run
   once, it returned −0.02% per 7-day hold [−0.06, +0.01] over 241 holds. The sign flipped, so
   it fails.

## P2: BTC leads the alt markets (kachoio, closes before 2026-04-30 17:41 UTC)

| L | k | Trades | Return [95% CI] |
|---|---|---|---|
| 5 s | 0.05 | 20,400 | −2.54% [−4.65, −0.38] |
| 5 s | 0.10 | 14,838 | −1.18% [−3.98, +1.64] |
| 10 s | 0.05 | 20,591 | −4.18% [−5.69, −2.58] |
| 10 s | 0.10 | 16,440 | −1.38% [−4.21, +1.44] |

26 UTC-day clusters; ETH, SOL and XRP. Out-of-sample log-loss gain from adding BTC's L-second logit
move to the alt's logit(mid), fitted on the first half of exploration and scored on the second:
L = 5 s: +0.0001 at 120 s and +0.0000 at 60 s before the close; L = 10 s: +0.0000 and −0.0000.

## P4: nested expiries (polyorderbooks, closes before 2026-08-23)

| pair | pairs | quoted seconds | under $1 after fees |
|---|---|---|---|
| 15m vs last 5m | 9 | 361 | 0 |
| 4h vs last 15m | 8 | 581 | 0 |
| 4h vs last 5m | 5 | 38 | 0 |

Rule P4(e): 0 trades at e = 0 and at e = 0.01. The sample holds about 60 five-minute and 30
fifteen-minute markets per coin over 4 days, so only 32 nested pairs exist (31 after the 3 bp
filter), 22 of them in exploration.

## B3: Hyperliquid funding carry (BTC, ETH, SOL; entries 2024-01-08 → 2025-09-01)

| rule | 7-day holds | week clusters | return per hold [95% CI] | ≈ APR |
|---|---|---|---|---|
| always on (reference, not a rule) | 1,803 | 86 | +0.14% [+0.08, +0.22] | +7.5% |
| trailing APR ≥ 10% | 1,124 | 77 | +0.27% [+0.19, +0.38] | +14.2% |
| trailing APR ≥ 20% | 537 | 44 | +0.48% [+0.34, +0.64] | +24.7% |
| trailing APR ≥ 30% | 312 | 25 | +0.62% [+0.43, +0.84] | +32.2% |

Cash at 5% is ≈ +0.10% per 7-day hold. Returns are net of the 0.20% round-trip cost and include
the change in premium.

- **The intervals are too narrow.** Entries are daily and holds last 7 days, so holds overlap
  inside a week cluster and across neighbouring weeks. The protocol fixed week clusters; a
  wider block would widen the CI.
- **Return is per $1 of spot.** The margin posted for the short perp is not counted, so the return
  per dollar of total capital is lower. Liquidation and exchange risk are not modelled.
- **Which rule to freeze.** The protocol freezes one rule per idea but does not say how to choose
  between cells that all clear the bar. **F ≥ 10%** was frozen (Amendment 3) because it has the
  most holds and clusters, so it is the most likely to reach the holdout's ≥ 200 trades over
  ≥ 20 clusters. It was not chosen for its return.

### B3 holdout (entries 2025-09-01 → 2026-10-07, run once, α = 0.05 / 7)

```
HOLDOUT B3 apr>=0.1 alpha=0.0071: n=241 clusters=28 ret=-0.02% [-0.06, +0.01] ≈ -1.0% APR
  non-overlapping (Monday entries): n=34 clusters=21 ret=-0.01% [-0.03, +0.02] ≈ -0.3% APR
  verdict (Amendment 3): FAIL
```

Funding cooled in the holdout: only 241 of about 1,200 coin-days had a trailing APR ≥ 10%,
against 1,124 of 1,803 in exploration. Even on those days, the next 7 days' funding did not cover
the 0.20% round trip. The rule's high trailing funding did not persist, so it fails.

## Notes

- **Fixed before any result was seen:**
  - `hl_funding.py`: a 429's body is `null`, which ended pagination, so ETH stopped at 2024-06-15
    and SOL at 2024-01-21. Now every non-200 reply is retried.
  - `bull.py`: `pd.Timestamp` rejects a `numpy.str_` unit under pandas 3.
  - `pm_nested.py`: clustered by the long market's event. Now it clusters by UTC hour and also
    prints the day-clustered interval, as the protocol says.
- **Polyorderbooks for P1.** Its 5m and 15m books start 60 s before the open (4h about 4 min), so
  P1 could be tested there (about 470 five-minute markets). That needs a new protocol; it is not
  part of this one.
- **P3, B1 and B2 are ready to run** with `run_round6.sh` once Gamma, data-api and CLOB resolve;
  their fetch steps are the `polymarket_api.py` lines in `fetch()`.

## Reproduce

```bash
cd research/ideas
PY=python3 ./run_round6.sh fetch     # Polymarket API steps need *.polymarket.com reachable
PY=python3 ./run_round6.sh explore   # one line per result; logs in research/data/out/round6.log
```

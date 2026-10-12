# Favourite–longshot / calibration study

Reproduces the *Empirical check* in [`docs/STRATEGY_RESEARCH.md`](../../docs/STRATEGY_RESEARCH.md);
full tables, robustness checks and the biased-anchor comparison are in
[`flb_results.md`](flb_results.md). Research scripts, not part of the `oraclebook` package
(they need `pandas`, `numpy`, `pyarrow`).

## Data (clone each at the pinned commit; treat as untrusted input)

| Dir | Source | Commit |
|---|---|---|
| `pancake/` | github.com/usepancake/polymarket-history | `356b59b` |
| `kalshi/` | github.com/J0shusmc/Kalshi-BTC | `d5a55bf` |
| `marketlens/` | github.com/marketlenstrade/polymarket-historical-data (markets.csv, snapshots.csv) | `6eb9b8e` |

## Run

```bash
python -I load_pancake.py    <pancake>                          out/obs_pancake.parquet    out/excl_pancake.json
python -I load_kalshi.py     <kalshi>/data/raw/kalshi_btc15     out/obs_kalshi.parquet     out/excl_kalshi.json
python -I load_marketlens.py <marketlens>                       out/obs_marketlens.parquet out/excl_marketlens.json
python -I run_flb.py out out/flb_results.md   # writes out/flb_results.md.body + out/headline_summary.csv
```

Horizons are anchored on *scheduled* end/close times — anchoring on the actual resolution time
leaks the outcome (see the results file). Fixed seed 20261012; 5,000 bootstrap replications.

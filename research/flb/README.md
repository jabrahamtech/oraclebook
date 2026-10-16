# Favourite–longshot / calibration study

Reproduces the *Empirical check* in [`docs/STRATEGY_RESEARCH.md`](../../docs/STRATEGY_RESEARCH.md);
full tables, robustness checks and the biased-anchor comparison are in
[`flb_results.md`](flb_results.md). Research scripts, not part of the `oraclebook` package
(they need `pandas`, `numpy`, `pyarrow`).

## Data

The raw downloads live in [`../data/raw/`](../data/README.md), one folder per source, fetched and
checksum-verified by `../data/fetch.py` (pinned commits / Kaggle versions in `../data/sources.json`).
Treat them as untrusted input.

| Section | Folder under `../data/raw/` | Source |
|---|---|---|
| A | `github_pancake/` | github.com/usepancake/polymarket-history @ `356b59b` |
| B | `github_kalshi/` | github.com/J0shusmc/Kalshi-BTC @ `d5a55bf` |
| C | `github_marketlens/` | github.com/marketlenstrade/polymarket-historical-data @ `6eb9b8e` |
| D | `kaggle_kachoio__polymarket-5-minute-crypto-updown-markets/` | Kaggle kachoio/…, v1 |
| E | `kaggle_polyorderbooks__polymarket-crypto-orderbooks-1s-l2/` | Kaggle polyorderbooks/…, v1 |
| F | `kaggle_madrading__polymarket-2026-fifa-world-cup-order-book/` | Kaggle madrading/…, v2 |

## Run

```bash
python -I ../data/fetch.py ../data/raw        # downloads anything missing; no Kaggle token needed
R=../data/raw
python -I load_pancake.py        $R/github_pancake                       out/obs_pancake.parquet        out/excl_pancake.json
python -I load_kalshi.py         $R/github_kalshi/data/raw/kalshi_btc15  out/obs_kalshi.parquet         out/excl_kalshi.json
python -I load_marketlens.py     $R/github_marketlens                    out/obs_marketlens.parquet     out/excl_marketlens.json
python -I load_kachoio.py        $R/kaggle_kachoio__polymarket-5-minute-crypto-updown-markets  out/obs_kachoio.parquet  out/excl_kachoio.json
python -I load_polyorderbooks.py $R/kaggle_polyorderbooks__polymarket-crypto-orderbooks-1s-l2  out/obs_polyorderbooks.parquet  out/excl_polyorderbooks.json
python -I load_worldcup.py       $R/kaggle_madrading__polymarket-2026-fifa-world-cup-order-book out/obs_worldcup.parquet out/excl_worldcup.json
python -I run_flb.py out out/flb_results.md   # writes out/flb_results.md.body + headline/calibration CSVs
```

The same observation tables can be rebuilt from the shared panels
(`python -I ../data/check_panels.py ../data/panels out` checks that they match row for row), so a
variant of this study can start from [`../data/panels.py`](../data/panels.py) instead of a new loader.

Horizons are anchored on *scheduled* end/close times — anchoring on the actual resolution time
leaks the outcome (see the results file). Return CIs resample markets (market-clustered
bootstrap), with an event-clustered column alongside. Fixed seed 20261012; 5,000 bootstrap
replications. Sections A–C are unchanged by adding D–F: they run first on the same seed.

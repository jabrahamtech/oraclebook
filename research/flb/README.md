# Favourite–longshot / calibration study

Reproduces the *Empirical check* in [`docs/STRATEGY_RESEARCH.md`](../../docs/STRATEGY_RESEARCH.md);
full tables, robustness checks and the biased-anchor comparison are in
[`flb_results.md`](flb_results.md). Research scripts, not part of the `oraclebook` package
(they need `pandas`, `numpy`, `pyarrow`).

## Data (treat as untrusted input; one folder per download under `data/`, git-ignored)

| Dir | Source | Version |
|---|---|---|
| `github_pancake/` | github.com/usepancake/polymarket-history | commit `356b59b` |
| `github_kalshi/` | github.com/J0shusmc/Kalshi-BTC | commit `d5a55bf` |
| `github_marketlens/` | github.com/marketlenstrade/polymarket-historical-data (markets.csv, snapshots.csv) | commit `6eb9b8e` |
| `kaggle_kachoio__polymarket-5-minute-crypto-updown-markets/` | kaggle.com/datasets/kachoio/polymarket-5-minute-crypto-updown-markets (`*_markets`, `*_ticks`) | v1 |
| `kaggle_polyorderbooks__polymarket-crypto-orderbooks-1s-l2/` | kaggle.com/datasets/polyorderbooks/polymarket-crypto-orderbooks-1s-l2 | v1 |
| `kaggle_madrading__polymarket-2026-fifa-world-cup-order-book/` | kaggle.com/datasets/madrading/polymarket-2026-fifa-world-cup-order-book (outcomes, reference_markets, candles_1m_mid) | v1 |

GitHub sources: download the commit tarball (`github.com/<repo>/archive/<commit>.tar.gz`). Kaggle files:
`https://www.kaggle.com/api/v1/datasets/download/<owner>/<slug>/<file>` (public datasets; no token needed
for a single-file download).

## Run

```bash
python -I load_pancake.py        data/github_pancake                       out/obs_pancake.parquet        out/excl_pancake.json
python -I load_kalshi.py         data/github_kalshi/data/raw/kalshi_btc15  out/obs_kalshi.parquet         out/excl_kalshi.json
python -I load_marketlens.py     data/github_marketlens                    out/obs_marketlens.parquet     out/excl_marketlens.json
python -I load_kachoio.py        data/kaggle_kachoio__polymarket-5-minute-crypto-updown-markets  out/obs_kachoio.parquet  out/excl_kachoio.json
python -I load_polyorderbooks.py data/kaggle_polyorderbooks__polymarket-crypto-orderbooks-1s-l2  out/obs_polyorderbooks.parquet  out/excl_polyorderbooks.json
python -I load_worldcup.py       data/kaggle_madrading__polymarket-2026-fifa-world-cup-order-book out/obs_worldcup.parquet out/excl_worldcup.json
python -I run_flb.py out out/flb_results.md   # writes out/flb_results.md.body + headline/calibration CSVs
```

Horizons are anchored on *scheduled* end/close times — anchoring on the actual resolution time
leaks the outcome (see the results file). Return CIs resample markets (market-clustered
bootstrap), with an event-clustered column alongside. Fixed seed 20261012; 5,000 bootstrap
replications. Sections A–C are unchanged by adding D–F: they run first on the same seed.

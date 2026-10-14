# Research datasets: download once, test any methodology

Six public datasets of resolved Polymarket / Kalshi markets with pre-close quotes and outcomes,
fetched reproducibly and normalised to one format. Research scripts, not part of the `oraclebook`
package (they need `pandas`, `numpy`, `pyarrow`). Downloads are untrusted data: run Python with
`-I` and keep scripts outside `raw/`.

## 1. Download (`raw/`, git-ignored, ~1.7 GB)

```bash
python -I fetch.py raw                    # all sources; or name some: fetch.py raw worldcup kalshi
python -I fetch.py raw worldcup --optional  # + World Cup trades / tape / depth files (~2 GB more)
```

`sources.json` pins each source: GitHub sources by commit (tarball, `.env*` files skipped), Kaggle
sources by dataset version with a sha256 per file. Kaggle's public download API needs no token
(the `kaggle` CLI does). A Kaggle file that no longer matches its pinned hash stops the run instead
of being used. Each folder gets a `_source.json` recording what was fetched.

| Name | Venue | What | Outcome | Licence |
|---|---|---|---|---|
| `pancake` | Polymarket | daily candles (last price), planned end times, 2023–2027 closes | official | see repo |
| `kalshi` | Kalshi | KXBTC15M, 1-minute YES bid/ask candles | official | see repo |
| `marketlens` | Polymarket | top-of-book snapshots, 7 series (stocks, crypto, sport, weather) | official | see repo |
| `kachoio` | Polymarket | 5-minute crypto Up/Down, per-second Up and Down books | **inferred** from the final tick; null for close calls | CC0 |
| `polyorderbooks` | Polymarket | crypto Up/Down 5m / 15m / 4h, ~1 s L2 books per outcome | official | CC BY 4.0, cite doi 10.5281/zenodo.22084114 |
| `worldcup` | Polymarket | every 2026 World Cup market, 1-minute bid/ask bars (first outcome) | official | mostly CC BY-NC 4.0 (non-commercial) |

## 2. Build panels (`panels/`, git-ignored, ~0.5 GB, under a minute)

```bash
python -I panels.py raw panels            # or name some: panels.py raw panels kalshi
python -I check_panels.py panels ../flb/out   # optional: panels reproduce the FLB study's inputs
```

Every dataset becomes `panels/<name>/markets.parquet` (one row per market: `market_id`,
`event_id`, `open_ts`, scheduled `close_ts`, actual `resolved_ts`, outcome `y` of the first token,
`y_official`) and `panels/<name>/quotes/*.parquet` (`mkey`, `token`, `ts`, `known_ts`, `bid`, `ask`,
`price`). Column details are in the `panels.py` docstring. The panels keep every quote for every
kept market; spread caps, crossed-book rules, staleness, horizons and clusters are left to you.

## 3. Test a methodology

From the command line (prints favourite / longshot returns per $1 with a clustered bootstrap, and
the calibration slope, per horizon):

```bash
python -I scan.py panels worldcup --offsets=-7D,-1D,-1h --before resolved_ts --stale 6h --spread 0.05
python -I scan.py panels kachoio  --offsets=-60s,-30s --stale 5s --spread 0.05 --parts btc --cluster event_id --fee 0.07
python -I scan.py panels marketlens --anchor data_start_ts --offsets=5min,30min --before resolved_ts --stale 15min
```

Or in Python, for anything `scan.py` doesn't cover:

```python
import sys; sys.path.insert(0, "research/data")
import pandas as pd, panels as P

m = P.load_markets("research/data/panels", "polyorderbooks")
q = P.quote_parts("research/data/panels", "polyorderbooks", parts=["15m"])
snap = P.sample(m[m.length == "15m"], q, "close_ts", {"5m": -pd.Timedelta("5min")}, pd.Timedelta("15s"))
obs = P.contracts(snap, max_spread=0.05)   # market_id, event_id, side, p_buy, p_cal, y, ...
```

`sample()` only ever uses the latest quote visible (`known_ts`) at or before the sample time, so
anchoring on the scheduled `close_ts` is look-ahead-free. Anchoring on `resolved_ts` is possible
but leaks the outcome for markets that resolve early (see `../flb/flb_results.md`).

These are descriptive research tools; nothing here shows that any strategy is profitable.

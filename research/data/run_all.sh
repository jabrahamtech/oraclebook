#!/usr/bin/env bash
# Whole research-data pipeline, each stage in parallel across datasets:
# fetch/verify -> panels -> FLB loaders -> FLB report + panel check + methodology grid.
# Usage (from research/data): PY=path/to/python ./run_all.sh [jobs]
set -euo pipefail
cd "$(dirname "$0")"
PY=${PY:-python3}
J=${1:-$(getconf _NPROCESSORS_ONLN)}
NAMES="pancake kalshi marketlens kachoio polyorderbooks worldcup"
R=raw
FLB=../flb

stage() { echo "== $1 ($(date +%T))"; }

stage "fetch + verify"
printf '%s\n' $NAMES | xargs -P "$J" -I{} "$PY" -I fetch.py "$R" {} > /dev/null

stage "panels"
printf '%s\n' $NAMES | xargs -P "$J" -I{} "$PY" -I panels.py "$R" panels {}

stage "FLB loaders"
mkdir -p "$FLB/out"
cat <<LOADERS | xargs -P "$J" -L1 sh -c '"$0" -I "$@" > /dev/null' "$PY"
$FLB/load_pancake.py $R/github_pancake $FLB/out/obs_pancake.parquet $FLB/out/excl_pancake.json
$FLB/load_kalshi.py $R/github_kalshi/data/raw/kalshi_btc15 $FLB/out/obs_kalshi.parquet $FLB/out/excl_kalshi.json
$FLB/load_marketlens.py $R/github_marketlens $FLB/out/obs_marketlens.parquet $FLB/out/excl_marketlens.json
$FLB/load_kachoio.py $R/kaggle_kachoio__polymarket-5-minute-crypto-updown-markets $FLB/out/obs_kachoio.parquet $FLB/out/excl_kachoio.json
$FLB/load_polyorderbooks.py $R/kaggle_polyorderbooks__polymarket-crypto-orderbooks-1s-l2 $FLB/out/obs_polyorderbooks.parquet $FLB/out/excl_polyorderbooks.json
$FLB/load_worldcup.py $R/kaggle_madrading__polymarket-2026-fifa-world-cup-order-book $FLB/out/obs_worldcup.parquet $FLB/out/excl_worldcup.json
LOADERS

mkdir -p out
stage "FLB report, panel check, methodology grid"
"$PY" -I "$FLB/run_flb.py" "$FLB/out" "$FLB/out/flb_results.md" > "$FLB/out/run_flb.log" &
flb=$!
"$PY" -I check_panels.py panels "$FLB/out" > out/check_panels.log &
chk=$!
"$PY" -I grid.py panels out --jobs "$J"
wait "$flb"
cat out/check_panels.log
wait "$chk"
stage "done"

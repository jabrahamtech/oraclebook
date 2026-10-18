#!/usr/bin/env bash
# Round 6 (PROTOCOL_V6.md): fetch everything, then run every idea's exploration.
# Output is one line per series fetched and one per result; full logs go to $LOG.
# Hosts: kaggle.com, data.binance.vision, gamma-api / data-api / clob .polymarket.com,
# api.hyperliquid.xyz.  Usage: PY=python3 ./run_round6.sh [fetch|explore|all]
set -euo pipefail
cd "$(dirname "$0")"
PY=${PY:-python3}
LOG=${LOG:-../data/out/round6.log}
mkdir -p "$(dirname "$LOG")"
RAW=../data/raw
run() { "$PY" -I "$@" 2>>"$LOG"; }

fetch() {
  (cd ../data && run fetch.py raw kachoio polyorderbooks binance >>"$LOG" \
    && run panels.py raw panels kachoio polyorderbooks >>"$LOG")
  run bull.py fetch
  run ../data/polymarket_api.py "$RAW" updown --interval 5m --start 2026-09-24 --end 2026-10-15 --trades
  for iv in 1h 4h; do
    run ../data/polymarket_api.py "$RAW" updown --interval "$iv"
    run ../data/polymarket_api.py "$RAW" prices --series "btc-$iv,eth-$iv" --minutes 10 --fidelity 1
  done
  run ../data/polymarket_api.py "$RAW" events --name hit \
    --title "^What price will (Bitcoin|Ethereum|Solana) hit in"
  run ../data/polymarket_api.py "$RAW" prices --series events-hit --token yes_token \
    --from-col month_start --minutes 1440 --fidelity 60
  run hl_funding.py fetch
}

explore() {
  for s in pm_preopen pm_btc_lead pm_wallets pm_nested pm_bull_drift pm_hit_longshots hl_funding; do
    run "$s.py" explore || echo "$s: failed (see $LOG)"
  done
}

case "${1:-all}" in
  fetch) fetch ;;
  explore) explore ;;
  all) fetch && explore ;;
esac

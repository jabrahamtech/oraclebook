# Round 4: machine learning, a time-series foundation model, bull regimes, LLM forecasters —
results

Protocol: [PROTOCOL_V4.md](PROTOCOL_V4.md) (written before the data was built). Rules and
holdouts: [CANDIDATES_V4.md](CANDIDATES_V4.md), judged at α = 0.05 / 4. Data: Kalshi's daily BTC
"above K" ladders (17:00 ET close), **744 events, 2024-11-01 → 2026-10-14** (most from Kalshi's
`/historical/*` endpoints), strikes within ±2% of spot, with Binance 1-minute spot; plus round 2's
15-minute BTC / ETH tapes. **Nothing here shows a profitable strategy.**

## Verdict

1. **Gradient boosting (G1) failed out of sample.** Walk-forward on Nov 2024 → Dec 2025 looked
   real: +5.9% at threshold 0.05 with asks ≤ 60 s old (+8.1% [+1.4, +14.8] with asks ≤ 30 s), and
   it survived a look-ahead fix and a stale-quote check. Frozen and run once on Jan → Oct 2026:
   **−1.05% [−8.6, +6.7]** over 1,186 trades and 276 events. The model's Brier score was worse
   than the market's throughout (0.050 vs 0.047).
2. **Chronos-Bolt, zero-shot, failed out of sample.** On the daily ladders it is worse than the
   market (Brier 0.053 vs 0.049) and its rules lose. On 15-minute up/down a 50/50 blend with the
   market scored slightly better than the market, and the taker rule rose with the threshold in
   exploration (+6.7% at 0.05); frozen, the holdout gave **−8.6% [−28.5, +12.8]**.
3. **Bull regimes offer no directional edge.** When BTC's 30-day return exceeds +10%, YES near
   the money trades a little rich (mid 0.554 vs YES rate 0.527), but after the spread and fees
   buying YES loses (−10.9%) and buying NO loses too (−1% to −12%). Takers and makers in the
   15-minute markets also did no better in rising weeks (takers −2.0% up / −2.6% down).
4. **LLM forecasters (Jev, OpenForecaster) cannot be backtested** — their training data may
   contain the outcomes. `llm_forward.py` logs live forecasts on open Kalshi markets next to the
   bid / ask and scores them after settlement; it needs `LLM_BASE_URL` + `LLM_MODEL` (+
   `LLM_API_KEY` for OpenRouter-hosted models such as Jev, or a local Ollama / LM Studio
   endpoint, which this sandbox blocks). Not run.

Four rounds, the same pattern: rules that look significant and grow with the edge threshold in
exploration (S1 order flow, K1, F15, G1) vanish on the holdout. Kalshi's crypto ladders price the
public information — spot, its history, flow — as well as these models do.

## What was tried

| Idea | Data | Result |
|---|---|---|
| G1 gradient boosting (HistGradientBoosting; z-distance, returns, vol, trend, flow, market price) | 416 BTC events walk-forward, 287 holdout | Exploration +5.9% / holdout −1.05% → fail |
| Look-ahead check | — | Binance bars are indexed by open time; the first build used the bar closing 1 min after t. Fixed before freezing (cut the exploration gain by ~1/4). |
| Stale-quote check | ask age | Median ask age ~90 s; restricting to asks ≤ 30 s made exploration *better*, so stale quotes were not the source. |
| F1 Chronos-Bolt small (zero-shot; weights dated 2024-11 / 2025-11) | daily ladders | Brier 0.053 vs market 0.049; rules −0.2% to −3.3% → not frozen |
| F15 Chronos-Bolt on 15-min up/down | round-2 sample | Exploration +6.7% → holdout −8.6% → fail |
| B1 bull regimes | 100 bull events | Both directions negative after spread + fees → not frozen |

## Tooling added

- `cx.py` — daily-ladder history via `/historical/*` (Kalshi lists only the 17:00 ET hour before
  2025) + Binance features; `cx_models.py` — G1 / F1 / B1; `cx15.py` — Chronos on 15-minute
  markets; `llm_forward.py` — forward test harness for LLM forecasters.
- Binance 1-minute history extended to 2024-10 (`research/data/sources.json`).
- Local model stack (scratch only, not a project dependency): scikit-learn 1.9.1, PyTorch 2.14.1,
  chronos-forecasting 2.3.2, `amazon/chronos-bolt-small` weights.

## Reproduce

```bash
cd research/data && python -I fetch.py raw binance
cd ../ideas
python -I cx.py fetch && python -I cx.py build
python -I cx_models.py chronos --model <dir with chronos-bolt-small>
python -I cx_models.py explore
python -I cx_models.py holdout --rule G1 --edge 0.05 --asset BTC --max-age 60 --alpha 0.0125
python -I cx15.py explore --model <dir>
python -I cx15.py holdout --model <dir> --edge 0.05 --alpha 0.0125
```

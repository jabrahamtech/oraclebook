# LLM forecasters on live Kalshi markets — forward test (interim)

Harness: [llm_forward.py](llm_forward.py) (PROTOCOL_V4.md: LLMs cannot be backtested honestly, so
forecasts are logged live and scored after settlement). Models ran locally on a home NAS through
Ollama 0.35.1: Qwen3.8 27B (Q4_K_M), Gemma 4 12B (Q4_K_M) and OpenForecaster-8B (Q8_0; Qwen3-8B
trained with RL on 52k forecasting questions for accuracy and calibration, arXiv 2512.25070,
knowledge cut-off April 2025). No web search or news: the model sees only the market's question,
rules and close time, and — in the "price shown" arm — the market mid.

Sample: one market per event, priced nearest 50/50, among two-sided Kalshi markets closing within
4 days of 2026-10-15 (price ladders on indices, crypto, FX and weather skipped), mostly sports and
esports. 3,831 forecasts; **2,353 settled as of 2026-10-16 04:30 UTC** (the rest settle by
2026-10-19; this file will be updated). Brier difference = model − market mid (lower is better),
95% interval from resampling events. Taker returns use listing quotes, which lag the book: indicative
only. **Nothing here shows a profitable strategy.**

## Result

| Model | Reasoning | Price shown | n | Brier model | Brier market | Diff [95% CI] |
|---|---|---|---|---|---|---|
| Qwen3.8 27B | off | no | 275 | 0.2758 | 0.2137 | +0.062 [+0.037, +0.089] |
| Qwen3.8 27B | 4,096 tokens | no | 295 | 0.2716 | 0.2065 | +0.065 [+0.043, +0.088] |
| Gemma 4 12B | off | no | 285 | 0.2743 | 0.2171 | +0.057 [+0.033, +0.083] |
| OpenForecaster-8B | 4,096 tokens | no | 295 | 0.2546 | 0.2253 | +0.029 [+0.010, +0.050] |
| Qwen3.8 27B | off | yes | 275 | 0.2261 | 0.2137 | +0.012 [+0.001, +0.026] |
| Qwen3.8 27B | 4,096 tokens | yes | 281 | 0.2122 | 0.2041 | +0.008 [−0.000, +0.018] |
| Gemma 4 12B | off | yes | 285 | 0.2266 | 0.2171 | +0.010 [+0.001, +0.021] |
| OpenForecaster-8B | 4,096 tokens | yes | 299 | 0.2275 | 0.2274 | +0.000 [−0.000, +0.001] |

1. **Every model is worse than the market.** Without the price, all four are clearly worse
   (Brier +0.03 to +0.07). OpenForecaster, the forecasting-tuned model, is the least bad.
   Reasoning did not help Qwen.
2. **Shown the price, the models mostly repeat it** and still score slightly worse; OpenForecaster
   ties the market by copying it (it never disagreed enough to trigger a trade).
3. **Trading on the disagreements loses.** Buying where the model beats the ask + fee: Qwen with
   reasoning, no price, −13.7% [−25.5, −1.8] at edge 0 and −33% at edge 0.10; the other arms
   range from −25% to +2%, all with intervals that include zero.
4. OpenForecaster with reasoning **off** cannot answer (it starts reasoning and hits the 32-token
   cap): 473 of 555 replies unparsed; excluded.

Without news retrieval, a local LLM has nothing the market lacks on a sports or esports question
a few days out. A retrieval-augmented setup (what Jev-style products add) is the remaining
untested variant.

## Reproduce

```bash
export LLM_API=ollama LLM_BASE_URL=http://<nas>:<port> LLM_MODEL=<model>
python -I llm_forward.py log ../data/raw/llm_forward/<date>.csv --closing-days 4 --max 400 \
    [--think 4096]
python -I llm_forward.py score ../data/raw/llm_forward/<date>.csv
```

# Macro frozen rules (PROTOCOL_MACRO.md; appended when chosen on exploration)

| id | idea | frozen rule | exploration |
|---|---|---|---|
| CPI1 | Cleveland Fed CPI nowcast vs Kalshi CPI MoM brackets, τ = close − 24 h | macro_cpi.py | 35 events, 171 markets: Brier market 0.122, nowcast 0.206, 50/50 blend 0.132; only 8 taker entries in total (asks rarely print in the 6 h before τ). Nowcast errors are the same size in every period (SD 0.144 pp 2014–21, 0.170 2022–24, 0.137 2025–26), so this is not a calibration problem: the market is sharper than the nowcast → **dropped, nothing frozen** |
| GDP1 | GDPNow vs Kalshi real-GDP brackets (secondary test) | macro_gdp.py, τ = close − 24 h, GDPNow dated the day before τ, normal(nowcast − 0.24, 1.30) from GDPNow's 2014–21 track record; buy a side when model − ask − fee > 0, ask = the latest print in the **24 h** before τ where a taker bought that side (protocol said 6 h; 6 h leaves 3 trades, so the window was widened on exploration — a tuned choice); 1 contract per market; CI by event; α = 0.05 (the only frozen macro rule) | 12 events, 52 markets: Brier market (last trade, median 8 h old) 0.175, GDPNow 0.120, GDPNow better in 8 of 12 events; rule (script) +21.05% [−6.09, +53.58], 40 trades, $21 |

## Holdout (run once)

| id | holdout | passes? |
|---|---|---|
| GDP1 | +30.09% [−10.80, +82.84], 53 trades (1 contract each, $17), 7 events; Brier market 0.120 vs GDPNow 0.120 | **no** — same sign as exploration, CI includes 0; 7 events cannot settle it. A lead worth watching, not a result |

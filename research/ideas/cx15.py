"""Round 4, F1 on 15-minute crypto (PROTOCOL_V4.md amendment): Chronos-Bolt zero-shot vs the
market on round 2's sampled KXBTC15M / KXETH15M tapes.

Kalshi settles on CF Benchmarks' 60-second BRTI average; the strike K* is that average just
before the open. To avoid a basis error the forecast is made in Binance terms: P(Binance price
at the close >= Binance price in the minute before the open).

Usage: python -I cx15.py explore --model DIR
       python -I cx15.py holdout --model DIR --edge E [--alpha A]
"""

import argparse
import math
import pathlib
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import cx
import kx

LEFTS = (10, 5)
PHI = np.vectorize(lambda z: 0.5 * (1 + math.erf(z / math.sqrt(2))))


def rows(model_dir):
    import torch
    from chronos import BaseChronosPipeline

    pipe = BaseChronosPipeline.from_pretrained(model_dir, device_map="cpu", dtype=torch.float32)
    out = []
    for asset in ("BTC", "ETH"):
        px = cx.spot(asset)
        _, t = kx.load([f"KX{asset}15M"])
        t = t.sort_values("ts")
        by = dict(tuple(t.groupby("ticker")))
        for left in LEFTS:
            ctx, meta = [], []
            for tk, g in by.items():
                close = g.close_time.iloc[0]
                at = (close - pd.Timedelta(minutes=left)).floor("min")
                ref = px.asof(close - pd.Timedelta(minutes=16))
                s = px.loc[:at].iloc[-513:-1]
                pre = g[g.ts < at]
                w = pre[pre.ts >= at - pd.Timedelta(minutes=2)]
                ay, an = w[w.taker_side == "yes"].yes_price, w[w.taker_side == "no"].yes_price
                if len(s) < 512 or pre.empty or np.isnan(ref):
                    continue
                ctx.append(torch.tensor(s.to_numpy(), dtype=torch.float32))
                meta.append(
                    {
                        "asset": asset,
                        "ticker": tk,
                        "event": g.event_ticker.iloc[0],
                        "left": left,
                        "split": g.split.iloc[0],
                        "y": g.y.iloc[0],
                        "ref": ref,
                        "last": pre.yes_price.iloc[-1],
                        "ask_yes": ay.iloc[-1] if len(ay) else np.nan,
                        "ask_no": 1 - an.iloc[-1] if len(an) else np.nan,
                    }
                )
            for i in range(0, len(ctx), 256):
                q, _ = pipe.predict_quantiles(
                    torch.stack(ctx[i : i + 256]),
                    prediction_length=left,
                    quantile_levels=[0.1, 0.5, 0.9],
                )
                for r, (q10, q50, q90) in zip(meta[i : i + 256], q[:, -1, :].numpy(), strict=True):
                    sd = (math.log(q90) - math.log(q10)) / 2.5631
                    r["f1"] = float(1 - PHI((math.log(r["ref"]) - math.log(q50)) / sd))
                    out.append(r)
    return pd.DataFrame(out)


def takers(d, edge):
    res = []
    for side in ("yes", "no"):
        p = d[f"ask_{side}"]
        q = d.f1 if side == "yes" else 1 - d.f1
        fee = 0.07 * p * (1 - p)
        sel = (q - p - fee > edge) & p.notna()
        x = d[sel]
        y = x.y if side == "yes" else 1 - x.y
        res.append(x.assign(count=1.0, cost=p[sel] + fee[sel], pnl=y - p[sel] - fee[sel]))
    return pd.concat(res)


def main():
    ap = argparse.ArgumentParser(usage=__doc__)
    ap.add_argument("mode", choices=["explore", "holdout"])
    ap.add_argument("--model", required=True)
    ap.add_argument("--edge", type=float, default=0.0)
    ap.add_argument("--alpha", type=float, default=0.05)
    a = ap.parse_args()
    d = rows(a.model)
    if a.mode == "holdout":
        f = takers(d[d.split == "holdout"], a.edge)
        print(f"HOLDOUT F1-15m edge {a.edge}: {kx.fmt(kx.ratio_boot(f, 'event', alpha=a.alpha))}")
        return
    e = d[d.split == "explore"]
    for (asset, left), g in e.groupby(["asset", "left"]):
        print(
            f"{asset} {left} min left: n={len(g)} "
            f"Brier market {((g['last'] - g.y) ** 2).mean():.4f}"
            f", Chronos {((g.f1 - g.y) ** 2).mean():.4f}, "
            f"blend {((0.5 * g.f1 + 0.5 * g['last'] - g.y) ** 2).mean():.4f}"
        )
    for edge in (0.0, 0.03, 0.05, 0.10):
        print(f"edge {edge:.2f}: {kx.fmt(kx.ratio_boot(takers(e, edge), 'event'))}")


if __name__ == "__main__":
    main()

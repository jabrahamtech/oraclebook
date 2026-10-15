"""Round 3: sample live top-of-book of every open Kalshi daily-high and daily-low market once a
minute, to measure the queue resting at 99¢ on contracts the observations have already decided
(to be paired with the trade tape afterwards).

Usage: python -I kx_live_wx.py <out.csv> [--minutes M] [--every S]
"""

import argparse
import csv
import json
import pathlib
import subprocess
import sys
import time
from datetime import UTC, datetime

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import wxobs

BASE = "https://api.elections.kalshi.com/trade-api/v2"
COLS = [
    "yes_bid_dollars",
    "yes_ask_dollars",
    "yes_bid_size_fp",
    "yes_ask_size_fp",
    "volume_fp",
    "last_price_dollars",
]


def get(path):
    for i in range(4):
        r = subprocess.run(
            ["curl", "-s", "-m", "20", "-w", "\n%{http_code}", BASE + path],
            capture_output=True,
            text=True,
            check=False,
        )
        body, _, code = r.stdout.rpartition("\n")
        if code == "200":
            try:
                return json.loads(body)
            except json.JSONDecodeError:
                pass
        time.sleep(1.5 if code == "429" else 2**i)
    return None


def main():
    ap = argparse.ArgumentParser(usage=__doc__)
    ap.add_argument("out")
    ap.add_argument("--minutes", type=float, default=480)
    ap.add_argument("--every", type=float, default=60)
    a = ap.parse_args()
    out = pathlib.Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    end = time.monotonic() + 60 * a.minutes
    with open(out, "a", newline="") as fh:
        w = csv.writer(fh)
        if fh.tell() == 0:
            w.writerow(["ts", "ticker", "status", "close_time", *COLS])
        while time.monotonic() < end:
            t0 = time.monotonic()
            ts = datetime.now(UTC).isoformat(timespec="seconds")
            n = 0
            for s in [*wxobs.STATIONS, *map(wxobs.low_series, wxobs.STATIONS)]:
                d = get(f"/markets?series_ticker={s}&status=open&limit=200")
                for m in (d or {}).get("markets", []):
                    w.writerow(
                        [ts, m["ticker"], m.get("status"), m.get("close_time")]
                        + [m.get(c) for c in COLS]
                    )
                    n += 1
            fh.flush()
            print(f"{ts}: {n} markets", flush=True)
            time.sleep(max(0.0, a.every - (time.monotonic() - t0)))


if __name__ == "__main__":
    main()

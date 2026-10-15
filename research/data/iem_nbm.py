"""Download archived National Blend of Models station guidance (NBS text product: hourly runs,
daytime max `txn` and its spread `xnd`) from the Iowa Environmental Mesonet MOS archive.

One file per station under <raw_root>/iem_nbm/<STATION>.csv. Responses are untrusted data:
written to disk and parsed as CSV, never executed.

Usage: python -I iem_nbm.py <raw_root> --stations KNYC,KMDW,... --start 2026-08-12 --end 2026-10-15
"""

import argparse
import datetime as dt
import pathlib
import subprocess
import sys
import time

BASE = "https://mesonet.agron.iastate.edu/cgi-bin/request/mos.py"


def fetch(station, start, end, out):
    q = f"{BASE}?station={station}&model=NBS&sts={start}T00:00Z&ets={end}T00:00Z&format=csv"
    code = ""
    for i in range(6):
        r = subprocess.run(
            ["curl", "-s", "-m", "300", "-o", str(out), "-w", "%{http_code}", q],
            capture_output=True,
            text=True,
            check=False,
        )
        code = r.stdout
        if code == "200" and out.stat().st_size > 200:
            return
        time.sleep(10 * 2**i)
    sys.exit(f"{station}: failed (last HTTP {code})")


def main():
    ap = argparse.ArgumentParser(usage=__doc__)
    ap.add_argument("raw_root")
    ap.add_argument("--stations", required=True)
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    a = ap.parse_args()
    for d in (a.start, a.end):
        dt.date.fromisoformat(d)
    folder = pathlib.Path(a.raw_root) / "iem_nbm"
    folder.mkdir(parents=True, exist_ok=True)
    for s in a.stations.split(","):
        out = folder / f"{s}.csv"
        fetch(s, a.start, a.end, out)
        print(f"{s}: {out.read_text().count(chr(10)) - 1} rows", flush=True)
        time.sleep(3)


if __name__ == "__main__":
    main()

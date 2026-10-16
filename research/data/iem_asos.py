"""Download routine + special METARs for ASOS stations from the Iowa Environmental Mesonet archive.

One file per station under <raw_root>/iem_asos/<STATION>.csv (IEM's comma format, raw METAR
included). Responses are untrusted data: written to disk and parsed as CSV, never executed.

Usage: python -I iem_asos.py <raw_root> --stations NYC,MDW,... --start 2026-08-14 --end 2026-10-16
"""

import argparse
import datetime as dt
import pathlib
import subprocess
import sys
import time

BASE = "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py"


def fetch(station, start, end, out):
    q = (
        f"{BASE}?station={station}&data=tmpf&data=metar&tz=UTC&format=onlycomma&latlon=no"
        f"&missing=empty&trace=empty&report_type=3&report_type=4"
        f"&year1={start.year}&month1={start.month}&day1={start.day}"
        f"&year2={end.year}&month2={end.month}&day2={end.day}"
    )
    for i in range(6):
        r = subprocess.run(
            ["curl", "-s", "-m", "120", "-o", str(out), "-w", "%{http_code}", q],
            capture_output=True,
            text=True,
            check=False,
        )
        if r.stdout == "200" and out.stat().st_size > 0:
            return
        time.sleep(5 * 2**i)
    sys.exit(f"{station}: failed (last HTTP {r.stdout})")


def main():
    ap = argparse.ArgumentParser(usage=__doc__)
    ap.add_argument("raw_root")
    ap.add_argument("--stations", required=True)
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    a = ap.parse_args()
    start, end = (dt.date.fromisoformat(x) for x in (a.start, a.end))
    folder = pathlib.Path(a.raw_root) / "iem_asos"
    folder.mkdir(parents=True, exist_ok=True)
    for s in a.stations.split(","):
        out = folder / f"{s}.csv"
        fetch(s, start, end, out)
        lines = out.read_text().count("\n")
        print(f"{s}: {lines - 1} observations", flush=True)
        time.sleep(1)


if __name__ == "__main__":
    main()

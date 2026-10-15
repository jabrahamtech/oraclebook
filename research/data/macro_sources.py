"""Download public macro nowcasts with daily as-of histories into <raw_root>/macro_sources/:
- Cleveland Fed inflation nowcast (monthly CPI / core CPI / PCE MoM, one chart per target month,
  daily values as they stood, plus the actual print);
- Atlanta Fed GDPNow tracking workbook (daily estimates per quarter) and its release dates.
Responses are untrusted data: written to disk and parsed as JSON / xlsx, never executed.

Usage: python -I macro_sources.py <raw_root>
"""

import argparse
import datetime as dt
import hashlib
import json
import pathlib
import subprocess
import sys

SOURCES = {
    "cleveland_nowcast_month.json": (
        "https://www.clevelandfed.org/-/media/files/webcharts/inflationnowcasting/"
        "nowcast_month.json?sc_lang=en"
    ),
    "gdpnow_tracking.xlsx": (
        "https://www.atlantafed.org/-/media/Project/Atlanta/FRBA/Documents/research-and-data/"
        "data/gdpnow/GDPTrackingModelDataAndForecasts.xlsx"
    ),
}


def main():
    ap = argparse.ArgumentParser(usage=__doc__)
    ap.add_argument("raw_root")
    a = ap.parse_args()
    folder = pathlib.Path(a.raw_root) / "macro_sources"
    folder.mkdir(parents=True, exist_ok=True)
    meta = {}
    for name, url in SOURCES.items():
        out = folder / name
        r = subprocess.run(
            ["curl", "-s", "-L", "-m", "300", "-o", str(out), "-w", "%{http_code}", url],
            capture_output=True,
            text=True,
            check=False,
        )
        if r.stdout != "200" or not out.exists() or out.stat().st_size < 1000:
            sys.exit(f"{name}: download failed (HTTP {r.stdout})")
        digest = hashlib.sha256(out.read_bytes()).hexdigest()
        meta[name] = {"url": url, "sha256": digest, "bytes": out.stat().st_size}
        print(f"{name}: {out.stat().st_size:,} bytes", flush=True)
    meta["fetched_at"] = dt.datetime.now(dt.UTC).isoformat()
    (folder / "_source.json").write_text(json.dumps(meta, indent=1))


if __name__ == "__main__":
    main()

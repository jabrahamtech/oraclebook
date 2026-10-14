"""Download the pinned research datasets in sources.json, one folder per source.

GitHub sources are commit tarballs; Kaggle files come from the public download API (no token needed)
at a pinned dataset version and are checked against the sha256 in sources.json. Existing files that
already match are skipped. Downloads are untrusted data: nothing in them is executed.

Usage: python -I fetch.py <raw_root> [name ...] [--optional]
  name        source keys from sources.json (default: all)
  --optional  also fetch each Kaggle source's optional_files (large; not checksum-pinned)
"""

import datetime as dt
import hashlib
import http.client
import json
import pathlib
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile

HERE = pathlib.Path(__file__).resolve().parent
KAGGLE = "https://www.kaggle.com/api/v1/datasets/download"
CODELOAD = "https://codeload.github.com"
BINANCE = "https://data.binance.vision/data"
CHUNK = 1 << 20


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while b := f.read(CHUNK):
            h.update(b)
    return h.hexdigest()


class NotFound(Exception):
    pass


def download(url, dest):
    """curl where available (it has proven more reliable through proxies), else urllib."""
    if shutil.which("curl"):
        r = subprocess.run(
            [
                "curl",
                "-sSL",
                "--retry",
                "8",
                "--retry-all-errors",
                "-o",
                str(dest),
                "-w",
                "%{http_code}",
                url,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if r.stdout.strip() == "404":
            raise NotFound(url)
        if r.returncode or r.stdout.strip() not in ("200", "206"):
            sys.exit(f"{url}: curl exit {r.returncode}, HTTP {r.stdout.strip()} {r.stderr.strip()}")
        return
    try:
        _download_urllib(url, dest)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise NotFound(url) from e
        raise


def _download_urllib(url, dest, attempts=8):
    """Connections are sometimes cut mid-body, with or without an error. A cut body with a known
    length is resumed with a Range request; anything else is downloaded again from the start."""
    have, total = 0, None
    for _ in range(attempts):
        headers = {"User-Agent": "oraclebook-research-fetch"}
        if have and total:
            headers["Range"] = f"bytes={have}-"
        try:
            with urllib.request.urlopen(
                urllib.request.Request(url, headers=headers), timeout=60
            ) as r:
                resumed = have > 0 and r.status == 206
                if not resumed:
                    url = r.geturl()
                    total = r.headers.get("Content-Length")
                    total = None if total is None else int(total)
                with open(dest, "ab" if resumed else "wb") as f:
                    shutil.copyfileobj(r, f, CHUNK)
            complete = True
        except (
            http.client.IncompleteRead,
            urllib.error.URLError,
            ConnectionError,
            TimeoutError,
        ) as e:
            print(f"    connection cut ({type(e).__name__}), retrying")
            complete = False
        have = dest.stat().st_size if dest.exists() else 0
        if total is None:
            if complete:
                return
            have = 0
        elif have == total:
            return
        else:
            print(f"    short read ({have:,} of {total:,} B), resuming")
    sys.exit(f"{url}: incomplete after {attempts} attempts")


def fetch_github(src, folder):
    meta = folder / "_source.json"
    if meta.exists() and json.loads(meta.read_text()).get("commit") == src["commit"]:
        print(f"  {folder.name}: commit {src['commit'][:7]} already present")
        return
    if folder.exists():
        sys.exit(f"{folder} exists without a matching _source.json; move it aside and re-run")
    url = f"{CODELOAD}/{src['repo']}/tar.gz/{src['commit']}"
    with tempfile.TemporaryDirectory(dir=folder.parent) as tmp:
        tgz = pathlib.Path(tmp) / "src.tar.gz"
        download(url, tgz)
        stage = pathlib.Path(tmp) / "x"
        with tarfile.open(tgz) as t:
            members = []
            for m in t.getmembers():
                parts = m.name.split("/", 1)
                if (
                    len(parts) < 2
                    or not parts[1]
                    or pathlib.PurePosixPath(parts[1]).name.startswith(".env")
                ):
                    continue
                m.name = parts[1]
                members.append(m)
            t.extractall(stage, members=members, filter="data")
        stage.rename(folder)
    meta.write_text(
        json.dumps(
            {
                "repo": src["repo"],
                "commit": src["commit"],
                "url": url,
                "fetched_at": dt.datetime.now(dt.UTC).isoformat(),
            },
            indent=1,
        )
    )
    print(f"  {folder.name}: extracted {len(members)} entries")


def fetch_kaggle_file(src, folder, name, want):
    dest = folder / name
    if dest.exists():
        got = sha256(dest)
        if want is None or got == want:
            print(f"  {name}: present")
            return got
    q = urllib.parse.urlencode({"datasetVersionNumber": src["version"]})
    part = folder / f".{name}.part"
    download(f"{KAGGLE}/{src['ref']}/{urllib.parse.quote(name)}?{q}", part)
    # Kaggle may wrap a single file in a zip; unwrap it unless a zip was asked for.
    with open(part, "rb") as f:
        zipped = f.read(4) == b"PK\x03\x04"
    if zipped and not name.endswith(".zip"):
        with zipfile.ZipFile(part) as z:
            inner = [i for i in z.infolist() if not i.is_dir()]
            if len(inner) != 1:
                sys.exit(f"{name}: expected one file in the zip, got {len(inner)}")
            with z.open(inner[0]) as zi, open(part.with_suffix(".unz"), "wb") as f:
                shutil.copyfileobj(zi, f, CHUNK)
        part.with_suffix(".unz").replace(part)
    got = sha256(part)
    if want is not None and got != want:
        part.unlink()
        sys.exit(f"{name}: sha256 {got} does not match the pinned {want}")
    part.replace(dest)
    print(f"  {name}: downloaded ({dest.stat().st_size:,} B)")
    return got


def fetch_binance(src, folder):
    """Monthly kline zips from data.binance.vision, checked against Binance's published sha256."""
    folder.mkdir(parents=True, exist_ok=True)
    got = {}
    for market, symbols in src["symbols"].items():
        for sym in symbols:
            for month in src["months"]:
                name = f"{sym}-{src['interval']}-{month}"
                dest = folder / f"{market.replace('/', '_')}__{name}.csv"
                if dest.exists():
                    got[dest.name] = "present"
                    continue
                url = f"{BINANCE}/{market}/monthly/klines/{sym}/{src['interval']}/{name}.zip"
                part = folder / f".{name}.zip.part"
                try:
                    download(url + ".CHECKSUM", part)
                except NotFound:
                    print(f"  {market} {name}: not published, skipped")
                    got[dest.name] = "missing"
                    continue
                want = part.read_text().split()[0]
                download(url, part)
                if sha256(part) != want:
                    part.unlink()
                    sys.exit(f"{name}: sha256 does not match Binance's CHECKSUM")
                with zipfile.ZipFile(part) as z, z.open(f"{name}.csv") as zi, open(dest, "wb") as f:
                    shutil.copyfileobj(zi, f, CHUNK)
                part.unlink()
                got[dest.name] = want
                print(f"  {dest.name}: downloaded")
    (folder / "_source.json").write_text(
        json.dumps(
            {
                "interval": src["interval"],
                "zip_sha256": got,
                "fetched_at": dt.datetime.now(dt.UTC).isoformat(),
            },
            indent=1,
        )
    )


def fetch_kaggle(src, folder, optional):
    folder.mkdir(parents=True, exist_ok=True)
    files = dict(src["files"])
    if optional:
        files.update({f: None for f in src.get("optional_files", [])})
    hashes = {name: fetch_kaggle_file(src, folder, name, want) for name, want in files.items()}
    (folder / "_source.json").write_text(
        json.dumps(
            {
                "ref": src["ref"],
                "version": src["version"],
                "licence": src.get("licence"),
                "sha256": hashes,
                "fetched_at": dt.datetime.now(dt.UTC).isoformat(),
            },
            indent=1,
        )
    )


def main(argv):
    optional = "--optional" in argv
    args = [a for a in argv if not a.startswith("--")]
    if not args:
        sys.exit(__doc__)
    root = pathlib.Path(args[0]).resolve()
    root.mkdir(parents=True, exist_ok=True)
    sources = json.loads((HERE / "sources.json").read_text())
    names = args[1:] or list(sources)
    unknown = set(names) - set(sources)
    if unknown:
        sys.exit(f"unknown sources {sorted(unknown)}; choose from {list(sources)}")
    for name in names:
        src = sources[name]
        print(f"{name} ({src['kind']})")
        folder = root / src["folder"]
        if src["kind"] == "github":
            fetch_github(src, folder)
        elif src["kind"] == "binance":
            fetch_binance(src, folder)
        else:
            fetch_kaggle(src, folder, optional)


if __name__ == "__main__":
    main(sys.argv[1:])

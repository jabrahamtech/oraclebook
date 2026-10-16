"""oraclebook CLI — `record` live order flow, then `replay` it through a strategy.

Two subcommands:

    oraclebook record --venue polymarket --assets BTC[,ETH] --intervals 5[,15] \\
        --out recordings/ [--minutes N]

    oraclebook replay --strategy dotted.module:ClassName --from recordings/ [--report]

`record` wires a venue's Feed onto a Bus with a Recorder subscribed to the market-data
topics, and captures JSONL recordings under `--out` until Ctrl-C or `--minutes` elapses.
Requires the `polymarket` extra (`pip install oraclebook[polymarket]`) for its aiohttp
websocket client.

`replay` imports a strategy class from a dotted `module:ClassName` path (there is no
strategy-name registry — the dotted path is the honest, unambiguous interface),
instantiates it with no constructor args, and runs `oraclebook.replay.run_replay` against
the recording(s) under `--from`. `--report` prints totals plus a per-market table
(fills, notional, fees, 1/10/60s markouts against the recording's own mids via
`oraclebook.analysis`) read back from the finished App's Ledger — numbers only, not a
profitability claim.
"""
from __future__ import annotations

import argparse
import asyncio
import glob
import os
import sys
import time
from importlib import import_module

import structlog

logger = structlog.get_logger()


def _parse_csv(value: str) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="oraclebook",
        description="Record live prediction-market order flow, and replay it through a "
        "strategy (reproducible for a single feed; paper fills).",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    record = subparsers.add_parser(
        "record",
        help="Capture live order flow from a venue to a recording.",
        description="Wire a venue's feed onto the bus with a Recorder and capture JSONL "
        "recordings until Ctrl-C or --minutes elapses.",
    )
    record.add_argument(
        "--venue",
        required=True,
        choices=["polymarket"],
        help="Venue to record from (currently only 'polymarket').",
    )
    record.add_argument(
        "--assets",
        required=True,
        help="Comma-separated asset symbols, e.g. BTC or BTC,ETH.",
    )
    record.add_argument(
        "--intervals",
        required=True,
        help="Comma-separated interval minutes, e.g. 5 or 5,15.",
    )
    record.add_argument(
        "--out",
        required=True,
        help="Output directory for the recording (created if missing).",
    )
    record.add_argument(
        "--minutes",
        type=float,
        default=None,
        help="Stop recording after this many minutes (default: run until Ctrl-C).",
    )

    replay = subparsers.add_parser(
        "replay",
        help="Replay a recording through a strategy and report fills/PnL.",
        description="Import a strategy from a dotted 'module:ClassName' path and replay it "
        "against recorded order flow (reproducible for a single feed; paper fills).",
    )
    replay.add_argument(
        "--strategy",
        required=True,
        metavar="MODULE:CLASS",
        help="Dotted path to the strategy class, e.g. "
        "oraclebook.strategies.complete_set_maker:CompleteSetMaker.",
    )
    replay.add_argument(
        "--from",
        dest="from_path",
        required=True,
        metavar="PATH",
        help="Recording file, or a directory containing .jsonl recordings.",
    )
    replay.add_argument(
        "--report",
        action="store_true",
        help="Print totals plus per-market fills, notional, fees and 1/10/60s markouts.",
    )

    return parser


def _import_strategy(dotted_path: str):
    """Import a strategy class from a 'module.path:ClassName' string."""
    if ":" not in dotted_path:
        raise ValueError(
            f"--strategy must be 'module.path:ClassName', got {dotted_path!r}"
        )
    module_path, _, class_name = dotted_path.partition(":")
    try:
        module = import_module(module_path)
    except ImportError as exc:
        raise ValueError(f"could not import module {module_path!r}: {exc}") from exc
    try:
        strategy_cls = getattr(module, class_name)
    except AttributeError as exc:
        raise ValueError(f"module {module_path!r} has no class {class_name!r}") from exc
    return strategy_cls


def _resolve_recording_paths(from_path: str) -> list[str]:
    """Resolve --from to a list of one or more .jsonl recording files."""
    if os.path.isdir(from_path):
        paths = sorted(glob.glob(os.path.join(from_path, "*.jsonl")))
        if not paths:
            raise ValueError(f"no .jsonl recordings found under {from_path!r}")
        return paths
    if os.path.isfile(from_path):
        return [from_path]
    raise ValueError(f"--from path does not exist: {from_path!r}")


async def _run_record(args: argparse.Namespace) -> int:
    try:
        import aiohttp  # noqa: F401  # optional adapter dependency, feed needs it
    except ImportError:
        logger.error(
            "cli.record.missing_dependency",
            hint="pip install oraclebook[polymarket]",
        )
        print(
            "error: recording requires the 'polymarket' extra (aiohttp).\n"
            "Install it with: pip install oraclebook[polymarket]",
            file=sys.stderr,
        )
        return 1

    from oraclebook.adapters.polymarket import PolymarketVenue
    from oraclebook.core.bus import Bus
    from oraclebook.replay import Recorder

    assets = _parse_csv(args.assets)
    intervals = [int(i) for i in _parse_csv(args.intervals)]
    os.makedirs(args.out, exist_ok=True)
    out_path = os.path.join(args.out, f"{args.venue}_{int(time.time())}.jsonl")

    venue = PolymarketVenue(assets=assets, intervals=intervals)
    bus = Bus()
    feed = venue.feed(bus)
    recorder = Recorder(bus, out_path)

    await recorder.start()
    await feed.start()
    logger.info(
        "cli.record.started",
        venue=args.venue,
        assets=assets,
        intervals=intervals,
        out=out_path,
        minutes=args.minutes,
    )
    try:
        if args.minutes is not None:
            await asyncio.sleep(args.minutes * 60)
        else:
            await asyncio.Event().wait()  # run until Ctrl-C
    except asyncio.CancelledError:
        pass
    finally:
        await feed.stop()
        await recorder.stop()
        logger.info("cli.record.stopped", out=out_path)

    print(f"Recording saved to {out_path}")
    return 0


_REPORT_HORIZONS = (1.0, 10.0, 60.0)


def _fmt(value: float | None, spec: str) -> str:
    return "n/a" if value is None else format(value, spec)


def _print_report(args: argparse.Namespace, n_recordings: int, fill_markouts: list) -> None:
    """Deterministic replay report: totals, then per-market fills / notional / fees / markouts."""
    from oraclebook.analysis import summarize

    by_market = summarize(fill_markouts, by="market_id")
    print("Replay report")
    print(f"  strategy:   {args.strategy}")
    print(f"  recordings: {n_recordings}")
    print(f"  fills:      {len(fill_markouts)}")
    print(f"  notional:   {sum(m.notional for m in fill_markouts):.2f}")
    print(f"  fees:       {sum(m.fee for m in fill_markouts):.4f}")
    print()
    print("Per market (markouts: size-weighted mean, per share, signed; n/a = no mid at horizon)")
    mo_headers = "".join(f"{'mo_' + format(h, 'g') + 's':>10}" for h in _REPORT_HORIZONS)
    print(f"  {'market':<24}{'fills':>7}{'notional':>12}{'fees':>10}{mo_headers}{'pnl_res':>11}")
    for market_id, summary in by_market.items():
        mo_cells = "".join(
            f"{_fmt(summary.mean_markout.get(h), '+.4f'):>10}" for h in _REPORT_HORIZONS
        )
        print(
            f"  {market_id:<24}{summary.count:>7}{summary.notional:>12.2f}{summary.fees:>10.4f}"
            f"{mo_cells}{_fmt(summary.pnl_to_resolution, '+.2f'):>11}"
        )
    print("pnl_res = $ PnL to settlement net of fees (n/a unless every fill's market resolved)")
    print("numbers only — not a profitability claim")


async def _run_replay(args: argparse.Namespace) -> int:
    from oraclebook.analysis import markouts, mid_series_from_recording
    from oraclebook.core.ledger import Ledger
    from oraclebook.replay import run_replay

    strategy_cls = _import_strategy(args.strategy)
    recording_paths = _resolve_recording_paths(args.from_path)

    fill_markouts = []
    for path in recording_paths:
        strategy = strategy_cls()
        app = await run_replay(path, strategy)
        ledger = app.ledger
        if not isinstance(ledger, Ledger):
            continue  # run_replay always wires a concrete Ledger; narrow for the type checker
        settlements = {
            rec["payload"]["token_id"]: rec["payload"]["settlement_value"]
            for rec in ledger.resolutions()
        }
        # Mids come from the same recording the fills were replayed against.
        fill_markouts.extend(
            markouts(
                ledger.fills(),
                mid_series_from_recording(path),
                horizons=_REPORT_HORIZONS,
                settlements=settlements,
            )
        )

    if args.report:
        _print_report(args, len(recording_paths), fill_markouts)
    else:
        print(
            f"Replay complete: {len(fill_markouts)} fills across "
            f"{len(recording_paths)} recording(s)"
        )

    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "record":
            return asyncio.run(_run_record(args))
        if args.command == "replay":
            return asyncio.run(_run_replay(args))
    except KeyboardInterrupt:
        return 0
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    parser.error(f"unknown command: {args.command}")
    return 1


if __name__ == "__main__":
    sys.exit(main())

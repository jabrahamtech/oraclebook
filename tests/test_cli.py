"""CLI: argparse parsing, dotted-path strategy import, replay end-to-end, record errors."""
import asyncio
import os
import sys
import tempfile

import pytest

import oraclebook as ob
from oraclebook.cli import _build_parser, _import_strategy, _resolve_recording_paths, main
from oraclebook.core.bus import Bus
from oraclebook.core.events import EVT_BOOK_UPDATE, BookUpdate
from oraclebook.replay import Recorder


class CheapBuyer(ob.BaseStrategy):
    name = "cb"

    async def on_book(self, book):
        if 0 < book.best_ask <= 0.40:
            return [
                ob.OrderRequest(
                    token_id=book.token_id,
                    market_id=book.market_id,
                    side=ob.Side.BUY,
                    price=book.best_ask,
                    size=10,
                    order_type=ob.OrderType.LIMIT,
                    strategy=self.name,
                )
            ]
        return []


async def _record(path):
    bus = Bus()
    rec = Recorder(bus, path)
    await rec.start()
    for bid in [0.50, 0.38, 0.42, 0.35, 0.60]:  # two are <= 0.40
        await bus.publish(EVT_BOOK_UPDATE, BookUpdate(token_id="UP", market_id="m1", best_bid=bid - 0.01, best_ask=bid))
        await asyncio.sleep(0.001)
    await asyncio.sleep(0.03)
    await rec.stop()


# --- argparse parsing --------------------------------------------------------


def test_parses_record_args():
    parser = _build_parser()
    args = parser.parse_args(
        [
            "record",
            "--venue",
            "polymarket",
            "--assets",
            "BTC,ETH",
            "--intervals",
            "5,15",
            "--out",
            "recordings/",
            "--minutes",
            "10",
        ]
    )
    assert args.command == "record"
    assert args.venue == "polymarket"
    assert args.assets == "BTC,ETH"
    assert args.intervals == "5,15"
    assert args.out == "recordings/"
    assert args.minutes == 10.0


def test_record_minutes_defaults_to_none():
    parser = _build_parser()
    args = parser.parse_args(
        ["record", "--venue", "polymarket", "--assets", "BTC", "--intervals", "5", "--out", "out/"]
    )
    assert args.minutes is None


def test_parses_replay_args():
    parser = _build_parser()
    args = parser.parse_args(
        [
            "replay",
            "--strategy",
            "oraclebook.strategies.complete_set_maker:CompleteSetMaker",
            "--from",
            "recordings/",
            "--report",
        ]
    )
    assert args.command == "replay"
    assert args.strategy == "oraclebook.strategies.complete_set_maker:CompleteSetMaker"
    assert args.from_path == "recordings/"
    assert args.report is True


def test_replay_report_defaults_to_false():
    parser = _build_parser()
    args = parser.parse_args(
        ["replay", "--strategy", "mod:Cls", "--from", "recordings/"]
    )
    assert args.report is False


def test_missing_required_arg_exits_nonzero():
    parser = _build_parser()
    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(["record", "--venue", "polymarket"])
    assert exc_info.value.code != 0


def test_unknown_venue_choice_rejected():
    parser = _build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(
            ["record", "--venue", "kalshi", "--assets", "BTC", "--intervals", "5", "--out", "o/"]
        )


# --- dotted-path strategy import ---------------------------------------------


def test_import_strategy_happy_path():
    cls = _import_strategy("oraclebook.strategies.complete_set_maker:CompleteSetMaker")
    assert cls is ob.CompleteSetMaker
    instance = cls()
    assert isinstance(instance, ob.BaseStrategy)


def test_import_strategy_missing_colon_raises():
    with pytest.raises(ValueError, match="module.path:ClassName"):
        _import_strategy("oraclebook.strategies.complete_set_maker.CompleteSetMaker")


def test_import_strategy_bad_module_raises():
    with pytest.raises(ValueError, match="could not import module"):
        _import_strategy("oraclebook.strategies.nonexistent_module:Foo")


def test_import_strategy_bad_class_raises():
    with pytest.raises(ValueError, match="no class"):
        _import_strategy("oraclebook.strategies.complete_set_maker:NoSuchStrategy")


# --- recording path resolution ------------------------------------------------


def test_resolve_recording_paths_single_file():
    path = os.path.join(tempfile.mkdtemp(), "rec.jsonl")
    with open(path, "w", encoding="utf-8") as f:
        f.write("")
    assert _resolve_recording_paths(path) == [path]


def test_resolve_recording_paths_directory():
    directory = tempfile.mkdtemp()
    p1 = os.path.join(directory, "a.jsonl")
    p2 = os.path.join(directory, "b.jsonl")
    for p in (p1, p2):
        with open(p, "w", encoding="utf-8") as f:
            f.write("")
    assert _resolve_recording_paths(directory) == [p1, p2]


def test_resolve_recording_paths_empty_directory_raises():
    directory = tempfile.mkdtemp()
    with pytest.raises(ValueError, match="no .jsonl recordings"):
        _resolve_recording_paths(directory)


def test_resolve_recording_paths_missing_path_raises():
    with pytest.raises(ValueError, match="does not exist"):
        _resolve_recording_paths(os.path.join(tempfile.mkdtemp(), "nope"))


# --- replay end-to-end (against a tiny synthetic recording) ------------------


def test_replay_command_end_to_end(capsys):
    path = os.path.join(tempfile.mkdtemp(), "rec.jsonl")
    asyncio.run(_record(path))
    exit_code = main(["replay", "--strategy", "test_cli:CheapBuyer", "--from", path, "--report"])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "Replay report" in out
    assert "fills:      2" in out


def test_replay_command_without_report(capsys):
    path = os.path.join(tempfile.mkdtemp(), "rec.jsonl")
    asyncio.run(_record(path))
    exit_code = main(["replay", "--strategy", "test_cli:CheapBuyer", "--from", path])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "Replay complete: 2 fills" in out


def test_replay_command_bad_strategy_path_exits_nonzero(capsys):
    path = os.path.join(tempfile.mkdtemp(), "rec.jsonl")
    asyncio.run(_record(path))
    exit_code = main(["replay", "--strategy", "test_cli:DoesNotExist", "--from", path])
    assert exit_code == 1
    err = capsys.readouterr().err
    assert "error:" in err


# --- record: missing aiohttp dependency --------------------------------------


def test_record_missing_aiohttp_returns_error(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "aiohttp", None)
    out_dir = tempfile.mkdtemp()
    exit_code = main(
        ["record", "--venue", "polymarket", "--assets", "BTC", "--intervals", "5", "--out", out_dir]
    )
    assert exit_code == 1
    err = capsys.readouterr().err
    assert "polymarket" in err
    assert "pip install oraclebook[polymarket]" in err

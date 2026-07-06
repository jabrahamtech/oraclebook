"""Replay — record real order flow, then backtest a strategy on it.

`Recorder` captures live feed events to a JSONL recording; `ReplayFeed` re-publishes them
onto the bus on a virtual clock; `run_replay` wires it all together and returns the
finished App with its Ledger of fills. For a single feed, the same recording + strategy
reproduces the same result; across multiple topics the interleaving is best-effort, and
fills are simulated by the paper executor. The headline oraclebook capability.
"""
from oraclebook.replay.recorder import Recorder
from oraclebook.replay.replay_feed import ReplayFeed
from oraclebook.replay.run_replay import run_replay

__all__ = ["Recorder", "ReplayFeed", "run_replay"]

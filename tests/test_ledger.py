"""Ledger: records fills/resolutions, reads them back, satisfies the sink protocols."""
import os
import tempfile

from oraclebook import Fill, Ledger, MetricsSink, Resolution, ResolutionSink, Side


def test_ledger_records_and_reads_back():
    path = os.path.join(tempfile.mkdtemp(), "events.jsonl")
    led = Ledger(path)
    assert isinstance(led, MetricsSink) and isinstance(led, ResolutionSink)

    led.record_fill(Fill(order_id="o", token_id="UP", market_id="m1", side=Side.BUY, price=0.5, size=10))
    led.record_resolution(
        Resolution(token_id="UP", market_id="m1", strategy="s", direction="UP",
                   settlement_value=1.0, pnl=5.0)
    )
    led.close()

    fills = list(led.fills())
    resolutions = list(led.resolutions())
    assert len(fills) == 1 and fills[0]["payload"]["side"] == "BUY"  # enum -> value
    assert len(resolutions) == 1 and resolutions[0]["payload"]["pnl"] == 5.0

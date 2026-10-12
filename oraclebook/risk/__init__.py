"""Risk layer — composable pre-trade Gates and an emergency CircuitBreaker.

Compose a chain of Gates (cheapest-first) and hand it to the App; `run_gates` evaluates
the chain and the first failure short-circuits. The CircuitBreaker halts trading on a
burst of losses or errors and auto-resets after a cooldown.
"""
from oraclebook.risk.circuit_breaker import CircuitBreaker
from oraclebook.risk.gates import (
    BalanceGate,
    DailyLossGate,
    FeedStalenessGate,
    InventoryGate,
    MaxPositionsGate,
    OrderSizeGate,
    OrderValidityGate,
    PerMarketCapGate,
    TotalExposureGate,
    run_gates,
)

__all__ = [
    "CircuitBreaker",
    "OrderValidityGate",
    "InventoryGate",
    "FeedStalenessGate",
    "DailyLossGate",
    "OrderSizeGate",
    "BalanceGate",
    "PerMarketCapGate",
    "TotalExposureGate",
    "MaxPositionsGate",
    "run_gates",
]

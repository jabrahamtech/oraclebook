"""Polymarket fee model.

Crypto up/down markets charge a *dynamic taker fee* that peaks near the 50/50 midpoint and
decays toward the extremes; makers pay zero. Standard markets are fee-free.

    taker_fee_per_share = rate * (p * (1 - p)) ** exponent

Each market publishes its schedule on Gamma (`feeSchedule`: rate, exponent, takerOnly);
discovery stores it in `Market.meta["fee_schedule"]`. The constants below are the crypto
schedule as published on 2026-11-10 (`crypto_fees_v2`: rate 0.07, exponent 1) — the default
when no per-market schedule is passed in.
"""
from __future__ import annotations

CRYPTO_FEE_RATE = 0.07
CRYPTO_FEE_EXPONENT = 1.0


def taker_fee_per_share(
    price: float,
    *,
    fee_rate: float = CRYPTO_FEE_RATE,
    fee_exponent: float = CRYPTO_FEE_EXPONENT,
) -> float:
    """Per-share taker fee at `price` (0.0175 at p=0.50 with the default curve)."""
    p = max(0.001, min(0.999, price))
    return fee_rate * (p * (1.0 - p)) ** fee_exponent


def fee_amount(
    price: float,
    size: float,
    *,
    is_maker: bool = False,
    fee_rate: float = CRYPTO_FEE_RATE,
    fee_exponent: float = CRYPTO_FEE_EXPONENT,
) -> float:
    """Total fee in USD for a trade. Makers pay zero."""
    if is_maker:
        return 0.0
    return taker_fee_per_share(price, fee_rate=fee_rate, fee_exponent=fee_exponent) * size

"""Polymarket fee model.

Polymarket charges a *dynamic taker fee* in most market categories (Fee Structure V2,
effective 2026-03-30; geopolitics markets are exempt). The fee peaks near the 50/50
midpoint and decays toward the extremes; under a taker-only schedule makers pay zero.
Parameters are per market, published by Gamma as `feesEnabled` plus
`feeSchedule = {rate, exponent, takerOnly, rebateRate}` and read into `Market` by
`markets._to_market`. The per-share fee (see docs.polymarket.com/trading/fees and
py-clob-client-v2 `fees.py`) is:

    taker_fee_per_share = rate * (p * (1 - p)) ** exponent     # crypto: rate 0.072, exp 1

Fees settle in collateral separately from the matched amounts. Maker rebates
(`rebateRate`) are paid out of a pool afterwards and are not modelled here.
"""
from __future__ import annotations

import structlog

from oraclebook.core.models import Market

logger = structlog.get_logger()

CRYPTO_FEE_RATE = 0.072
CRYPTO_FEE_EXPONENT = 1.0

_warned_unverified: set[str] = set()


def taker_fee_per_share(
    price: float,
    *,
    fee_rate: float = CRYPTO_FEE_RATE,
    fee_exponent: float = CRYPTO_FEE_EXPONENT,
) -> float:
    """Per-share taker fee at `price` (peaks ~0.018 at p=0.50 with the default curve)."""
    p = max(0.0, min(1.0, price))
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


def fee_for_market(market: Market, price: float, size: float, is_maker: bool) -> float:
    """Total fee in USD for a trade on `market`, using its Gamma fee schedule.

    Falls back to the crypto up/down default (logged once per market) when the market's
    fee parameters were not read from Gamma."""
    if is_maker and market.fee_taker_only:
        return 0.0
    if market.fee_verified:
        rate = float(market.meta.get("fee_rate", 0.0))
        exponent = float(market.meta.get("fee_exponent", CRYPTO_FEE_EXPONENT))
    else:
        if market.market_id not in _warned_unverified:
            _warned_unverified.add(market.market_id)
            logger.warning(
                "polymarket_fees.unverified_using_crypto_default",
                market=market.market_id, fee_rate=CRYPTO_FEE_RATE,
                fee_exponent=CRYPTO_FEE_EXPONENT,
            )
        rate, exponent = CRYPTO_FEE_RATE, CRYPTO_FEE_EXPONENT
    if rate <= 0.0:
        return 0.0
    return taker_fee_per_share(price, fee_rate=rate, fee_exponent=exponent) * size

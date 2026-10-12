"""PolymarketExecutor — LIVE order placement via the official py-clob-client-v2.

⚠️ EXPERIMENTAL — live trading risks real money. Credentials come ONLY from environment
variables (never embedded); EIP-712 order signing is delegated to py-clob-client-v2.

Order types map onto the SDK as follows:
  LIMIT + GTC / GTD   -> `create_and_post_order(..., order_type=GTC|GTD)`. GTD sends the
                         order's `expiration` plus the CLOB's 60s security threshold, so the
                         order stops resting at `OrderRequest.expiration`.
  FOK / FAK, MARKET   -> `create_and_post_market_order(..., order_type=FOK|FAK)`; the order's
                         `price` is the worst-price limit. BUY amounts are USDC
                         (`size * price`), SELL amounts are shares. MARKET defaults to FOK.

Fills: only what the CLOB matches synchronously (status "matched") is reported, sized from
the response's `makingAmount`/`takingAmount` (BUY: making = USDC, taking = shares; SELL the
reverse). Resting orders ("live"/"delayed", or the unmatched remainder of a GTC/GTD order)
are tracked in `open_orders()` and can be cancelled by venue order id. Resting fills arrive
asynchronously via the user WebSocket, which this reference does not yet wire, so a passive
maker strategy must add that stream. Validate in paper + Replay first; this is the live path,
not a tested-profitable one.

When market metadata is supplied (`markets=` or `update_markets`), prices are rounded to the
market's tick (BUY down, SELL up — never worse than requested) and resting orders below the
market's minimum size are rejected locally; otherwise the SDK validates.

Environment:
  POLYMARKET_PRIVATE_KEY                  (required) signer private key
  POLYMARKET_FUNDER                       (optional) proxy/funder address -> signature_type=2
  POLYMARKET_API_KEY / _SECRET / _PASSPHRASE  (optional) else derived from the key
  POLYMARKET_HOST                         (default https://clob.polymarket.com)
  POLYMARKET_CHAIN_ID                     (default 137)

Builder attribution (disclosed in README — costs users nothing, paid by Polymarket's
rewards pool, never deducted from fills; this framework never sets a builder FEE):
  POLYMARKET_BUILDER_CODE                 (optional) your own builder code overrides the default
  POLYMARKET_DISABLE_BUILDER_ATTRIBUTION  set to "true" to send no builder code at all

Requires: pip install oraclebook[polymarket-live]   (py-clob-client-v2)
"""
from __future__ import annotations

import asyncio
import dataclasses
import os
import time
from collections.abc import Iterable
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, InvalidOperation

import structlog

from oraclebook.adapters.polymarket.fees import fee_for_market
from oraclebook.core.models import (
    Fill,
    Market,
    OrderRequest,
    OrderType,
    Position,
    Side,
    TimeInForce,
)

logger = structlog.get_logger()
DEFAULT_HOST = "https://clob.polymarket.com"

_BYTES32_ZERO = "0x" + "0" * 64

# The CLOB enforces a one-minute security threshold on GTD expirations: an order meant to
# expire at T must be sent with expiration T + 60.
GTD_SECURITY_BUFFER_S = 60

_TICK_SIZES = frozenset({"0.1", "0.01", "0.005", "0.0025", "0.001", "0.0001"})
_RESTING_STATUSES = frozenset({"live", "delayed"})
_ACCEPTED_STATUSES = _RESTING_STATUSES | {"matched"}
_SIZE_EPSILON = 1e-9

# The maintainer's registered Polymarket Builder Code. Attached to orders by default so
# framework-attributed volume earns a share of Polymarket's weekly builder-rewards pool.
# This is NOT a fee: it costs users nothing and nothing is deducted from fills (the
# framework never sets builder fee bps). Fully disclosed in the README; override with
# POLYMARKET_BUILDER_CODE or disable with POLYMARKET_DISABLE_BUILDER_ATTRIBUTION=true.
MAINTAINER_BUILDER_CODE = _BYTES32_ZERO  # zero until a code is registered — a clean no-op


def resolve_builder_code() -> str:
    """Builder code for outgoing orders: user override > maintainer default > disabled."""
    if os.environ.get("POLYMARKET_DISABLE_BUILDER_ATTRIBUTION", "").lower() in ("1", "true", "yes"):
        return _BYTES32_ZERO
    return os.environ.get("POLYMARKET_BUILDER_CODE") or MAINTAINER_BUILDER_CODE


def _amount(value) -> float | None:
    """Parse a CLOB decimal-string amount; "" means zero, a missing key means unknown."""
    if value is None:
        return None
    if value == "":
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _sdk_order_type(o: OrderRequest) -> str:
    if o.time_in_force in (TimeInForce.FOK, TimeInForce.FAK):
        return o.time_in_force.value
    if o.order_type == OrderType.MARKET:
        return TimeInForce.FOK.value
    return o.time_in_force.value


def _rests(order_type: str) -> bool:
    """GTC/GTD orders keep any unmatched remainder on the book; FOK/FAK never rest."""
    return order_type in (TimeInForce.GTC.value, TimeInForce.GTD.value)


class PolymarketExecutor:
    """Live CLOB V2 executor implementing the Executor protocol via py-clob-client-v2."""

    def __init__(
        self,
        *,
        host: str | None = None,
        chain_id: int | None = None,
        private_key: str | None = None,
        funder: str | None = None,
        client=None,  # inject a client (tests); otherwise built from env creds
        markets: Iterable[Market] | None = None,
    ) -> None:
        self._builder_code = resolve_builder_code()
        self._markets: dict[str, Market] = {}
        self._open: dict[str, OrderRequest] = {}
        if markets is not None:
            self.update_markets(markets)
        # Operational transparency: the active attribution is always visible in logs,
        # never only in config (see README "Builder attribution").
        logger.info(
            "polymarket_exec.builder_attribution",
            builder_code=self._builder_code,
            attribution_active=self._builder_code != _BYTES32_ZERO,
            builder_fee_bps=0,
        )
        if client is not None:
            self._client = client
            return
        pk = private_key or os.environ.get("POLYMARKET_PRIVATE_KEY")
        if not pk:
            raise RuntimeError(
                "POLYMARKET_PRIVATE_KEY not set — live execution requires a signer key in the "
                "environment. Use executor='paper' for simulation."
            )
        self._client = self._build_client(
            host or os.environ.get("POLYMARKET_HOST", DEFAULT_HOST),
            int(chain_id or os.environ.get("POLYMARKET_CHAIN_ID", "137")),
            pk,
            funder or os.environ.get("POLYMARKET_FUNDER"),
        )

    @staticmethod
    def _build_client(host: str, chain_id: int, pk: str, funder: str | None):
        from py_clob_client_v2 import ApiCreds, ClobClient

        client = ClobClient(
            host, chain_id=chain_id, key=pk,
            signature_type=(2 if funder else 0), funder=funder,
        )
        api_key = os.environ.get("POLYMARKET_API_KEY")
        if api_key:
            client.set_api_creds(
                ApiCreds(api_key, os.environ["POLYMARKET_API_SECRET"],
                         os.environ["POLYMARKET_API_PASSPHRASE"])
            )
        else:
            client.set_api_creds(client.create_or_derive_api_key())
        return client

    def update_markets(self, markets: Iterable[Market]) -> None:
        """Register market metadata (tick, min size, neg-risk, fees) by outcome token."""
        for m in markets:
            for token in m.token_ids:
                self._markets[token] = m

    def open_orders(self) -> list[OrderRequest]:
        """Orders resting on the book, sized to their unmatched remainder. Each carries its
        venue order id in `meta["venue_order_id"]` for `cancel()`."""
        return list(self._open.values())

    async def submit(self, orders: list[OrderRequest]) -> list[Fill]:
        fills: list[Fill] = []
        for o in orders:
            price = self._validated_price(o)
            if price is None:
                continue
            try:
                resp = await asyncio.to_thread(self._post, o, price)
            except Exception as exc:  # noqa: BLE001
                if getattr(exc, "status_code", None) is not None:
                    logger.warning(
                        "polymarket_exec.order_rejected", order=o.id, token=o.token_id,
                        status_code=exc.status_code, error=str(exc),
                    )
                else:
                    # A transport failure after signing leaves the order's fate unknown: it
                    # may be resting on the book. Reconcile against the venue before retrying.
                    logger.error(
                        "polymarket_exec.submit_state_unknown", order=o.id, token=o.token_id,
                        side=o.side.value, price=price, size=o.size, error=str(exc),
                    )
                continue
            fill = self._handle_response(o, price, resp)
            if fill is not None:
                fills.append(fill)
        return fills

    def _reject(self, o: OrderRequest, reason: str, **details) -> None:
        logger.warning("polymarket_exec.order_rejected", order=o.id, token=o.token_id,
                       reason=reason, **details)

    def _validated_price(self, o: OrderRequest) -> float | None:
        """The price to send (tick-rounded when the market is known), or None if rejected."""
        if not 0.0 < o.price < 1.0:
            self._reject(o, "price_out_of_range", price=o.price)
            return None
        order_type = _sdk_order_type(o)
        if order_type == TimeInForce.GTD.value and o.expiration <= time.time():
            self._reject(o, "gtd_expiration_not_in_future", expiration=o.expiration)
            return None
        market = self._markets.get(o.token_id)
        if market is None:
            return o.price
        try:
            tick = Decimal(market.tick_size)
            rounding = ROUND_FLOOR if o.side == Side.BUY else ROUND_CEILING
            price = float((Decimal(str(o.price)) / tick).to_integral_value(rounding) * tick)
        except (InvalidOperation, ZeroDivisionError):
            return o.price
        if not float(tick) <= price <= 1.0 - float(tick):
            self._reject(o, "price_outside_tick_bounds", price=o.price, tick=market.tick_size)
            return None
        if _rests(order_type) and market.min_order_size and o.size < market.min_order_size:
            self._reject(o, "below_min_order_size", size=o.size, min_size=market.min_order_size)
            return None
        return price

    def _options(self, token_id: str):
        market = self._markets.get(token_id)
        if market is None:
            return None
        from py_clob_client_v2 import PartialCreateOrderOptions

        neg_risk = market.meta.get("neg_risk")
        return PartialCreateOrderOptions(
            tick_size=market.tick_size if market.tick_size in _TICK_SIZES else None,
            neg_risk=neg_risk if isinstance(neg_risk, bool) else None,
        )

    def _post(self, o: OrderRequest, price: float):
        from py_clob_client_v2 import MarketOrderArgs, OrderArgs

        order_type = _sdk_order_type(o)
        options = self._options(o.token_id)
        side = "BUY" if o.side == Side.BUY else "SELL"
        if not _rests(order_type):
            args = MarketOrderArgs(
                token_id=o.token_id,
                amount=o.size * price if o.side == Side.BUY else o.size,
                side=side, price=price, order_type=order_type,
                builder_code=self._builder_code,
            )
            post = self._client.create_and_post_market_order
        else:
            gtd = order_type == TimeInForce.GTD.value
            expiration = o.expiration + GTD_SECURITY_BUFFER_S if gtd else 0
            args = OrderArgs(
                token_id=o.token_id, price=price, size=o.size, side=side,
                expiration=expiration, builder_code=self._builder_code,
            )
            post = self._client.create_and_post_order
        try:
            return post(args, options=options, order_type=order_type)
        except Exception as exc:
            # A builder code Polymarket has disabled makes the CLOB reject orders. The
            # code must never break a user's order flow: fall back to zero attribution
            # for this and all subsequent orders, loudly.
            if "builder" in str(exc).lower() and self._builder_code != _BYTES32_ZERO:
                logger.error(
                    "polymarket_exec.builder_code_rejected_falling_back",
                    order=o.id, builder_code=self._builder_code, error=str(exc),
                )
                self._builder_code = _BYTES32_ZERO
                args.builder_code = _BYTES32_ZERO
                return post(args, options=options, order_type=order_type)
            raise

    def _handle_response(self, o: OrderRequest, price: float, resp) -> Fill | None:
        if not isinstance(resp, dict):
            logger.error("polymarket_exec.unexpected_response", order=o.id, response=repr(resp))
            return None
        oid = str(resp.get("orderID") or "")
        status = str(resp.get("status") or "").lower()
        error = resp.get("errorMsg") or ""
        if resp.get("success") is False or error or status not in _ACCEPTED_STATUSES:
            self._reject(o, "venue_rejected", venue_order_id=oid, status=status, error=error)
            return None
        if status in _RESTING_STATUSES:
            self._track_open(o, oid, price, o.size)
            return None

        shares, avg_price = self._matched(o, price, resp)
        if _rests(_sdk_order_type(o)):
            self._track_open(o, oid, price, o.size - shares)
        if shares <= _SIZE_EPSILON:
            logger.warning("polymarket_exec.matched_without_size", order=o.id, venue_order_id=oid)
            return None
        market = self._markets.get(o.token_id)
        fee = fee_for_market(market, avg_price, shares, is_maker=False) if market else 0.0
        return Fill(
            order_id=o.id, token_id=o.token_id, market_id=o.market_id, side=o.side,
            price=avg_price, size=shares, fee=fee, venue_order_id=oid,
            strategy=o.strategy, asset=o.asset, direction=o.direction, fill_source="live",
        )

    @staticmethod
    def _matched(o: OrderRequest, price: float, resp: dict) -> tuple[float, float]:
        """(shares, average price) actually matched. BUY: making = USDC, taking = shares;
        SELL: making = shares, taking = USDC. Fees settle separately and are excluded."""
        making = _amount(resp.get("makingAmount"))
        taking = _amount(resp.get("takingAmount"))
        shares, usdc = (taking, making) if o.side == Side.BUY else (making, taking)
        if shares is None and usdc is None:
            logger.warning("polymarket_exec.matched_without_amounts", order=o.id)
            return o.size, price
        if shares is None:
            shares = usdc / price
        if usdc is None or shares <= 0 or usdc <= 0:
            return shares, price
        return shares, usdc / shares

    def _track_open(self, o: OrderRequest, oid: str, price: float, remaining: float) -> None:
        if remaining <= _SIZE_EPSILON:
            return
        if not oid:
            logger.error("polymarket_exec.resting_without_order_id", order=o.id)
            return
        self._open[oid] = dataclasses.replace(
            o, price=price, size=remaining, meta={**o.meta, "venue_order_id": oid}
        )

    async def cancel(self, venue_order_id: str) -> bool:
        from py_clob_client_v2 import OrderPayload

        try:
            resp = await asyncio.to_thread(
                self._client.cancel_order, OrderPayload(orderID=venue_order_id)
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("polymarket_exec.cancel_failed", id=venue_order_id, error=str(exc))
            return False
        not_canceled = resp.get("not_canceled") if isinstance(resp, dict) else None
        if not_canceled and venue_order_id in not_canceled:
            logger.warning("polymarket_exec.cancel_refused", id=venue_order_id,
                           reason=not_canceled[venue_order_id])
            return False
        self._open.pop(venue_order_id, None)
        return True

    async def cancel_all(self) -> int:
        try:
            resp = await asyncio.to_thread(self._client.cancel_all)
        except Exception as exc:  # noqa: BLE001
            logger.warning("polymarket_exec.cancel_all_failed", error=str(exc),
                           open_orders=list(self._open))
            return 0
        canceled = resp.get("canceled") if isinstance(resp, dict) else None
        count = len(canceled) if isinstance(canceled, list) else len(self._open)
        self._open.clear()
        return count

    async def get_balance(self) -> float:
        from py_clob_client_v2 import AssetType, BalanceAllowanceParams

        try:
            resp = await asyncio.to_thread(
                self._client.get_balance_allowance,
                BalanceAllowanceParams(asset_type=AssetType.COLLATERAL),
            )
            return float(resp.get("balance", 0)) / 1_000_000  # pUSD collateral has 6 decimals
        except Exception as exc:  # noqa: BLE001
            logger.warning("polymarket_exec.balance_failed", error=str(exc))
            return 0.0

    def get_position(self, token_id: str) -> Position | None:
        return None  # the Book tracks positions; live on-chain positions are not wired here

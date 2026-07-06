"""PolymarketExecutor — LIVE order placement via the official py-clob-client-v2.

⚠️ EXPERIMENTAL — live trading risks real money. Credentials come ONLY from environment
variables (never embedded); EIP-712 order signing is delegated to py-clob-client-v2.

Caveat: resting **maker** orders fill asynchronously via the user WebSocket, which this
reference does not yet wire. `submit()` posts orders and reports only the fills the CLOB
confirms synchronously (status "matched" — crossing/marketable orders), using the
exchange-reported size/price when present. A passive maker strategy must add a user-WS
fill stream to capture resting fills. Validate in paper + Replay first; this is the live
path, not a tested-profitable one.

Order types: LIMIT + GTC/GTD map to a standard order (GTD carries the order's
`expiration`). MARKET orders are not yet wired through this reference client — only
LIMIT orders are supported; `CompleteSetMaker` only emits LIMIT+GTC.

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
import os

import structlog

from oraclebook.core.models import Fill, OrderRequest, Position, Side, TimeInForce

logger = structlog.get_logger()
DEFAULT_HOST = "https://clob.polymarket.com"

_BYTES32_ZERO = "0x" + "0" * 64

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
    ) -> None:
        self._builder_code = resolve_builder_code()
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

    async def submit(self, orders: list[OrderRequest]) -> list[Fill]:
        fills: list[Fill] = []
        for o in orders:
            try:
                resp = await asyncio.to_thread(self._post, o)
            except Exception as exc:
                logger.warning("polymarket_exec.submit_failed", order=o.id, error=str(exc))
                continue
            fill = self._fill_from_response(o, resp)
            if fill is not None:
                fills.append(fill)
        return fills

    def _post(self, o: OrderRequest):
        from py_clob_client_v2 import OrderArgs

        # GTD (good-til-date) is the maker primitive for short-dated markets: pass the
        # order's expiration through so it isn't left resting past the market's close.
        expiration = int(o.expiration) if o.time_in_force == TimeInForce.GTD and o.expiration else 0
        args = OrderArgs(
            token_id=o.token_id, price=o.price, size=o.size,
            side="BUY" if o.side == Side.BUY else "SELL",
            expiration=expiration,
            builder_code=self._builder_code,
        )
        try:
            return self._client.create_and_post_order(args)
        except Exception as exc:
            # A builder code Polymarket has disabled makes the CLOB reject orders. The
            # code must never break a user's order flow: fall back to zero attribution
            # for this and all subsequent orders, loudly.
            if "builder" in str(exc).lower() and self._builder_code != _BYTES32_ZERO:
                logger.error(
                    "polymarket_exec.builder_code_rejected_falling_back",
                    builder_code=self._builder_code, error=str(exc),
                )
                self._builder_code = _BYTES32_ZERO
                args.builder_code = _BYTES32_ZERO
                return self._client.create_and_post_order(args)
            raise

    @staticmethod
    def _fill_from_response(o: OrderRequest, resp) -> Fill | None:
        # Only "matched" is a synchronous fill; "live"/"delayed" rest and fill via the user WS.
        if not isinstance(resp, dict) or resp.get("status") != "matched":
            return None
        oid = resp.get("orderID") or resp.get("orderId") or ""
        # Prefer exchange-reported size/price over the requested values — the CLOB can
        # partial-fill or price-improve a marketable order, and the Ledger should record
        # what actually happened, not what was requested.
        size = float(resp.get("size_matched") or resp.get("making_amount") or o.size)
        price = float(resp.get("price") or o.price)
        return Fill(
            order_id=o.id, token_id=o.token_id, market_id=o.market_id, side=o.side,
            price=price, size=size, venue_order_id=str(oid),
            strategy=o.strategy, asset=o.asset, direction=o.direction, fill_source="live",
        )

    async def cancel(self, venue_order_id: str) -> bool:
        from py_clob_client_v2 import OrderPayload

        try:
            await asyncio.to_thread(self._client.cancel_order, OrderPayload(orderID=venue_order_id))
            return True
        except Exception as exc:
            logger.warning("polymarket_exec.cancel_failed", id=venue_order_id, error=str(exc))
            return False

    async def cancel_all(self) -> int:
        try:
            await asyncio.to_thread(self._client.cancel_all)
            return 1
        except Exception as exc:
            logger.warning("polymarket_exec.cancel_all_failed", error=str(exc))
            return 0

    async def get_balance(self) -> float:
        from py_clob_client_v2 import AssetType, BalanceAllowanceParams

        try:
            resp = await asyncio.to_thread(
                self._client.get_balance_allowance,
                BalanceAllowanceParams(asset_type=AssetType.COLLATERAL),
            )
            return float(resp.get("balance", 0)) / 1_000_000  # pUSD collateral has 6 decimals
        except Exception as exc:
            logger.warning("polymarket_exec.balance_failed", error=str(exc))
            return 0.0

    def get_position(self, token_id: str) -> Position | None:
        return None  # the Book tracks positions; live on-chain positions are not wired here

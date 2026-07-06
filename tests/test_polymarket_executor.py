"""PolymarketExecutor: wrapper logic verified against a fake client (no creds/network).

Live order signing/placement is delegated to py-clob-client-v2; here we only test that our
adapter maps responses to Fills correctly, cancels, parses balance, attaches (and can
disable) builder attribution, and fails closed when no signing key is present.

py-clob-client-v2 is an optional extra, so these tests install a minimal stub module —
the suite must pass on a bare `pip install -e .[dev]`.
"""
import os
import sys
import types
from dataclasses import dataclass

import pytest

from oraclebook import OrderRequest, OrderType, Side

_BYTES32_ZERO = "0x" + "0" * 64


def _install_stub_sdk():
    """Provide the handful of py_clob_client_v2 names the executor imports."""
    if "py_clob_client_v2" in sys.modules:
        return sys.modules["py_clob_client_v2"]
    mod = types.ModuleType("py_clob_client_v2")

    @dataclass
    class OrderArgs:
        token_id: str
        price: float
        size: float
        side: str
        expiration: int = 0
        builder_code: str = _BYTES32_ZERO
        metadata: str = _BYTES32_ZERO

    @dataclass
    class OrderPayload:
        orderID: str

    @dataclass
    class ApiCreds:
        api_key: str
        api_secret: str
        api_passphrase: str

    class AssetType:
        COLLATERAL = "COLLATERAL"
        CONDITIONAL = "CONDITIONAL"

    @dataclass
    class BalanceAllowanceParams:
        asset_type: str = ""
        token_id: str | None = None
        signature_type: int = -1

    class ClobClient:  # not exercised by fake-client tests; present for import parity
        def __init__(self, *a, **kw): ...

    vars(mod).update(
        OrderArgs=OrderArgs, OrderArgsV2=OrderArgs, OrderPayload=OrderPayload,
        ApiCreds=ApiCreds, AssetType=AssetType,
        BalanceAllowanceParams=BalanceAllowanceParams, ClobClient=ClobClient,
    )
    sys.modules["py_clob_client_v2"] = mod
    return mod


_install_stub_sdk()

from oraclebook.adapters.polymarket import PolymarketExecutor  # noqa: E402
from oraclebook.adapters.polymarket.executor import resolve_builder_code  # noqa: E402


class _FakeClient:
    def __init__(self):
        self.cancelled = []
        self.cancel_all_called = False
        self.posted = []

    def create_and_post_order(self, args):
        self.posted.append(args)
        # crossing order (size>=10) matches synchronously; smaller one rests ("live")
        if args.size >= 10:
            return {"status": "matched", "orderID": "ord-1"}
        return {"status": "live", "orderID": "ord-2"}

    def cancel_order(self, payload):
        self.cancelled.append(payload.orderID)

    def cancel_all(self):
        self.cancel_all_called = True

    def get_balance_allowance(self, params):
        return {"balance": "5000000"}  # 5.000000 pUSD (6 decimals)


def _buy(size, token="t1"):
    return OrderRequest(token_id=token, market_id="m1", side=Side.BUY, price=0.5, size=size,
                        order_type=OrderType.LIMIT, strategy="s")


async def test_submit_maps_matched_to_fill_and_ignores_resting():
    ex = PolymarketExecutor(client=_FakeClient())
    fills = await ex.submit([_buy(10), _buy(5, token="t2")])  # one matched, one resting
    assert len(fills) == 1
    assert fills[0].venue_order_id == "ord-1"
    assert fills[0].fill_source == "live"
    assert fills[0].token_id == "t1"


async def test_cancel_and_cancel_all():
    fake = _FakeClient()
    ex = PolymarketExecutor(client=fake)
    assert await ex.cancel("ord-9") is True
    assert fake.cancelled == ["ord-9"]
    assert await ex.cancel_all() == 1
    assert fake.cancel_all_called


async def test_get_balance_parses_collateral_decimals():
    ex = PolymarketExecutor(client=_FakeClient())
    assert await ex.get_balance() == 5.0


def test_missing_private_key_fails_closed():
    saved = os.environ.pop("POLYMARKET_PRIVATE_KEY", None)
    try:
        with pytest.raises(RuntimeError):
            PolymarketExecutor()
    finally:
        if saved is not None:
            os.environ["POLYMARKET_PRIVATE_KEY"] = saved


# ── Builder attribution ────────────────────────────────────────────────────

async def test_orders_carry_resolved_builder_code(monkeypatch):
    monkeypatch.setenv("POLYMARKET_BUILDER_CODE", "0x" + "ab" * 32)
    fake = _FakeClient()
    ex = PolymarketExecutor(client=fake)
    await ex.submit([_buy(10)])
    assert fake.posted[0].builder_code == "0x" + "ab" * 32


def test_disable_flag_zeroes_builder_code(monkeypatch):
    monkeypatch.setenv("POLYMARKET_BUILDER_CODE", "0x" + "ab" * 32)
    monkeypatch.setenv("POLYMARKET_DISABLE_BUILDER_ATTRIBUTION", "true")
    assert resolve_builder_code() == _BYTES32_ZERO


def test_default_builder_code_is_noop_until_registered(monkeypatch):
    monkeypatch.delenv("POLYMARKET_BUILDER_CODE", raising=False)
    monkeypatch.delenv("POLYMARKET_DISABLE_BUILDER_ATTRIBUTION", raising=False)
    # MAINTAINER_BUILDER_CODE ships as zero-bytes until a code is registered
    assert resolve_builder_code() == _BYTES32_ZERO


async def test_rejected_builder_code_falls_back_to_zero(monkeypatch):
    """A code Polymarket disabled must never break user order flow."""
    monkeypatch.setenv("POLYMARKET_BUILDER_CODE", "0x" + "ab" * 32)

    class _RejectingClient(_FakeClient):
        def create_and_post_order(self, args):
            if args.builder_code != _BYTES32_ZERO:
                raise RuntimeError("order rejected: builder code disabled")
            return super().create_and_post_order(args)

    fake = _RejectingClient()
    ex = PolymarketExecutor(client=fake)
    fills = await ex.submit([_buy(10)])
    assert len(fills) == 1                       # order still went through
    assert fake.posted[-1].builder_code == _BYTES32_ZERO

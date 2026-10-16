"""PolymarketExecutor: wrapper logic verified against a fake client (no creds/network).

Live order signing/placement is delegated to py-clob-client-v2; here we only test that our
adapter routes order types to the right SDK call, maps CLOB responses to Fills (and resting
orders to `open_orders()`), cancels, parses balance, attaches (and can disable) builder
attribution, and fails closed when no signing key is present.

py-clob-client-v2 is an optional extra, so these tests install a minimal stub module —
the suite must pass on a bare `pip install -e .[dev]`. The executor imports the SDK lazily,
so the stub only has to be in place before orders are posted.
"""
import os
import sys
import time
import types
from dataclasses import dataclass

import pytest

from oraclebook import Market, OrderRequest, OrderType, Side, TimeInForce
from oraclebook.adapters.polymarket import PolymarketExecutor
from oraclebook.adapters.polymarket.executor import GTD_SECURITY_BUFFER_S, resolve_builder_code

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
    class MarketOrderArgs:
        token_id: str
        amount: float
        side: str
        price: float = 0
        order_type: str = "FOK"
        builder_code: str = _BYTES32_ZERO

    @dataclass
    class PartialCreateOrderOptions:
        tick_size: str | None = None
        neg_risk: bool | None = None

    class SdkOrderType:
        GTC, FOK, GTD, FAK = "GTC", "FOK", "GTD", "FAK"

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
        OrderArgs=OrderArgs, OrderArgsV2=OrderArgs, MarketOrderArgs=MarketOrderArgs,
        PartialCreateOrderOptions=PartialCreateOrderOptions, OrderType=SdkOrderType,
        OrderPayload=OrderPayload, ApiCreds=ApiCreds, AssetType=AssetType,
        BalanceAllowanceParams=BalanceAllowanceParams, ClobClient=ClobClient,
    )
    sys.modules["py_clob_client_v2"] = mod
    return mod


_install_stub_sdk()


def _matched(making: str, taking: str, oid: str = "ord-1") -> dict:
    return {"success": True, "errorMsg": "", "orderID": oid, "status": "matched",
            "makingAmount": making, "takingAmount": taking}


def _live(oid: str = "ord-2") -> dict:
    return {"success": True, "errorMsg": "", "orderID": oid, "status": "live",
            "makingAmount": "", "takingAmount": ""}


class _FakeClient:
    def __init__(self, resp=None):
        self.resp = resp
        self.cancelled = []
        self.cancel_all_called = False
        self.posted = []
        self.market_posted = []

    def create_and_post_order(self, args, options=None, order_type="GTC"):
        self.posted.append((args, options, order_type))
        if self.resp is not None:
            return self.resp
        # crossing order (size>=10) matches synchronously; smaller one rests ("live")
        if args.size >= 10:
            return _matched(f"{args.size * args.price:.2f}", f"{args.size}")
        return _live()

    def create_and_post_market_order(self, args, options=None, order_type="FOK"):
        self.market_posted.append((args, options, order_type))
        return self.resp

    def cancel_order(self, payload):
        self.cancelled.append(payload.orderID)
        return {"canceled": [payload.orderID], "not_canceled": {}}

    def cancel_all(self):
        self.cancel_all_called = True
        return {"canceled": ["a", "b"], "not_canceled": {}}

    def get_balance_allowance(self, params):
        return {"balance": "5000000"}  # 5.000000 pUSD (6 decimals)


def _order(size=10.0, price=0.5, token="t1", side=Side.BUY, otype=OrderType.LIMIT,
           tif=TimeInForce.GTC, expiration=0):
    return OrderRequest(token_id=token, market_id="m1", side=side, price=price, size=size,
                        order_type=otype, strategy="s", time_in_force=tif, expiration=expiration)


def _market(tick="0.01", min_size=5.0, **meta):
    return Market(venue="polymarket", market_id="m1", condition_id="0xc",
                  token_ids=("t1", "t2"), outcomes=("Up", "Down"), tick_size=tick,
                  min_order_size=min_size, fee_verified=True,
                  meta={"fee_rate": 0.072, "fee_exponent": 1.0, **meta})


async def test_submit_maps_matched_to_fill_and_tracks_resting():
    ex = PolymarketExecutor(client=_FakeClient())
    fills = await ex.submit([_order(10), _order(5, token="t2")])  # one matched, one resting
    assert len(fills) == 1
    assert fills[0].venue_order_id == "ord-1"
    assert fills[0].fill_source == "live"
    assert fills[0].token_id == "t1"
    (resting,) = ex.open_orders()
    assert resting.token_id == "t2" and resting.size == 5
    assert resting.meta["venue_order_id"] == "ord-2"


# ── Order types ────────────────────────────────────────────────────────────

async def test_limit_gtc_posts_limit_order_with_gtc():
    fake = _FakeClient()
    await PolymarketExecutor(client=fake).submit([_order(10)])
    (args, _, order_type) = fake.posted[0]
    assert order_type == "GTC" and args.expiration == 0


async def test_gtd_sends_expiration_plus_security_buffer():
    fake = _FakeClient(_live())
    expiry = int(time.time()) + 300
    await PolymarketExecutor(client=fake).submit([_order(tif=TimeInForce.GTD, expiration=expiry)])
    (args, _, order_type) = fake.posted[0]
    assert order_type == "GTD"
    assert args.expiration == expiry + GTD_SECURITY_BUFFER_S == expiry + 60


async def test_gtd_without_future_expiration_is_rejected_locally():
    fake = _FakeClient(_live())
    ex = PolymarketExecutor(client=fake)
    assert await ex.submit([_order(tif=TimeInForce.GTD, expiration=0)]) == []
    assert fake.posted == []


@pytest.mark.parametrize(
    ("otype", "tif", "expected"),
    [
        (OrderType.LIMIT, TimeInForce.FOK, "FOK"),
        (OrderType.LIMIT, TimeInForce.FAK, "FAK"),
        (OrderType.MARKET, TimeInForce.FAK, "FAK"),
        (OrderType.MARKET, TimeInForce.GTC, "FOK"),
    ],
)
async def test_immediate_orders_use_market_order_path(otype, tif, expected):
    fake = _FakeClient(_matched("4.3", "10"))
    fills = await PolymarketExecutor(client=fake).submit(
        [_order(size=10, price=0.43, otype=otype, tif=tif)]
    )
    assert fake.posted == []
    (args, _, order_type) = fake.market_posted[0]
    assert order_type == expected and args.order_type == expected
    assert args.amount == pytest.approx(4.3)  # BUY amount is USDC: 10 shares * 0.43
    assert args.price == 0.43                 # worst-price limit
    assert fills[0].size == 10.0


async def test_market_sell_amount_is_shares():
    fake = _FakeClient(_matched("10", "4.3"))
    await PolymarketExecutor(client=fake).submit(
        [_order(size=10, price=0.43, side=Side.SELL, tif=TimeInForce.FOK)]
    )
    (args, _, _) = fake.market_posted[0]
    assert args.amount == 10 and args.side == "SELL"


async def test_fok_kill_never_rests():
    fake = _FakeClient({"success": False, "errorMsg": "order couldn't be fully filled. FOK "
                        "orders are fully filled or killed.", "orderID": "", "status": "",
                        "makingAmount": "0", "takingAmount": "0"})
    ex = PolymarketExecutor(client=fake)
    assert await ex.submit([_order(tif=TimeInForce.FOK)]) == []
    assert ex.open_orders() == []


# ── Response parsing ───────────────────────────────────────────────────────

async def test_partial_buy_match_uses_camelcase_amounts_and_rests_remainder():
    # Bought 4 of 10 shares, paying 1.72 USDC (avg 0.43); 6 shares keep resting.
    ex = PolymarketExecutor(client=_FakeClient(_matched("1.72", "4", oid="0xabc")))
    (fill,) = await ex.submit([_order(size=10, price=0.43)])
    assert fill.size == 4.0
    assert fill.price == pytest.approx(0.43)
    (rest,) = ex.open_orders()
    assert rest.size == pytest.approx(6.0) and rest.meta["venue_order_id"] == "0xabc"


async def test_sell_amounts_are_reversed():
    # SELL: making = shares given, taking = USDC received (price-improved to 0.45).
    ex = PolymarketExecutor(client=_FakeClient(_matched("10", "4.5")))
    (fill,) = await ex.submit([_order(size=10, price=0.43, side=Side.SELL)])
    assert fill.size == 10.0
    assert fill.price == pytest.approx(0.45)
    assert ex.open_orders() == []


async def test_zero_amounts_never_produce_a_fill():
    ex = PolymarketExecutor(client=_FakeClient(_matched("0", "0")))
    assert await ex.submit([_order(tif=TimeInForce.FAK)]) == []


async def test_success_false_is_a_rejection_not_a_resting_order():
    resp = {"success": False, "errorMsg": "not enough balance / allowance", "orderID": "",
            "status": "", "makingAmount": "0", "takingAmount": "0"}
    ex = PolymarketExecutor(client=_FakeClient(resp))
    assert await ex.submit([_order()]) == []
    assert ex.open_orders() == []


async def test_delayed_order_is_tracked_as_open():
    resp = dict(_live("0xdel"), status="delayed")
    ex = PolymarketExecutor(client=_FakeClient(resp))
    assert await ex.submit([_order()]) == []
    assert [o.meta["venue_order_id"] for o in ex.open_orders()] == ["0xdel"]


async def test_transport_error_is_logged_not_raised():
    class _Boom(_FakeClient):
        def create_and_post_order(self, args, options=None, order_type="GTC"):
            raise TimeoutError("read timeout")

    assert await PolymarketExecutor(client=_Boom()).submit([_order()]) == []


# ── Market metadata: tick rounding, min size, options ──────────────────────

async def test_price_rounded_to_tick_never_worse_than_requested():
    fake = _FakeClient(_live())
    ex = PolymarketExecutor(client=fake, markets=[_market(tick="0.01", neg_risk=True)])
    await ex.submit([_order(price=0.437), _order(price=0.433, side=Side.SELL)])
    (buy, opts, _), (sell, _, _) = fake.posted
    assert buy.price == pytest.approx(0.43)
    assert sell.price == pytest.approx(0.44)
    assert opts.tick_size == "0.01" and opts.neg_risk is True


async def test_fine_tick_market_accepts_extreme_prices():
    fake = _FakeClient(_live())
    ex = PolymarketExecutor(client=fake, markets=[_market(tick="0.0001")])
    await ex.submit([_order(price=0.0003)])
    assert fake.posted[0][0].price == pytest.approx(0.0003)


async def test_below_min_size_rejected_when_market_known():
    fake = _FakeClient(_live())
    ex = PolymarketExecutor(client=fake, markets=[_market(min_size=5.0)])
    assert await ex.submit([_order(size=2)]) == []
    assert fake.posted == []


async def test_unknown_market_is_left_to_the_sdk():
    fake = _FakeClient(_live())
    await PolymarketExecutor(client=fake).submit([_order(size=2, price=0.437)])
    (args, opts, _) = fake.posted[0]
    assert args.price == 0.437 and opts is None


async def test_taker_fill_carries_market_fee():
    ex = PolymarketExecutor(client=_FakeClient(_matched("5", "10")), markets=[_market()])
    (fill,) = await ex.submit([_order(size=10, price=0.5)])
    assert fill.fee == pytest.approx(0.072 * 0.25 * 10)


# ── Cancel / balance / credentials ─────────────────────────────────────────

async def test_cancel_removes_open_order():
    fake = _FakeClient(_live("0xrest"))
    ex = PolymarketExecutor(client=fake)
    await ex.submit([_order()])
    assert len(ex.open_orders()) == 1
    assert await ex.cancel("0xrest") is True
    assert fake.cancelled == ["0xrest"]
    assert ex.open_orders() == []


async def test_cancel_refused_keeps_order():
    class _Refusing(_FakeClient):
        def cancel_order(self, payload):
            return {"canceled": [], "not_canceled": {payload.orderID: "order not found"}}

    ex = PolymarketExecutor(client=_Refusing(_live("0xrest")))
    await ex.submit([_order()])
    assert await ex.cancel("0xrest") is False
    assert len(ex.open_orders()) == 1


async def test_cancel_all_clears_open_orders_and_counts_cancelled():
    fake = _FakeClient(_live())
    ex = PolymarketExecutor(client=fake)
    await ex.submit([_order()])
    assert await ex.cancel_all() == 2
    assert fake.cancel_all_called
    assert ex.open_orders() == []


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
    await ex.submit([_order(10)])
    assert fake.posted[0][0].builder_code == "0x" + "ab" * 32


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
        def create_and_post_order(self, args, options=None, order_type="GTC"):
            if args.builder_code != _BYTES32_ZERO:
                raise RuntimeError("order rejected: builder code disabled")
            return super().create_and_post_order(args, options, order_type)

    fake = _RejectingClient()
    ex = PolymarketExecutor(client=fake)
    fills = await ex.submit([_order(10)])
    assert len(fills) == 1                       # order still went through
    assert fake.posted[-1][0].builder_code == _BYTES32_ZERO

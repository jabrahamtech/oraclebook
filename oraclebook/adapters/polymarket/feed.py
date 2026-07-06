"""PolymarketFeed — the Polymarket CLOB market WebSocket book feed.

Discovers the active up/down markets (Gamma), subscribes to their outcome tokens on the
CLOB market socket, parses L2 book snapshots, and publishes `BookUpdate` events. Read-only
— no credentials required, so it powers paper trading, recording, and Replay against real
Polymarket data. (The production bot runs redundant connections with freeze detection; this
reference uses a single auto-reconnecting connection — adequate for most use, extensible.)
"""
from __future__ import annotations

import asyncio
import json
import time

import structlog

from oraclebook.adapters.polymarket.markets import discover
from oraclebook.core.bus import Bus
from oraclebook.core.events import EVT_BOOK_UPDATE, BookUpdate

logger = structlog.get_logger()

WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
_CHANNEL_INIT = {"assets_ids": [], "type": "market", "level": 2, "initial_dump": True}


def _valid(level: dict) -> bool:
    try:
        p = float(level["price"])
    except (KeyError, TypeError, ValueError):
        return False
    return 0.001 <= p <= 0.999


class PolymarketFeed:
    """Streams Polymarket order books onto the bus. Implements the `Feed` protocol."""

    def __init__(self, bus: Bus, assets: list[str], intervals: list[int], *, ping_interval: float = 10.0) -> None:
        self._bus = bus
        self._assets = assets
        self._intervals = intervals
        self._ping_interval = ping_interval
        self._token_market: dict[str, str] = {}
        self._session = None
        self._ws = None
        self._task: asyncio.Task | None = None
        self._ping_task: asyncio.Task | None = None
        self._running = False

    async def start(self) -> None:
        self._running = True
        # Discover markets off the event loop (markets.discover uses blocking urllib).
        markets = await asyncio.to_thread(discover, self._assets, self._intervals)
        self._token_market = {t: m.market_id for m in markets for t in m.token_ids}
        logger.info("polymarket_feed.discovered", markets=len(markets), tokens=len(self._token_market))
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._running = False
        for t in (self._task, self._ping_task):
            if t is not None:
                t.cancel()
        if self._ws is not None and not self._ws.closed:
            await self._ws.close()
        if self._session is not None and not self._session.closed:
            await self._session.close()

    async def _run(self) -> None:
        import aiohttp  # optional adapter dependency

        self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None, connect=10))
        tokens = list(self._token_market)
        while self._running:
            try:
                self._ws = await self._session.ws_connect(WS_URL)
                await self._ws.send_json(_CHANNEL_INIT)
                if tokens:
                    await self._ws.send_json({"operation": "subscribe", "assets_ids": tokens, "level": 2})
                self._ping_task = asyncio.create_task(self._ping())
                async for msg in self._ws:
                    if not self._running:
                        break
                    if msg.type == aiohttp.WSMsgType.TEXT and msg.data != "PONG":
                        await self._on_message(msg.data)
                    elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                        break
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("polymarket_feed.error", error=str(exc))
                await asyncio.sleep(2.0)

    async def _ping(self) -> None:
        while self._running and self._ws is not None and not self._ws.closed:
            try:
                await self._ws.send_str("PING")
                await asyncio.sleep(self._ping_interval)
            except Exception:
                break

    async def _on_message(self, raw: str) -> None:
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return
        for item in (data if isinstance(data, list) else [data]):
            if not isinstance(item, dict):
                continue
            if "bids" in item or "asks" in item:
                await self._publish_book(item)
            elif "price_changes" in item or item.get("event_type") == "price_change":
                await self._publish_price_changes(item)  # incremental top-of-book updates

    @staticmethod
    def _age_ms(d: dict) -> float:
        ts = d.get("timestamp")
        if ts is None:
            return 0.0
        try:  # Polymarket sends millisecond timestamps
            return max(0.0, time.time() * 1000.0 - float(ts))
        except (TypeError, ValueError):
            return 0.0

    async def _publish_book(self, d: dict) -> None:
        token = d.get("asset_id", "")
        bids = sorted(
            ((float(b["price"]), float(b["size"])) for b in d.get("bids", []) if _valid(b)),
            key=lambda x: -x[0],
        )
        asks = sorted(
            ((float(a["price"]), float(a["size"])) for a in d.get("asks", []) if _valid(a)),
            key=lambda x: x[0],
        )
        await self._bus.publish(
            EVT_BOOK_UPDATE,
            BookUpdate(
                token_id=token,
                market_id=self._token_market.get(token, ""),
                best_bid=bids[0][0] if bids else 0.0,
                best_ask=asks[0][0] if asks else 0.0,
                bids=tuple(bids),
                asks=tuple(asks),
                age_ms=self._age_ms(d),
            ),
        )

    async def _publish_price_changes(self, d: dict) -> None:
        """Polymarket emits `price_change` events between full book snapshots; without
        handling them the feed goes stale after the initial dump."""
        age = self._age_ms(d)
        for change in d.get("price_changes", [d]):
            token = change.get("asset_id", "")
            if not token:
                continue
            try:
                best_bid = float(change.get("best_bid", 0) or 0)
                best_ask = float(change.get("best_ask", 0) or 0)
            except (TypeError, ValueError):
                continue
            if best_bid <= 0 and best_ask <= 0:
                continue
            await self._bus.publish(
                EVT_BOOK_UPDATE,
                BookUpdate(
                    token_id=token,
                    market_id=self._token_market.get(token, ""),
                    best_bid=best_bid,
                    best_ask=best_ask,
                    age_ms=age,
                ),
            )

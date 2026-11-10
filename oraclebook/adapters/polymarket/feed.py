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
        float(level["size"])
    except (KeyError, TypeError, ValueError):
        return False
    return 0.001 <= p <= 0.999


class PolymarketFeed:
    """Streams Polymarket order books onto the bus. Implements the `Feed` protocol."""

    def __init__(
        self,
        bus: Bus,
        assets: list[str],
        intervals: list[int],
        *,
        ping_interval: float = 10.0,
        rediscover_interval: float = 30.0,
    ) -> None:
        self._bus = bus
        self._assets = assets
        self._intervals = intervals
        self._ping_interval = ping_interval
        self._rediscover_interval = rediscover_interval
        self._token_market: dict[str, str] = {}
        # Local L2 book per token, rebuilt from snapshots + price_change deltas.
        self._levels: dict[str, dict[str, dict[float, float]]] = {}
        self._session = None
        self._ws = None
        self._task: asyncio.Task | None = None
        self._ping_task: asyncio.Task | None = None
        self._discover_task: asyncio.Task | None = None
        self._running = False

    async def start(self) -> None:
        self._running = True
        await self._discover()
        self._task = asyncio.create_task(self._run())
        self._discover_task = asyncio.create_task(self._rediscover())

    async def _discover(self) -> None:
        """Find the live up/down markets and subscribe to any tokens not yet subscribed.
        These markets roll every interval, so this runs on a timer, not just at start."""
        # Off the event loop: markets.discover uses blocking urllib.
        markets = await asyncio.to_thread(discover, self._assets, self._intervals)
        new = {t: m.market_id for m in markets for t in m.token_ids if t not in self._token_market}
        if not new:
            return
        self._token_market.update(new)
        logger.info("polymarket_feed.discovered", markets=len(markets), new_tokens=len(new))
        if self._ws is not None and not self._ws.closed:
            await self._ws.send_json(
                {"operation": "subscribe", "assets_ids": list(new), "level": 2}
            )

    async def _rediscover(self) -> None:
        while self._running:
            await asyncio.sleep(self._rediscover_interval)
            try:
                await self._discover()
            except Exception as exc:
                logger.warning("polymarket_feed.discover_failed", error=str(exc))

    async def stop(self) -> None:
        self._running = False
        for t in (self._task, self._ping_task, self._discover_task):
            if t is not None:
                t.cancel()
        if self._ws is not None and not self._ws.closed:
            await self._ws.close()
        if self._session is not None and not self._session.closed:
            await self._session.close()

    async def _run(self) -> None:
        import aiohttp  # optional adapter dependency

        # trust_env: honour HTTPS_PROXY / NO_PROXY like urllib does for market discovery.
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=None, connect=10), trust_env=True
        )
        while self._running:
            try:
                self._ws = await self._session.ws_connect(WS_URL)
                await self._ws.send_json(_CHANNEL_INIT)
                tokens = list(self._token_market)
                if tokens:
                    await self._ws.send_json({"operation": "subscribe", "assets_ids": tokens, "level": 2})
                if self._ping_task is not None:
                    self._ping_task.cancel()  # one pinger per live socket
                self._ping_task = asyncio.create_task(self._ping(self._ws))
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

    async def _ping(self, ws) -> None:
        while self._running and not ws.closed:
            try:
                await ws.send_str("PING")
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
            try:
                if "bids" in item or "asks" in item:
                    await self._publish_book(item)
                elif "price_changes" in item or item.get("event_type") == "price_change":
                    await self._publish_price_changes(item)
            except Exception:
                # One malformed item must not drop the connection.
                logger.exception(
                    "polymarket_feed.bad_message", asset_id=item.get("asset_id") or item.get("token_id")
                )

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
        token = d.get("asset_id") or d.get("token_id", "")
        self._levels[token] = {
            "bids": {float(b["price"]): float(b["size"]) for b in d.get("bids", []) if _valid(b)},
            "asks": {float(a["price"]): float(a["size"]) for a in d.get("asks", []) if _valid(a)},
        }
        await self._publish_levels(token, self._age_ms(d))

    async def _publish_levels(self, token: str, age_ms: float) -> None:
        book = self._levels[token]
        bids = sorted(((p, s) for p, s in book["bids"].items() if s > 0), key=lambda x: -x[0])
        asks = sorted((p, s) for p, s in book["asks"].items() if s > 0)
        await self._bus.publish(
            EVT_BOOK_UPDATE,
            BookUpdate(
                token_id=token,
                market_id=self._token_market.get(token, ""),
                best_bid=bids[0][0] if bids else 0.0,
                best_ask=asks[0][0] if asks else 0.0,
                bids=tuple(bids),
                asks=tuple(asks),
                age_ms=age_ms,
            ),
        )

    async def _publish_price_changes(self, d: dict) -> None:
        """Polymarket emits `price_change` events between full book snapshots; without
        handling them the feed goes stale after the initial dump. Each change sets one
        level's size (0 removes it) — applied to the local book so depth stays current.
        Changes without a level, or before a token's first snapshot, publish top-of-book only."""
        age = self._age_ms(d)
        for change in d.get("price_changes", [d]):
            token = change.get("asset_id") or change.get("token_id", "")
            if not token:
                continue
            book = self._levels.get(token)
            side = {"BUY": "bids", "SELL": "asks"}.get(str(change.get("side", "")).upper())
            if book is not None and side is not None and _valid(change):
                try:
                    price, size = float(change["price"]), float(change["size"])
                except (KeyError, TypeError, ValueError):
                    pass
                else:
                    if size > 0:
                        book[side][price] = size
                    else:
                        book[side].pop(price, None)
                    await self._publish_levels(token, age)
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

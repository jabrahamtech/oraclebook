"""PolymarketFeed — the Polymarket CLOB market WebSocket book feed.

Discovers the active up/down markets (Gamma), subscribes to their outcome tokens on the
CLOB market socket, and maintains a local L2 book per token: a `book` message replaces it,
and each `price_change` sets the aggregate size at one price level (size 0 removes the
level). Every change publishes a `BookUpdate` with full depth and the best bid/ask derived
from the maintained book. `tick_size_change` updates the token's tick (`tick_size()`).

Rolling 5m/15m markets expire, so discovery re-runs every `rediscover_interval` seconds,
subscribing to new tokens and unsubscribing from ended ones. On disconnect the feed
publishes `BookInvalidated` for every subscribed token and reconnects with capped
exponential backoff. Read-only — no credentials required, so it powers paper trading,
recording, and Replay against real Polymarket data. (The production bot runs redundant
connections with freeze detection; this reference uses a single connection.)
"""
from __future__ import annotations

import asyncio
import json
import time

import structlog

from oraclebook.adapters.polymarket.markets import discover
from oraclebook.core.bus import Bus
from oraclebook.core.events import (
    EVT_BOOK_INVALIDATED,
    EVT_BOOK_UPDATE,
    BookInvalidated,
    BookUpdate,
)
from oraclebook.core.models import Market

logger = structlog.get_logger()

WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
_CHANNEL_INIT = {"assets_ids": [], "type": "market", "level": 2, "initial_dump": True}

Levels = dict[float, float]


def _level(raw) -> tuple[float, float] | None:
    """(price, size) for a well-formed level with 0 < price < 1, else None."""
    try:
        price = float(raw["price"])
        size = float(raw["size"])
    except (KeyError, TypeError, ValueError):
        return None
    if not 0.0 < price < 1.0 or size < 0.0:
        return None
    return price, size


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
        backoff_initial: float = 1.0,
        backoff_max: float = 30.0,
    ) -> None:
        self._bus = bus
        self._assets = assets
        self._intervals = intervals
        self._ping_interval = ping_interval
        self._rediscover_interval = rediscover_interval
        self._backoff_initial = backoff_initial
        self._backoff_max = backoff_max
        self._token_market: dict[str, str] = {}
        self._token_end: dict[str, float | None] = {}
        self._books: dict[str, tuple[Levels, Levels]] = {}
        self._ticks: dict[str, str] = {}
        self._session = None
        self._ws = None
        self._task: asyncio.Task | None = None
        self._ping_task: asyncio.Task | None = None
        self._discover_task: asyncio.Task | None = None
        self._running = False

    async def start(self) -> None:
        self._running = True
        # Discover markets off the event loop (markets.discover uses blocking urllib).
        markets = await asyncio.to_thread(discover, self._assets, self._intervals)
        await self._apply_markets(markets)
        self._task = asyncio.create_task(self._run())
        self._discover_task = asyncio.create_task(self._rediscover())

    async def stop(self) -> None:
        self._running = False
        tasks = [t for t in (self._task, self._ping_task, self._discover_task) if t is not None]
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if self._ws is not None and not self._ws.closed:
            await self._ws.close()
        if self._session is not None and not self._session.closed:
            await self._session.close()

    def tick_size(self, token_id: str) -> str | None:
        """The latest tick size seen for a token (from `book` or `tick_size_change`)."""
        return self._ticks.get(token_id)

    # --- connection ------------------------------------------------------------

    async def _run(self) -> None:
        import aiohttp  # optional adapter dependency

        self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None, connect=10))
        backoff = self._backoff_initial
        while self._running:
            connected_at: float | None = None
            try:
                self._ws = await self._session.ws_connect(WS_URL)
                await self._ws.send_json(_CHANNEL_INIT)
                tokens = list(self._token_market)
                if tokens:
                    await self._ws.send_json(
                        {"operation": "subscribe", "assets_ids": tokens, "level": 2}
                    )
                connected_at = time.monotonic()
                self._ping_task = asyncio.create_task(self._ping(self._ws))
                async for msg in self._ws:
                    if not self._running:
                        break
                    if msg.type == aiohttp.WSMsgType.TEXT and msg.data != "PONG":
                        await self._on_message(msg.data)
                    elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                        break
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning("polymarket_feed.error", error=str(exc))
            finally:
                if self._ping_task is not None:
                    self._ping_task.cancel()
                    self._ping_task = None
            if not self._running:
                break
            await self._invalidate_all()
            if connected_at is not None and time.monotonic() - connected_at >= self._backoff_max:
                backoff = self._backoff_initial
            logger.info("polymarket_feed.reconnecting", delay_s=backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2.0, self._backoff_max)

    async def _ping(self, ws) -> None:
        while not ws.closed:
            try:
                await ws.send_str("PING")
            except Exception as exc:  # noqa: BLE001
                logger.debug("polymarket_feed.ping_failed", error=str(exc))
                return
            await asyncio.sleep(self._ping_interval)

    async def _invalidate_all(self) -> None:
        self._books.clear()
        if self._token_market:
            await self._bus.publish(
                EVT_BOOK_INVALIDATED, BookInvalidated(token_ids=tuple(self._token_market))
            )

    # --- market rediscovery ------------------------------------------------------

    async def _rediscover(self) -> None:
        while self._running:
            await asyncio.sleep(self._rediscover_interval)
            try:
                markets = await asyncio.to_thread(discover, self._assets, self._intervals)
            except Exception as exc:  # noqa: BLE001
                logger.warning("polymarket_feed.rediscover_failed", error=str(exc))
                continue
            await self._apply_markets(markets)

    async def _apply_markets(self, markets: list[Market]) -> None:
        """Track newly discovered tokens and drop ended ones, updating a live subscription."""
        now = time.time()
        found = {t: m for m in markets for t in m.token_ids}
        added = [t for t in found if t not in self._token_market]
        removed = [
            t for t in self._token_market
            if t not in found and (self._token_end.get(t) is None or self._token_end[t] <= now)
        ]
        for t, m in found.items():
            self._token_market[t] = m.market_id
            self._token_end[t] = m.end_ts
        for t in removed:
            for state in (self._token_market, self._token_end, self._books, self._ticks):
                state.pop(t, None)
        logger.info("polymarket_feed.discovered", markets=len(markets),
                    tokens=len(self._token_market), added=len(added), removed=len(removed))
        ws = self._ws
        if ws is None or ws.closed:
            return
        try:
            if added:
                await ws.send_json({"operation": "subscribe", "assets_ids": added, "level": 2})
            if removed:
                await ws.send_json({"operation": "unsubscribe", "assets_ids": removed})
        except Exception as exc:  # noqa: BLE001
            # The reconnect path resubscribes to every tracked token.
            logger.warning("polymarket_feed.resubscribe_failed", error=str(exc))

    # --- messages ------------------------------------------------------------------

    async def _on_message(self, raw: str) -> None:
        received = time.time()
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return
        for item in (data if isinstance(data, list) else [data]):
            if not isinstance(item, dict):
                continue
            kind = item.get("event_type")
            if kind == "book" or (kind is None and ("bids" in item or "asks" in item)):
                await self._on_book(item, received)
            elif kind == "price_change" or "price_changes" in item:
                await self._on_price_change(item, received)
            elif kind == "tick_size_change":
                self._on_tick_size_change(item)

    @staticmethod
    def _timing(d: dict, received: float) -> tuple[float, float]:
        """(event ts in unix seconds, age_ms). Uses the exchange's millisecond `timestamp`
        when present, else the local receive time (age unknown -> 0)."""
        try:
            exchange_ms = float(d["timestamp"])
        except (KeyError, TypeError, ValueError):
            return received, 0.0
        return exchange_ms / 1000.0, max(0.0, received * 1000.0 - exchange_ms)

    async def _on_book(self, d: dict, received: float) -> None:
        token = d.get("asset_id", "")
        if not token:
            return
        bids: Levels = {}
        asks: Levels = {}
        for side, levels in ((bids, d.get("bids") or ()), (asks, d.get("asks") or ())):
            for raw in levels:
                level = _level(raw)
                if level is not None and level[1] > 0.0:
                    side[level[0]] = level[1]
        self._books[token] = (bids, asks)
        if d.get("tick_size"):
            self._ticks[token] = str(d["tick_size"])
        await self._publish(token, *self._timing(d, received))

    async def _on_price_change(self, d: dict, received: float) -> None:
        touched: list[str] = []
        for change in d.get("price_changes", [d]):
            if not isinstance(change, dict):
                continue
            token = change.get("asset_id", "")
            book = self._books.get(token)
            level = _level(change)
            side = str(change.get("side", "")).upper()
            if book is None or level is None or side not in ("BUY", "SELL"):
                continue  # no snapshot yet for this token, or a malformed change
            levels = book[0] if side == "BUY" else book[1]
            price, size = level
            if size == 0.0:
                levels.pop(price, None)
            else:
                levels[price] = size
            if token not in touched:
                touched.append(token)
        ts, age_ms = self._timing(d, received)
        for token in touched:
            await self._publish(token, ts, age_ms)

    def _on_tick_size_change(self, d: dict) -> None:
        token = d.get("asset_id", "")
        new_tick = d.get("new_tick_size")
        if token and new_tick:
            self._ticks[token] = str(new_tick)
            logger.info("polymarket_feed.tick_size_changed", token=token,
                        old=d.get("old_tick_size"), new=str(new_tick))

    async def _publish(self, token: str, ts: float, age_ms: float) -> None:
        bids_map, asks_map = self._books[token]
        bids = tuple(sorted(bids_map.items(), key=lambda lv: -lv[0]))
        asks = tuple(sorted(asks_map.items()))
        await self._bus.publish(
            EVT_BOOK_UPDATE,
            BookUpdate(
                ts=ts,
                token_id=token,
                market_id=self._token_market.get(token, ""),
                best_bid=bids[0][0] if bids else 0.0,
                best_ask=asks[0][0] if asks else 0.0,
                bids=bids,
                asks=asks,
                age_ms=age_ms,
            ),
        )

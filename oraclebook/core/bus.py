"""Bus — the pub/sub event backbone.

All inter-service communication goes through the Bus; no service calls another
directly. Feeds publish events, strategies subscribe, orders/fills flow back —
everything is events on topics.

Generalised from the original Polymarket bot's `MessageBus`: the venue-specific
event contracts and the legacy migration bridge are gone. *Coalescing* and
*critical* topics are now **configurable** — an adapter or the App declares which
high-frequency topics keep only the latest value per key (e.g. a book update per
token) and which must never drop — instead of the kernel hardcoding them.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, Union

import structlog

logger = structlog.get_logger()


class CoalescingBuffer:
    """Queue-compatible buffer that keeps only the latest value per key — for
    high-frequency topics where only the freshest value matters (e.g. order books)."""

    def __init__(self, key_attr: str) -> None:
        self._key_attr = key_attr
        self._latest: dict[Any, Any] = {}
        self._event = asyncio.Event()

    def put_nowait(self, item: Any) -> None:
        self._latest[getattr(item, self._key_attr)] = item
        self._event.set()

    def get_nowait(self) -> Any:
        if not self._latest:
            raise asyncio.QueueEmpty
        _, item = self._latest.popitem()
        return item

    async def get(self) -> Any:
        while not self._latest:
            self._event.clear()
            if self._latest:
                break
            await self._event.wait()
        return self.get_nowait()

    def empty(self) -> bool:
        return not self._latest

    def qsize(self) -> int:
        return len(self._latest)


Subscription = Union[asyncio.Queue, CoalescingBuffer]


class Bus:
    """String-topic pub/sub + typed command routing + typed event fan-out.

    Args:
        coalescing: topic -> key attribute. Subscribers to these topics get a
            `CoalescingBuffer` keyed on that attribute (latest-per-key).
        critical: topics whose subscribers get an unbounded queue (never drop).
    """

    def __init__(
        self,
        coalescing: dict[str, str] | None = None,
        critical: frozenset[str] | None = None,
    ) -> None:
        self._coalescing = coalescing or {}
        self._critical = critical or frozenset()
        self._subscribers: dict[str, list[Subscription]] = defaultdict(list)
        self._drop_counts: dict[str, int] = defaultdict(int)
        self._command_handlers: dict[type, Callable[..., Awaitable[Any]]] = {}
        self._typed_subscribers: dict[type, list[asyncio.Queue]] = defaultdict(list)

    # --- string-topic pub/sub ------------------------------------------------

    def subscribe(self, topic: str, maxsize: int = 1000) -> Subscription:
        """Subscribe to a topic. Coalescing topics return a latest-per-key buffer;
        critical topics an unbounded queue; everything else a bounded queue."""
        if topic in self._coalescing:
            sub: Subscription = CoalescingBuffer(self._coalescing[topic])
        elif topic in self._critical:
            sub = asyncio.Queue(maxsize=0)
        else:
            sub = asyncio.Queue(maxsize=maxsize)
        self._subscribers[topic].append(sub)
        return sub

    def subscribe_raw(self, topic: str, maxsize: int = 10_000) -> asyncio.Queue:
        """Subscribe without coalescing — every individual emission is delivered (not just the
        latest per key), for recorders and auditors. The queue is bounded by `maxsize`; if a
        consumer falls that far behind, the oldest event is dropped (and logged). Size it for
        your throughput, or drain promptly, if you need a strict no-drop guarantee."""
        sub: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        self._subscribers[topic].append(sub)
        return sub

    async def publish(self, topic: str, payload: Any) -> None:
        """Publish to all subscribers of a topic. Bounded queues drop-oldest under
        backpressure (logged); coalescing buffers overwrite per key."""
        for sub in self._subscribers.get(topic, []):
            if isinstance(sub, CoalescingBuffer):
                sub.put_nowait(payload)
                continue
            try:
                sub.put_nowait(payload)
            except asyncio.QueueFull:
                try:
                    sub.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                self._drop_counts[topic] += 1
                count = self._drop_counts[topic]
                if count == 1 or count % 100 == 0:
                    logger.warning("bus.event_dropped", topic=topic, total_drops=count)
                try:
                    sub.put_nowait(payload)
                except asyncio.QueueFull:
                    logger.error("bus.double_drop", topic=topic)

    # --- typed command routing -----------------------------------------------

    def register_handler(self, cmd_type: type, handler: Callable[..., Awaitable[Any]]) -> None:
        """Register the single handler for a command type."""
        if cmd_type in self._command_handlers:
            raise ValueError(f"Handler already registered for {cmd_type.__name__}")
        self._command_handlers[cmd_type] = handler

    async def send(self, cmd: Any) -> Any:
        """Route a command to its handler. Raises if none registered."""
        handler = self._command_handlers.get(type(cmd))
        if handler is None:
            raise ValueError(f"No handler registered for {type(cmd).__name__}")
        return await handler(cmd)

    # --- typed event fan-out -------------------------------------------------

    def subscribe_event(self, event_type: type, maxsize: int = 0) -> asyncio.Queue:
        """Subscribe to a typed event class."""
        queue: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        self._typed_subscribers[event_type].append(queue)
        return queue

    async def publish_event(self, event: Any) -> None:
        """Publish a typed event to all subscribers of its class."""
        for queue in self._typed_subscribers.get(type(event), []):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                name = type(event).__name__
                self._drop_counts[name] += 1
                if self._drop_counts[name] % 100 == 1:
                    logger.warning("bus.typed_event_dropped", event_type=name)
                try:
                    queue.put_nowait(event)
                except asyncio.QueueFull:
                    pass

    # --- consumption + observability -----------------------------------------

    @staticmethod
    async def consume(
        sub: Subscription,
        *,
        running: Callable[[], bool],
        timeout: float = 5.0,
        label: str = "",
    ) -> AsyncIterator[Any]:
        """Drain a subscription with standard timeout/cancel/error handling."""
        while running():
            try:
                yield await asyncio.wait_for(sub.get(), timeout=timeout)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("bus.consumer_error", label=label)
                continue

    @property
    def queue_depths(self) -> dict[str, list[int]]:
        return {k: [q.qsize() for q in v] for k, v in self._subscribers.items()}

    @property
    def drop_counts(self) -> dict[str, int]:
        return dict(self._drop_counts)

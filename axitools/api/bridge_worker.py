"""Rate limiting and serialized sending for relayed AxiBridge reports.

A hosted bot shares ONE global Discord rate-limit bucket across every guild, so
report sends are queued off the HTTP request: a guild dumping forty logs must
not make slash commands feel laggy for everybody else.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Dict, Optional

LOGGER = logging.getLogger(__name__)


class RateLimiter:
    """Token bucket keyed by credential hash, so one guild cannot starve another."""

    def __init__(self, rate_per_minute: int = 10, burst: int = 5) -> None:
        self._rate = rate_per_minute / 60.0
        self._burst = float(burst)
        self._buckets: Dict[str, tuple] = {}

    def check(self, key_hash: str) -> Optional[float]:
        """Consume a token. Returns ``None`` when allowed, else seconds to wait."""
        now = time.monotonic()
        tokens, last = self._buckets.get(key_hash, (self._burst, now))
        tokens = min(self._burst, tokens + (now - last) * self._rate)
        if tokens < 1.0:
            self._buckets[key_hash] = (tokens, now)
            return max(1.0, (1.0 - tokens) / self._rate)
        self._buckets[key_hash] = (tokens - 1.0, now)
        return None


class BridgeSendQueue:
    """Serialize report sends through a single worker task."""

    def __init__(self, bot) -> None:
        self.bot = bot
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._task: Optional[asyncio.Task] = None

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self.run())

    def is_running(self) -> bool:
        """Whether the consumer task is alive and able to drain submissions."""
        return self._task is not None and not self._task.done()

    async def submit(self, channel, payload: dict, files=None) -> None:
        """Enqueue a send. Raises ``asyncio.QueueFull`` if the backlog is full."""
        self._queue.put_nowait((channel, payload, files))

    async def run(self) -> None:
        while True:
            channel, payload, files = await self._queue.get()
            try:
                await self.bot.send_bridge_report(channel, payload, files)
            except Exception:  # one bad report must not kill the worker
                LOGGER.exception("bridge report send failed")
            finally:
                self._queue.task_done()

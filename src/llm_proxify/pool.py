"""Atomic backend reservations, bounded waiting and failure cooldowns."""

import asyncio
import time
from dataclasses import dataclass

from .config import Settings


class QueueFull(Exception):
    pass


@dataclass
class Backend:
    url: str
    inflight: int = 0
    cooldown_until: float = 0
    successes: int = 0
    failures: int = 0


class BackendPool:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.backends = [Backend(url) for url in settings.backends]
        self.condition = asyncio.Condition()
        self.next_index = 0
        self.waiting = 0

    async def acquire(self, attempted: set[int], deadline: float) -> int | None:
        queued = False
        async with self.condition:
            try:
                while True:
                    now = time.monotonic()
                    if now >= deadline:
                        raise TimeoutError
                    allowed = [i for i in range(len(self.backends)) if i not in attempted]
                    if not allowed:
                        return None
                    available = [
                        i
                        for i in allowed
                        if self.backends[i].cooldown_until <= now
                        and self.backends[i].inflight < self.settings.max_inflight_per_backend
                    ]
                    if available:
                        count = len(self.backends)
                        index = min(
                            available,
                            key=lambda i: (
                                self.backends[i].inflight,
                                (i - self.next_index) % count,
                            ),
                        )
                        self.next_index = (index + 1) % count
                        self.backends[index].inflight += 1
                        return index
                    if not queued:
                        if self.waiting >= self.settings.max_queue:
                            raise QueueFull
                        self.waiting += 1
                        queued = True
                    wake_at = min(
                        [deadline]
                        + [
                            self.backends[i].cooldown_until
                            for i in allowed
                            if self.backends[i].cooldown_until > now
                        ]
                    )
                    try:
                        await asyncio.wait_for(self.condition.wait(), max(0.001, wake_at - now))
                    except asyncio.TimeoutError:
                        if time.monotonic() >= deadline:
                            raise TimeoutError from None
            finally:
                if queued:
                    self.waiting -= 1

    async def release(self, index: int, status: int | None, *, failed: bool = False):
        async with self.condition:
            backend = self.backends[index]
            backend.inflight -= 1
            if failed or status is None or status == 429 or status >= 500:
                backend.failures += 1
                backend.cooldown_until = time.monotonic() + self.settings.cooldown_sec
            elif 200 <= status < 300:
                backend.successes += 1
            else:
                backend.failures += 1
            self.condition.notify_all()

    def snapshot(self) -> dict:
        now = time.monotonic()
        return {
            "queue": self.waiting,
            "backends": [
                {
                    "id": i,
                    "inflight": b.inflight,
                    "cooldown_sec": round(max(0, b.cooldown_until - now), 2),
                    "successes": b.successes,
                    "failures": b.failures,
                }
                for i, b in enumerate(self.backends)
            ],
        }

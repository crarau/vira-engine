"""One rate-limit bucket for everything that generates an image.

This lived in `routes/image.py` — the public Gemini proxy — until that endpoint
was withdrawn. The limiter had to survive it: `/v1/ads/image` still spends real
money per call on an unauthenticated service, and a limit that disappears along
with an unrelated route is the kind of thing nobody notices until the bill does.

One bucket, deliberately, rather than one per endpoint. Two ceilings that each
allow the maximum are not a ceiling.

Both counters are per-process and reset on restart, which is the right trade for
a service measured in days rather than years.
"""

from __future__ import annotations

import asyncio
import time

from fastapi import HTTPException

BURST_PER_MINUTE = 20
DAILY_MAX = 500

_recent: list[float] = []
_today: dict[str, int] = {}
_lock = asyncio.Lock()


async def admit(day: str) -> None:
    """Raises 429 rather than quietly queueing — a caller in a loop should be
    told, not slowed."""
    async with _lock:
        now = time.monotonic()
        _recent[:] = [t for t in _recent if now - t < 60]
        if len(_recent) >= BURST_PER_MINUTE:
            raise HTTPException(429, f"more than {BURST_PER_MINUTE} images in a minute")
        if _today.get(day, 0) >= DAILY_MAX:
            raise HTTPException(429, f"daily ceiling of {DAILY_MAX} images reached")
        _recent.append(now)
        _today[day] = _today.get(day, 0) + 1


def reset() -> None:
    """Test helper. Production never calls this."""
    _recent.clear()
    _today.clear()

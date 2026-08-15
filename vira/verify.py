"""Stage 2 — confirm each source is still live.

TikTok URLs rot. Videos get deleted, accounts go private, regions block. A
recommendation whose proof 404s while a judge is clicking it is worse than no
recommendation, so nothing reaches the model until its source has answered.
"""

from __future__ import annotations

import asyncio
import logging

import httpx

from vira.models import Trend

log = logging.getLogger(__name__)

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0 Safari/537.36"

# TikTok serves a soft 200 for removed videos, so the status code alone is not
# enough — these strings in the body mean the video is gone.
GONE_MARKERS = (
    "video currently unavailable",
    "this post is unavailable",
    "couldn't find this account",
    "video has been removed",
)


async def _check(client: httpx.AsyncClient, trend: Trend) -> Trend:
    try:
        r = await client.get(trend.source_url, follow_redirects=True)
    except httpx.HTTPError as exc:
        trend.drop_reason = f"unreachable: {type(exc).__name__}"
        return trend

    if r.status_code == 404:
        trend.drop_reason = "404 — video deleted"
        return trend
    if r.status_code == 403:
        trend.drop_reason = "403 — private or region-blocked"
        return trend
    if r.status_code >= 400:
        trend.drop_reason = f"HTTP {r.status_code}"
        return trend

    body = r.text[:20000].lower()
    for marker in GONE_MARKERS:
        if marker in body:
            trend.drop_reason = f"removed ({marker})"
            return trend

    trend.verified = True
    return trend


async def verify_all(
    trends: list[Trend], *, concurrency: int = 8
) -> tuple[list[Trend], list[Trend]]:
    """Return (verified, dropped). Concurrency is capped — TikTok rate-limits."""
    sem = asyncio.Semaphore(concurrency)

    async with httpx.AsyncClient(
        timeout=20, headers={"User-Agent": UA}, follow_redirects=True
    ) as client:

        async def guarded(t: Trend) -> Trend:
            async with sem:
                return await _check(client, t)

        results = await asyncio.gather(
            *(guarded(t) for t in trends), return_exceptions=True
        )

    verified: list[Trend] = []
    dropped: list[Trend] = []
    for trend, res in zip(trends, results):
        if isinstance(res, BaseException):
            trend.drop_reason = f"verification error: {res}"
            dropped.append(trend)
        elif res.verified:
            verified.append(res)
        else:
            dropped.append(res)

    log.info("verified %d, dropped %d", len(verified), len(dropped))
    return verified, dropped

"""Stage 1 — pick candidate trends for one company.

The corpus is 2,999 videos of wildly mixed age, language, and relevance. Getting
this filter right matters more than any prompt downstream: feed the model a 2021
mop video and it will confidently explain why mops are the future of energy
drinks.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from datetime import datetime

from datetime import timedelta, timezone

from vira.config import settings
from vira.models import Company, Trend
from vira.supa import Supa, fresh_company_trends

log = logging.getLogger(__name__)

# Cheap latin-script heuristic. The corpus has Indonesian snack ads that produce
# unusable remixes; a full language detector is not worth the dependency here.
_NON_LATIN = re.compile(r"[^\x00-\x7FÀ-ɏ\s]")


def _looks_english(text: str) -> bool:
    stripped = re.sub(r"[#@\w]+://\S+", "", text)
    if not stripped.strip():
        return True
    non_latin = len(_NON_LATIN.findall(stripped))
    return non_latin / max(len(stripped), 1) < 0.08


def _parse(row: dict) -> Trend | None:
    try:
        posted = row.get("posted_at")
        return Trend(
            trend_key=row["trend_key"],
            platform=row.get("platform") or "tiktok",
            title=row.get("title") or "",
            caption=row.get("caption") or row.get("title") or "",
            source_url=row.get("source_url") or "",
            author=row.get("author") or "",
            format=row.get("format") or "",
            hashtags=row.get("hashtags") or [],
            views=int(row.get("views") or 0),
            likes=int(row.get("likes") or 0),
            engagement_rate=float(row.get("engagement_rate") or 0),
            trend_score=float(row.get("trend_score") or 0),
            posted_at=datetime.fromisoformat(posted) if posted else None,
            relevance_rank=int(row.get("relevance_rank") or 1),
        )
    except Exception as exc:  # noqa: BLE001 - one bad row must not kill selection
        log.warning("skipping unparseable trend row: %s", exc)
        return None


async def shortlist(
    supa: Supa, company: Company, product: str, *, limit: int | None = None
) -> tuple[list[Trend], dict[str, int]]:
    """Return (shortlist, rejection counts by reason).

    The counts are not diagnostics for us — they are the "what the engine
    rejected" panel, and they are the most persuasive thing in the demo.
    """
    s = settings()
    limit = limit or s.shortlist_size

    # Age filter runs in the database, not here. See fresh_company_trends for why.
    since = (
        datetime.now(timezone.utc) - timedelta(days=s.max_age_days)
    ).isoformat()
    cat_rows = await supa.select(
        "companies", id=f"eq.{company.id}", select="category_id"
    )
    if not cat_rows:
        return [], {"company has no category": 1}

    rows = await fresh_company_trends(
        supa, cat_rows[0]["category_id"], since_iso=since, limit=300
    )
    candidates = [t for t in (_parse(r) for r in rows) if t is not None]
    log.info("category join returned %d fresh candidates", len(candidates))

    rejected: Counter[str] = Counter()
    kept: list[Trend] = []

    for t in candidates:
        if not t.source_url:
            rejected["no source url"] += 1
            continue
        if t.age_days > s.max_age_days:
            rejected[f"older than {s.max_age_days}d"] += 1
            continue
        if s.english_only and not _looks_english(t.caption):
            rejected["not english"] += 1
            continue
        kept.append(t)

    # Rank by the engagement-derived score, never by raw views — views alone
    # floats four-year-old megaviral clips to the top.
    kept.sort(key=lambda t: t.trend_score, reverse=True)

    # Enforce format diversity so the shortlist isn't six unboxings.
    per_format: Counter[str] = Counter()
    diverse: list[Trend] = []
    for t in kept:
        fmt = t.format or "unknown"
        if per_format[fmt] >= s.max_per_format:
            rejected["format quota"] += 1
            continue
        per_format[fmt] += 1
        diverse.append(t)
        if len(diverse) >= limit:
            break

    log.info(
        "shortlist: %d kept from %d (rejected: %s)",
        len(diverse), len(candidates), dict(rejected),
    )
    return diverse, dict(rejected)

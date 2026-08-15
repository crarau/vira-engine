"""Read-only windows onto the Lovable corpus.

`/v1/companies` serves this engine's OWN table — the companies it has generated
for. That is the right answer for the generation flow and the wrong one for a
human who wants to look at what actually exists, because the corpus and its
companies live in Lovable Cloud and this service only borrows them.

So these endpoints proxy Lovable directly. Nothing is cached and nothing is
copied: the corpus grew from 2,999 to 3,976 rows in about an hour, so a mirror
would be stale on arrival, and every score computed against the stale half would
be quietly wrong. See docs/DATA-BOUNDARY.md.

Read-only, by construction — the anonymous PostgREST key is RLS-bound and cannot
write.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query

from vira.supa import Supa

router = APIRouter(prefix="/v1/corpus", tags=["corpus"])

# PostgREST caps a response at 1000 rows whatever `limit` says, so anything
# claiming to be a total must come from a count, not from len(rows).
PAGE_MAX = 200


def _thumb(row: dict) -> str | None:
    """TikTok cover, if the scrape captured one. Signed and expires in ~2 days."""
    raw = row.get("raw") or {}
    return raw.get("coverUrl") if isinstance(raw, dict) else None


def _age_days(posted_at: str | None) -> float | None:
    if not posted_at:
        return None
    try:
        d = datetime.fromisoformat(posted_at)
    except ValueError:
        return None
    return round((datetime.now(timezone.utc) - d).total_seconds() / 86_400, 1)


@router.get("/categories")
async def categories() -> list[dict]:
    supa = Supa()
    rows = await supa.select("categories", select="id,name,slug", order="name")
    counts = await asyncio.gather(
        *(supa.count("category_trends", category_id=f"eq.{r['id']}") for r in rows),
        return_exceptions=True,
    )
    return [
        {**r, "trend_count": c if isinstance(c, int) else None}
        for r, c in zip(rows, counts)
    ]


@router.get("/companies")
async def companies(limit: int = Query(100, le=PAGE_MAX)) -> list[dict]:
    """Companies as Lovable knows them, with the two fields that predict quality.

    `website` and `enriched` are surfaced deliberately: a company with a null
    website produces "enrichment" that is only an LLM paraphrase of the bio the
    user typed, because the scraper has nothing to fetch. A UI should show that
    before someone generates against it and wonders why the ad is vague.
    """
    supa = Supa()
    rows = await supa.select(
        "companies",
        select="id,slug,name,bio,mission,website,owner_name,status,created_at,"
        "categories(name,slug),company_insights(summary,positioning,tone,keywords,ad_themes,sources)",
        order="created_at.desc",
        limit=limit,
    )
    out = []
    for r in rows:
        insights = r.get("company_insights") or []
        latest = insights[0] if insights else {}
        sources = latest.get("sources") or []
        out.append({
            "slug": r["slug"],
            "name": r["name"],
            "bio": r.get("bio") or "",
            "mission": r.get("mission") or "",
            "website": r.get("website"),
            "owner_name": r.get("owner_name"),
            "status": r.get("status"),
            "category": (r.get("categories") or {}).get("name"),
            "category_slug": (r.get("categories") or {}).get("slug"),
            "created_at": r.get("created_at"),
            # Enrichment that cites nothing is a paraphrase, not research.
            "enriched": bool(sources),
            "positioning": latest.get("positioning"),
            "keywords": latest.get("keywords") or [],
            "ad_themes": latest.get("ad_themes") or [],
        })
    return out


@router.get("/trends")
async def trends(
    category: str | None = Query(None, description="category slug"),
    max_age_days: int | None = Query(None, description="omit to see stale rows too"),
    order: str = Query("trend_score", pattern="^(trend_score|views|posted_at)$"),
    limit: int = Query(60, le=PAGE_MAX),
) -> dict:
    """The scraped corpus.

    `order=views` is offered because a human will want it, but it is the wrong
    default and the response says so: reach is half of `trend_score`, so sorting
    by raw views floats four-year-old megaviral clips to the top. That is
    precisely the bug that made `company_trends()` return 100% stale rows.
    """
    supa = Supa()

    if category:
        cats = await supa.select("categories", slug=f"eq.{category}", select="id")
        if not cats:
            raise HTTPException(404, f"no category {category!r}")
        since = (
            datetime.now(timezone.utc) - timedelta(days=max_age_days)
        ).isoformat() if max_age_days else "1970-01-01T00:00:00+00:00"
        # Not `fresh_company_trends`: that one omits `raw` on purpose, because
        # selection does not need a jsonb blob per row and 300 of them is a lot
        # of payload. A browsing UI does need it — the thumbnail lives in
        # raw.coverUrl, and 399 of the newest 400 rows have one.
        embed = await supa.select(
            "category_trends",
            select="relevance_rank,trends!inner("
            "trend_key,platform,title,caption,source_url,author,format,hashtags,"
            "views,likes,engagement_rate,trend_score,posted_at,query,raw)",
            category_id=f"eq.{cats[0]['id']}",
            order="relevance_rank.asc",
            limit=limit,
            **{"trends.posted_at": f"gte.{since}"},
        )
        rows = [e["trends"] for e in embed if e.get("trends")]
    else:
        params: dict = {
            "select": "trend_key,platform,title,caption,source_url,author,format,"
            "hashtags,views,likes,engagement_rate,trend_score,posted_at,raw,query",
            "order": f"{order}.desc",
            "limit": limit,
        }
        if max_age_days:
            since = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).isoformat()
            params["posted_at"] = f"gte.{since}"
        rows = await supa.select("trends", **params)

    total = await supa.count("trends")
    items = []
    for r in rows:
        age = _age_days(r.get("posted_at"))
        items.append({
            "trend_key": r.get("trend_key"),
            "author": r.get("author"),
            "caption": (r.get("caption") or r.get("title") or "")[:400],
            "source_url": r.get("source_url"),
            "thumbnail": _thumb(r),
            "format": r.get("format"),
            "hashtags": (r.get("hashtags") or [])[:10],
            "views": r.get("views"),
            "likes": r.get("likes"),
            "engagement_rate": r.get("engagement_rate"),
            "trend_score": r.get("trend_score"),
            "posted_at": r.get("posted_at"),
            "age_days": age,
            # A >90d "trend" is the known failure mode; flag it rather than
            # leaving a UI to rediscover it.
            "stale": age is not None and age > 90,
            "query": r.get("query"),
        })
    return {
        "total_in_corpus": total,
        "returned": len(items),
        "order": order,
        "note": (
            "sorted by raw views — reach dominates, so expect stale megaviral clips"
            if order == "views" else None
        ),
        "items": items,
    }


@router.get("/stats")
async def stats() -> dict:
    """Enough to judge, at a glance, whether the corpus can support an ad."""
    supa = Supa()
    now = datetime.now(timezone.utc)

    async def since(days: int) -> int:
        iso = (now - timedelta(days=days)).isoformat()
        return await supa.count("trends", posted_at=f"gte.{iso}")

    total, d30, d90, d365, companies_n, cats = await asyncio.gather(
        supa.count("trends"), since(30), since(90), since(365),
        supa.count("companies"), supa.select("categories", select="id,name,slug"),
    )
    per_cat = await asyncio.gather(
        *(supa.count("category_trends", category_id=f"eq.{c['id']}") for c in cats),
        return_exceptions=True,
    )
    return {
        "trends_total": total,
        "fresh_30d": d30,
        "fresh_90d": d90,
        "within_1y": d365,
        # The number that matters: selection filters to 90 days, so anything
        # older is invisible to generation however good it is.
        "usable_share_90d": round(d90 / total, 3) if total else 0.0,
        "companies": companies_n,
        "by_category": [
            {"name": c["name"], "slug": c["slug"],
             "mapped": n if isinstance(n, int) else None}
            for c, n in zip(cats, per_cat)
        ],
    }

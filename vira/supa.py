"""PostgREST client for Lovable Cloud.

There is no Postgres connection string on Lovable Cloud, so this is the only way
in. Reads are anonymous where RLS allows; writes need a JWT from the agent
account.

PostgREST caps a response at 1000 rows regardless of `limit`, which silently
truncates large reads — `select_all` pages past it rather than pretending 1000
is the whole table.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from vira.config import settings

log = logging.getLogger(__name__)

PAGE = 1000


class SupabaseError(RuntimeError):
    pass


class Supa:
    def __init__(self, token: str | None = None) -> None:
        s = settings()
        self.url = s.supabase_url.rstrip("/")
        self.key = s.supabase_key
        self.token = token

    # -- auth ------------------------------------------------------------
    @classmethod
    async def signed_in(cls) -> "Supa":
        """Authenticate as the agent account so writes are permitted."""
        s = settings()
        if not (s.agent_email and s.agent_password):
            raise SupabaseError(
                "AGENT_EMAIL / AGENT_PASSWORD are unset — reads only. "
                "Create an agent account in the app to enable writes."
            )
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(
                f"{s.supabase_url}/auth/v1/token?grant_type=password",
                headers={"apikey": s.supabase_key, "Content-Type": "application/json"},
                json={"email": s.agent_email, "password": s.agent_password},
            )
            if r.status_code >= 400:
                raise SupabaseError(f"agent sign-in failed [{r.status_code}]: {r.text[:200]}")
            return cls(token=r.json()["access_token"])

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        h = {
            "apikey": self.key,
            "Authorization": f"Bearer {self.token or self.key}",
            "Content-Type": "application/json",
        }
        if extra:
            h.update(extra)
        return h

    # -- read ------------------------------------------------------------
    async def select(self, table: str, **params: Any) -> list[dict]:
        params.setdefault("select", "*")
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.get(
                f"{self.url}/rest/v1/{table}", headers=self._headers(), params=params
            )
            if r.status_code >= 400:
                raise SupabaseError(f"{table} read [{r.status_code}]: {r.text[:200]}")
            return r.json()

    async def select_all(self, table: str, **params: Any) -> list[dict]:
        """Page past PostgREST's 1000-row ceiling."""
        out: list[dict] = []
        offset = 0
        while True:
            page = await self.select(table, **params, offset=offset, limit=PAGE)
            out.extend(page)
            if len(page) < PAGE:
                return out
            offset += PAGE

    async def count(self, table: str, **params: Any) -> int:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.get(
                f"{self.url}/rest/v1/{table}",
                headers=self._headers({"Prefer": "count=exact", "Range": "0-0"}),
                params={**params, "select": "id"},
            )
            rng = r.headers.get("content-range", "/0")
            return int(rng.split("/")[-1] or 0)

    async def rpc(self, fn: str, payload: dict) -> Any:
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(
                f"{self.url}/rest/v1/rpc/{fn}", headers=self._headers(), json=payload
            )
            if r.status_code >= 400:
                raise SupabaseError(f"rpc {fn} [{r.status_code}]: {r.text[:200]}")
            return r.json()

    # -- write -----------------------------------------------------------
    async def insert(self, table: str, rows: list[dict], upsert: bool = False) -> list[dict]:
        if not self.token:
            raise SupabaseError("writes require the agent JWT — use Supa.signed_in()")
        prefer = "return=representation"
        if upsert:
            prefer += ",resolution=merge-duplicates"
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(
                f"{self.url}/rest/v1/{table}",
                headers=self._headers({"Prefer": prefer}),
                json=rows,
            )
            if r.status_code >= 400:
                raise SupabaseError(f"{table} insert [{r.status_code}]: {r.text[:300]}")
            return r.json()


# -- convenience reads ----------------------------------------------------


async def get_company(supa: Supa, slug: str) -> dict | None:
    rows = await supa.select(
        "companies",
        slug=f"eq.{slug}",
        select="id,name,slug,bio,mission,website,owner_name,category_id,"
        "categories(name,slug),company_insights(summary,positioning,tone,keywords,ad_themes)",
    )
    return rows[0] if rows else None


async def company_trends(supa: Supa, company_id: str, limit: int = 200) -> list[dict]:
    """Category-matched trends via the RPC the Lovable app already defines.

    Kept for parity with the app, but prefer `fresh_company_trends`. This RPC
    hard-caps at 200 rows ordered by `trend_score`, and because trend_score is
    half reach, that window fills with old megaviral clips — 100% of what it
    returned for Food & Beverage was over 90 days old, while 56% of the corpus
    is under 90 days. The freshness filter has to happen server-side, before
    the cap, or it has nothing to work with.
    """
    return await supa.rpc("company_trends", {"_company_id": company_id, "_limit": limit})


async def fresh_company_trends(
    supa: Supa, category_id: str, *, since_iso: str, limit: int = 300
) -> list[dict]:
    """Category-matched trends, date-filtered in the database.

    Uses an inner-join embed so the age filter is applied before any row cap,
    then flattens the embedded shape into plain trend rows.
    """
    rows = await supa.select(
        "category_trends",
        select="relevance_rank,trends!inner("
        "trend_key,platform,title,caption,source_url,author,format,hashtags,"
        "views,likes,engagement_rate,trend_score,posted_at)",
        category_id=f"eq.{category_id}",
        order="relevance_rank.asc",
        limit=limit,
        **{"trends.posted_at": f"gte.{since_iso}"},
    )
    flat: list[dict] = []
    for row in rows:
        trend = row.get("trends")
        if trend:
            flat.append({**trend, "relevance_rank": row.get("relevance_rank", 1)})
    return flat

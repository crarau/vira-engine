"""Companies — the thing a video is made for.

A company exists in two places and that is not an accident. Lovable Cloud is the
source of truth: it holds the category join the engine's `select` stage needs,
and it is where the frontend's own data lives. The local table is what jobs and
videos hang off, because a foreign key into someone else's PostgREST endpoint is
not a thing.

So the write here goes to both, in that order. Writing only locally would give a
frontend a company it could POST and then a generation that fails with "no
company with slug" — a worse outcome than refusing the write outright when the
agent credentials are missing.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request

from vira.api import store
from vira.api.schemas import CompanyIn, CompanyOut, VideoOut
from vira.api.worker import media_url, resolve_company
from vira.config import settings
from vira.supa import Supa, SupabaseError

log = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["companies"])


@router.get("/companies", response_model=list[CompanyOut])
async def list_companies() -> list[CompanyOut]:
    return [CompanyOut.of(row) for row in await store.list_companies()]


@router.post("/companies", response_model=CompanyOut, status_code=201)
async def create_company(body: CompanyIn) -> CompanyOut:
    anon = Supa()

    if await anon.select("companies", slug=f"eq.{body.slug}", select="id"):
        raise HTTPException(409, f"company {body.slug!r} already exists")

    cats = await anon.select("categories", slug=f"eq.{body.category}", select="id,name")
    if not cats:
        known = await anon.select("categories", select="slug", order="slug")
        raise HTTPException(
            422,
            f"no category {body.category!r}. known: "
            + ", ".join(c["slug"] for c in known),
        )
    category = cats[0]

    owner_id = settings().agent_user_id
    if not owner_id:
        raise HTTPException(503, "AGENT_USER_ID is unset — the row needs an owner RLS accepts")

    try:
        supa = await Supa.signed_in()
        created = await supa.insert("companies", [{
            "owner_id": owner_id,
            "category_id": category["id"],
            "name": body.name,
            "slug": body.slug,
            "owner_name": body.owner_name,
            "bio": body.bio,
            "mission": body.mission,
            "website": body.website,
            "status": "published",
        }])
    except SupabaseError as exc:
        raise HTTPException(502, f"could not create company: {exc}") from exc

    local = await store.upsert_company(
        slug=body.slug, name=body.name, bio=body.bio, mission=body.mission,
        website=body.website, category=category["name"], owner_name=body.owner_name,
    )
    log.info("created company %s (lovable %s)", body.slug, created[0].get("id"))
    return CompanyOut.of(local)


@router.get("/companies/{slug}/videos", response_model=list[VideoOut])
async def company_videos(slug: str, request: Request) -> list[VideoOut]:
    company = await resolve_company(slug)
    if not company:
        raise HTTPException(404, f"no company with slug {slug!r}")
    base = str(request.base_url)
    return [
        VideoOut.of(row, media_url(base, row.get("mp4_path") or ""), company_slug=slug)
        for row in await store.list_videos_for_company(company["id"])
    ]

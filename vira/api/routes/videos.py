"""Videos — start one, read one, take it apart, run it again.

The only endpoint here that does work is the POST, and it deliberately does none
of it inline: it writes a job row, hands the job to the worker, and returns 202
in milliseconds. Generation takes 74s deterministic and ~350s with the crew, so
a synchronous version would time out behind any proxy in front of this service.

`/recipe` and `/regenerate` are the pair that make a generated ad a starting
point rather than a lottery ticket. The recipe is the verbatim prompts, the
corpus that was in scope, and the settings in force; regenerate takes reviewer
notes and runs the same lane again with them folded into the brief.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request

from vira.api import store, worker
from vira.api.schemas import (
    JobAccepted,
    LaneOut,
    RecipeOut,
    RegenerateRequest,
    VideoOut,
    VideoRequest,
)
from vira.lanes import BY_NAME, LANES

log = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["videos"])

# What to tell a UI to expect on the progress bar. Measured, not guessed:
# see the table in CLAUDE.md.
ETA_SECONDS = {"fast": 90, "agentic": 360}


@router.get("/lanes", response_model=list[LaneOut])
async def lanes() -> list[LaneOut]:
    """The creative lanes a video can be forced down.

    A lane is a whole creative identity — copy direction, voice and look — which
    is why a UI should offer these as choices rather than exposing the prompt.
    """
    return [
        LaneOut(name=l.name, brief=l.brief, voice_note=l.voice_note, look=l.look)
        for l in LANES
    ]


@router.post("/videos", response_model=JobAccepted, status_code=202)
async def create_video(body: VideoRequest, request: Request) -> JobAccepted:
    if body.lane not in BY_NAME:
        raise HTTPException(422, f"unknown lane {body.lane!r}. known: {', '.join(BY_NAME)}")

    company = await worker.resolve_company(body.company_slug)
    if not company:
        raise HTTPException(404, f"no company with slug {body.company_slug!r}")

    job = await store.create_job(
        company_id=company["id"],
        product=body.product,
        lane=body.lane,
        mode=body.mode,
    )
    job_id = str(job["id"])
    worker.spawn(
        job_id,
        company_slug=body.company_slug,
        product=body.product,
        lane_name=body.lane,
        mode=body.mode,
    )
    return JobAccepted(
        job_id=job_id,
        poll=f"{str(request.base_url).rstrip('/')}/v1/jobs/{job_id}",
        estimated_seconds=ETA_SECONDS.get(body.mode, 120),
    )


@router.get("/videos/{video_id}", response_model=VideoOut)
async def get_video(video_id: str, request: Request) -> VideoOut:
    row = await store.get_video(video_id)
    if not row:
        raise HTTPException(404, f"no video {video_id}")
    return VideoOut.of(row, worker.media_url(str(request.base_url), row.get("mp4_path") or ""))


@router.get("/videos/{video_id}/recipe", response_model=RecipeOut)
async def get_recipe(video_id: str) -> RecipeOut:
    recipe = await store.get_recipe(video_id)
    if not recipe:
        raise HTTPException(404, f"no recipe for video {video_id}")
    return RecipeOut(video_id=video_id, recipe=recipe)


@router.post("/videos/{video_id}/regenerate", response_model=JobAccepted, status_code=202)
async def regenerate(
    video_id: str, body: RegenerateRequest, request: Request
) -> JobAccepted:
    """Re-run the recipe with reviewer notes applied.

    The corpus is re-selected rather than replayed from the recipe: sources age
    out of the freshness window and some are dead by now, and an ad grounded in
    a video that no longer exists fails verification for good reason. What the
    recipe pins is the creative input — company, product, lane, mode — which is
    the part a reviewer's note is actually about.

    The notes themselves travel to the worker, not to the job row: they change
    the lane brief, so the place they belong is the new video's recipe, next to
    the prompt they altered. The link back to this `video_id` rides along in the
    same recipe — `jobs` has no lineage column.
    """
    row = await store.get_video(video_id)
    if not row:
        raise HTTPException(404, f"no video {video_id}")
    recipe = await store.get_recipe(video_id) or {}
    plan = recipe.get("plan") or {}

    company_slug = row.get("company_slug")
    product = row.get("product") or (recipe.get("settings") or {}).get("product")
    if not company_slug or not product:
        raise HTTPException(409, "the stored recipe has no company/product to re-run")

    lane = body.lane or row.get("lane") or plan.get("lane")
    if lane not in BY_NAME:
        raise HTTPException(422, f"unknown lane {lane!r}. known: {', '.join(BY_NAME)}")
    mode = row.get("mode") or plan.get("mode") or "fast"

    job = await store.create_job(
        company_id=row["company_id"],
        product=product,
        lane=lane,
        mode=mode,
    )
    job_id = str(job["id"])
    worker.spawn(
        job_id,
        company_slug=company_slug,
        product=product,
        lane_name=lane,
        mode=mode,
        notes=body.notes,
        source_video_id=video_id,
    )
    return JobAccepted(
        job_id=job_id,
        poll=f"{str(request.base_url).rstrip('/')}/v1/jobs/{job_id}",
        estimated_seconds=ETA_SECONDS.get(mode, 120),
    )

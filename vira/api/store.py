"""Every read and write the REST API makes, as plain async functions.

Plain SQL, not an ORM. The queries here are shaped by what an HTTP endpoint
returns — a job with its videos, a batch with its ranked entries, a per-video
vote aggregate — and those are joins and aggregates, not object graphs. A
mapping layer would add a translation step to every one of them and hide the
`FILTER (WHERE ...)` clauses that make the aggregates cheap.

Two contracts hold everywhere in this module:

**Values are never interpolated into SQL.** Every value travels as a bind
parameter. Identifiers are literal text in the query. There is no code path
where caller input reaches the statement string.

**Rows come back JSON-ready.** uuid, Decimal and datetime are converted at the
boundary, so a route can hand a dict straight to a response without a custom
encoder. jsonb columns are already dicts and lists — the codec in db.py does
that — so they pass through untouched.
"""

from __future__ import annotations

import logging
import secrets
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Result

from vira.api.db import connection

log = logging.getLogger(__name__)

# The panel URL is the only thing standing between an anonymous judge and the
# batch, so the token is long enough that guessing it is not a strategy.
TOKEN_BYTES = 24


# -- boundary helpers ------------------------------------------------------


def _uuid(value: str | uuid.UUID) -> uuid.UUID:
    """Coerce an id at the edge so a malformed one fails here, not in the driver."""
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def _numeric(value: float | int | None) -> Decimal | None:
    """asyncpg will not encode a float into a numeric column; it wants Decimal."""
    return None if value is None else Decimal(str(value))


def _clean(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, list):
        return [_clean(v) for v in value]
    return value


def _row(result: Result) -> dict | None:
    row = result.mappings().first()
    return {k: _clean(v) for k, v in row.items()} if row else None


def _rows(result: Result) -> list[dict]:
    return [{k: _clean(v) for k, v in row.items()} for row in result.mappings()]


def _overall(breakdown: dict[str, Any] | None) -> float | None:
    """The five scoring dimensions averaged, matching models.Score.overall."""
    if not breakdown:
        return None
    vals = [v for v in breakdown.values() if isinstance(v, (int, float))]
    return round(sum(vals) / len(vals), 2) if vals else None


# -- companies -------------------------------------------------------------


async def list_companies(*, limit: int = 100) -> list[dict]:
    async with connection() as conn:
        return _rows(await conn.execute(
            text("SELECT * FROM companies ORDER BY created_at DESC LIMIT :limit"),
            {"limit": limit},
        ))


async def upsert_company(
    *,
    slug: str,
    name: str,
    bio: str = "",
    mission: str = "",
    website: str | None = None,
    category: str = "",
    owner_name: str | None = None,
) -> dict:
    """Create or refresh the local copy, keyed on slug.

    Slug, not id: this row is seeded from Lovable, whose ids belong to a
    different database. Re-seeding the same company must update it in place or
    every job it already owns would be orphaned.
    """
    async with connection() as conn:
        return _row(await conn.execute(
            text(
                """
                INSERT INTO companies (slug, name, bio, mission, website, category, owner_name)
                VALUES (:slug, :name, :bio, :mission, :website, :category, :owner_name)
                ON CONFLICT (slug) DO UPDATE SET
                    name       = EXCLUDED.name,
                    bio        = EXCLUDED.bio,
                    mission    = EXCLUDED.mission,
                    website    = EXCLUDED.website,
                    category   = EXCLUDED.category,
                    owner_name = EXCLUDED.owner_name
                RETURNING *
                """
            ),
            {
                "slug": slug, "name": name, "bio": bio, "mission": mission,
                "website": website, "category": category, "owner_name": owner_name,
            },
        ))


# -- jobs ------------------------------------------------------------------


async def create_job(
    *, company_id: str | uuid.UUID | None = None, company_slug: str | None = None,
    product: str, lane: str | None = None, mode: str = "fast",
) -> dict:
    """Queue one generation request, by company id or by slug.

    Slug is accepted because that is what the caller has: Lovable knows a
    company by slug, and the engine-local uuid is an implementation detail it
    should never have to learn. The resolution happens in the INSERT so an
    unknown slug cannot race a company being created underneath it.
    """
    params: dict[str, Any] = {"product": product, "lane": lane, "mode": mode}
    if company_id:
        sql = (
            "INSERT INTO jobs (company_id, product, lane, mode) "
            "VALUES (:company_id, :product, :lane, :mode) RETURNING *"
        )
        params["company_id"] = _uuid(company_id)
    elif company_slug:
        sql = (
            "INSERT INTO jobs (company_id, product, lane, mode) "
            "SELECT id, :product, :lane, :mode FROM companies WHERE slug = :company_slug "
            "RETURNING *"
        )
        params["company_slug"] = company_slug
    else:
        raise ValueError("create_job needs company_id or company_slug")

    async with connection() as conn:
        job = _row(await conn.execute(text(sql), params))
    if job is None:
        raise LookupError(f"no company with slug {company_slug!r} in this database")
    return job


async def update_job_status(
    job_id: str | uuid.UUID, status: str, *,
    progress_note: str | None = None, error: str | None = None,
) -> dict | None:
    """Move a job along, stamping the clock the transition implies.

    started_at is set on the first move to running and never overwritten — a
    worker that reports progress twice must not reset its own start time. The
    COALESCE does that in one statement rather than a read-then-write race.
    Passing progress_note=None leaves the existing note alone; it is not a way
    to clear it, because a nulled note reads as "no idea what is happening".
    """
    async with connection() as conn:
        return _row(await conn.execute(
            text(
                """
                UPDATE jobs SET
                    status        = :status,
                    progress_note = COALESCE(:progress_note, progress_note),
                    error         = COALESCE(:error, error),
                    started_at    = CASE WHEN :status = 'running'
                                         THEN COALESCE(started_at, now())
                                         ELSE started_at END,
                    finished_at   = CASE WHEN :status IN ('done', 'failed')
                                         THEN now() ELSE finished_at END
                WHERE id = :job_id
                RETURNING *
                """
            ),
            {
                "job_id": _uuid(job_id), "status": status,
                "progress_note": progress_note, "error": error,
            },
        ))


async def get_job(job_id: str | uuid.UUID) -> dict | None:
    """The job plus a thumbnail of each video it produced.

    Polling is the only way a caller learns a job finished, so the poll answer
    carries enough to render a result list without a second round trip.
    """
    async with connection() as conn:
        params = {"job_id": _uuid(job_id)}
        job = _row(await conn.execute(
            text("SELECT * FROM jobs WHERE id = :job_id"), params
        ))
        if not job:
            return None
        job["videos"] = _rows(await conn.execute(
            text(
                """
                SELECT id, lane, hook, score, disposition, drop_reason, mp4_path, duration_s
                FROM videos WHERE job_id = :job_id ORDER BY created_at
                """
            ),
            params,
        ))
        return job


# -- videos ----------------------------------------------------------------


async def create_video(
    *,
    job_id: str | uuid.UUID,
    recipe: dict[str, Any],
    mp4_path: str | None = None,
    duration_s: float | None = None,
    disposition: str | None = None,
    drop_reason: str | None = None,
    audio_path: str | None = None,
) -> dict:
    """Persist one finished video and everything that explains it, atomically.

    `recipe` is the dict `vira.provenance.Recorder.finish` writes to
    recipe.json. A video without its prompts, its corpus and its settings is
    not reproducible, so the four tables are written in one transaction: either
    the whole explanation lands or none of it does.

    The score's `overall` is derived rather than stored upstream — Score
    computes it as a property — so it is recomputed here from the breakdown.
    """
    output = recipe.get("output") or {}
    breakdown = recipe.get("score") or {}
    notes = recipe.get("notes") or {}

    async with connection() as conn:
        video = _row(await conn.execute(
            text(
                """
                INSERT INTO videos (
                    job_id, lane, hook, cta, caption, hashtags, duration_s,
                    mp4_path, score, score_breakdown, disposition, drop_reason
                ) VALUES (
                    :job_id, :lane, :hook, :cta, :caption, :hashtags, :duration_s,
                    :mp4_path, :score, :score_breakdown, :disposition, :drop_reason
                )
                RETURNING *
                """
            ),
            {
                "job_id": _uuid(job_id),
                "lane": notes.get("lane") or "",
                "hook": output.get("hook") or "",
                "cta": output.get("cta") or "",
                "caption": output.get("caption") or "",
                "hashtags": list(output.get("hashtags") or []),
                "duration_s": _numeric(duration_s),
                "mp4_path": mp4_path,
                "score": _numeric(_overall(breakdown)),
                "score_breakdown": breakdown,
                "disposition": disposition,
                "drop_reason": drop_reason,
            },
        ))
        video_id = _uuid(video["id"])

        # `notes` is the whole authored-intent layer, not just notes["plan"]:
        # the critique, the lane brief and the look are equally what a human
        # edits before a re-run, and dropping them would make the recipe a
        # partial record. `settings` absorbs the fields that decide whether a
        # re-run reproduces this video or merely something like it.
        await conn.execute(
            text(
                """
                INSERT INTO recipes (video_id, plan, settings, corpus, beats)
                VALUES (:video_id, :plan, :settings, :corpus, :beats)
                ON CONFLICT (video_id) DO UPDATE SET
                    plan = EXCLUDED.plan, settings = EXCLUDED.settings,
                    corpus = EXCLUDED.corpus, beats = EXCLUDED.beats
                """
            ),
            {
                "video_id": video_id,
                "plan": notes,
                "settings": {
                    **(recipe.get("settings") or {}),
                    "voice_id": recipe.get("voice_id"),
                    "git_commit": recipe.get("git_commit"),
                    "generated_at": recipe.get("generated_at"),
                    "product": recipe.get("product"),
                },
                "corpus": recipe.get("corpus") or [],
                "beats": output.get("beats") or [],
            },
        )

        calls = [
            {
                "video_id": video_id,
                "n": call.get("n") or i + 1,
                "stage": call.get("stage") or "",
                "model": call.get("model") or "",
                "max_tokens": call.get("max_tokens"),
                "stop_reason": call.get("stop_reason"),
                "system_prompt": call.get("system_prompt") or "",
                "user_prompt": call.get("user_prompt") or "",
                "response": call.get("response") or "",
            }
            for i, call in enumerate(recipe.get("llm_calls") or [])
        ]
        if calls:
            await conn.execute(
                text(
                    """
                    INSERT INTO llm_calls (
                        video_id, n, stage, model, max_tokens, stop_reason,
                        system_prompt, user_prompt, response
                    ) VALUES (
                        :video_id, :n, :stage, :model, :max_tokens, :stop_reason,
                        :system_prompt, :user_prompt, :response
                    )
                    """
                ),
                calls,
            )

        # Shot records come from either generator, and the two disagree on key
        # names: imagegen writes `prompt`, stock writes `query`. Both mean "what
        # was asked for", which is the column.
        rows = [
            {
                "video_id": video_id,
                "beat_index": i,
                "kind": "image",
                "path": shot.get("path") or shot.get("file"),
                "prompt": shot.get("prompt") or shot.get("query"),
                "credit": shot.get("credit"),
                "description": shot.get("description"),
            }
            for i, shot in enumerate(recipe.get("stock") or [])
        ]
        if audio_path:
            rows.append({
                "video_id": video_id, "beat_index": None, "kind": "audio",
                "path": audio_path, "prompt": output.get("narration"),
                "credit": recipe.get("voice_id"), "description": None,
            })
        if rows:
            await conn.execute(
                text(
                    """
                    INSERT INTO assets (video_id, beat_index, kind, path, prompt, credit, description)
                    VALUES (:video_id, :beat_index, :kind, :path, :prompt, :credit, :description)
                    """
                ),
                rows,
            )

    return video


async def get_video(video_id: str | uuid.UUID) -> dict | None:
    async with connection() as conn:
        params = {"video_id": _uuid(video_id)}
        video = _row(await conn.execute(
            text(
                """
                SELECT v.*, j.product, j.mode, j.company_id, c.slug AS company_slug,
                       c.name AS company_name
                FROM videos v
                JOIN jobs j ON j.id = v.job_id
                JOIN companies c ON c.id = j.company_id
                WHERE v.id = :video_id
                """
            ),
            params,
        ))
        if not video:
            return None
        video["assets"] = _rows(await conn.execute(
            text(
                "SELECT id, beat_index, kind, path, prompt, credit, description "
                "FROM assets WHERE video_id = :video_id "
                "ORDER BY kind, beat_index NULLS FIRST"
            ),
            params,
        ))
        return video


async def get_recipe(video_id: str | uuid.UUID) -> dict | None:
    """The full tweakable record, prompts included.

    Verbatim prompts are the point of this endpoint, so they are returned whole
    — this is the one read in the API that is deliberately large.
    """
    async with connection() as conn:
        params = {"video_id": _uuid(video_id)}
        recipe = _row(await conn.execute(
            text("SELECT * FROM recipes WHERE video_id = :video_id"), params
        ))
        if not recipe:
            return None
        recipe["llm_calls"] = _rows(await conn.execute(
            text(
                "SELECT n, stage, model, max_tokens, stop_reason, "
                "system_prompt, user_prompt, response "
                "FROM llm_calls WHERE video_id = :video_id ORDER BY n"
            ),
            params,
        ))
        recipe["assets"] = _rows(await conn.execute(
            text(
                "SELECT beat_index, kind, path, prompt, credit, description "
                "FROM assets WHERE video_id = :video_id "
                "ORDER BY kind, beat_index NULLS FIRST"
            ),
            params,
        ))
        return recipe


async def list_videos_for_company(
    company: str | uuid.UUID, *, limit: int = 50, offset: int = 0,
) -> list[dict]:
    """Newest first, dropped ones included — the rejections are part of the story.

    Takes an id or a slug, for the same reason create_job does.
    """
    # `where` is one of two literals chosen here — the caller's value never
    # reaches the statement string, only the bind parameter.
    try:
        where, params = "j.company_id = :company", {"company": _uuid(company)}
    except ValueError:
        where, params = "c.slug = :company", {"company": str(company)}

    async with connection() as conn:
        return _rows(await conn.execute(
            text(
                f"""
                SELECT v.id, v.lane, v.hook, v.caption, v.score, v.disposition,
                       v.drop_reason, v.mp4_path, v.duration_s, v.created_at,
                       v.job_id, j.product, c.slug AS company_slug
                FROM videos v
                JOIN jobs j ON j.id = v.job_id
                JOIN companies c ON c.id = j.company_id
                WHERE {where}
                ORDER BY v.created_at DESC
                LIMIT :limit OFFSET :offset
                """
            ),
            {**params, "limit": limit, "offset": offset},
        ))


# -- human review ----------------------------------------------------------


async def create_review_batch(
    *, title: str, video_ids: list[str | uuid.UUID], public_token: str | None = None,
) -> dict:
    """A batch plus its ordered entries, in one transaction.

    Order is taken from `video_ids` as given. Callers that want to defeat
    position bias should shuffle before calling — every judge must see the same
    sequence, so it cannot be randomised per view.
    """
    token = public_token or secrets.token_urlsafe(TOKEN_BYTES)
    async with connection() as conn:
        batch = _row(await conn.execute(
            text(
                "INSERT INTO review_batches (public_token, title) "
                "VALUES (:public_token, :title) RETURNING *"
            ),
            {"public_token": token, "title": title},
        ))
        entries = [
            {"batch_id": _uuid(batch["id"]), "video_id": _uuid(vid), "position": i}
            for i, vid in enumerate(video_ids)
        ]
        if entries:
            await conn.execute(
                text(
                    "INSERT INTO review_batch_videos (batch_id, video_id, position) "
                    "VALUES (:batch_id, :video_id, :position) "
                    "ON CONFLICT (batch_id, video_id) DO UPDATE SET position = EXCLUDED.position"
                ),
                entries,
            )
    batch["video_count"] = len(entries)
    return batch


async def get_batch_with_videos(
    public_token: str | None = None, *, batch_id: str | uuid.UUID | None = None,
) -> dict | None:
    """Look up by id (internal) or by token (what a judge's link carries).

    Two statements rather than one with an OR: a NULL bind parameter has no
    type Postgres can infer, and a single query would have to cast its way out
    of that for no gain.
    """
    if batch_id:
        lookup = "SELECT * FROM review_batches WHERE id = :batch_id"
        params: dict[str, Any] = {"batch_id": _uuid(batch_id)}
    elif public_token:
        lookup = "SELECT * FROM review_batches WHERE public_token = :public_token"
        params = {"public_token": public_token}
    else:
        raise ValueError("get_batch_with_videos needs batch_id or public_token")

    async with connection() as conn:
        batch = _row(await conn.execute(text(lookup), params))
        if not batch:
            return None
        batch["videos"] = _rows(await conn.execute(
            text(
                """
                SELECT bv.position, v.id, v.lane, v.hook, v.caption, v.hashtags,
                       v.mp4_path, v.duration_s
                FROM review_batch_videos bv
                JOIN videos v ON v.id = bv.video_id
                WHERE bv.batch_id = :batch_id
                ORDER BY bv.position
                """
            ),
            {"batch_id": _uuid(batch["id"])},
        ))
        return batch


async def record_vote(
    *,
    batch_id: str | uuid.UUID,
    video_id: str | uuid.UUID,
    reviewer_ref: str,
    rating: int | None = None,
    picked: bool = False,
    comment: str | None = None,
) -> dict:
    """One vote per judge per video; a resubmission replaces the earlier one.

    Panel platforms retry, and a double-counted 5 would bias the only signal
    this whole feature exists to collect.
    """
    async with connection() as conn:
        return _row(await conn.execute(
            text(
                """
                INSERT INTO review_votes (batch_id, video_id, reviewer_ref, rating, picked, comment)
                VALUES (:batch_id, :video_id, :reviewer_ref, :rating, :picked, :comment)
                ON CONFLICT (batch_id, video_id, reviewer_ref) DO UPDATE SET
                    rating     = EXCLUDED.rating,
                    picked     = EXCLUDED.picked,
                    comment    = EXCLUDED.comment,
                    created_at = now()
                RETURNING *
                """
            ),
            {
                "batch_id": _uuid(batch_id), "video_id": _uuid(video_id),
                "reviewer_ref": reviewer_ref, "rating": rating,
                "picked": picked, "comment": comment,
            },
        ))


async def batch_results(batch_id: str | uuid.UUID) -> list[dict]:
    """Per-video aggregate for the batch, in presentation order.

    LEFT JOIN, so a video nobody voted on still appears with zeros — an
    unrated lane is a finding, and dropping the row would hide it. The FILTER
    clauses keep picks and comments on the same single pass as the average.
    """
    async with connection() as conn:
        return _rows(await conn.execute(
            text(
                """
                SELECT v.id AS video_id, v.lane, v.hook, v.score AS engine_score,
                       bv.position,
                       count(rv.id) AS votes,
                       round(avg(rv.rating), 2) AS avg_rating,
                       count(*) FILTER (WHERE rv.picked) AS picks,
                       coalesce(
                           array_agg(rv.comment) FILTER (
                               WHERE rv.comment IS NOT NULL AND rv.comment <> ''
                           ),
                           '{}'
                       ) AS comments
                FROM review_batch_videos bv
                JOIN videos v ON v.id = bv.video_id
                LEFT JOIN review_votes rv
                       ON rv.video_id = bv.video_id AND rv.batch_id = bv.batch_id
                WHERE bv.batch_id = :batch_id
                GROUP BY v.id, v.lane, v.hook, v.score, bv.position
                ORDER BY bv.position
                """
            ),
            {"batch_id": _uuid(batch_id)},
        ))

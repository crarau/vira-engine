"""Run the existing pipeline for one API job, off the request path.

A video takes 74s deterministic and ~350s with the crew. No HTTP client waits
that long, so the API's job is to accept the request, hand it to this module,
and let the caller poll. Everything here is the same code `variants.py` and
`agentic_video.py` run from the CLI — the pipeline is not reimplemented, only
re-driven, so a fix to `select`/`remix`/`render` reaches the API for free.

What this module adds on top of the CLI entry points:

  - **Progress that a UI can render.** Each stage writes a human sentence to the
    job row. "verifying 18 sources" is the difference between a spinner that
    looks alive and one that looks hung for six minutes.
  - **Per-job asset namespacing.** Two concurrent jobs would otherwise both
    write `public/shots/shot00.jpg` and render each other's frames. Shots go to
    `public/shots/<job_id>/`, narration to `public/narration-<job_id>.mp3`.
  - **A bound on the machine.** Renders are CPU-bound; N concurrent requests
    must not become N concurrent Remotion invocations.
  - **A recipe and a live feed off the same calls.** The whole generation runs
    inside a `Recorder` — so `GET /v1/videos/{id}/recipe` carries every prompt
    verbatim — and inside `events.watching`, which is what lets `vira.llm`
    publish those same prompts to `?level=debug` while the run is happening.

The score and its disposition are computed here, after the creative work, in
Python — same as the CLI. Nothing in the API can lower the evidence floor.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

from vira.agentic.crew import Production, direct
from vira.analyze import analyze_corpus
from vira.api import events, store
from vira.config import settings
from vira.director import critique, plan as make_plan, revise
from vira.lanes import Lane, get as get_lane
from vira.models import Company, CorpusAnalysis, Remix, Trend
from vira.provenance import Recorder
from vira.remix import build_remix
from vira.render import VIDEO_DIR, build_props, render, write_props
from vira.score import disposition, score_remix
from vira.select import shortlist
from vira.shots import fetch_or_generate
from vira.supa import Supa, get_company
from vira.verify import verify_all
from vira.voice import synthesize

log = logging.getLogger(__name__)

# Rendered mp4s live under out/ and are served from there by the static mount,
# so the API and the CLI write to the same tree and a CLI-made video is
# reachable over HTTP without a copy.
OUT_DIR = Path(os.environ.get("VIRA_OUT_DIR", "out")).resolve()

# Two renders at four workers each, per variants.py — sized for an 11-core
# laptop. On the 32-core box, raise VIRA_RENDER_PARALLEL rather than editing.
RENDER_PARALLEL = int(os.environ.get("VIRA_RENDER_PARALLEL", "2"))
RENDER_CONCURRENCY = int(os.environ.get("VIRA_RENDER_CONCURRENCY", "4"))
# Generation is mostly network-bound, so this ceiling exists to protect the
# metered image and TTS APIs, not the CPU. The render semaphore protects the CPU.
MAX_ACTIVE_JOBS = int(os.environ.get("VIRA_MAX_ACTIVE_JOBS", "4"))

_render_slots = asyncio.Semaphore(RENDER_PARALLEL)
_job_slots = asyncio.Semaphore(MAX_ACTIVE_JOBS)

# asyncio only holds a weak reference to a running task; without this the
# garbage collector can cancel a generation mid-render.
_running: set[asyncio.Task[None]] = set()


class JobFailed(RuntimeError):
    """A generation that stopped for a reason worth showing the caller."""


def media_url(base_url: str, media_path: str) -> str:
    """Absolute URL for a rendered file, for a frontend on another origin."""
    return f"{base_url.rstrip('/')}/media/{media_path.lstrip('/')}"


def spawn(job_id: str, **kwargs: Any) -> None:
    """Start a generation in the background and return immediately."""
    # Published before the task is created, so the moment POST /v1/videos
    # answers, this process is already the one that owns the job's event stream
    # — a client that connects immediately finds a live feed, not an empty one.
    events.publish(job_id, "queued", "job accepted", data={
        k: v for k, v in kwargs.items() if k in ("company_slug", "product", "lane_name", "mode")
    })
    task = asyncio.create_task(run_job(job_id, **kwargs))
    _running.add(task)
    task.add_done_callback(_running.discard)


async def resolve_company(slug: str) -> dict | None:
    """The local company row for a slug, seeded from Lovable Cloud on first use.

    Jobs are keyed on the local `companies` table, but the engine's `select`
    stage reads companies from Lovable Cloud — that is where the category join
    lives and it is the source of truth. Rather than make a frontend register
    the same company in two places, the first job for a slug copies the Lovable
    row down. A linear scan is fine at this scale and avoids asking the store
    for a lookup it does not expose.
    """
    for row in await store.list_companies(limit=500):
        if row.get("slug") == slug:
            return row

    remote = await get_company(Supa(), slug)
    if not remote:
        return None
    company = Company.from_row(remote)
    return await store.upsert_company(
        slug=company.slug, name=company.name, bio=company.bio,
        mission=company.mission, website=company.website,
        category=company.category, owner_name=remote.get("owner_name"),
    )


def steer(lane: Lane, notes: list[str] | None) -> Lane:
    """Fold reviewer notes into the lane brief.

    Regeneration does not patch the previous script — it re-runs the lane with
    the notes as additional creative direction. A rewrite of a weak script tends
    to keep the weak structure; a re-run with the objection stated up front does
    not.
    """
    if not notes:
        return lane
    joined = "\n".join(f"- {n}" for n in notes if n.strip())
    if not joined:
        return lane
    return replace(
        lane,
        brief=(
            f"{lane.brief}\n\nREVISION NOTES — these came from human reviewers "
            f"who watched the previous cut. Address them:\n{joined}"
        ),
    )


def _new_out_dir(slug: str, lane_name: str, mode: str) -> Path:
    """Versioned output dir, same convention as the CLI. Nothing is overwritten."""
    root = OUT_DIR / slug
    root.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    n = len([d for d in root.glob("v*-*") if d.is_dir()]) + 1
    suffix = "-agentic" if mode == "agentic" else ""
    out_dir = root / f"v{n:03d}-{stamp}{suffix}" / lane_name
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


def _relocate_shots(shots: list[dict], src: Path, dest: Path) -> list[dict]:
    """Move crew-generated frames into this job's slot under public/shots.

    The crew writes to `<public_dir>/shots/shotNN.jpg` with the name hardcoded,
    so a per-job public dir is the only way to keep two concurrent crews apart
    during the loop. Remotion resolves images as `staticFile("shots/" + image)`,
    which means the frames have to end up back under public/shots afterwards.
    """
    dest.mkdir(parents=True, exist_ok=True)
    for shot in shots:
        name = shot.get("file")
        if name and (src / name).exists():
            shutil.move(str(src / name), str(dest / name))
    shutil.rmtree(src.parent, ignore_errors=True)
    return shots


async def _note(job_id: str, note: str, stage: str = "crew", **data: Any) -> None:
    """One stage transition: the job row for pollers, the bus for watchers.

    Both, not one: the row survives a restart and every worker can read it, and
    the bus carries the resolution a UI needs. `stage` defaults so the signature
    stays backwards compatible with a call that has nothing better to say.
    """
    log.info("[%s] %s", job_id, note)
    # Also the point at which the ambient stage moves, so a prompt published
    # from four frames down the stack knows it belongs to "write" and not to
    # whatever ran last.
    events.set_stage(stage)
    events.publish(job_id, stage, note, data=data)
    await store.update_job_status(job_id, "running", progress_note=note)


async def _fast(
    job_id: str, rec: Recorder, company: Company, product: str,
    picked: list[Trend], corpus: CorpusAnalysis, lane: Lane,
    out_dir: Path, shots_dir: Path,
) -> tuple[Remix, list[dict], Path, float]:
    """plan → write → critique → revise → (voice ‖ imagery). ~74s."""
    steered = Company(**{
        **company.model_dump(),
        "mission": f"{company.mission}\n\nCREATIVE DIRECTION FOR THIS AD: {lane.brief}",
    })

    await _note(job_id, f"planning the {lane.name} cut", "plan", lane=lane.name)
    vp = await make_plan(company, product, lane.brief, corpus)
    rec.note("plan", vp.model_dump())

    await _note(job_id, f"writing {vp.beat_count} beats over {vp.target_seconds}s",
                "write", beats=vp.beat_count, target_s=vp.target_seconds)
    remix = await build_remix(steered, product, picked, corpus, vp)

    await _note(job_id, "hostile first viewer reading it back", "critique")
    crit = await critique(remix, vp)
    rec.note("critique", crit.model_dump())
    if crit.notes:
        remix = await revise(remix, crit, picked)

    # Voice and imagery both need the script and neither needs the other.
    await _note(job_id, "recording narration and generating frames", "voice")
    (mp3, duration), shots = await asyncio.gather(
        synthesize(remix, out_dir, lane),
        fetch_or_generate(company, product, remix, shots_dir, lane.look),
    )
    return remix, shots, mp3, duration


async def _agentic(
    job_id: str, rec: Recorder, company: Company, product: str,
    picked: list[Trend], corpus: CorpusAnalysis, lane: Lane,
    out_dir: Path, shots_dir: Path,
) -> tuple[Remix, list[dict], Path, float]:
    """Director plans, delegates, inspects what came back, and fixes it. ~350s."""
    await _note(job_id, "director is planning the film", "plan")
    crew_public = VIDEO_DIR / "public" / "jobs" / job_id
    (crew_public / "shots").mkdir(parents=True, exist_ok=True)

    prod = Production(
        company=company, product=product, lane=lane, corpus=corpus,
        trends=picked, out_dir=out_dir, public_dir=crew_public,
        # The crew's running commentary is the most interesting thing this
        # service produces while a caller waits, and until now it only reached
        # the log. `crew_sink` cannot raise, so the Director loop is unaffected.
        on_event=events.crew_sink(job_id),
    )
    closing = await direct(prod)
    rec.note("director_closing", closing)
    rec.note("crew_log", prod.log)
    rec.note("image_calls", prod.image_calls)

    if not prod.remix:
        raise JobFailed("the crew never produced a script")
    if not prod.mp3:
        raise JobFailed("the crew never synthesized narration")
    if not any(s.get("file") for s in prod.shots):
        # Rendering would 404 on every image and fail after a minute of retries.
        raise JobFailed("the crew never produced frames — nothing to render")

    shots = _relocate_shots(prod.shots, crew_public / "shots", shots_dir)
    return prod.remix, shots, prod.mp3, prod.duration


async def _produce(
    job_id: str, company_slug: str, product: str, lane: Lane, mode: str,
    notes: list[str] | None = None, source_video_id: str | None = None,
) -> dict:
    t0 = time.monotonic()

    await _note(job_id, "selecting candidate trends", "select")
    supa = Supa()
    row = await get_company(supa, company_slug)
    if not row:
        raise JobFailed(f"no company with slug {company_slug!r}")
    company = Company.from_row(row)

    out_dir = _new_out_dir(company_slug, lane.name, mode)
    public = VIDEO_DIR / "public"
    shots_dir = public / "shots" / job_id

    # The Recorder opens here, before selection, rather than after the corpus
    # analysis: `analyze_corpus` is a model call, its prompt carries the whole
    # corpus, and it decides what the writer is told the category rewards. A
    # recipe that skipped it recorded the ad's second cause and not its first.
    # (The CLI cannot do this — `variants.py` analyses once and shares the
    # result across five recipes — but one API job is exactly one video.)
    async with Recorder(out_dir) as rec:
        rec.note("job_id", job_id)
        rec.note("mode", mode)
        rec.note("lane", lane.name)
        rec.note("lane_brief", lane.brief)
        rec.note("voice", lane.voice_note)
        rec.note("look", lane.look)
        # Lineage lives in the recipe, not on the job row: `jobs` has no column
        # for it, and the recipe is where a reader asking "why does this cut
        # differ from the last one" is already looking. The store lands `notes`
        # in recipes.plan, so both fields survive.
        rec.note("revision_notes", notes or [])
        rec.note("regenerated_from_video_id", source_video_id)

        picked, rejected = await shortlist(supa, company, product)
        await _note(job_id, f"verifying {len(picked)} source URLs", "verify",
                    sources=len(picked))
        picked, dead = await verify_all(picked)
        if not picked:
            raise JobFailed("nothing survived selection — no verified sources to build on")

        await _note(job_id, f"analysing {len(picked)} verified sources", "analyze",
                    verified=len(picked), dead=len(dead))
        corpus = await analyze_corpus(company, product, picked)
        rec.note("rejected_at_selection", rejected)
        rec.note("dead_urls", len(dead))

        build = _agentic if mode == "agentic" else _fast
        remix, shots, mp3, duration = await build(
            job_id, rec, company, product, picked, corpus, lane, out_dir, shots_dir
        )

        # Deterministic, after the creative work, out of any agent's reach.
        await _note(job_id, "scoring against the cited sources", "score")
        score = await score_remix(company, product, remix, picked)
        dispo, reason = disposition(score)

        s = settings()
        recipe_path = rec.finish(
            company=company, product=product, remix=remix, score=score,
            shots=shots, sources=picked, voice_id=lane.voice_id,
            settings_snapshot={
                "mode": mode,
                "llm_model": s.agent_model,
                "agent_model": s.agent_model if mode == "agentic" else None,
                "image_model": s.image_model,
                "max_age_days": s.max_age_days,
                "shortlist_size": s.shortlist_size,
                "max_per_format": s.max_per_format,
                "english_only": s.english_only,
                "surface_threshold": s.surface_threshold,
                "watchlist_threshold": s.watchlist_threshold,
                "evidence_floor": s.evidence_floor,
            },
        )
    recipe: dict[str, Any] = json.loads(recipe_path.read_text())

    # Namespace the assets only now — the recipe records the frames by their
    # own names, the prefix is a Remotion path detail.
    audio_name = f"narration-{job_id}.mp3"
    shutil.copy(mp3, public / audio_name)
    for shot in shots:
        if shot.get("file"):
            shot["file"] = f"{job_id}/{shot['file']}"

    props = build_props(
        company, product, remix, audio_path=public / audio_name,
        duration_s=duration + 2.4, shots=shots,
    )
    write_props(props, out_dir)

    mp4 = out_dir / f"{company_slug}-{lane.name}.mp4"
    await _note(job_id, "rendering", "render")
    async with _render_slots:
        await asyncio.to_thread(
            render, out_dir / "props.json", mp4, concurrency=RENDER_CONCURRENCY
        )

    elapsed = time.monotonic() - t0
    video = await store.create_video(
        job_id=job_id,
        recipe=recipe,
        # Relative to out/ — the API turns this into a /media URL. An absolute
        # path would break the moment the service is redeployed somewhere else.
        mp4_path=str(mp4.relative_to(OUT_DIR)),
        duration_s=round(duration, 2),
        disposition=dispo,
        drop_reason=reason,
        audio_path=audio_name,
    )
    log.info("[%s] done in %.0fs → %s", job_id, elapsed, mp4)
    return video


async def run_job(
    job_id: str, *, company_slug: str, product: str, lane_name: str, mode: str,
    notes: list[str] | None = None, source_video_id: str | None = None,
) -> None:
    """One job, start to finish. Never raises — failure lands on the job row.

    The whole run is bound to the job id here, not inside `_produce`: a failure
    published from the `except` clauses below is as much part of this job's feed
    as a progress note, and so is anything a cleanup path decides to say.
    """
    async with _job_slots:
        with events.watching(job_id):
            try:
                lane = steer(get_lane(lane_name), notes)
            except KeyError:
                await _fail(job_id, f"unknown lane {lane_name!r}")
                return
            try:
                video = await _produce(
                    job_id, company_slug, product, lane, mode, notes, source_video_id
                )
                # The job row carries no video id; get_job joins the videos it
                # produced. Nothing to write back here beyond the terminal state.
                hook = str(video.get("hook") or "")
                await store.update_job_status(
                    job_id, "done", progress_note=f"done · {hook[:80]}"
                )
                # The terminal event closes every open stream, and carries the
                # id a watcher needs to fetch the video without a second round
                # trip.
                events.publish(job_id, "done", f"done · {hook[:80]}", data={
                    "video_id": str(video.get("id") or ""),
                    "hook": hook,
                    "mp4_path": video.get("mp4_path"),
                    "score": video.get("score"),
                    "disposition": video.get("disposition"),
                })
            except JobFailed as exc:
                log.warning("[%s] %s", job_id, exc)
                await _fail(job_id, str(exc))
            except Exception as exc:  # noqa: BLE001 - a dead job must still be reportable
                log.exception("[%s] generation crashed", job_id)
                await _fail(job_id, f"{type(exc).__name__}: {exc}")


async def _fail(job_id: str, error: str) -> None:
    """Terminal failure, on the row and on the stream. Never leave a stream open."""
    await store.update_job_status(job_id, "failed", progress_note="failed", error=error)
    events.publish(job_id, "failed", error, level="error", data={"error": error})

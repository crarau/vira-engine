"""Jobs — the poll target for work that outlives its request, and its live feed.

Three ways to watch the same job, in descending order of how good they are:

  `GET /v1/jobs/{id}/stream`  Server-Sent Events. Every trace line the pipeline
                              produces, as it produces it, ending in a terminal
                              event that carries the video id or the error.
  `GET /v1/jobs/{id}/events`  The same events as JSON, for a client that cannot
                              hold a connection open. Falls back to the job row
                              when this process is not the one doing the work.
  `GET /v1/jobs/{id}`         Status and the latest sentence. What a two-line
                              client needs and nothing more.

SSE rather than WebSocket because progress is one-directional: the client has
nothing to say back. That buys a protocol every proxy already understands, an
automatic reconnect with resumption built into the browser, and `EventSource` on
the client instead of a socket lifecycle.

`progress_note` is still written by the worker as a human sentence rather than a
stage enum, and so is `Event.message`, so a UI can render "verifying 18 source
URLs" without shipping a translation table that falls out of date the next time
a stage is added. `Event.stage` exists for the UI that wants to do more than
print, and is documented as a growing vocabulary for exactly that reason.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import replace
from typing import Any, AsyncIterator, Literal

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from vira.api import events, store
from vira.api.schemas import JobOut

log = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["jobs"])

# Cloudflare's tunnel drops an idle connection at 100s and most reverse proxies
# are stricter, so the stream has to say something well inside that even when
# the pipeline is quiet — a Remotion render is two silent minutes.
HEARTBEAT_S = 15.0

# How long a browser waits before reconnecting on its own. It resumes from
# Last-Event-ID, so a reconnect is cheap and there is no reason to make it slow.
RETRY_MS = 3000

# When this process is not the one running the job, the job row is the only
# shared surface. Two seconds is well under the rate at which stages change.
DB_POLL_S = 2.0

# A stream is closed rather than held forever. The browser reconnects with
# Last-Event-ID and loses nothing; an abandoned tab stops costing a connection.
STREAM_MAX_S = 900.0

_TERMINAL_STATUS = {"done": "done", "failed": "failed"}


# The event wire types live here rather than in schemas.py because they are the
# contract of these two endpoints alone, and schemas.py is the shared one.


class EventOut(BaseModel):
    seq: int
    ts: str
    job_id: str
    stage: str
    message: str
    level: str = "info"
    data: dict[str, Any] = Field(default_factory=dict)


class EventsOut(BaseModel):
    """A window onto a job's trace, plus enough to ask for the next window.

    `source` is not decoration. "database" means this process is not running the
    job and the client is seeing the coarse job row rather than the crew's own
    words — see the multi-worker note in docs/API.md.
    """

    job_id: str
    source: Literal["memory", "database"]
    status: str
    complete: bool
    next_after: int
    events: list[EventOut] = Field(default_factory=list)


@router.get("/jobs/{job_id}", response_model=JobOut)
async def get_job(job_id: str) -> JobOut:
    row = await store.get_job(job_id)
    if not row:
        raise HTTPException(404, f"no job {job_id}")
    return JobOut.of(row)


@router.get("/jobs/{job_id}/events", response_model=EventsOut)
async def get_job_events(
    job_id: str,
    after: int = Query(0, ge=0, description="return only events with seq greater than this"),
    level: str = Query(
        events.DEFAULT_LEVEL,
        description="minimum level: debug · info · warn · error. debug carries "
                    "whole prompts and is excluded unless asked for.",
    ),
) -> EventsOut:
    """The buffered trace as JSON, for a client that cannot use SSE.

    Correct on every worker, which the stream cannot promise: if this process
    holds the job's events it returns them, and if it does not it reads the job
    row every worker shares and synthesises one event from it. A client polling
    this always learns that the job finished and what it produced; on the wrong
    worker it learns it in one sentence instead of thirty.

    `?level=debug` adds the verbatim prompt of every model call. It is opt-in
    because those payloads are kilobytes each and a normal poller wants a trace,
    not a transcript. Sequence numbers are shared across levels, so a client
    that switches level mid-run sees a gap in `seq` rather than renumbered
    events — the gap is the events it chose not to receive.
    """
    row = await store.get_job(job_id)
    if not row:
        raise HTTPException(404, f"no job {job_id}")
    status = str(row.get("status") or "queued")

    if events.bus.known(job_id):
        buffered = events.bus.history(
            job_id, after_seq=after or None, level=events.normalise_level(level)
        )
        last = buffered[-1].seq if buffered else after
        return EventsOut(
            job_id=job_id,
            source="memory",
            status=status,
            complete=events.bus.closed(job_id),
            next_after=last,
            events=[EventOut(**e.as_dict()) for e in buffered],
        )

    # Degraded mode: another worker owns the job, so the shared job row is all
    # there is. It holds one mutable sentence and no history, which cannot be
    # paged — so this returns a snapshot rather than a log, and reuses the same
    # `seq` (1 while running, 2 once terminal) every time. A client that keys
    # events by seq therefore replaces the line instead of appending a
    # duplicate, which is the correct rendering of a status field.
    synthetic = _from_job_row(job_id, row)
    return EventsOut(
        job_id=job_id,
        source="database",
        status=status,
        complete=synthetic.stage in events.TERMINAL_STAGES,
        next_after=synthetic.seq,
        events=[EventOut(**synthetic.as_dict())],
    )


@router.get("/jobs/{job_id}/stream")
async def stream_job(
    job_id: str,
    request: Request,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    after: int | None = Query(
        default=None,
        description="resume point for clients that cannot set Last-Event-ID",
    ),
    level: str = Query(
        events.DEFAULT_LEVEL,
        description="minimum level: debug · info · warn · error. debug is the "
                    "verbose feed — every prompt, verbatim.",
    ),
) -> StreamingResponse:
    """Live trace over Server-Sent Events, ending in `done` or `failed`.

    Resumption is the browser's job and it does it for free: every frame carries
    `id: <seq>`, and on an automatic reconnect `EventSource` sends the last one
    back as `Last-Event-ID`. `?after=` is the same thing for a client that is
    not a browser, since `EventSource` cannot set a header.

    `?level=debug` opens the verbose feed. It is a different subscription rather
    than a client-side filter, so a watcher on the default feed never pays for
    the prompts — and a verbose watcher gets the replay at its own level too,
    which is what makes turning it on mid-run show the calls already made.
    """
    row = await store.get_job(job_id)
    if not row:
        raise HTTPException(404, f"no job {job_id}")

    resume = events.parse_last_event_id(last_event_id)
    if resume is None and after:
        resume = after

    return StreamingResponse(
        _frames(job_id, row, resume, events.normalise_level(level), request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            # nginx buffers a proxied response by default, which turns a live
            # feed into one delivery at the end. Ignored by proxies that do not
            # know it, harmless where it is not needed.
            "X-Accel-Buffering": "no",
        },
    )


# -- frame production ------------------------------------------------------


def _from_job_row(job_id: str, row: dict[str, Any]) -> events.Event:
    """One event standing in for a whole trace, built from the shared job row.

    The sequence is deliberately 1 for a live job and 2 once it is terminal, so
    a polling client can tell "still going" from "finished" with the same
    `after` cursor it uses against the real buffer.
    """
    status = str(row.get("status") or "queued")
    stage = _TERMINAL_STATUS.get(status, "crew" if status == "running" else "queued")
    videos = row.get("videos") or []
    data: dict[str, Any] = {"source": "database", "status": status}
    if stage == "done" and videos:
        # Same keys the live terminal event carries, so a client needs one
        # branch for both modes rather than one per source.
        first = videos[0]
        data["video_id"] = str(first["id"])
        data["hook"] = first.get("hook") or ""
        data["mp4_path"] = first.get("mp4_path")
        data["score"] = first.get("score")
        data["disposition"] = first.get("disposition")
    if row.get("error"):
        data["error"] = row["error"]
    # On a failure the row's note is the word "failed" and the reason is in
    # `error`. The reason is the message a person needs to read.
    message = row.get("error") if stage == "failed" else row.get("progress_note")
    return events.Event(
        seq=2 if stage in events.TERMINAL_STAGES else 1,
        ts=str(row.get("finished_at") or row.get("started_at") or row.get("created_at") or ""),
        job_id=job_id,
        stage=stage,
        message=str(message or row.get("progress_note") or status),
        level="error" if stage == "failed" else "info",
        data=data,
    )


async def _frames(
    job_id: str, row: dict[str, Any], resume: int | None, level: str, request: Request
) -> AsyncIterator[str]:
    """The byte stream. Live from the bus when this process owns the job, polled
    from the job row when it does not."""
    yield f"retry: {RETRY_MS}\n: watching {job_id} at {level}\n\n"
    try:
        if events.bus.known(job_id):
            async for frame in _live(job_id, resume, level, request):
                yield frame
        else:
            async for frame in _polled(job_id, row, resume, request):
                yield frame
    except asyncio.CancelledError:
        # The client went away mid-render. Normal, and not worth a traceback.
        raise
    except Exception:  # noqa: BLE001 - a broken feed must not look like a crashed job
        log.exception("[%s] event stream failed", job_id)
        yield ": stream error — reconnect\n\n"


async def _live(
    job_id: str, resume: int | None, level: str, request: Request
) -> AsyncIterator[str]:
    deadline = time.monotonic() + STREAM_MAX_S
    async with events.bus.subscribe(job_id, after_seq=resume, level=level) as q:
        while time.monotonic() < deadline:
            try:
                event = await asyncio.wait_for(q.get(), timeout=HEARTBEAT_S)
            except asyncio.TimeoutError:
                # A comment is a valid SSE frame that no handler ever sees. It
                # exists to put bytes on the wire so an idle-timeout proxy does
                # not decide the connection is dead during a silent render.
                if await request.is_disconnected():
                    return
                yield ": ping\n\n"
                continue
            yield event.sse()
            if event.terminal:
                return


async def _polled(
    job_id: str, row: dict[str, Any], resume: int | None, request: Request
) -> AsyncIterator[str]:
    """Degraded mode: another uvicorn worker is running this job.

    The bus is per process, so there is nothing here to subscribe to. Rather
    than hold open a connection that will never say anything, watch the one
    surface both processes share — the job row — and emit its coarse progress in
    the same event shape. A client written against the live feed keeps working;
    it just hears sentences instead of the crew's running commentary.
    """
    deadline = time.monotonic() + STREAM_MAX_S
    yield ": this worker is not running this job — falling back to the job row\n\n"
    seen: str | None = None
    last_beat = time.monotonic()
    # The job row has no sequence of its own — it is one mutable cell — so this
    # connection numbers what it observes. Ids stay monotonic and unique, which
    # is what a client deduplicating on `seq` requires; without it every
    # progress note would arrive as id 1 and all but the first would be dropped.
    seq = resume or 0
    while time.monotonic() < deadline:
        event = _from_job_row(job_id, row)
        fingerprint = f"{event.stage}|{event.message}"
        if fingerprint != seen:
            seen = fingerprint
            last_beat = time.monotonic()
            seq += 1
            yield replace(event, seq=seq).sse()
            if event.terminal:
                return
        elif time.monotonic() - last_beat >= HEARTBEAT_S:
            last_beat = time.monotonic()
            yield ": ping\n\n"
        await asyncio.sleep(DB_POLL_S)
        if await request.is_disconnected():
            return
        fresh = await store.get_job(job_id)
        if not fresh:
            return
        row = fresh

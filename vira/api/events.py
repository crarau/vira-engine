"""In-process pub/sub for job progress, and the SSE frames it serialises to.

Why this exists: a generation runs 74–350 seconds and the job row can hold
exactly one sentence — the latest. A client polling that sees one line at a time
and misses everything between two polls, which on an agentic run is most of what
happened. The interesting output of the crew is the *trace* ("cohesion: two
beats contradict their scripted descriptions", "frame 3 regenerated"), and a
single mutable column cannot carry it.

Why in memory rather than a table: these events are conversation, not record.
The durable account of a run is the recipe — verbatim prompts, corpus, settings,
already written atomically with the video. Putting a database round trip on the
hot path of a Director tool loop would buy nothing anyone reads once the mp4
exists, and would make the progress feed capable of slowing the work it reports.

What that costs, stated plainly: with more than one uvicorn worker a client can
connect to a process that is not running its job, and this bus will have nothing
to say. `vira.api.routes.jobs` detects that and falls back to the job row, which
every worker shares. Doing it properly across processes is Postgres
LISTEN/NOTIFY or Redis; docs/API.md says so to the client's face.

Two invariants hold everywhere in this module:

**Publishing cannot raise and cannot block.** It is called from inside the
pipeline, including from a synchronous crew callback. Every failure mode — no
subscribers, a subscriber that stopped reading, an unserialisable payload — ends
in a dropped event, never in a broken generation.

**Sequence numbers are per job and start at 1.** They are the SSE event id, so
`Last-Event-ID` resumption is "give me everything after n" and nothing else.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import OrderedDict, deque
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Callable

log = logging.getLogger(__name__)

# Enough to replay a whole agentic run — ~350s producing a line every few
# seconds, plus the Director's tool announcements. A client that reconnects
# after a laptop lid closes gets the full story rather than a suffix.
RING_SIZE = 400

# Jobs whose buffers we keep. The API process is long-lived and every job would
# otherwise be retained forever; the oldest is evicted, not the busiest.
MAX_TRACKED_JOBS = 128

# Must be >= RING_SIZE so a fresh subscriber's replay always fits without the
# overflow policy kicking in on connect.
SUBSCRIBER_QUEUE = RING_SIZE + 64

# A job ends exactly once, in one of these two ways. The stream closes on them
# and a client that sees one never needs to poll again.
TERMINAL_STAGES = frozenset({"done", "failed"})

# The vocabulary a UI is allowed to switch on. Anything else is still delivered
# — an unknown stage must render as a plain trace line, never be dropped — but
# these are the ones documented in docs/API.md and worth an icon.
STAGES = (
    "queued",     # accepted, nothing started yet
    "select",     # shortlisting candidate trends from the corpus
    "verify",     # fetching every source URL before it reaches a prompt
    "analyze",    # what the surviving corpus says about this category
    "plan",       # choosing the shape of the film
    "write",      # script written or revised
    "motion",     # caption treatments and camera moves (agentic)
    "critique",   # hostile first viewer
    "voice",      # narration synthesised
    "imagery",    # frames generated or regenerated
    "cohesion",   # what the frames ACTUALLY show vs the script (agentic)
    "tool",       # a Director tool call starting (agentic)
    "director",   # the Director's own reasoning and budget decisions
    "crew",       # a crew trace line with no more specific stage
    "score",      # the evidence gate
    "render",     # Remotion
    "done",       # terminal, carries video_id
    "failed",     # terminal, carries error
)

LEVELS = ("debug", "info", "warn", "error")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class Event:
    """One thing that happened, in a shape a browser can render without a lookup table.

    `message` is a human sentence because the stage vocabulary will grow and a
    UI that only knows how to draw stages it recognises goes blank on the next
    one added. `stage` and `data` are for the UI that wants to do better than
    print the sentence — group by stage, show a per-beat spinner, link the video.
    """

    seq: int
    ts: str
    job_id: str
    stage: str
    message: str
    level: str = "info"
    data: dict[str, Any] = field(default_factory=dict)

    @property
    def terminal(self) -> bool:
        return self.stage in TERMINAL_STAGES

    def as_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "ts": self.ts,
            "job_id": self.job_id,
            "stage": self.stage,
            "message": self.message,
            "level": self.level,
            "data": self.data,
        }

    def sse(self) -> str:
        """One SSE frame.

        The payload is JSON on a single line by construction — `json.dumps`
        escapes newlines inside strings — which matters because a raw newline in
        a `data:` field would be read as the end of the event. The `id:` is the
        sequence number, and it is what the browser echoes back as
        `Last-Event-ID` when it reconnects on its own.
        """
        body = json.dumps(self.as_dict(), ensure_ascii=False, default=str)
        return f"id: {self.seq}\ndata: {body}\n\n"


class JobBus:
    """Fan-out of job events to whoever is watching, plus what they missed.

    Single-threaded by design: every producer runs on the API's event loop, so
    `publish` needs no lock — it never awaits, and therefore cannot be
    interleaved with a subscribe or another publish.
    """

    def __init__(self, *, ring_size: int = RING_SIZE, max_jobs: int = MAX_TRACKED_JOBS) -> None:
        self._ring_size = ring_size
        self._max_jobs = max_jobs
        self._buffers: OrderedDict[str, deque[Event]] = OrderedDict()
        self._seq: dict[str, int] = {}
        self._subs: dict[str, set[asyncio.Queue[Event]]] = {}

    # -- producing ---------------------------------------------------------

    def publish(
        self,
        job_id: str,
        stage: str,
        message: str,
        *,
        level: str = "info",
        data: dict[str, Any] | None = None,
    ) -> Event | None:
        """Record an event and hand it to every current subscriber.

        Returns the event, or None if it could not be recorded at all. Never
        raises: this sits inside a 350-second pipeline and a progress feed that
        can kill a generation is worse than no progress feed.
        """
        try:
            job_id = str(job_id)
            event = Event(
                seq=self._next_seq(job_id),
                ts=_now(),
                job_id=job_id,
                stage=stage,
                message=message,
                level=level if level in LEVELS else "info",
                data=dict(data or {}),
            )
            self._buffer(job_id).append(event)
            self._deliver(job_id, event)
            return event
        except Exception:  # noqa: BLE001 - the contract is that this cannot fail
            log.exception("dropped a job event for %s (%s)", job_id, stage)
            return None

    def _next_seq(self, job_id: str) -> int:
        n = self._seq.get(job_id, 0) + 1
        self._seq[job_id] = n
        return n

    def _buffer(self, job_id: str) -> deque[Event]:
        buf = self._buffers.get(job_id)
        if buf is None:
            buf = deque(maxlen=self._ring_size)
            self._buffers[job_id] = buf
            self._evict()
        self._buffers.move_to_end(job_id)
        return buf

    def _evict(self) -> None:
        """Forget the least recently active job once we are tracking too many.

        A job still being watched is never the least recent — its producer is
        publishing into it — so this drops history for finished work, which is
        the only history nobody is going to ask for.
        """
        while len(self._buffers) > self._max_jobs:
            stale, _ = self._buffers.popitem(last=False)
            self._seq.pop(stale, None)
            self._subs.pop(stale, None)

    def _deliver(self, job_id: str, event: Event) -> None:
        for q in tuple(self._subs.get(job_id, ())):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # A reader this far behind is a stalled connection, not a slow
                # one. Drop its oldest event rather than the newest: the client
                # sees a gap in `seq`, which docs/API.md tells it to repair with
                # GET /v1/jobs/{id}/events. Blocking here would stall the render.
                try:
                    q.get_nowait()
                    q.put_nowait(event)
                except (asyncio.QueueEmpty, asyncio.QueueFull):  # pragma: no cover
                    pass

    # -- consuming ---------------------------------------------------------

    def known(self, job_id: str) -> bool:
        """True when this process is the one producing events for the job."""
        return str(job_id) in self._buffers

    def closed(self, job_id: str) -> bool:
        """True once a terminal event has been published for the job."""
        buf = self._buffers.get(str(job_id))
        return bool(buf) and buf[-1].terminal

    def history(self, job_id: str, *, after_seq: int | None = None) -> list[Event]:
        """Buffered events, oldest first, optionally only those after a sequence."""
        buf = self._buffers.get(str(job_id))
        if not buf:
            return []
        if after_seq is None:
            return list(buf)
        return [e for e in buf if e.seq > after_seq]

    @asynccontextmanager
    async def subscribe(
        self, job_id: str, *, after_seq: int | None = None
    ) -> AsyncIterator[asyncio.Queue[Event]]:
        """A queue of this job's events, primed with whatever the client missed.

        The replay and the registration happen with no `await` between them, so
        an event published in the meantime cannot fall down the gap between
        "what was buffered" and "what is live" — the property that makes a
        reconnect lossless rather than merely fast.
        """
        job_id = str(job_id)
        q: asyncio.Queue[Event] = asyncio.Queue(maxsize=SUBSCRIBER_QUEUE)
        for event in self.history(job_id, after_seq=after_seq):
            q.put_nowait(event)
        self._subs.setdefault(job_id, set()).add(q)
        try:
            yield q
        finally:
            subs = self._subs.get(job_id)
            if subs is not None:
                subs.discard(q)
                if not subs:
                    self._subs.pop(job_id, None)

    def subscriber_count(self, job_id: str) -> int:
        return len(self._subs.get(str(job_id), ()))

    def forget(self, job_id: str) -> None:
        """Drop a job's history. Used by tests; eviction handles it in production."""
        job_id = str(job_id)
        self._buffers.pop(job_id, None)
        self._seq.pop(job_id, None)
        self._subs.pop(job_id, None)


# One bus per process. Module-level rather than app state because the producers
# are worker coroutines and a crew callback, neither of which has a request.
bus = JobBus()


def publish(
    job_id: str,
    stage: str,
    message: str,
    *,
    level: str = "info",
    data: dict[str, Any] | None = None,
) -> Event | None:
    """Publish to the process bus. A no-op with no subscribers, and never raises."""
    return bus.publish(job_id, stage, message, level=level, data=data)


def crew_sink(job_id: str) -> Callable[[str, str, str, dict[str, Any]], None]:
    """The callback `vira.agentic.crew.Production` calls for each trace line.

    Handed out from here rather than built at the call site so the guarantee
    that a progress callback cannot break a generation lives in one place. The
    crew's own default is a no-op, which is what keeps `agentic_video.py`
    working with no API in the picture.
    """

    def sink(stage: str, message: str, level: str, data: dict[str, Any]) -> None:
        publish(job_id, stage, message, level=level, data=data)

    return sink


def parse_last_event_id(value: str | None) -> int | None:
    """The sequence a reconnecting client already has, or None.

    Browsers echo the last `id:` they saw verbatim, and a proxy or a hand-rolled
    client can send anything at all. A value that is not a positive integer
    means "start from the beginning of the buffer", which is the safe direction
    to fail — a duplicate line in the UI beats a silently missing one.
    """
    if value is None:
        return None
    try:
        seq = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return seq if seq > 0 else None

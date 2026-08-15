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

**Verbose is opt-in on the server, not filtered in the browser.** `debug` events
carry whole prompts — 1–12 KB each — and a client that did not ask for them
never has one queued, let alone serialised. `?level=debug` is what turns them
on; everything defaults to info and above.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import OrderedDict, deque
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Callable, Iterator

log = logging.getLogger(__name__)

# Enough to replay a whole agentic run — ~350s producing a line every few
# seconds, plus the Director's tool announcements. A client that reconnects
# after a laptop lid closes gets the full story rather than a suffix.
RING_SIZE = 400

# Jobs whose buffers we keep. The API process is long-lived and every job would
# otherwise be retained forever; the oldest is evicted, not the busiest.
MAX_TRACKED_JOBS = 128

# The count cap alone stopped being a memory bound the moment verbose mode
# started putting whole prompts on the wire. A measured fast run sends system +
# user prompts of 1–12 KB and gets 0.3–3.6 KB back, so one `llm` event is
# ~16 KB and 400 of them would be 6 MB *per job* — 800 MB across the 128 jobs
# this bus tracks. Both caps therefore apply, whichever bites first:
#
#   MAX_JOB_BYTES   an agentic run's ~60 model calls at 30 KB is 1.8 MB, so 8 MB
#                   is comfortable headroom and still evicts a runaway.
#   MAX_TOTAL_BYTES the process-wide ceiling. Reached only if several verbose
#                   jobs are watched at once, and it drops whole finished jobs.
#
# Nothing is truncated to fit — a half prompt is not a prompt you can paste
# back, which is the entire point of carrying it. The oldest events go instead.
MAX_JOB_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024

# Must be >= RING_SIZE so a fresh subscriber's replay always fits without the
# overflow policy kicking in on connect. Queued events are references to the
# same Event objects the ring holds, so a subscriber costs pointers, not
# prompts, and the byte caps above bound both.
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
    "llm",        # one model call, verbatim prompts included (debug only)
    "score",      # the evidence gate
    "render",     # Remotion
    "done",       # terminal, carries video_id
    "failed",     # terminal, carries error
)

LEVELS = ("debug", "info", "warn", "error")

# Severity order, so "give me info and above" is one comparison rather than a
# set the caller has to assemble. `debug` is deliberately rank 0 and excluded by
# default everywhere: a prompt event is several KB and a normal watcher wants a
# trace, not a transcript.
LEVEL_RANK = {name: i for i, name in enumerate(LEVELS)}
DEFAULT_LEVEL = "info"


def normalise_level(value: str | None) -> str:
    """A client-supplied level, or the default. Never raises, never 422s.

    An unknown level means "show me the normal feed" rather than an error: this
    is a progress stream, and refusing to open it over a typo in a query string
    would be a worse failure than quietly showing one line too few.
    """
    return value if value in LEVEL_RANK else DEFAULT_LEVEL


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

    @property
    def rank(self) -> int:
        return LEVEL_RANK.get(self.level, LEVEL_RANK[DEFAULT_LEVEL])

    @property
    def weight(self) -> int:
        """Roughly how many bytes this event costs the ring.

        An estimate, not a measurement: the expensive members of `data` are the
        prompt strings and they are top-level, so summing their lengths is
        accurate where it matters and free where it does not. Calling
        `json.dumps` here would put a serialisation of every prompt on the hot
        path of a 350-second pipeline to make a cache-eviction decision.
        """
        n = len(self.message) + 128
        for key, value in self.data.items():
            n += len(key) + (len(value) if isinstance(value, str) else 32)
        return n

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


@dataclass(slots=True, eq=False)
class _Sub:
    """One open connection, and the least severe level it asked for.

    The filter lives on the subscriber rather than on the reader so a client
    watching at `info` never has a 12 KB prompt copied into its queue at all —
    the point of a server-side level is that debug traffic does not reach a
    stream that did not ask for it.
    """

    queue: asyncio.Queue[Event]
    min_rank: int


class JobBus:
    """Fan-out of job events to whoever is watching, plus what they missed.

    Single-threaded by design: every producer runs on the API's event loop, so
    `publish` needs no lock — it never awaits, and therefore cannot be
    interleaved with a subscribe or another publish.
    """

    def __init__(
        self,
        *,
        ring_size: int = RING_SIZE,
        max_jobs: int = MAX_TRACKED_JOBS,
        max_job_bytes: int = MAX_JOB_BYTES,
        max_total_bytes: int = MAX_TOTAL_BYTES,
    ) -> None:
        self._ring_size = ring_size
        self._max_jobs = max_jobs
        self._max_job_bytes = max_job_bytes
        self._max_total_bytes = max_total_bytes
        self._buffers: OrderedDict[str, deque[Event]] = OrderedDict()
        self._seq: dict[str, int] = {}
        self._bytes: dict[str, int] = {}
        self._total_bytes = 0
        self._subs: dict[str, set[_Sub]] = {}

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
            self._retain(job_id, event)
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
            # No `maxlen`: the ring is trimmed by `_retain`, which has to honour
            # a byte budget as well as a count and therefore needs to see what
            # it is dropping. A deque that silently discards its own left end
            # would leave the byte accounting wrong forever.
            buf = deque()
            self._buffers[job_id] = buf
            self._bytes[job_id] = 0
        self._buffers.move_to_end(job_id)
        return buf

    def _retain(self, job_id: str, event: Event) -> None:
        """Buffer the event and bring the job back inside both caps."""
        buf = self._buffer(job_id)
        buf.append(event)
        self._charge(job_id, event.weight)
        while buf and (len(buf) > self._ring_size or self._bytes[job_id] > self._max_job_bytes):
            self._charge(job_id, -buf.popleft().weight)
        self._evict()

    def _charge(self, job_id: str, delta: int) -> None:
        self._bytes[job_id] = self._bytes.get(job_id, 0) + delta
        self._total_bytes += delta

    def _evict(self) -> None:
        """Forget the least recently active job once we are holding too much.

        A job still being watched is never the least recent — its producer is
        publishing into it — so this drops history for finished work, which is
        the only history nobody is going to ask for. The byte ceiling matters
        because verbose mode made a single job's history three orders of
        magnitude larger than the count cap assumed.
        """
        while len(self._buffers) > self._max_jobs or self._total_bytes > self._max_total_bytes:
            stale, _ = self._buffers.popitem(last=False)
            self._total_bytes -= self._bytes.pop(stale, 0)
            self._seq.pop(stale, None)
            self._subs.pop(stale, None)
            if not self._buffers:  # pragma: no cover - defensive, keeps the loop finite
                self._total_bytes = 0
                return

    def _deliver(self, job_id: str, event: Event) -> None:
        for sub in tuple(self._subs.get(job_id, ())):
            if event.rank < sub.min_rank:
                continue
            try:
                sub.queue.put_nowait(event)
            except asyncio.QueueFull:
                # A reader this far behind is a stalled connection, not a slow
                # one. Drop its oldest event rather than the newest: the client
                # sees a gap in `seq`, which docs/API.md tells it to repair with
                # GET /v1/jobs/{id}/events. Blocking here would stall the render.
                try:
                    sub.queue.get_nowait()
                    sub.queue.put_nowait(event)
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

    def history(
        self, job_id: str, *, after_seq: int | None = None, level: str = DEFAULT_LEVEL
    ) -> list[Event]:
        """Buffered events, oldest first, at `level` and above.

        `level` defaults to info, so every existing caller keeps the feed it had
        before prompts started arriving on the same bus. Filtering here rather
        than in the route means the debug payload is never assembled into a
        response for a client that did not ask for it.
        """
        buf = self._buffers.get(str(job_id))
        if not buf:
            return []
        floor = LEVEL_RANK.get(level, LEVEL_RANK[DEFAULT_LEVEL])
        return [
            e for e in buf
            if e.rank >= floor and (after_seq is None or e.seq > after_seq)
        ]

    @asynccontextmanager
    async def subscribe(
        self, job_id: str, *, after_seq: int | None = None, level: str = DEFAULT_LEVEL
    ) -> AsyncIterator[asyncio.Queue[Event]]:
        """A queue of this job's events, primed with whatever the client missed.

        The replay and the registration happen with no `await` between them, so
        an event published in the meantime cannot fall down the gap between
        "what was buffered" and "what is live" — the property that makes a
        reconnect lossless rather than merely fast.
        """
        job_id = str(job_id)
        q: asyncio.Queue[Event] = asyncio.Queue(maxsize=SUBSCRIBER_QUEUE)
        for event in self.history(job_id, after_seq=after_seq, level=level):
            q.put_nowait(event)
        sub = _Sub(queue=q, min_rank=LEVEL_RANK.get(level, LEVEL_RANK[DEFAULT_LEVEL]))
        self._subs.setdefault(job_id, set()).add(sub)
        try:
            yield q
        finally:
            subs = self._subs.get(job_id)
            if subs is not None:
                subs.discard(sub)
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
        self._total_bytes -= self._bytes.pop(job_id, 0)

    def buffered_bytes(self, job_id: str | None = None) -> int:
        """What the ring is holding, for one job or for the whole process."""
        if job_id is None:
            return self._total_bytes
        return self._bytes.get(str(job_id), 0)


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


# -- the ambient job, and the prompts published against it ------------------
#
# `vira.llm.complete` is called from ten places that know nothing about HTTP,
# and threading a job id through all of them would put an API concept into every
# creative stage's signature. The same problem `vira.provenance` already solved
# with a context variable, solved the same way: the worker declares which job
# its task is running, and anything downstream that wants to say something can.
#
# Unset everywhere else — `variants.py` and `agentic_video.py` never enter
# `watching`, so `publish_llm_call` returns without touching the bus and the CLI
# behaves exactly as it did before verbose mode existed.

_job: ContextVar[str | None] = ContextVar("vira_job_id", default=None)
_stage: ContextVar[str] = ContextVar("vira_job_stage", default="")


def current_job() -> str | None:
    """The job this task is generating for, or None outside the API worker."""
    return _job.get()


def current_stage() -> str:
    """The pipeline stage in flight, for events raised deep in the call tree."""
    return _stage.get()


@contextmanager
def watching(job_id: str) -> Iterator[None]:
    """Bind a job id to this task for the duration of its generation."""
    token = _job.set(str(job_id))
    stage_token = _stage.set("queued")
    try:
        yield
    finally:
        _job.reset(token)
        _stage.reset(stage_token)


def set_stage(stage: str) -> None:
    """Record which stage is running, so a model call can name where it came from."""
    _stage.set(stage)


def publish_llm_call(
    *,
    model: str,
    max_tokens: int,
    system_prompt: str,
    user_prompt: str,
    response: str,
    stop_reason: str | None,
    elapsed_ms: int | None = None,
    stage: str | None = None,
) -> Event | None:
    """Put one model call on the job's feed, prompts and all. No-op off the API.

    Nothing is shortened here. A prompt clipped to fit a buffer is a prompt you
    cannot paste back into the code that sent it, which would make the whole
    feature decorative; the ring's byte budget is what keeps that safe, and the
    `debug` level is what keeps it out of a normal watcher's stream.
    """
    job_id = _job.get()
    if not job_id:
        return None
    where = stage or _stage.get() or "crew"
    chars_in = len(system_prompt) + len(user_prompt)
    summary = (
        f"{model} · {where} · {chars_in:,} chars in, {len(response):,} out"
        f" · stop {stop_reason or '—'}"
    )
    return publish(job_id, "llm", summary, level="debug", data={
        "kind": "llm_call",
        "pipeline_stage": where,
        "model": model,
        "max_tokens": max_tokens,
        "stop_reason": stop_reason,
        "system_prompt": system_prompt,
        "user_prompt": user_prompt,
        "response": response,
        "elapsed_ms": elapsed_ms,
    })


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

"""The job event bus — the four properties a live progress feed lives or dies on.

These are not tests of a queue. Each one guards a promise made to the browser in
docs/API.md, and the promise is the reason the code is shaped the way it is:

**A watcher that arrives late still sees the whole run.** Generation is 74–350
seconds and a user opens the tab whenever they open it. A feed that only carries
what happens after `EventSource` connects shows an empty box for a job that is
four minutes in, which is worse than the spinner it replaced.

**A reconnect resumes, it does not restart.** Every frame carries `id: <seq>`
and the browser echoes the last one back. If `history(after_seq=n)` ever
returned events at or below `n`, a laptop lid closing would duplicate half the
trace; if it skipped one, the UI would silently lose a line.

**Publishing with nobody listening cannot fail.** This runs inside a 350-second
pipeline. A render that dies because no browser was open would be an absurd way
to lose six minutes of paid API calls, so `publish` swallows everything.

**A stalled reader is dropped, not waited on.** `put_nowait` and a bounded queue
mean a client that stops reading costs one buffer, never a stalled generation.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from vira.api.events import Event, JobBus, parse_last_event_id

JOB = "11111111-2222-3333-4444-555555555555"


@pytest.fixture
def bus() -> JobBus:
    return JobBus(ring_size=8, max_jobs=3)


def messages(seq_events) -> list[str]:
    return [e.message for e in seq_events]


async def drain(q: asyncio.Queue) -> list[Event]:
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


# -- buffering -------------------------------------------------------------


async def test_events_are_buffered_and_numbered_from_one(bus):
    bus.publish(JOB, "select", "selecting candidate trends")
    bus.publish(JOB, "verify", "verifying 18 source URLs", data={"sources": 18})

    history = bus.history(JOB)

    assert [e.seq for e in history] == [1, 2]
    assert [e.stage for e in history] == ["select", "verify"]
    assert history[1].data == {"sources": 18}
    assert history[1].level == "info"
    assert history[1].job_id == JOB
    assert history[1].ts.endswith("Z")


async def test_the_ring_keeps_the_newest_and_drops_the_oldest(bus):
    for i in range(12):  # ring_size is 8
        bus.publish(JOB, "crew", f"line {i}")

    history = bus.history(JOB)

    assert len(history) == 8
    assert messages(history) == [f"line {i}" for i in range(4, 12)]
    # Sequence numbers keep counting; they are ids, not indices into the buffer.
    assert [e.seq for e in history] == list(range(5, 13))


async def test_a_job_nobody_touched_has_no_history_and_is_unknown(bus):
    assert bus.history("nope") == []
    assert bus.known("nope") is False
    assert bus.closed("nope") is False


async def test_the_oldest_job_is_evicted_once_too_many_are_tracked(bus):
    for job in ("a", "b", "c", "d"):  # max_jobs is 3
        bus.publish(job, "queued", "job accepted")

    assert bus.known("a") is False
    assert [bus.known(j) for j in ("b", "c", "d")] == [True, True, True]


async def test_terminal_is_recognised_only_at_the_end(bus):
    bus.publish(JOB, "render", "rendering")
    assert bus.closed(JOB) is False

    bus.publish(JOB, "done", "done · The hook", data={"video_id": "v-1"})

    assert bus.closed(JOB) is True
    assert bus.history(JOB)[-1].terminal is True


# -- late subscribers ------------------------------------------------------


async def test_a_late_subscriber_replays_what_it_missed(bus):
    """The whole reason the ring exists: a tab opened four minutes in."""
    bus.publish(JOB, "select", "selecting candidate trends")
    bus.publish(JOB, "verify", "verifying 18 source URLs")
    bus.publish(JOB, "imagery", "frame 3 regenerated: the bowl was missing")

    async with bus.subscribe(JOB) as q:
        replayed = await drain(q)
        bus.publish(JOB, "render", "rendering")
        live = await asyncio.wait_for(q.get(), timeout=1)

    assert messages(replayed) == [
        "selecting candidate trends",
        "verifying 18 source URLs",
        "frame 3 regenerated: the bowl was missing",
    ]
    assert live.message == "rendering"


async def test_two_subscribers_each_get_every_event(bus):
    async with bus.subscribe(JOB) as first, bus.subscribe(JOB) as second:
        assert bus.subscriber_count(JOB) == 2
        bus.publish(JOB, "voice", "voice: 27.4s (target 28s)")

        assert (await first.get()).message == (await second.get()).message

    assert bus.subscriber_count(JOB) == 0


async def test_unsubscribing_stops_delivery_without_stopping_the_job(bus):
    async with bus.subscribe(JOB) as q:
        pass

    bus.publish(JOB, "render", "rendering")

    assert q.empty()
    assert messages(bus.history(JOB)) == ["rendering"]


async def test_nothing_published_during_the_handshake_is_lost(bus):
    """Replay and registration happen with no await between them.

    If they did not, an event published in that window would be past the
    snapshot and before the queue existed — invisible in both, and the exact
    line the UI would be missing.
    """
    bus.publish(JOB, "select", "before")
    async with bus.subscribe(JOB) as q:
        bus.publish(JOB, "verify", "during")
        received = await drain(q)

    assert messages(received) == ["before", "during"]


# -- Last-Event-ID resumption ----------------------------------------------


async def test_history_after_a_sequence_is_strictly_after_it(bus):
    for i in range(1, 6):
        bus.publish(JOB, "crew", f"line {i}")

    resumed = bus.history(JOB, after_seq=3)

    assert [e.seq for e in resumed] == [4, 5]
    assert messages(resumed) == ["line 4", "line 5"]


async def test_a_reconnect_resumes_from_last_event_id(bus):
    for i in range(1, 4):
        bus.publish(JOB, "crew", f"line {i}")

    # What the browser saw before the connection dropped.
    async with bus.subscribe(JOB) as before:
        seen = await drain(before)
    last_id = str(seen[-1].seq)

    bus.publish(JOB, "render", "rendering")

    async with bus.subscribe(JOB, after_seq=parse_last_event_id(last_id)) as after:
        resumed = await drain(after)

    assert messages(resumed) == ["rendering"]


async def test_resuming_past_the_end_yields_nothing_rather_than_everything(bus):
    bus.publish(JOB, "done", "done", data={"video_id": "v-1"})

    async with bus.subscribe(JOB, after_seq=99) as q:
        assert await drain(q) == []


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("7", 7), (" 7 ", 7), (None, None), ("", None),
        ("nonsense", None), ("0", None), ("-3", None), ("3.5", None),
    ],
)
def test_last_event_id_falls_back_to_the_whole_buffer_when_unusable(raw, expected):
    """A junk id must mean "replay everything", never "skip ahead".

    A duplicated line in the UI is a cosmetic bug; a skipped one is a lie about
    what the agent did.
    """
    assert parse_last_event_id(raw) == expected


# -- publishing is never a hazard -------------------------------------------


async def test_publishing_with_no_subscribers_is_safe(bus):
    """The common case: a job generating while every browser tab is closed."""
    assert bus.subscriber_count(JOB) == 0

    event = bus.publish(JOB, "render", "rendering")

    assert event is not None and event.seq == 1
    assert bus.subscriber_count(JOB) == 0
    assert messages(bus.history(JOB)) == ["rendering"]


async def test_publish_swallows_a_bad_payload_instead_of_killing_the_job(bus):
    """`publish` sits inside the pipeline. Its contract is that it cannot raise."""

    class Hostile:
        def __repr__(self):
            raise RuntimeError("boom")

    assert bus.publish(JOB, "crew", "fine") is not None
    # dict(data) over something that is not a mapping is the realistic failure.
    assert bus.publish(JOB, "crew", "trouble", data=Hostile()) is None
    # And the bus is still usable afterwards.
    assert bus.publish(JOB, "done", "done") is not None
    assert bus.closed(JOB) is True


async def test_an_unknown_level_is_normalised_rather_than_rejected(bus):
    assert bus.publish(JOB, "crew", "x", level="catastrophe").level == "info"
    assert bus.publish(JOB, "crew", "y", level="warn").level == "warn"


async def test_a_stalled_reader_loses_its_oldest_event_not_the_generation(bus):
    """A client that stopped reading must cost one buffer, never a stalled render."""
    async with bus.subscribe(JOB) as q:
        # Simulate a reader so far behind its queue has filled up.
        while not q.full():
            q.put_nowait(Event(seq=0, ts="", job_id=JOB, stage="crew", message="old"))

        published = bus.publish(JOB, "done", "done")

        assert published is not None  # the producer was not blocked or broken
        drained = await drain(q)
        assert drained[-1].message == "done"


# -- the wire frame ---------------------------------------------------------


def test_an_sse_frame_carries_the_sequence_as_its_id():
    event = Event(seq=12, ts="2026-08-15T14:03:11.482Z", job_id=JOB,
                  stage="imagery", message="frame 3 regenerated", data={"beat_index": 3})

    frame = event.sse()

    assert frame.startswith("id: 12\ndata: ")
    assert frame.endswith("\n\n")
    payload = json.loads(frame.split("data: ", 1)[1])
    assert payload == {
        "seq": 12, "ts": "2026-08-15T14:03:11.482Z", "job_id": JOB,
        "stage": "imagery", "message": "frame 3 regenerated",
        "level": "info", "data": {"beat_index": 3},
    }


def test_a_multiline_message_stays_one_sse_frame():
    """A raw newline in `data:` would end the event early and split it in two."""
    event = Event(seq=1, ts="t", job_id=JOB, stage="crew",
                  message="cohesion:\ntwo beats contradict\ntheir descriptions")

    body = event.sse()

    assert body.count("\n\n") == 1
    assert body.count("data: ") == 1
    assert "contradict" in json.loads(body.split("data: ", 1)[1])["message"]


# -- the crew hook ----------------------------------------------------------


async def test_the_crew_default_sink_keeps_the_cli_unchanged():
    """`agentic_video.py` builds a Production with no callback and must not care."""
    from vira.agentic.crew import Production

    p = Production.__new__(Production)  # no company/lane/corpus needed for note()
    p.log = []
    p.on_event = Production.__dataclass_fields__["on_event"].default

    p.note("script written: 6 beats", "write", beats=6)

    assert p.log == ["script written: 6 beats"]


# -- verbose mode -----------------------------------------------------------
#
# Prompts on the feed are the reason the level filter exists. Each of these
# guards one half of the trade: the transcript must arrive whole for someone who
# asked for it, and must not arrive at all for someone who did not.


PROMPT = "You are a sceptical grader.\n" + ("x" * 20_000) + "\nScore it."


def llm_event(bus_, job=JOB, **kw):
    payload = {
        "kind": "llm_call", "model": "claude-sonnet-5", "max_tokens": 4000,
        "stop_reason": "end_turn", "system_prompt": PROMPT,
        "user_prompt": "Write the ad.", "response": "{}",
    }
    payload.update(kw)
    return bus_.publish(job, "llm", "one model call", level="debug", data=payload)


async def test_debug_is_excluded_from_the_default_feed(bus):
    bus.publish(JOB, "write", "writing 6 beats")
    llm_event(bus)
    bus.publish(JOB, "score", "scoring")

    assert messages(bus.history(JOB)) == ["writing 6 beats", "scoring"]
    assert len(bus.history(JOB, level="debug")) == 3


async def test_a_default_subscriber_never_receives_a_prompt(bus):
    """Filtered at the bus, not in the browser — the payload is never queued."""
    async with bus.subscribe(JOB) as plain, bus.subscribe(JOB, level="debug") as verbose:
        llm_event(bus)
        bus.publish(JOB, "done", "done")

    assert messages(await drain(plain)) == ["done"]
    assert messages(await drain(verbose)) == ["one model call", "done"]


async def test_turning_verbose_on_late_replays_the_calls_already_made(bus):
    llm_event(bus)
    bus.publish(JOB, "write", "writing 6 beats")

    async with bus.subscribe(JOB, level="debug") as q:
        assert messages(await drain(q)) == ["one model call", "writing 6 beats"]


async def test_a_prompt_event_round_trips_its_full_text(bus):
    """Nothing is truncated server-side, including through the SSE frame."""
    event = llm_event(bus)

    assert event is not None
    assert event.data["system_prompt"] == PROMPT

    replayed = bus.history(JOB, level="debug")[0]
    assert replayed.data["system_prompt"] == PROMPT

    payload = json.loads(replayed.sse().split("data: ", 1)[1])
    assert payload["data"]["system_prompt"] == PROMPT
    assert payload["level"] == "debug"


async def test_an_unknown_level_asks_for_the_ordinary_feed():
    from vira.api.events import normalise_level

    assert normalise_level("debug") == "debug"
    assert normalise_level("error") == "error"
    assert normalise_level(None) == "info"
    assert normalise_level("verbose") == "info"
    assert normalise_level("DEBUG") == "info"


async def test_the_byte_budget_evicts_before_the_count_does():
    """400 events of 20 KB would be 8 MB per job. The ring is capped in bytes too."""
    small = JobBus(ring_size=400, max_jobs=4, max_job_bytes=100_000)

    for _ in range(20):
        llm_event(small)

    kept = small.history(JOB, level="debug")
    assert len(kept) < 20                       # the count cap never fired
    assert small.buffered_bytes(JOB) <= 100_000
    assert kept[-1].data["system_prompt"] == PROMPT   # what survives is whole


async def test_a_verbose_job_cannot_starve_the_whole_process():
    """The process-wide ceiling drops the least recently active job, not a prompt."""
    small = JobBus(ring_size=400, max_jobs=64, max_job_bytes=100_000,
                   max_total_bytes=150_000)

    for _ in range(6):
        llm_event(small, job="job-a")
    for _ in range(6):
        llm_event(small, job="job-b")

    assert small.known("job-a") is False
    assert small.known("job-b") is True
    assert small.buffered_bytes() <= 150_000

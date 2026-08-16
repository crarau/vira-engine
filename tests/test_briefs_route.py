"""`POST /v1/briefs` — the rich payload, answered exactly like `POST /v1/videos`.

The whole value of this endpoint is that it changes what the engine is told and
nothing about how the caller waits. So the test that matters most is the boring
one: the 202 carries `job_id`, `poll` and `estimated_seconds`, and Lovable's
existing poll and stream code keeps working untouched.

The rest holds the brief onto the worker: the payload that arrives is the
payload the pipeline runs on, the brand becomes a company row without a separate
registration step, and a request for something the engine cannot do comes back
said out loud rather than silently approximated.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from vira.api import store, worker
from vira.api.app import app
from vira.brief import Brief

COMPANY = {"id": "11111111-1111-1111-1111-111111111111", "slug": "sunday-oats"}
JOB = {"id": "22222222-2222-2222-2222-222222222222"}


def payload(**kw) -> dict:
    body = {
        "durationSeconds": 6,
        "aspectRatio": "9:16",
        "brand": {
            "name": "Sunday Oats",
            "slug": "sunday-oats",
            "bio": "overnight oats that set in the fridge in four hours",
            "mission": "breakfast that is already made when you wake up",
            "category": "Food & Beverage",
            "toneGuardrails": ["dry"],
            "palette": ["#0B0B0F"],
            "mustSay": ["four hours"],
            "neverSay": ["superfood"],
        },
        "references": [
            {"trendKey": "VIRA-TR-1", "platform": "tiktok", "hook": "I gave it ten SECONDS",
             "format": "unboxing", "whyItWorks": "withholds the result", "weight": 0.9},
        ],
        "narrative": {"hook": "I stopped buying breakfast for two YEARS",
                      "beats": [{"t": 0.0, "shot": "close on the jar",
                                 "onScreenText": "four hours"}],
                      "voiceover": "It sets while you sleep.",
                      "cta": "Tap to try the first batch",
                      "textOverlayPolicy": "one line per beat"},
        "style": {"look": "cold kitchen at 6am", "pace": "slow burn", "captions": True},
        "constraints": {"noCompetitorMarks": True, "language": "en"},
        "excluded": [],
        "signalQuality": "high",
        "product": "cocoa hazelnut overnight oats",
    }
    body.update(kw)
    return body


@pytest.fixture
def client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://api.test", timeout=30
    )


@pytest.fixture
def api(monkeypatch):
    """The route with its store and worker stubbed. Nothing is generated here."""
    state: dict[str, Any] = {"spawned": [], "upserted": [], "known": True}

    async def resolve(slug: str) -> dict | None:
        return dict(COMPANY) if state["known"] else None

    async def upsert(**kw: Any) -> dict:
        state["upserted"].append(kw)
        return dict(COMPANY)

    async def create_job(**kw: Any) -> dict:
        state["job_args"] = kw
        return dict(JOB)

    def spawn(job_id: str, **kw: Any) -> None:
        state["spawned"].append({"job_id": job_id, **kw})

    monkeypatch.setattr(worker, "resolve_company", resolve)
    monkeypatch.setattr(store, "upsert_company", upsert)
    monkeypatch.setattr(store, "create_job", create_job)
    monkeypatch.setattr(worker, "spawn", spawn)
    return state


# --- the contract with their existing client ------------------------------


async def test_a_brief_is_accepted_exactly_like_a_video_request(client, api):
    async with client as c:
        r = await c.post("/v1/briefs", json=payload())
    body = r.json()
    assert r.status_code == 202
    assert body["job_id"] == JOB["id"]
    assert body["poll"] == f"http://api.test/v1/jobs/{JOB['id']}"
    assert body["estimated_seconds"] > 0
    assert body["status"] == "queued"


async def test_the_brief_reaches_the_worker_intact(client, api):
    async with client as c:
        await c.post("/v1/briefs", json=payload())
    spawned = api["spawned"][0]
    brief: Brief = spawned["brief"]
    assert isinstance(brief, Brief)
    assert brief.duration_seconds == 6
    assert brief.brand.never_say == ["superfood"]
    assert [r.trend_key for r in brief.trend_refs] == ["VIRA-TR-1"]
    assert brief.narrative.beats[0].on_screen_text == "four hours"


async def test_the_engines_own_arguments_do_not_travel_as_part_of_the_brief(client, api):
    """`product`, `lane` and `mode` are ours; the brief stays theirs."""
    async with client as c:
        await c.post("/v1/briefs", json=payload(lane="contrarian"))
    brief = api["spawned"][0]["brief"]
    assert not hasattr(brief, "lane")
    assert api["spawned"][0]["lane_name"] == "contrarian"
    assert api["spawned"][0]["product"] == "cocoa hazelnut overnight oats"


async def test_the_response_says_which_retrieval_the_brief_earned(client, api):
    async with client as c:
        with_refs = await c.post("/v1/briefs", json=payload())
        without = await c.post("/v1/briefs", json=payload(references=[]))
    assert with_refs.json()["grounded_on"] == "brief references"
    assert with_refs.json()["references_used"] == 1
    assert without.json()["grounded_on"] == "category selection"


@pytest.mark.parametrize("seconds,beats", [(4, 1), (8, 1)])
async def test_the_authored_beat_count_is_echoed_back(client, api, seconds, beats):
    async with client as c:
        r = await c.post("/v1/briefs", json=payload(durationSeconds=seconds))
    assert r.json()["duration_seconds"] == seconds
    assert r.json()["beats"] == beats, "the brief wrote one beat; the table is a default"


async def test_no_authored_beats_falls_back_to_the_duration_table(client, api):
    async with client as c:
        r = await c.post("/v1/briefs", json=payload(durationSeconds=4, narrative={}))
    assert r.json()["beats"] == 2


# --- zero friction --------------------------------------------------------


async def test_a_brand_the_engine_has_never_seen_is_created_from_the_brief(client, api):
    api["known"] = False
    async with client as c:
        r = await c.post("/v1/briefs", json=payload())
    assert r.status_code == 202
    assert api["upserted"][0]["slug"] == "sunday-oats"
    assert api["upserted"][0]["bio"].startswith("overnight oats")


async def test_the_brand_name_stands_in_for_a_product_nobody_named(client, api):
    async with client as c:
        r = await c.post("/v1/briefs", json=payload(product=None))
    assert r.json()["product"] == "Sunday Oats"


# --- saying what the engine cannot do -------------------------------------


async def test_an_unrenderable_aspect_ratio_is_refused_rather_than_approximated(client, api):
    async with client as c:
        r = await c.post("/v1/briefs", json=payload(aspectRatio="1:1"))
    assert r.status_code == 422
    assert "9:16" in r.json()["detail"]
    assert api["spawned"] == []


async def test_an_unknown_lane_is_refused(client, api):
    async with client as c:
        r = await c.post("/v1/briefs", json=payload(lane="cinematic"))
    assert r.status_code == 422


async def test_low_signal_quality_is_warned_about_at_accept_time(client, api):
    async with client as c:
        r = await c.post("/v1/briefs", json=payload(signalQuality="low"))
    warnings = " ".join(r.json()["warnings"])
    assert r.json()["signal_quality"] == "low"
    assert "scored down" in warnings


async def test_a_field_the_engine_has_no_stage_for_is_named_not_swallowed(client, api):
    async with client as c:
        r = await c.post("/v1/briefs", json=payload(
            style={"look": "cold kitchen", "musicMood": "ambient"}))
    assert any("musicMood" in w for w in r.json()["warnings"])


async def test_four_seconds_comes_with_the_tradeoff_stated(client, api):
    async with client as c:
        r = await c.post("/v1/briefs", json=payload(durationSeconds=4))
    warnings = " ".join(r.json()["warnings"])
    assert "ten spoken words" in warnings
    assert "2.4s" in warnings, "the fixed CTA card is part of the running time"


async def test_agentic_mode_says_it_will_not_pin_the_brief_s_beats(client, api):
    async with client as c:
        r = await c.post("/v1/briefs", json=payload(mode="agentic"))
    assert any("agentic" in w for w in r.json()["warnings"])


async def test_a_reference_with_no_key_is_a_422_not_a_silently_dropped_asset(client, api):
    async with client as c:
        r = await c.post("/v1/briefs", json=payload(references=[{"weight": 1.0}]))
    assert r.status_code == 422

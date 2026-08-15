"""The /v1/terac/* surface, driven through the real ASGI app.

These run without a database: the store is stubbed, because what is under test
is the wiring — that a batch's judge link becomes an opportunity's `task_url`,
and that no route can spend money.

The lifespan is deliberately not run. `init_db` would demand `API_DATABASE_URL`,
and every route exercised here either stubs the store or never touches it.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from vira import terac
from vira.api import store
from vira.api.app import app
from vira.api.routes import terac as terac_routes

BATCH = {
    "id": "11111111-1111-1111-1111-111111111111",
    "public_token": "tok-abc",
    "title": "Sunday Oats — five cuts",
    "videos": [{"id": "v1"}, {"id": "v2"}],
}


@pytest.fixture
def client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://api.test", timeout=30
    )


@pytest.fixture
def batch(monkeypatch):
    async def fake(*_a: Any, **kwargs: Any) -> dict | None:
        wanted = kwargs.get("batch_id") or kwargs.get("public_token")
        if wanted in (BATCH["id"], BATCH["public_token"]):
            return dict(BATCH)
        return None

    monkeypatch.setattr(store, "get_batch_with_videos", fake)


@pytest.fixture
def sent(monkeypatch) -> list[tuple[str, dict]]:
    """Record every MCP tool call the routes make, and answer none of them for real."""
    calls: list[tuple[str, dict]] = []

    async def fake(name: str, arguments: dict | None = None) -> Any:
        calls.append((name, arguments or {}))
        if name == "terac_create_opportunity":
            return {
                "id": "opp-new",
                "status": "draft",
                "pricing": {"total_cost_cents": 1850},
                "links": {"dashboard": {"draft_editor": "https://terac.com/x/create?id=opp-new"}},
            }
        return {}

    monkeypatch.setattr(terac, "call_json", fake)
    monkeypatch.setattr(terac_routes.terac, "call_json", fake)
    monkeypatch.setattr(terac.settings(), "terac_api_key", "tk_test_key")
    return calls


# -- publish ---------------------------------------------------------------


async def test_publish_defaults_to_a_dry_run_and_sends_nothing(client, batch, sent):
    async with client as c:
        response = await c.post(f"/v1/review-batches/{BATCH['id']}/publish-to-terac", json={})
    body = response.json()
    assert response.status_code == 200
    assert body["dry_run"] is True
    assert sent == [], "a dry run must not reach Terac at all"


async def test_the_dry_run_payload_points_terac_at_our_judge_link(client, batch, sent):
    """The entire integration, asserted: judge_url in, task_url out."""
    async with client as c:
        response = await c.post(f"/v1/review-batches/{BATCH['id']}/publish-to-terac", json={})
    body = response.json()
    task = body["payload"]["tasks"][0]
    assert task["task_url"] == body["judge_url"] == "http://api.test/v1/review-batches/tok-abc"
    assert task["task_type"] == "activity"
    assert body["payload"]["num_participants"] == 5


async def test_publish_refuses_more_participants_than_the_budget_can_hold(client, batch, sent):
    async with client as c:
        response = await c.post(
            f"/v1/review-batches/{BATCH['id']}/publish-to-terac", json={"num_participants": 50}
        )
    assert response.status_code == 422


async def test_publish_creates_only_a_draft_never_a_launch(client, batch, sent):
    async with client as c:
        response = await c.post(
            f"/v1/review-batches/{BATCH['id']}/publish-to-terac", json={"dry_run": False}
        )
    body = response.json()
    assert [name for name, _ in sent] == ["terac_create_opportunity"]
    assert body["status"] == "draft"
    assert body["estimated_cost"] == "$18.50"
    assert "launch" in body["note"]


async def test_no_route_anywhere_can_launch_an_opportunity():
    """A launch is irreversible, so it is not reachable over HTTP by any path."""
    paths = [getattr(route, "path", "") for route in app.routes]
    assert not [p for p in paths if "launch" in p.lower()]


async def test_publish_404s_on_an_unknown_batch(client, batch, sent):
    async with client as c:
        response = await c.post(
            "/v1/review-batches/22222222-2222-2222-2222-222222222222/publish-to-terac", json={}
        )
    assert response.status_code == 404


# -- status ----------------------------------------------------------------


async def test_status_reports_not_configured_rather_than_failing(client, monkeypatch):
    monkeypatch.setattr(terac.settings(), "terac_api_key", None)
    async with client as c:
        response = await c.get("/v1/terac/status")
    assert response.status_code == 200
    assert response.json()["configured"] is False


async def test_status_degrades_to_a_detail_line_when_terac_is_down(client, monkeypatch):
    async def boom(*_a: Any, **_kw: Any) -> Any:
        raise terac.TeracError("terac tools/list HTTP 503")

    monkeypatch.setattr(terac.settings(), "terac_api_key", "tk_test_key")
    monkeypatch.setattr(terac_routes.terac, "org_summary", boom)
    async with client as c:
        response = await c.get("/v1/terac/status")
    assert response.status_code == 200
    assert "503" in response.json()["detail"]


# -- sync ------------------------------------------------------------------


async def test_sync_never_invents_a_rating(client, batch, monkeypatch, sent):
    """A submission with prose and no vote is reported, not scored."""
    monkeypatch.setattr(
        terac_routes.terac, "get_opportunity",
        lambda _id: _async({"status": "active", "tasks": [{"task_url": "http://x/t/tok-abc"}]}),
    )
    monkeypatch.setattr(
        terac_routes.terac, "get_submissions",
        lambda *_a, **_k: _async([{"id": "s1", "status": "approved",
                                   "tasks": [{"response": "the second one"}]}]),
    )
    monkeypatch.setattr(terac_routes, "_refs_with_votes", lambda _b: _async(set()))

    def refuse(**_kw: Any):
        raise AssertionError("sync must not write a vote without an operator saying where")

    monkeypatch.setattr(store, "record_vote", refuse)

    async with client as c:
        response = await c.post("/v1/terac/opportunities/opp1/sync")
    body = response.json()
    assert body["comments_recorded"] == 0
    assert body["unlinked"][0]["reviewer_ref"] == "terac:s1"
    assert body["unlinked"][0]["text"] == "the second one"


async def test_sync_counts_a_submission_that_already_voted_as_linked(client, batch, monkeypatch):
    monkeypatch.setattr(
        terac_routes.terac, "get_opportunity",
        lambda _id: _async({"status": "active", "tasks": [{"task_url": "http://x/t/tok-abc"}]}),
    )
    monkeypatch.setattr(
        terac_routes.terac, "get_submissions",
        lambda *_a, **_k: _async([{"id": "s1", "status": "approved"}]),
    )
    monkeypatch.setattr(terac_routes, "_refs_with_votes", lambda _b: _async({"terac:s1"}))
    async with client as c:
        response = await c.post("/v1/terac/opportunities/opp1/sync")
    assert response.json()["votes_linked"] == 1


async def _async(value: Any) -> Any:
    return value

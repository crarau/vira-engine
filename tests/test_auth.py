"""Optional bearer auth: off by default, and never in front of a judge.

Two states and one boundary. With `VIRA_ENGINE_TOKEN` unset the service behaves
exactly as it did before this existed — that is the default and it is what
`CLAUDE.md` means by "public by design". With it set, the four generation
endpoints need the header and nothing else does.

The tests that matter most are the negative ones. A panellist arriving from
Terac holds a batch token and no credential, so if the gate ever grows into
"all POSTs" their votes start 401ing and the panel spend is wasted before anyone
notices. `test_the_judge_flow_is_never_gated` is the test that fails first.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from vira.api import auth, store, worker
from vira.api.app import app

TOKEN = "engine-token-under-test"


@pytest.fixture
def client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://api.test", timeout=30
    )


@pytest.fixture(autouse=True)
def open_by_default(monkeypatch):
    """No token in the environment unless a test sets one."""
    monkeypatch.delenv(auth.ENV_VAR, raising=False)


@pytest.fixture
def gated(monkeypatch):
    monkeypatch.setenv(auth.ENV_VAR, TOKEN)


@pytest.fixture
def no_such_company(monkeypatch):
    """Let a request through to the route and stop it there, before any spend.

    A 404 from the route is the proof that the gate did not intercept — asserting
    only on "not 401" would pass against a middleware that silently swallowed it.
    """
    async def none(_slug: str) -> dict | None:
        return None

    monkeypatch.setattr(worker, "resolve_company", none)


# --- default: nothing is gated -------------------------------------------


def test_no_token_in_the_environment_means_the_gate_is_off():
    assert auth.enabled() is False
    assert auth.authorised(None) is True


async def test_generation_is_open_when_no_token_is_configured(client, no_such_company):
    async with client as c:
        r = await c.post("/v1/videos", json={"company_slug": "nope", "product": "oats"})
    assert r.status_code == 404, "the route answered, so the gate let it through"


async def test_the_withdrawn_image_proxy_is_gone_not_merely_unrouted(client):
    """It was public and documented, so a 404 is the contract now."""
    async with client as c:
        r = await c.post("/v1/image", json={"prompt": "a jar"})
        models = await c.get("/v1/image/models")
    assert r.status_code == 404
    assert models.status_code == 404


# --- configured: the four write endpoints need the header ----------------


@pytest.mark.parametrize("method,path,body", [
    ("POST", "/v1/videos", {"company_slug": "x", "product": "oats"}),
    ("POST", "/v1/videos/11111111-1111-1111-1111-111111111111/regenerate", {}),
    ("POST", "/v1/briefs", {"brand": {"name": "X"}}),
    ("POST", "/v1/ads/image", {"brand": "Sunday Oats", "product": "oats"}),
])
async def test_a_configured_token_is_required_on_every_write(client, gated, method, path, body):
    async with client as c:
        r = await c.request(method, path, json=body)
    assert r.status_code == 401
    assert r.headers["www-authenticate"].startswith("Bearer")


async def test_the_wrong_token_is_a_401_not_a_403(client, gated):
    async with client as c:
        r = await c.post("/v1/videos", json={"company_slug": "x", "product": "oats"},
                         headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401


async def test_a_non_bearer_scheme_does_not_pass(client, gated):
    async with client as c:
        r = await c.post("/v1/videos", json={"company_slug": "x", "product": "oats"},
                         headers={"Authorization": f"Basic {TOKEN}"})
    assert r.status_code == 401


async def test_the_right_token_reaches_the_route(client, gated, no_such_company):
    async with client as c:
        r = await c.post("/v1/videos", json={"company_slug": "nope", "product": "oats"},
                         headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 404


# --- what the gate must never touch --------------------------------------


async def test_healthz_is_never_gated(client, gated):
    async with client as c:
        r = await c.get("/healthz")
    assert r.status_code == 200


@pytest.mark.parametrize("path", ["/v1/lanes", "/"])
async def test_reads_are_never_gated(client, gated, path):
    async with client as c:
        r = await c.get(path)
    assert r.status_code == 200


async def test_the_judge_flow_is_never_gated(client, gated, monkeypatch):
    """Panellists arrive from Terac with a batch token and no credential."""
    batch = {"id": "11111111-1111-1111-1111-111111111111", "public_token": "tok-abc",
             "title": "five cuts", "videos": [{"id": "22222222-2222-2222-2222-222222222222",
                                               "position": 0, "hook": "h", "mp4_path": "a.mp4"}]}

    async def fake_batch(*_a: Any, **_kw: Any) -> dict:
        return dict(batch)

    async def fake_vote(**kwargs: Any) -> dict:
        return {"reviewer_ref": kwargs["reviewer_ref"], "video_id": str(kwargs["video_id"])}

    monkeypatch.setattr(store, "get_batch_with_videos", fake_batch)
    monkeypatch.setattr(store, "record_vote", fake_vote)

    async with client as c:
        read = await c.get("/v1/review-batches/tok-abc")
        vote = await c.post("/v1/review-batches/tok-abc/votes", json={
            "reviewer_ref": "terac:abc123",
            "video_id": "22222222-2222-2222-2222-222222222222",
            "rating": 4,
        })
    assert read.status_code == 200
    assert vote.status_code != 401, "a judge has no engine token and never will"


async def test_the_gated_set_is_enumerated_not_derived_from_the_method():
    """If this list ever becomes "every POST", the judge routes break silently."""
    assert auth.is_gated("POST", "/v1/videos") is True
    assert auth.is_gated("GET", "/v1/videos") is False
    assert auth.is_gated("POST", "/v1/videos/abc") is False
    assert auth.is_gated("POST", "/v1/review-batches/tok/votes") is False
    assert auth.is_gated("POST", "/v1/companies") is False
    assert auth.is_gated("OPTIONS", "/v1/videos") is False


async def test_a_rejection_still_carries_cors_headers(client, gated):
    """CORS is the outer layer, so a browser sees a 401 rather than a dead fetch."""
    async with client as c:
        r = await c.post("/v1/videos", json={}, headers={"Origin": "https://lovable.app"})
    assert r.status_code == 401
    assert r.headers.get("access-control-allow-origin") == "*"

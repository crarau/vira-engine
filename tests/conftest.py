"""Shared fixtures.

Two things every test in here depends on:

1. **Pinned settings.** `settings()` is `lru_cache`d and reads the repo's `.env`
   at first call, so a developer whose `.env` sets `MAX_AGE_DAYS=7` would change
   what "the age filter" means for the whole suite. The autouse fixture pins the
   documented defaults and restores whatever was there afterwards.
2. **No network.** Nothing here builds a real client. The one module that
   imports a vendor SDK at call time (`vira.llm`) is fed a fake in its own test.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from vira.config import settings
from vira.models import Beat, Company, Remix, Score, Trend, Word

BASELINE = {
    "max_age_days": 90,
    "shortlist_size": 20,
    "max_per_format": 4,
    "english_only": True,
    "surface_threshold": 4.5,
    "watchlist_threshold": 3.5,
    "evidence_floor": 3.0,
    "fps": 30,
    "llm_model": "claude-sonnet-5",
    "anthropic_api_key": "test-key",
}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """The suite must pass on a plane. Anything reaching for a socket is a hole
    in a mock, and the failure should say so rather than hang for 20 seconds."""
    import socket

    def blocked(*_a, **_kw):
        raise RuntimeError("a test tried to open a network connection")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)


@pytest.fixture(autouse=True)
def cfg():
    s = settings()
    before = {k: getattr(s, k) for k in BASELINE}
    for k, v in BASELINE.items():
        setattr(s, k, v)
    yield s
    for k, v in before.items():
        setattr(s, k, v)


def make_trend(key: str = "t1", *, age_days: float | None = 10, **kw) -> Trend:
    posted = (
        None
        if age_days is None
        else datetime.now(timezone.utc) - timedelta(days=age_days)
    )
    fields = {
        "trend_key": key,
        "caption": "a perfectly ordinary english caption about snacks",
        "source_url": f"https://www.tiktok.com/@someone/video/{key}",
        "author": "someone",
        "format": "unboxing",
        "trend_score": 50.0,
        "posted_at": posted,
    }
    fields.update(kw)
    return Trend(**fields)


def trend_row(key: str = "t1", *, age_days: float | None = 10, **kw) -> dict:
    """A raw corpus row as `fresh_company_trends` hands it to the selector."""
    posted = (
        None
        if age_days is None
        else (datetime.now(timezone.utc) - timedelta(days=age_days)).isoformat()
    )
    row = {
        "trend_key": key,
        "platform": "tiktok",
        "title": key,
        "caption": "a perfectly ordinary english caption about snacks",
        "source_url": f"https://www.tiktok.com/@someone/video/{key}",
        "author": "someone",
        "format": "unboxing",
        "hashtags": ["snacks"],
        "views": 1000,
        "likes": 100,
        "engagement_rate": 0.1,
        "trend_score": 50.0,
        "posted_at": posted,
        "relevance_rank": 1,
    }
    row.update(kw)
    return row


def make_company(**kw) -> Company:
    fields = {"id": "c1", "name": "Sunday Oats", "slug": "sunday-oats", "bio": "oats"}
    fields.update(kw)
    return Company(**fields)


def make_remix(*, beats: list[Beat] | None = None, **kw) -> Remix:
    fields = {
        "hook": "Everyone's chasing heat.",
        "beats": beats
        if beats is not None
        else [
            Beat(say="Hello world", show="a bowl"),
            Beat(say="Goodbye now", show="a spoon"),
        ],
        "caption": "a caption",
        "hashtags": ["oats"],
        "cta": "Try it",
        "why_this_works": "because",
        "grounded_in": ["t1"],
    }
    fields.update(kw)
    return Remix(**fields)


def make_score(**kw) -> Score:
    fields = dict(
        relevance=4.0,
        specificity=4.0,
        actionability=4.0,
        differentiation=4.0,
        evidence=4.0,
    )
    fields.update(kw)
    return Score(**fields)


def word(w: str, start: float, end: float) -> Word:
    return Word(w=w, start=start, end=end)

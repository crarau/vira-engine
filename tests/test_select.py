"""Stage 1 — selection.

The whole downstream pipeline inherits whatever this stage lets through, so the
filters are tested individually and the rejection counter is tested as a
product surface in its own right: it is the "what the engine rejected" panel,
and a wrong number there is a wrong claim to a judge.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from vira import select as sel
from tests.conftest import make_company, trend_row


class FakeSupa:
    """Stands in for PostgREST. Records nothing it is not asked about."""

    def __init__(self, category_id: str | None = "cat-1"):
        self.category_id = category_id
        self.select_calls: list[dict] = []

    async def select(self, table, **kw):
        self.select_calls.append({"table": table, **kw})
        if table == "companies":
            return [{"category_id": self.category_id}] if self.category_id else []
        return []


@pytest.fixture
def rows_patch(monkeypatch):
    """Swap the database join for a list of synthetic rows."""

    captured: dict = {}

    def install(rows):
        async def fake(supa, category_id, *, since_iso, limit=300):
            captured["category_id"] = category_id
            captured["since_iso"] = since_iso
            captured["limit"] = limit
            return rows

        monkeypatch.setattr(sel, "fresh_company_trends", fake)
        return captured

    return install


# --- _looks_english ---------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "the best overnight oats you will ever eat",
        "Café crème brûlée, naïve façade",  # accented latin is still latin
        "Check out this amazing product right now 🔥",  # one emoji in a long line
        "",
        "https://www.tiktok.com/@x/video/123",  # URL-only strips to nothing
    ],
)
def test_looks_english_accepts(text):
    assert sel._looks_english(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "これは日本語のキャプションです",
        "снэки для вечеринки очень вкусные",
        "🔥🔥🔥🔥🔥🔥🔥🔥🔥🔥",
        "makanan ringan enak 🇮🇩 你好世界 你好世界 你好世界",
    ],
)
def test_looks_english_rejects(text):
    assert sel._looks_english(text) is False


def test_looks_english_ignores_urls_when_judging():
    """A caption is judged on its words, not on a link that happens to be latin."""
    assert sel._looks_english("你好世界你好 https://tiktok.com/@x/video/1") is False


# --- _parse -----------------------------------------------------------------


def test_parse_survives_a_bad_row():
    assert sel._parse({"caption": "no trend_key here"}) is None


def test_parse_coerces_loose_types():
    t = sel._parse(trend_row("t1", views="123", likes=None, engagement_rate="0.5"))
    assert (t.views, t.likes, t.engagement_rate) == (123, 0, 0.5)


# --- shortlist --------------------------------------------------------------


async def test_no_category_short_circuits(rows_patch):
    rows_patch([trend_row("t1")])
    picked, rejected = await sel.shortlist(
        FakeSupa(category_id=None), make_company(), "oats"
    )
    assert picked == []
    assert rejected == {"company has no category": 1}


async def test_age_filter_drops_stale_and_undated(cfg, rows_patch):
    rows_patch(
        [
            trend_row("fresh", age_days=10),
            trend_row("stale", age_days=400),
            trend_row("undated", age_days=None),
        ]
    )
    picked, rejected = await sel.shortlist(FakeSupa(), make_company(), "oats")

    assert [t.trend_key for t in picked] == ["fresh"]
    # Undated rows report age 9999, so they fall out through the same branch.
    assert rejected == {"older than 90d": 2}


async def test_age_window_sent_to_the_database_matches_max_age_days(cfg, rows_patch):
    """The primary age cut runs in SQL; the Python check is only a backstop."""
    cfg.max_age_days = 30
    captured = rows_patch([])
    await sel.shortlist(FakeSupa(), make_company(), "oats")

    since = datetime.fromisoformat(captured["since_iso"])
    age = (datetime.now(timezone.utc) - since).total_seconds() / 86_400
    assert 29.9 < age < 30.1


async def test_missing_source_url_is_rejected(rows_patch):
    rows_patch([trend_row("good"), trend_row("bare", source_url="")])
    picked, rejected = await sel.shortlist(FakeSupa(), make_company(), "oats")

    assert [t.trend_key for t in picked] == ["good"]
    assert rejected == {"no source url": 1}


async def test_non_english_is_rejected_when_the_flag_is_on(cfg, rows_patch):
    rows_patch(
        [trend_row("en"), trend_row("jp", caption="これは日本語のキャプションです")]
    )
    picked, rejected = await sel.shortlist(FakeSupa(), make_company(), "oats")
    assert [t.trend_key for t in picked] == ["en"]
    assert rejected == {"not english": 1}


async def test_english_filter_is_skippable(cfg, rows_patch):
    cfg.english_only = False
    rows_patch(
        [trend_row("en"), trend_row("jp", caption="これは日本語のキャプションです")]
    )
    picked, rejected = await sel.shortlist(FakeSupa(), make_company(), "oats")
    assert {t.trend_key for t in picked} == {"en", "jp"}
    assert rejected == {}


async def test_format_quota_caps_each_format_and_counts_the_overflow(cfg, rows_patch):
    cfg.max_per_format = 2
    rows_patch(
        [trend_row(f"u{i}", format="unboxing", trend_score=100 - i) for i in range(5)]
        + [trend_row("d0", format="duet", trend_score=10)]
    )
    picked, rejected = await sel.shortlist(FakeSupa(), make_company(), "oats")

    assert [t.trend_key for t in picked] == ["u0", "u1", "d0"]
    assert rejected == {"format quota": 3}


async def test_blank_format_is_quota_counted_as_one_bucket(cfg, rows_patch):
    """An empty `format` must not become an unlimited pass-through lane."""
    cfg.max_per_format = 2
    rows_patch([trend_row(f"x{i}", format="") for i in range(4)])
    picked, rejected = await sel.shortlist(FakeSupa(), make_company(), "oats")

    assert len(picked) == 2
    assert rejected == {"format quota": 2}


async def test_ranked_by_trend_score_not_by_views(cfg, rows_patch):
    rows_patch(
        [
            trend_row("viral-old", views=10_000_000, trend_score=5, format="a"),
            trend_row("strong", views=1_000, trend_score=90, format="b"),
            trend_row("middling", views=5_000, trend_score=40, format="c"),
        ]
    )
    picked, _ = await sel.shortlist(FakeSupa(), make_company(), "oats")
    assert [t.trend_key for t in picked] == ["strong", "middling", "viral-old"]


async def test_limit_truncates_after_ranking(cfg, rows_patch):
    rows_patch(
        [trend_row(f"t{i}", format=f"f{i}", trend_score=100 - i) for i in range(10)]
    )
    picked, _ = await sel.shortlist(FakeSupa(), make_company(), "oats", limit=3)
    assert [t.trend_key for t in picked] == ["t0", "t1", "t2"]


async def test_limit_defaults_to_the_configured_shortlist_size(cfg, rows_patch):
    cfg.shortlist_size = 2
    cfg.max_per_format = 99
    rows_patch([trend_row(f"t{i}", trend_score=100 - i) for i in range(6)])
    picked, _ = await sel.shortlist(FakeSupa(), make_company(), "oats")
    assert len(picked) == 2


async def test_rejection_counts_sum_to_everything_not_kept(cfg, rows_patch):
    """The panel claims "rejected N of M"; N + kept must equal M or the claim lies."""
    cfg.max_per_format = 1
    rows = [
        trend_row("keep", format="a"),
        trend_row("keep2", format="a"),  # quota
        trend_row("stale", format="b", age_days=500),
        trend_row("nourl", format="c", source_url=""),
        trend_row("jp", format="d", caption="これは日本語のキャプションです"),
    ]
    rows_patch(rows)
    picked, rejected = await sel.shortlist(FakeSupa(), make_company(), "oats")

    assert len(picked) + sum(rejected.values()) == len(rows)
    assert sorted(rejected) == ["format quota", "no source url", "not english",
                                "older than 90d"]

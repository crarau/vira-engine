"""Integration tests for vira.api.store, against a real Postgres.

These deliberately do not mock the database. Nearly everything store.py relies
on is Postgres behaviour rather than Python behaviour — jsonb round-tripping
through the codec in db.py, `ON CONFLICT` upserts, `FILTER` aggregates,
`array_agg`, and transaction rollback across four tables. A fake would confirm
that the strings in store.py have not changed, which is not the property worth
protecting.

Two of the assertions here are enforcing stated design rules rather than
guarding an implementation detail, and should not be relaxed to make a change
pass:

**A video and its explanation land together or not at all.** A video row
without its prompts, corpus and settings is unreproducible, which defeats the
reason the recipe tables exist. `test_failed_write_rolls_back_the_whole_video`
proves the transaction actually covers all four.

**Judges never see the engine's own grade.** `get_batch_with_videos` is the
judge-facing read; showing it `score`, `disposition` or `drop_reason` would
anchor the humans to the number the panel exists to check independently.

Start the database with `sql/dev-db.sh start`. With nothing listening the whole
module skips, so the offline unit suite still runs.
"""

from __future__ import annotations

import os
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text

# Set before vira.config.settings() is first constructed — it is lru_cached, so
# the first read of API_DATABASE_URL is the only one. Matches sql/dev-db.sh.
DEFAULT_URL = "postgresql://vira:vira-local-dev@127.0.0.1:55432/vira"
os.environ.setdefault("API_DATABASE_URL", DEFAULT_URL)

from vira.api import db, store  # noqa: E402  (import must follow the env default)

SCHEMA_PATH = db.SCHEMA_PATH

# Every test coins its own slugs and hooks from this, so runs can share a
# long-lived dev database without a truncate step between them.
def _tag() -> str:
    return uuid.uuid4().hex[:12]


# asyncpg connections belong to the loop that opened them, and db.py caches one
# engine per process — so the pool and every test share a single session loop.
pytestmark = pytest.mark.asyncio(loop_scope="session")


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def database():
    """Live connection to the engine's own Postgres, or a clean skip."""
    try:
        await db.init_db()
    except Exception as exc:  # noqa: BLE001 - any failure to reach it means skip
        pytest.skip(f"no database at {os.environ['API_DATABASE_URL']}: {exc}")
    yield
    await db.close_db()


@pytest_asyncio.fixture(loop_scope="session")
async def company(database):
    tag = _tag()
    return await store.upsert_company(slug=f"test-{tag}", name=f"Test {tag}")


@pytest_asyncio.fixture(loop_scope="session")
async def job(company):
    return await store.create_job(company_slug=company["slug"], product="Test Widget")


def recipe_fixture(**overrides) -> dict:
    """A recipe shaped like the one vira.provenance.Recorder.finish writes."""
    recipe = {
        "output": {
            "hook": "The hook",
            "cta": "Buy it",
            "caption": "A caption",
            "hashtags": ["#one", "#two"],
            "narration": "Narration text",
            "beats": [{"index": 0, "text": "beat zero"}],
        },
        "score": {"clarity": 4.0, "hook": 5.0, "evidence": 4.5, "craft": 4.0, "fit": 4.5},
        "notes": {"lane": "demo-first", "plan": "open on the product", "critique": "tighten"},
        "settings": {"evidence_floor": 3.0},
        "voice_id": "voice-abc",
        "git_commit": "deadbeef",
        "generated_at": "2026-08-15T00:00:00Z",
        "product": "Test Widget",
        "corpus": [{"source_url": "https://example.com/a", "title": "A"}],
        "llm_calls": [
            {
                "n": 1, "model": "claude-sonnet-5", "max_tokens": 2000,
                "stop_reason": "end_turn", "system_prompt": "You are a director.",
                "user_prompt": "Write a hook.", "response": "The hook",
            },
            {
                "n": 2, "model": "claude-sonnet-5", "max_tokens": None,
                "stop_reason": None, "system_prompt": "You are a critic.",
                "user_prompt": "Critique it.", "response": "Tighten it",
            },
        ],
        "stock": [
            {"path": "/shots/0.png", "prompt": "a widget on a bench",
             "credit": "gemini", "description": "a widget on a wooden bench"},
        ],
    }
    recipe.update(overrides)
    return recipe


async def scalar(sql: str, **params):
    async with db.connection() as conn:
        return (await conn.execute(text(sql), params)).scalar()


# -- connection string -----------------------------------------------------
# These need no database, so they stay useful when the suite is run offline.


async def test_normalise_puts_the_driver_in_the_scheme():
    url, connect_args = db._normalise("postgres://u:p@host:5432/vira")
    assert url == "postgresql+asyncpg://u:p@host:5432/vira"
    assert connect_args == {}


async def test_sslmode_is_passed_through_as_libpq_spells_it():
    """A regression guard, not a style preference.

    asyncpg reads `ssl=True` as verify-full — hostname checked against a CA
    chain from ~/.postgresql/root.crt. Every managed provider that issues a
    `?sslmode=require` URL (Render, Heroku, the Supabase pooler) serves a
    certificate that fails that check, so collapsing the mode to a bool turns a
    valid connection string into CERTIFICATE_VERIFY_FAILED at boot. libpq's
    "require" means encrypt without verifying; asyncpg honours the string.
    """
    url, connect_args = db._normalise("postgresql://u:p@host/vira?sslmode=require")

    assert url == "postgresql+asyncpg://u:p@host/vira"  # asyncpg rejects sslmode
    assert connect_args == {"ssl": "require"}
    assert connect_args["ssl"] is not True


@pytest.mark.parametrize("mode", ["disable", "allow", "prefer", "require", "verify-full"])
async def test_every_sslmode_survives_verbatim(mode):
    _, connect_args = db._normalise(f"postgresql://u:p@host/vira?sslmode={mode}")
    assert connect_args == {"ssl": mode}


# -- schema ----------------------------------------------------------------


async def test_schema_is_idempotent(database, job):
    """Re-applying schema.sql must be a no-op, because the deploy always does.

    Run against a database that already has rows in it (the `job` fixture puts
    them there) — an empty-database pass would not catch a statement that
    rewrites or drops something.
    """
    before = await scalar("SELECT count(*) FROM jobs")

    await db.init_db()  # would raise on any error in the file

    assert await scalar("SELECT count(*) FROM jobs") == before
    assert await scalar("SELECT to_regclass('public.videos')::text") == "videos"


# -- companies -------------------------------------------------------------


async def test_upsert_company_round_trips(database):
    tag = _tag()
    written = await store.upsert_company(
        slug=f"acme-{tag}", name="Acme", bio="We make things",
        mission="Make more things", website="https://acme.test",
        category="Manufacturing", owner_name="Wile E.",
    )

    assert written["slug"] == f"acme-{tag}"
    assert written["name"] == "Acme"
    assert written["bio"] == "We make things"
    assert written["website"] == "https://acme.test"
    # Boundary conversion: a route hands these straight to a JSON response.
    assert isinstance(written["id"], str)
    assert isinstance(written["created_at"], str)

    listed = [c for c in await store.list_companies(limit=1000) if c["id"] == written["id"]]
    assert listed == [written]


async def test_upsert_same_slug_updates_in_place(database):
    """Re-seeding a company must not fork it — its jobs would be orphaned."""
    tag = _tag()
    first = await store.upsert_company(slug=f"dup-{tag}", name="Before", bio="old")
    second = await store.upsert_company(slug=f"dup-{tag}", name="After", bio="new")

    assert second["id"] == first["id"]
    assert second["name"] == "After"
    assert second["bio"] == "new"
    assert await scalar(
        "SELECT count(*) FROM companies WHERE slug = :slug", slug=f"dup-{tag}"
    ) == 1


# -- jobs ------------------------------------------------------------------


async def test_job_transitions_queued_running_done(company):
    created = await store.create_job(
        company_slug=company["slug"], product="Widget", lane="demo-first"
    )
    assert created["status"] == "queued"
    assert created["started_at"] is None
    assert created["finished_at"] is None

    fetched = await store.get_job(created["id"])
    assert fetched["id"] == created["id"]
    assert fetched["product"] == "Widget"
    assert fetched["videos"] == []

    running = await store.update_job_status(
        created["id"], "running", progress_note="verifying 20 sources"
    )
    assert running["status"] == "running"
    assert running["started_at"] is not None
    assert running["progress_note"] == "verifying 20 sources"

    # A second progress report must not reset the clock the first one started.
    again = await store.update_job_status(created["id"], "running", progress_note="rendering")
    assert again["started_at"] == running["started_at"]
    assert again["progress_note"] == "rendering"

    done = await store.update_job_status(created["id"], "done")
    assert done["status"] == "done"
    assert done["finished_at"] is not None
    # None means "leave it alone", not "clear it".
    assert done["progress_note"] == "rendering"

    assert (await store.get_job(created["id"]))["status"] == "done"


async def test_create_job_rejects_an_unknown_slug(database):
    with pytest.raises(LookupError):
        await store.create_job(company_slug=f"missing-{_tag()}", product="Widget")


async def test_get_job_returns_none_for_an_unknown_id(database):
    assert await store.get_job(uuid.uuid4()) is None


# -- videos ----------------------------------------------------------------


async def test_create_video_writes_four_tables_in_one_transaction(job):
    video = await store.create_video(
        job_id=job["id"], recipe=recipe_fixture(), mp4_path="/out/ad.mp4",
        duration_s=12.5, disposition="surfaced", audio_path="/out/voice.mp3",
    )

    assert video["hook"] == "The hook"
    assert video["hashtags"] == ["#one", "#two"]
    assert video["duration_s"] == 12.5
    assert video["disposition"] == "surfaced"
    # overall is the mean of the five dimensions, recomputed here.
    assert video["score"] == 4.4
    assert video["score_breakdown"]["clarity"] == 4.0

    vid = video["id"]
    assert await scalar("SELECT count(*) FROM recipes WHERE video_id = :v", v=vid) == 1
    assert await scalar("SELECT count(*) FROM llm_calls WHERE video_id = :v", v=vid) == 2
    # one generated shot plus the narration track
    assert await scalar("SELECT count(*) FROM assets WHERE video_id = :v", v=vid) == 2
    assert await scalar(
        "SELECT count(*) FROM assets WHERE video_id = :v AND kind = 'audio'", v=vid
    ) == 1

    assert len((await store.get_job(job["id"]))["videos"]) == 1


async def test_failed_write_rolls_back_the_whole_video(job):
    """A design rule, not an implementation detail.

    A video row whose prompts, corpus and settings never landed is not
    reproducible, so a failure anywhere in the four writes must leave nothing
    behind. The NUL byte is rejected by Postgres itself (22021) while inserting
    llm_calls — i.e. *after* the videos row has already been written inside the
    transaction, which is exactly the case that must roll back.
    """
    hook = f"orphan-{_tag()}"
    doomed = recipe_fixture()
    doomed["output"]["hook"] = hook
    doomed["llm_calls"][0]["system_prompt"] = "before\x00after"

    jobs_before = await scalar("SELECT count(*) FROM videos WHERE job_id = :j", j=job["id"])

    with pytest.raises(Exception):  # noqa: B017 - driver wraps the Postgres error
        await store.create_video(job_id=job["id"], recipe=doomed)

    assert await scalar("SELECT count(*) FROM videos WHERE hook = :h", h=hook) == 0
    assert await scalar(
        "SELECT count(*) FROM videos WHERE job_id = :j", j=job["id"]
    ) == jobs_before
    # Nothing in the child tables either — they cascade from a row that must
    # never have existed.
    assert await scalar(
        "SELECT count(*) FROM llm_calls WHERE system_prompt LIKE 'before%'"
    ) == 0

    # The connection is still usable afterwards: a rolled-back transaction must
    # not poison the pool for the next caller.
    assert await store.create_video(job_id=job["id"], recipe=recipe_fixture()) is not None


async def test_get_recipe_returns_the_verbatim_prompts(job):
    """Verbatim means verbatim — quotes, newlines and unicode included.

    This table exists so that "this exact system prompt over this exact corpus
    wrote a bad hook" is answerable. Any normalisation on the way in or out
    destroys the only property it has.
    """
    system = "You are a 'director'.\nRules:\n  1. Don't be dull — ever.\t★"
    user = 'Write a hook for "Widget"; avoid the word \\"cheap\\".'
    recipe = recipe_fixture()
    recipe["llm_calls"][0]["system_prompt"] = system
    recipe["llm_calls"][0]["user_prompt"] = user

    video = await store.create_video(
        job_id=job["id"], recipe=recipe, audio_path="/out/voice.mp3"
    )
    stored = await store.get_recipe(video["id"])

    assert stored["llm_calls"][0]["system_prompt"] == system
    assert stored["llm_calls"][0]["user_prompt"] == user
    assert [c["n"] for c in stored["llm_calls"]] == [1, 2]
    assert stored["llm_calls"][1]["max_tokens"] is None
    # A recipe written before the stage was recorded still round-trips; the
    # column is additive and empty means "nobody said", not "unknown stage".
    assert stored["llm_calls"][0]["stage"] == ""

    # jsonb comes back as dicts and lists, not as JSON text.
    assert stored["plan"] == recipe["notes"]
    assert stored["corpus"] == recipe["corpus"]
    assert stored["beats"] == recipe["output"]["beats"]
    assert stored["settings"]["voice_id"] == "voice-abc"
    assert stored["settings"]["git_commit"] == "deadbeef"

    assert {a["kind"] for a in stored["assets"]} == {"image", "audio"}


async def test_get_recipe_returns_none_for_an_unknown_video(database):
    assert await store.get_recipe(uuid.uuid4()) is None


async def test_list_videos_for_company_takes_an_id_or_a_slug(company, job):
    await store.create_video(job_id=job["id"], recipe=recipe_fixture())

    by_slug = await store.list_videos_for_company(company["slug"])
    by_id = await store.list_videos_for_company(company["id"])

    assert len(by_slug) == 1
    assert by_slug == by_id
    assert by_slug[0]["company_slug"] == company["slug"]


# -- human review ----------------------------------------------------------


async def test_review_batch_votes_and_results(job):
    videos = [
        await store.create_video(job_id=job["id"], recipe=recipe_fixture()),
        await store.create_video(job_id=job["id"], recipe=recipe_fixture()),
    ]
    ids = [v["id"] for v in videos]

    batch = await store.create_review_batch(title="Lane test", video_ids=ids)
    assert batch["video_count"] == 2
    assert batch["public_token"]
    assert batch["closed_at"] is None

    fetched = await store.get_batch_with_videos(batch["public_token"])
    assert fetched["id"] == batch["id"]
    assert [v["id"] for v in fetched["videos"]] == ids  # given order is presentation order
    assert [v["position"] for v in fetched["videos"]] == [0, 1]

    await store.record_vote(
        batch_id=batch["id"], video_id=ids[0], reviewer_ref="judge-1",
        rating=5, picked=True, comment="strong open",
    )
    await store.record_vote(
        batch_id=batch["id"], video_id=ids[0], reviewer_ref="judge-2", rating=3, picked=False,
    )
    await store.record_vote(
        batch_id=batch["id"], video_id=ids[1], reviewer_ref="judge-1", rating=2, picked=False,
    )

    first, second = await store.batch_results(batch["id"])

    assert first["video_id"] == ids[0]
    assert first["votes"] == 2
    assert first["avg_rating"] == 4.0
    assert first["picks"] == 1
    assert first["comments"] == ["strong open"]

    assert second["video_id"] == ids[1]
    assert second["votes"] == 1
    assert second["avg_rating"] == 2.0
    assert second["picks"] == 0
    assert second["comments"] == []


async def test_a_resubmitted_vote_replaces_rather_than_doubles(job):
    """Panel platforms retry. A double-counted 5 would bias the only signal."""
    video = await store.create_video(job_id=job["id"], recipe=recipe_fixture())
    batch = await store.create_review_batch(title="Retry", video_ids=[video["id"]])

    await store.record_vote(
        batch_id=batch["id"], video_id=video["id"], reviewer_ref="judge-1",
        rating=5, picked=True, comment="first take",
    )
    await store.record_vote(
        batch_id=batch["id"], video_id=video["id"], reviewer_ref="judge-1",
        rating=2, picked=False, comment="on reflection",
    )

    (row,) = await store.batch_results(batch["id"])
    assert row["votes"] == 1
    assert row["avg_rating"] == 2.0
    assert row["picks"] == 0
    assert row["comments"] == ["on reflection"]


async def test_an_unvoted_video_still_appears_with_zeros(job):
    """Dropping the row would hide the finding that nobody rated a lane."""
    video = await store.create_video(job_id=job["id"], recipe=recipe_fixture())
    batch = await store.create_review_batch(title="Silent", video_ids=[video["id"]])

    (row,) = await store.batch_results(batch["id"])
    assert row["votes"] == 0
    assert row["avg_rating"] is None
    assert row["picks"] == 0
    assert row["comments"] == []


async def test_judges_never_see_the_engine_grade(job):
    """A design rule: the panel exists to check the score, not to echo it.

    `get_batch_with_videos` is the judge-facing read, reached by an unguessable
    token and no account. Handing it the engine's own grade would anchor every
    human answer to the number under test.
    """
    graded = recipe_fixture()
    video = await store.create_video(
        job_id=job["id"], recipe=graded, disposition="dropped", drop_reason="thin evidence",
    )
    batch = await store.create_review_batch(title="Blind", video_ids=[video["id"]])

    for fetched in (
        await store.get_batch_with_videos(batch["public_token"]),
        await store.get_batch_with_videos(batch_id=batch["id"]),
    ):
        (entry,) = fetched["videos"]
        forbidden = {"score", "score_breakdown", "disposition", "drop_reason"}
        assert forbidden.isdisjoint(entry.keys())
        assert forbidden.isdisjoint(fetched.keys())
        # and the fields a judge does need are present
        assert {"id", "lane", "hook", "caption", "mp4_path", "position"} <= entry.keys()


async def test_get_batch_with_videos_needs_a_lookup_key(database):
    with pytest.raises(ValueError):
        await store.get_batch_with_videos()


async def test_unknown_token_is_a_miss_not_an_error(database):
    assert await store.get_batch_with_videos(f"nope-{_tag()}") is None


# -- injection -------------------------------------------------------------


HOSTILE = [
    "O'Brien",                                  # the bare single quote
    "Bobby'); DROP TABLE videos;--",            # the classic
    "' OR '1'='1",                              # always-true tail
    '"; DELETE FROM companies WHERE \'\'=\'',   # mixed quoting
    "%_\\",                                     # LIKE metacharacters
]


@pytest.mark.parametrize("hostile", HOSTILE)
async def test_hostile_text_round_trips_as_data(database, hostile):
    """Every value is a bind parameter, so this is text and never syntax."""
    tag = _tag()
    written = await store.upsert_company(
        slug=f"{hostile}-{tag}", name=hostile, bio=hostile, category=hostile,
    )

    assert written["slug"] == f"{hostile}-{tag}"
    assert written["name"] == hostile
    assert written["bio"] == hostile

    reread = [c for c in await store.list_companies(limit=1000) if c["id"] == written["id"]]
    assert reread == [written]

    # The tables the payloads tried to name are all still here.
    for table in ("videos", "companies", "jobs", "recipes"):
        assert await scalar(f"SELECT to_regclass('public.{table}')::text") == table


async def test_hostile_text_survives_the_whole_pipeline(company):
    """Including the paths that build a WHERE clause or aggregate comments."""
    hostile = "'; DROP TABLE llm_calls;--"

    job = await store.create_job(company_slug=company["slug"], product=hostile)
    recipe = recipe_fixture()
    recipe["output"]["hook"] = hostile
    recipe["llm_calls"][0]["user_prompt"] = hostile
    recipe["notes"]["lane"] = hostile

    video = await store.create_video(job_id=job["id"], recipe=recipe)
    assert video["hook"] == hostile
    assert (await store.get_recipe(video["id"]))["llm_calls"][0]["user_prompt"] == hostile

    # list_videos_for_company interpolates its WHERE clause, so a hostile slug
    # is the value most worth proving cannot reach the statement string.
    assert await store.list_videos_for_company(hostile) == []
    assert len(await store.list_videos_for_company(company["slug"])) == 1

    batch = await store.create_review_batch(title=hostile, video_ids=[video["id"]])
    await store.record_vote(
        batch_id=batch["id"], video_id=video["id"], reviewer_ref=hostile,
        rating=4, picked=True, comment=hostile,
    )
    (row,) = await store.batch_results(batch["id"])
    assert row["comments"] == [hostile]
    assert row["hook"] == hostile

    assert await scalar("SELECT to_regclass('public.llm_calls')::text") == "llm_calls"


async def test_a_recipe_keeps_the_stage_each_prompt_came_from(job):
    """Which stage wrote a prompt is what makes a long recipe navigable."""
    recipe = recipe_fixture()
    recipe["llm_calls"][0]["stage"] = "plan"
    recipe["llm_calls"][1]["stage"] = "critique"

    video = await store.create_video(job_id=job["id"], recipe=recipe)
    stored = await store.get_recipe(video["id"])

    assert [c["stage"] for c in stored["llm_calls"]] == ["plan", "critique"]

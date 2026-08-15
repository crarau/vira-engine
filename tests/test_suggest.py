"""Product suggestions — the bio grader and the cache.

Two things here are worth a test and the rest is the model's problem.

**The bio grader is the honesty switch.** Half the companies in the live
database have bios like "I am the ceo". If the grader calls one of those usable,
the endpoint quietly paraphrases it and the user gets a confident suggestion
built on nothing — the exact failure the endpoint exists to avoid. The fixtures
below are the real strings, not invented ones.

**The cache is the difference between 35s and 0.2s.** It guards a paid call, so
a regression that makes it miss is expensive and completely silent.
"""

from __future__ import annotations

import pytest

from vira.api.routes import suggest as mod

# Verbatim from the live Lovable rows — see docs/CORPUS-SURVEY.md §6.
REBULL_BIO = "I am the ceo"
CHIPS_BIO = "Selling chips"
SQUIRT_BIO = "i am the founder"
GIBBERISH_BIO = "rkwejtkwegrg"
OVERCAST_BIO = (
    "Overcast makes a mineral SPF 50 serum for people who gave up on sunscreen "
    "because every mineral formula they tried left a grey cast on their skin. "
    "We mill non-nano zinc finer than the category standard and suspend it in "
    "squalane, so it disappears on deeper skin tones and you actually reapply "
    "it at lunchtime instead of skipping it."
)


@pytest.fixture(autouse=True)
def clean_cache():
    mod._CACHE.clear()
    mod._LOCKS.clear()
    yield
    mod._CACHE.clear()
    mod._LOCKS.clear()


# --- the bio grader ------------------------------------------------------


@pytest.mark.parametrize("bio", [REBULL_BIO, CHIPS_BIO, SQUIRT_BIO, GIBBERISH_BIO, ""])
def test_the_real_junk_bios_are_called_junk(bio):
    graded = mod.grade_bio(bio)
    assert graded.verdict == "junk", f"{bio!r} graded {graded.verdict}"
    assert graded.lean_on_corpus is True
    assert graded.reason


def test_keyboard_mash_is_junk_even_at_length():
    """A long bio is not a good bio — "does not read as words" must still fire."""
    graded = mod.grade_bio("rkwejtkwegrg " * 8)
    assert graded.verdict == "junk"
    assert "words" in graded.reason


def test_a_real_bio_is_usable():
    graded = mod.grade_bio(OVERCAST_BIO, mission="Sunscreen people finish the bottle of.")
    assert graded.verdict == "usable"
    assert graded.words > mod.MIN_USABLE_WORDS


def test_a_category_without_a_mechanism_is_thin():
    """Neither junk nor trustworthy: real words, no product in them."""
    graded = mod.grade_bio("We sell sunscreen for everyone.")
    assert graded.verdict == "thin"
    assert graded.lean_on_corpus is True


def test_every_verdict_has_prompt_guidance():
    """A verdict with no guidance would KeyError inside a paid request."""
    for verdict in ("junk", "thin", "usable"):
        assert mod._GUIDANCE[verdict].strip()


# --- the junk-bio path end to end ---------------------------------------


def _company_row(slug: str, bio: str) -> dict:
    return {
        "id": "c1",
        "name": slug,
        "slug": slug,
        "bio": bio,
        "mission": "",
        "website": None,
        "categories": {"name": "Food & Beverage", "slug": "food-beverage"},
        "company_insights": [],
    }


def _stub(monkeypatch, *, bio: str, slug: str = "rebull", answer: dict | None = None):
    """Wire the three things `suggest` reaches out to, and count the LLM calls."""
    from tests.conftest import make_trend

    trends = [make_trend("VIRA-TR-1"), make_trend("VIRA-TR-2")]
    calls: list[str] = []

    async def fake_company(_supa, _slug):
        return _company_row(slug, bio)

    async def fake_shortlist(_supa, _company, _product, *, limit=None):
        return trends, {"not english": 3}

    async def fake_llm(prompt, *, system, max_tokens=4000):
        calls.append(prompt)
        return answer or {
            "suggestions": [
                {
                    "product": "a zero-sugar blood orange ginseng energy drink for the 5am build",
                    "angle": "the corpus rewards a named flavour over a category",
                    "lane": "demo-first",
                    "lane_reason": "the drink is the demo",
                    "grounded_in": ["VIRA-TR-1", "VIRA-TR-not-in-the-slice"],
                    "evidence": ["a pour shot at 2.3M views"],
                },
            ],
        }

    monkeypatch.setattr(mod, "get_company", fake_company)
    monkeypatch.setattr(mod, "shortlist", fake_shortlist)
    monkeypatch.setattr(mod, "complete_json", fake_llm)
    return calls


async def test_a_junk_bio_is_declared_not_hidden(monkeypatch):
    calls = _stub(monkeypatch, bio=REBULL_BIO)

    out = await mod.suggest("rebull", refresh=False)

    assert out.bio_quality.verdict == "junk"
    assert out.note and "unusable" in out.note
    # And the model was told to lean on the corpus rather than the bio.
    assert mod.JUNK_BIO_GUIDANCE in calls[0]


async def test_a_good_bio_does_not_carry_the_junk_warning(monkeypatch):
    _stub(monkeypatch, bio=OVERCAST_BIO, slug="overcast")

    out = await mod.suggest("overcast", refresh=False)

    assert out.bio_quality.verdict == "usable"
    assert "unusable" not in (out.note or "")


async def test_citations_outside_the_slice_are_stripped(monkeypatch):
    """The grounding rule is enforced in Python, not asked for in the prompt."""
    _stub(monkeypatch, bio=OVERCAST_BIO, slug="overcast")

    out = await mod.suggest("overcast", refresh=False)

    assert out.suggestions[0].grounded_in == ["VIRA-TR-1"]
    assert [s.trend_key for s in out.sources] == ["VIRA-TR-1"]


async def test_a_suggestion_citing_nothing_is_dropped(monkeypatch):
    _stub(monkeypatch, bio=OVERCAST_BIO, slug="overcast", answer={"suggestions": [
        {"product": "a mineral SPF 50 serum with no white cast", "lane": "demo-first",
         "grounded_in": ["VIRA-TR-invented"], "angle": "", "evidence": []},
        {"product": "a mineral SPF 50 serum for deeper skin tones", "lane": "not-a-lane",
         "grounded_in": ["VIRA-TR-1"], "angle": "", "evidence": []},
    ]})

    out = await mod.suggest("overcast", refresh=False)

    assert out.suggestions == []
    assert out.note and "dropped" in out.note


async def test_an_empty_corpus_slice_costs_no_llm_call(monkeypatch):
    calls = _stub(monkeypatch, bio=OVERCAST_BIO, slug="overcast")

    async def empty(_supa, _company, _product, *, limit=None):
        return [], {"older than 90d": 40}

    monkeypatch.setattr(mod, "shortlist", empty)
    out = await mod.suggest("overcast", refresh=False)

    assert calls == []
    assert out.suggestions == []
    assert out.note and "grounded" in out.note


# --- the cache -----------------------------------------------------------


async def test_the_second_read_does_not_pay_for_a_second_call(monkeypatch):
    calls = _stub(monkeypatch, bio=OVERCAST_BIO, slug="overcast")

    first = await mod.suggest("overcast", refresh=False)
    second = await mod.suggest("overcast", refresh=False)

    assert len(calls) == 1
    assert first.cached is False and second.cached is True
    assert [s.product for s in second.suggestions] == [s.product for s in first.suggestions]


async def test_refresh_bypasses_the_cache(monkeypatch):
    calls = _stub(monkeypatch, bio=OVERCAST_BIO, slug="overcast")

    await mod.suggest("overcast", refresh=False)
    out = await mod.suggest("overcast", refresh=True)

    assert len(calls) == 2
    assert out.cached is False


async def test_the_cache_expires(monkeypatch):
    calls = _stub(monkeypatch, bio=OVERCAST_BIO, slug="overcast")
    monkeypatch.setattr(mod, "TTL_SECONDS", -1)

    await mod.suggest("overcast", refresh=False)
    await mod.suggest("overcast", refresh=False)

    assert len(calls) == 2


async def test_the_key_carries_the_category(monkeypatch):
    """A recategorised company must miss — its old suggestions cite the wrong corpus."""
    _stub(monkeypatch, bio=OVERCAST_BIO, slug="overcast")
    await mod.suggest("overcast", refresh=False)

    assert list(mod._CACHE) == [("overcast", "food-beverage")]


async def test_concurrent_cold_reads_make_one_call(monkeypatch):
    """Two page loads racing on a cold cache must not both pay."""
    import asyncio

    calls = _stub(monkeypatch, bio=OVERCAST_BIO, slug="overcast")
    slow = mod.complete_json

    async def delayed(prompt, *, system, max_tokens=4000):
        await asyncio.sleep(0.01)
        return await slow(prompt, system=system, max_tokens=max_tokens)

    monkeypatch.setattr(mod, "complete_json", delayed)
    await asyncio.gather(*(mod.suggest("overcast", refresh=False) for _ in range(4)))

    assert len(calls) == 1


async def test_the_cache_is_bounded(monkeypatch):
    """10 companies today, but an unbounded dict in a long-lived process is a leak."""
    monkeypatch.setattr(mod, "CACHE_MAX", 3)
    for n in range(5):
        _stub(monkeypatch, bio=OVERCAST_BIO, slug=f"co{n}")
        await mod.suggest(f"co{n}", refresh=False)

    assert len(mod._CACHE) <= 3

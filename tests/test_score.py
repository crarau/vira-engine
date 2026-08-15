"""Stage 5 — the A-E eval and the evidence gate.

The gate is the one rule in the engine that a fluent model must not be able to
talk its way past: an ungrounded concept is dropped no matter how good the other
four dimensions look. If a refactor ever averages evidence in with the rest,
`test_evidence_gate_beats_a_high_average` is the test that fails.
"""

from __future__ import annotations

import pytest

from vira import score as sc
from vira.models import Score
from tests.conftest import make_company, make_remix, make_score, make_trend


# --- disposition ------------------------------------------------------------


def test_evidence_gate_beats_a_high_average(cfg):
    """Evidence 2.9 with four perfect fives averages 4.58 — above the surface
    threshold — and must still be dropped. This is the whole point of the gate."""
    s = Score(relevance=5, specificity=5, actionability=5, differentiation=5,
              evidence=2.9)
    assert s.overall >= cfg.surface_threshold

    dispo, reason = sc.disposition(s)
    assert dispo == "dropped"
    assert reason == "not supported by the cited source videos"


def test_evidence_exactly_at_the_floor_passes_the_gate(cfg):
    s = make_score(evidence=cfg.evidence_floor, relevance=5, specificity=5,
                   actionability=5, differentiation=5)
    assert sc.disposition(s)[0] == "surfaced"


def test_zero_evidence_is_dropped_even_with_a_watchlist_average(cfg):
    s = Score(relevance=5, specificity=5, actionability=5, differentiation=5,
              evidence=0)
    assert s.overall == 4.0  # would be watchlist on the average alone
    assert sc.disposition(s) == (
        "dropped", "not supported by the cited source videos"
    )


def test_surfaced_at_the_threshold(cfg):
    s = make_score(relevance=4.5, specificity=4.5, actionability=4.5,
                   differentiation=4.5, evidence=4.5)
    assert s.overall == 4.5
    assert sc.disposition(s) == ("surfaced", None)


def test_watchlist_band(cfg):
    s = make_score(relevance=4, specificity=4, actionability=4,
                   differentiation=4, evidence=4)
    assert s.overall == 4.0
    assert sc.disposition(s) == ("watchlist", None)


def test_watchlist_lower_boundary_is_inclusive(cfg):
    s = make_score(relevance=3.5, specificity=3.5, actionability=3.5,
                   differentiation=3.5, evidence=3.5)
    assert sc.disposition(s) == ("watchlist", None)


def test_below_the_watchlist_threshold_is_dropped_with_the_number(cfg):
    s = make_score(relevance=3, specificity=3, actionability=3,
                   differentiation=3, evidence=3.4)
    dispo, reason = sc.disposition(s)
    assert dispo == "dropped"
    assert str(s.overall) in reason


def test_thresholds_are_read_from_settings_at_call_time(cfg):
    """Retuning the bands must not require a restart or a re-import."""
    s = make_score(relevance=4, specificity=4, actionability=4,
                   differentiation=4, evidence=4)
    assert sc.disposition(s)[0] == "watchlist"
    cfg.surface_threshold = 3.9
    assert sc.disposition(s)[0] == "surfaced"


def test_the_gate_moves_with_the_evidence_floor(cfg):
    s = make_score(evidence=3.0)
    assert sc.disposition(s)[0] != "dropped"
    cfg.evidence_floor = 4.0
    assert sc.disposition(s) == (
        "dropped", "not supported by the cited source videos"
    )


# --- score_remix / clamp ----------------------------------------------------


@pytest.fixture
def fake_llm(monkeypatch):
    """Replace the model call; `score_remix` is being tested, not Anthropic."""
    calls: list[dict] = []

    def install(payload: dict):
        async def fake_complete_json(prompt, *, system, max_tokens=4000):
            calls.append({"prompt": prompt, "system": system,
                          "max_tokens": max_tokens})
            return payload

        monkeypatch.setattr(sc, "complete_json", fake_complete_json)
        return calls

    return install


async def test_scores_pass_through_when_the_model_behaves(fake_llm):
    fake_llm({"relevance": 4, "specificity": 3.5, "actionability": 5,
              "differentiation": 2, "evidence": 4.25})
    got = await sc.score_remix(make_company(), "oats", make_remix(), [make_trend()])

    assert (got.relevance, got.specificity, got.actionability,
            got.differentiation, got.evidence) == (4.0, 3.5, 5.0, 2.0, 4.25)


@pytest.mark.parametrize(
    "raw, expected",
    [
        (9, 5.0),            # above range
        (5.0001, 5.0),
        (-3, 0.0),           # below range
        ("4", 4.0),          # numeric string
        ("high", 0.0),       # prose instead of a number
        (None, 0.0),         # key present but null
        ([4], 0.0),          # wrong shape entirely
        ({"score": 4}, 0.0),
        (float("nan"), 0.0),  # NaN would poison every comparison downstream
    ],
)
async def test_clamp_refuses_to_trust_the_model(fake_llm, raw, expected):
    fake_llm({"relevance": raw})
    got = await sc.score_remix(make_company(), "oats", make_remix(), [make_trend()])
    assert got.relevance == expected


async def test_a_missing_dimension_scores_zero_not_a_free_pass(fake_llm):
    fake_llm({"relevance": 5})
    got = await sc.score_remix(make_company(), "oats", make_remix(), [make_trend()])
    assert got.evidence == 0.0
    assert sc.disposition(got)[0] == "dropped"


async def test_only_real_corpus_keys_are_quoted_as_evidence(fake_llm):
    """A hallucinated `grounded_in` key must not turn into a phantom citation."""
    calls = fake_llm({"relevance": 4, "evidence": 4})
    remix = make_remix(grounded_in=["t1", "does-not-exist"])
    await sc.score_remix(make_company(), "oats", remix, [make_trend("t1")])

    prompt = calls[0]["prompt"]
    assert "[t1]" in prompt
    assert "does-not-exist" in prompt  # still shown as a claim...
    assert prompt.count("does-not-exist") == 1  # ...but never as a cited video


async def test_no_citations_at_all_is_stated_explicitly(fake_llm):
    calls = fake_llm({"evidence": 0})
    await sc.score_remix(make_company(), "oats", make_remix(grounded_in=[]), [])
    assert "(none cited)" in calls[0]["prompt"]
    assert "nothing" in calls[0]["prompt"]

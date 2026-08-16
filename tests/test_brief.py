"""The Lovable brief, and where each of its fields lands.

The brief is a new input shape, not a new pipeline, so what these tests protect
is the mapping: that a field Lovable spent effort producing actually reaches the
prompt it is supposed to change, and that the two rules a richer input must not
weaken — verified grounding and the evidence gate — come out the other side
intact or stricter.
"""

from __future__ import annotations

import pytest

from vira import brief as B
from vira.lanes import get as get_lane
from vira.models import Score
from tests.conftest import make_score, make_trend, trend_row

LEAD_TREND = {
    "trendKey": "VIRA-TR-LEAD",
    "platform": "tiktok",
    "hook": "I gave it ten SECONDS",
    "format": "unboxing",
    "whyItWorks": "withholds the result until the midpoint",
    "weight": 0.9,
}
SECOND_TREND = {"trendKey": "VIRA-TR-SECOND", "weight": 0.4}
IMAGE_REF = {
    "imageKey": "IMG-1",
    "sourceUrl": "https://example.com/a",
    "imageUrl": "https://cdn.example.com/a.jpg",
    "weight": 0.6,
    "ocr": {"text": "50% off", "headline": "Half price", "cta": "Shop now",
            "confidence": 0.91},
    "sentiment": {"tone": "urgent", "score": 0.7, "emotionTags": ["fomo"],
                  "intent": "conversion", "urgency": "high"},
    "texture": {"palette": ["#0B0B0F", "#F5C518"], "lighting": "hard side light",
                "surfaceTexture": "brushed steel", "finish": "matte",
                "contrast": "high", "saturation": "low", "noiseLevel": "fine"},
    "composition": {"framing": "tight crop", "subject": "one hand",
                    "focalDepth": "shallow", "textPlacement": "lower third",
                    "negativeSpace": "generous"},
    "motion": {"impliedMotion": "a hand entering frame",
               "suggestedCamera": "push", "suggestedBeats": ["reveal"]},
    "keep": ["the steel counter"],
    "avoid": ["stock-photo smiles"],
}


def payload(**kw) -> dict:
    body = {
        "durationSeconds": 8,
        "aspectRatio": "9:16",
        "brand": {
            "name": "Sunday Oats",
            "slug": "sunday-oats",
            "bio": "overnight oats that set in the fridge in four hours",
            "mission": "breakfast that is already made when you wake up",
            "category": "Food & Beverage",
            "toneGuardrails": ["dry", "never chirpy"],
            "palette": ["#0B0B0F", "#F5C518"],
            "mustSay": ["four hours"],
            "neverSay": ["superfood", "game-changing"],
        },
        "references": [LEAD_TREND, IMAGE_REF, SECOND_TREND],
        "narrative": {
            "hook": "I stopped buying breakfast for two YEARS",
            "beats": [
                {"t": 0.0, "shot": "close on the jar", "onScreenText": "four hours"},
                {"t": 3.0, "shot": "hand lifting the lid", "onScreenText": "no cooking"},
            ],
            "voiceover": "It sets while you sleep.",
            "cta": "Tap to try the first batch",
            "textOverlayPolicy": "one line per beat",
        },
        "style": {"look": "cold kitchen at 6am", "palette": ["#0B0B0F"],
                  "pace": "slow burn", "musicMood": "ambient", "captions": True},
        "constraints": {"noRealPeopleLikeness": True, "noCompetitorMarks": True,
                        "language": "en", "safetyNotes": ["no health claims"]},
        "excluded": [],
        "signalQuality": "high",
    }
    body.update(kw)
    return body


def make_brief(**kw) -> B.Brief:
    return B.Brief.model_validate(payload(**kw))


# --- parsing --------------------------------------------------------------


def test_camel_case_and_snake_case_are_both_accepted():
    """Their generated client emits camelCase; a curl written by hand does not."""
    camel = B.Brief.model_validate(payload())
    snake = B.Brief.model_validate({
        "duration_seconds": 4,
        "brand": {"name": "Sunday Oats", "never_say": ["superfood"]},
        "signal_quality": "low",
    })
    assert camel.duration_seconds == 8
    assert snake.duration_seconds == 4
    assert snake.brand.never_say == ["superfood"]
    assert snake.signal_quality == "low"


def test_a_reference_is_typed_by_its_key_not_by_field_overlap():
    """An image reference carrying a `format` must not parse as a trend."""
    brief = make_brief(references=[{**IMAGE_REF, "format": "unboxing"}])
    assert brief.trend_refs == []
    assert len(brief.image_refs) == 1


def test_a_reference_with_neither_key_is_rejected():
    with pytest.raises(ValueError):
        make_brief(references=[{"weight": 1.0}])


# --- the reference set ----------------------------------------------------


def test_the_lead_reference_comes_first_because_order_is_dominance():
    brief = make_brief()
    assert [r.key for r in brief.kept] == ["VIRA-TR-LEAD", "IMG-1", "VIRA-TR-SECOND"]
    assert brief.lead is not None and brief.lead.key == "VIRA-TR-LEAD"


def test_an_explicit_lead_flag_outranks_a_higher_weight():
    brief = make_brief(references=[LEAD_TREND, {**SECOND_TREND, "lead": True}])
    assert brief.lead.key == "VIRA-TR-SECOND"


def test_excluded_assets_leave_the_reference_set_entirely():
    brief = make_brief(excluded=["VIRA-TR-LEAD"])
    assert [r.key for r in brief.kept] == ["IMG-1", "VIRA-TR-SECOND"]
    assert [r.trend_key for r in brief.trend_refs] == ["VIRA-TR-SECOND"]


# --- brand → Company ------------------------------------------------------


def test_the_brand_becomes_the_company_every_prompt_sees():
    company = B.company_from_brief(make_brief())
    context = company.context("cocoa oats")
    assert "Sunday Oats" in context
    assert "four hours" in context, "mustSay has to reach the prompts"
    assert "dry; never chirpy" in context


def test_a_missing_slug_is_derived_from_the_name():
    brief = B.Brief.model_validate({"brand": {"name": "Sunday Oats & Co."}})
    assert brief.slug == "sunday-oats-co"
    assert B.company_from_brief(brief).slug == "sunday-oats-co"


def test_the_lovable_row_supplies_the_id_the_brief_cannot():
    """Without the corpus id there is no category join, so selection cannot run."""
    row = {"id": "uuid-from-lovable", "name": "Old Name", "slug": "sunday-oats",
           "website": "https://sundayoats.example"}
    company = B.company_from_brief(make_brief(), row)
    assert company.id == "uuid-from-lovable"
    assert company.website == "https://sundayoats.example"
    # The brief is the newer statement of the brand and wins on content.
    assert company.name == "Sunday Oats"


def test_without_a_row_the_slug_stands_in_for_the_id():
    assert B.company_from_brief(make_brief()).id == "sunday-oats"


# --- durationSeconds ------------------------------------------------------


@pytest.mark.parametrize("seconds,beats,words", [(4, 2, 10), (6, 3, 16), (8, 4, 21)])
def test_duration_sets_the_beat_count_and_the_word_budget(seconds, beats, words):
    brief = B.Brief.model_validate({"brand": {"name": "X"}, "durationSeconds": seconds})
    assert brief.shape == (beats, words)
    plan = B.plan_from_brief(brief)
    assert plan.beat_count == beats
    assert plan.target_seconds == seconds


def test_an_authored_beat_list_overrides_the_duration_table():
    plan = B.plan_from_brief(make_brief())
    assert plan.beat_count == 2, "the brief wrote two beats; the table said four"


def test_the_word_budget_reaches_the_writer_as_a_number():
    direction = B.direction_from_brief(make_brief(durationSeconds=4))
    assert "4 SECONDS" in direction
    assert "10 WORDS OR FEWER" in direction, "the budget is stated, not implied"
    assert "EXACTLY 2 beats" in direction


def test_a_script_that_overran_the_clock_is_measured_not_assumed():
    """A four-second brief has come back as ten seconds of narration."""
    from tests.conftest import make_remix
    from vira.models import Beat

    brief = make_brief(durationSeconds=4)
    long = make_remix(beats=[
        Beat(say="I haven't bought breakfast in two YEARS", show="a jar"),
        Beat(say="This is why: it's already made, actually fills me up, and "
                 "tastes like dessert", show="a spoon"),
        Beat(say="Tap to try the first batch", show="the lid"),
    ])
    miss = B.budget_miss(brief, long)
    assert miss is not None
    assert "3 beats and 27 words" in miss
    assert "10s of narration" in miss


def test_a_script_inside_the_clock_reports_nothing():
    from tests.conftest import make_remix
    from vira.models import Beat

    short = make_remix(beats=[Beat(say="I haven't bought breakfast in two YEARS",
                                   show="a jar")])
    assert B.budget_miss(make_brief(durationSeconds=4), short) is None


# --- cutting to length ----------------------------------------------------


def long_script():
    from tests.conftest import make_remix
    from vira.models import Beat

    return make_remix(beats=[
        Beat(say="I haven't bought breakfast in two YEARS", show="a jar"),
        Beat(say="This is why: it's already made, actually fills me up, and "
                 "tastes like dessert", show="a spoon"),
        Beat(say="Tap to try the first batch", show="the lid"),
    ])


CUT = {
    "hook": "I haven't bought breakfast in two YEARS",
    "beats": [
        {"say": "I haven't bought breakfast in two YEARS", "show": "a jar",
         "motion": "punch"},
        {"say": "Tap to try the first batch", "show": "the lid", "motion": "stack"},
    ],
    "cta": "Tap to try the first batch",
    "grounded_in": ["t1"],
}


async def test_an_over_long_script_is_cut_to_the_brief_s_clock(monkeypatch):
    seen: dict = {}

    async def fake(prompt, *, system, max_tokens=4000):
        seen["prompt"] = prompt
        return dict(CUT)

    monkeypatch.setattr(B, "complete_json", fake)
    cut = await B.compress(long_script(), make_brief(durationSeconds=4),
                           [make_trend("t1")])
    assert len(cut.beats) == 2
    assert len(cut.narration().split()) == 13
    assert "2 beats. 10 words" in seen["prompt"], "the budget is stated as a number"
    assert "3 beats and 27 words" in seen["prompt"], "and so is the overrun"


async def test_the_clients_approved_hook_survives_the_cut(monkeypatch):
    async def fake(*_a, **_kw):
        return {**CUT, "hook": "A completely different opening line about oats"}

    monkeypatch.setattr(B, "complete_json", fake)
    cut = await B.compress(long_script(), make_brief(), [make_trend("t1")])
    assert cut.hook == "I stopped buying breakfast for two YEARS"


async def test_a_failed_cut_keeps_the_long_script_rather_than_losing_the_ad(monkeypatch):
    async def boom(*_a, **_kw):
        raise RuntimeError("the model fell over")

    monkeypatch.setattr(B, "complete_json", boom)
    original = long_script()
    assert await B.compress(original, make_brief(), [make_trend("t1")]) is original


async def test_a_cut_that_did_not_shorten_anything_is_discarded(monkeypatch):
    """A "cut" that comes back longer is a rewrite, and a rewrite was not asked for."""
    async def longer(*_a, **_kw):
        return {**CUT, "beats": CUT["beats"] + [
            {"say": "And it tastes like dessert every single morning of every "
                    "single week, which is more than I can say for anything else "
                    "I have ever bought before breakfast", "show": "a bowl"}]}

    monkeypatch.setattr(B, "complete_json", longer)
    original = long_script()
    assert await B.compress(original, make_brief(), [make_trend("t1")]) is original


def test_an_unsupported_duration_is_a_validation_error():
    with pytest.raises(ValueError):
        B.Brief.model_validate({"brand": {"name": "X"}, "durationSeconds": 15})


# --- narrative → the writer ----------------------------------------------


def test_authored_beats_are_handed_to_the_writer_in_order():
    direction = B.direction_from_brief(make_brief())
    assert "REQUIRED BEATS" in direction
    assert direction.index("close on the jar") < direction.index("hand lifting the lid")
    assert "on-screen text: four hours" in direction


def test_no_authored_beats_leaves_the_writer_to_invent_them():
    brief = make_brief(narrative={})
    assert "REQUIRED BEATS" not in B.direction_from_brief(brief)
    assert B.plan_from_brief(brief).beat_count == 4


def test_the_hook_the_voiceover_and_the_cta_all_reach_the_prompt():
    direction = B.direction_from_brief(make_brief())
    assert "I stopped buying breakfast for two YEARS" in direction
    assert "It sets while you sleep." in direction
    assert "Tap to try the first batch" in direction


def test_a_supplied_hook_leaves_the_measured_hook_shape_unconstrained():
    """Two authorities on one line is one too many."""
    assert B.plan_from_brief(make_brief()).hook_shape == ""


# --- constraints ----------------------------------------------------------


def test_never_say_and_the_constraints_arrive_as_hard_prohibitions():
    direction = B.direction_from_brief(make_brief())
    assert "HARD PROHIBITIONS" in direction
    for phrase in ('never say "superfood"', "no competitor brand names",
                   "no real person's name", "no health claims"):
        assert phrase in direction


def test_an_avoid_from_an_image_reference_is_a_prohibition_too():
    assert "stock-photo smiles" in B.direction_from_brief(make_brief())


def test_a_non_english_brief_says_so_in_the_prompt():
    brief = make_brief(constraints={"language": "fr"})
    assert "Write every spoken line in fr" in B.direction_from_brief(brief)


# --- style → the look -----------------------------------------------------


def test_the_look_carries_the_palette_and_the_lead_assets_measured_texture():
    look = B.look_from_brief(make_brief())
    assert "cold kitchen at 6am" in look
    assert "#0B0B0F" in look
    assert "hard side light" in look and "tight crop" in look
    assert "the steel counter" in look


def test_the_look_forbids_what_the_constraints_forbid_visually():
    look = B.look_from_brief(make_brief())
    assert "competitor logo" in look
    assert "likeness" in look


def test_an_empty_style_falls_back_to_the_lanes_own_look():
    brief = B.Brief.model_validate({"brand": {"name": "X"}})
    assert B.look_from_brief(brief, "the lane look") == "the lane look"


def test_the_brief_is_folded_into_the_lane_so_every_stage_reads_it():
    lane = B.lane_from_brief(get_lane("founder-story"), make_brief())
    assert "HARD PROHIBITIONS" in lane.brief
    assert "First person, founder voice" in lane.brief, "the lane survives underneath"
    assert "#0B0B0F" in lane.look
    assert lane.voice_id == get_lane("founder-story").voice_id


# --- grounding ------------------------------------------------------------


class FakeSupa:
    def __init__(self, rows):
        self.rows = rows
        self.params: dict = {}

    async def select(self, table, **params):
        self.params = {"table": table, **params}
        return self.rows


async def test_trend_references_are_fetched_and_returned_in_brief_order():
    supa = FakeSupa([trend_row("VIRA-TR-SECOND"), trend_row("VIRA-TR-LEAD")])
    picked, rejected = await B.resolve_trend_refs(supa, make_brief())
    assert [t.trend_key for t in picked] == ["VIRA-TR-LEAD", "VIRA-TR-SECOND"]
    assert rejected == {}
    assert 'in.("VIRA-TR-LEAD","VIRA-TR-SECOND")' == supa.params["trend_key"]


async def test_a_cited_key_the_corpus_does_not_have_is_reported_not_invented():
    supa = FakeSupa([trend_row("VIRA-TR-LEAD")])
    picked, rejected = await B.resolve_trend_refs(supa, make_brief())
    assert [t.trend_key for t in picked] == ["VIRA-TR-LEAD"]
    assert rejected == {"cited by the brief but not in the corpus": 1}


async def test_a_brief_with_no_trend_references_asks_the_corpus_for_nothing():
    supa = FakeSupa([trend_row("x")])
    picked, rejected = await B.resolve_trend_refs(supa, make_brief(references=[IMAGE_REF]))
    assert picked == [] and rejected == {}
    assert supa.params == {}, "no keys means no round trip"


# --- signalQuality --------------------------------------------------------


def test_low_signal_quality_scores_evidence_down_before_the_gate(cfg):
    score = make_score(evidence=3.5)
    tempered = B.temper(score, make_brief(signalQuality="low"))
    assert tempered.evidence == 2.5
    # And that is enough to move the verdict, which is the point of the penalty.
    from vira.score import disposition
    assert disposition(score)[0] != "dropped"
    assert disposition(tempered)[0] == "dropped"


def test_high_signal_quality_changes_nothing():
    score = make_score(evidence=3.5)
    assert B.temper(score, make_brief()).evidence == 3.5


def test_the_penalty_can_only_move_the_score_down():
    """A request parameter must never be able to walk a weak ad through the gate."""
    for evidence in (0.0, 0.5, 3.0, 5.0):
        tempered = B.temper(Score(evidence=evidence), make_brief(signalQuality="low"))
        assert tempered.evidence <= evidence
        assert tempered.evidence >= 0.0


def test_low_signal_also_tells_the_writer_to_claim_less():
    assert "SIGNAL QUALITY IS LOW" in B.direction_from_brief(make_brief(signalQuality="low"))


def test_confidence_reports_low_even_when_the_ad_clears_the_gate():
    assert B.confidence(make_brief(signalQuality="low"), make_score(evidence=5.0)) == "low"
    assert B.confidence(make_brief(), make_score(evidence=5.0)) == "high"
    assert B.confidence(make_brief(), make_score(evidence=3.0)) == "medium"

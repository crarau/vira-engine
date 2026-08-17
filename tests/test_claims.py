"""The claim gate decides what a published ad is allowed to assert, so its
failure modes are asymmetric: a false claim that slips through is a liability,
while a true claim wrongly cut is a sentence the user adds back. These tests lean
on the first case.
"""

from __future__ import annotations

import pytest

from vira.claims import (
    ANECDOTE,
    COMPARATIVE,
    FACTUAL,
    MIN_BEATS,
    PROMOTIONAL,
    SUBJECTIVE,
    classify,
    find_support,
    gate,
    quantities,
    supports,
)
from vira.models import Beat, Remix
from vira.reader import Fact


def _remix(*sentences: str) -> Remix:
    return Remix(
        hook=sentences[0] if sentences else "",
        beats=[Beat(say=s, show="a thing", motion="stack") for s in sentences],
        cta="Tap to try it",
    )


def _facts(*texts: str) -> list[Fact]:
    return [Fact(text=t, sentence=t, url="https://brand.example/p") for t in texts]


# --- classification -------------------------------------------------------


@pytest.mark.parametrize("sentence,kind", [
    ("Each jar has 12g of protein.", FACTUAL),
    ("Twelve grams of protein in every jar.", FACTUAL),
    ("It costs $39 for a box.", FACTUAL),
    ("It is cheaper than every competitor.", COMPARATIVE),
    ("These are the highest protein oats you can buy.", COMPARATIVE),
    ("Clinically proven to keep you full.", FACTUAL),
    ("Honestly, mornings are just hard.", SUBJECTIVE),
    ("These taste incredible.", SUBJECTIVE),
    ("We made the breakfast we could not find.", PROMOTIONAL),
])
def test_classification(sentence, kind):
    assert classify(sentence) == kind


def test_spoken_numbers_are_claims_not_opinions():
    """Scripts spell numbers out. A digit-only check would let 'twelve grams of
    protein' through ungated, which is the exact claim that needs a source."""
    assert classify("Twelve grams of protein.") == FACTUAL


def test_regulated_language_beats_puffery_in_classification():
    """'Clinically proven to taste amazing' contains puffery AND a clinical
    claim. Classifying it as subjective would ship it unchecked."""
    assert classify("Clinically proven to taste amazing.") == FACTUAL


# --- quantity normalisation ----------------------------------------------


def test_word_and_digit_numbers_normalise_together():
    assert "12" in quantities("twelve grams")
    assert "12" in quantities("12g")
    assert "39" in quantities("$39 a box")


def test_hyphenated_speech_numbers_resolve_both_ways():
    """'three twenty-five' on a voice track is '$3.25' on a page."""
    q = quantities("three twenty-five a jar")
    assert "25" in q and "3" in q


# --- support checking ----------------------------------------------------


def test_a_paraphrase_of_the_source_is_supported():
    assert supports(
        "Twelve grams of protein in every single jar.",
        "Each jar contains 12g of plant protein and 8g of fibre.",
    )


def test_a_number_the_source_does_not_contain_is_never_supported():
    """The invented-fact case. No amount of shared vocabulary excuses it."""
    assert not supports(
        "Fifty grams of protein in every single jar.",
        "Each jar contains 12g of plant protein and 8g of fibre.",
    )


def test_the_right_number_on_the_wrong_subject_is_not_supported():
    """Quantity matching alone would accept this. It must not."""
    assert not supports(
        "Twelve grams of caffeine per serving.",
        "Each jar contains 12g of plant protein.",
    )


def test_heavy_paraphrase_still_resolves():
    """Spoken copy rewords its source; it never quotes it."""
    assert supports(
        "Every jar packs twelve grams of plant protein.",
        "Each jar contains 12g of plant protein and 8g of fibre.",
    )


def test_a_rhetorical_comparison_to_something_unmentioned_is_not_supported():
    """"A kitchen smaller than your bedroom" reads like a flourish on the real
    400-square-foot fact, and it is tempting to allow. It compares the kitchen to
    the VIEWER'S bedroom, which no page of theirs can speak to — so it is cut.

    Loosening the threshold until this passed would be the same mistake as
    lowering an evidence floor to make a score go up: the gate would then also
    accept comparisons that are simply false."""
    assert not supports(
        "Made in a kitchen smaller than your bedroom.",
        "We started in a 400 square foot kitchen in 2024.",
    )


def test_find_support_prefers_the_most_specific_source():
    facts = _facts(
        "Our oats are made in small batches.",
        "Each jar contains 12g of plant protein and 8g of fibre.",
    )
    got = find_support("Twelve grams of protein.", facts)
    assert got is not None and "12g" in got.text


def test_find_support_returns_none_rather_than_a_loose_match():
    assert find_support(
        "Clinically proven to lower cholesterol.",
        _facts("Our oats are delicious and made in small batches."),
    ) is None


# --- the gate ------------------------------------------------------------


def test_an_unsupported_sentence_is_cut_and_the_video_survives():
    """The whole point. V1 dropped the entire film; this removes one sentence."""
    remix = _remix(
        "I skipped breakfast for two YEARS.",
        "Twelve grams of protein.",
        "These are the highest protein oats you can buy.",
        "We started in a small kitchen.",
    )
    facts = _facts(
        "Each jar contains 12g of plant protein.",
        "We started in a 400 square foot kitchen in 2024.",
    )

    r = gate(remix, facts)

    assert not r.failed
    assert len(r.cut) == 1
    assert "highest protein" in r.cut[0].text
    said = " ".join(b.say for b in r.remix.beats)
    assert "highest protein" not in said
    assert "Twelve grams of protein" in said


def test_every_cut_carries_a_reason_the_user_can_act_on():
    r = gate(_remix("Clinically proven to keep you full."), _facts("Tastes good."))
    assert r.cut
    reason = r.cut[0].reason
    assert reason and ("study" in reason.lower() or "pages" in reason.lower())


def test_subjective_lines_pass_untouched():
    """Gating opinion would strip every film to nothing."""
    remix = _remix("Honestly, mornings are hard.", "We made the breakfast we wanted.")
    r = gate(remix, [])
    assert not r.cut
    assert len(r.remix.beats) == 2


def test_a_beat_whose_every_sentence_is_cut_disappears_entirely():
    """An empty `say` would render a silent shot and hand the voice stage a beat
    with no words — which is how the prototype produced blank frames."""
    remix = _remix(
        "Mornings are hard.",
        "Clinically proven to cure fatigue.",
        "We started in a small kitchen.",
    )
    r = gate(remix, _facts("Tastes good.", "We started in a 400 square foot kitchen."))
    assert all(b.say.strip() for b in r.remix.beats)
    assert len(r.remix.beats) == 2


def test_cutting_past_two_beats_fails_rather_than_shipping_a_fragment():
    remix = _remix(
        "Clinically proven to cure fatigue.",
        "The highest protein oats on earth.",
        "Fifty grams of protein per jar.",
    )
    r = gate(remix, _facts("Our oats taste nice."))
    assert r.failed
    assert "support" in r.failure_reason
    assert len(r.remix.beats) < MIN_BEATS


def test_the_input_remix_is_never_mutated():
    """The cut panel shows before and after, so the caller needs both."""
    remix = _remix("Mornings are hard.", "Fifty grams of protein.")
    original = remix.beats[1].say
    gate(remix, _facts("It has 12g of protein."))
    assert remix.beats[1].say == original


def test_a_kept_factual_claim_records_where_it_came_from():
    r = gate(_remix("Twelve grams of protein."), _facts("Each jar has 12g of protein."))
    kept = [c for c in r.kept if c.kind == FACTUAL]
    assert kept and kept[0].fact is not None
    assert kept[0].fact.url == "https://brand.example/p"


def test_no_facts_at_all_cuts_every_factual_claim():
    """A user who supplied no readable pages gets an honest failure, not an ad
    full of invented numbers."""
    r = gate(_remix("It has 12g of protein.", "It costs $39."), [])
    assert len(r.cut) == 2
    assert r.failed


def test_multiple_sentences_in_one_beat_are_gated_independently():
    remix = Remix(
        hook="h",
        beats=[Beat(
            say="Mornings are hard. Fifty grams of protein. We started small.",
            show="x",
        )],
        cta="c",
    )
    r = gate(remix, _facts("We started in a 400 square foot kitchen."))
    survived = r.remix.beats[0].say
    assert "Mornings are hard." in survived
    assert "Fifty grams" not in survived


# --- anecdote: the gate must not fight the hook rules --------------------


def test_a_first_person_hook_is_not_gated():
    """`first-person-admission` is a hook shape the writer is REQUIRED to produce
    (director.HOOK_SHAPES). "I skipped breakfast for two YEARS" contains a number
    and classified as FACTUAL, so the gate cut the opening line the director had
    just asked for. A gate that deletes its own hook is worse than no gate."""
    assert classify("I skipped breakfast for two YEARS.") == ANECDOTE
    r = gate(_remix("I skipped breakfast for two YEARS.", "Mornings are hard."), [])
    assert not r.cut


def test_a_product_attribute_in_first_person_is_still_gated():
    """The narrowing that keeps the anecdote class safe. Wearing a first-person
    hat does not exempt a claim about the product itself."""
    assert classify("I packed 50g of protein into every jar.") == FACTUAL
    r = gate(_remix("I packed 50g of protein into every jar."), _facts("It has 12g."))
    assert len(r.cut) == 1


def test_a_first_person_medical_claim_is_not_an_anecdote():
    assert classify("I was clinically diagnosed with a deficiency.") == FACTUAL

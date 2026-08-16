"""Hook grammar — the rules in docs/HOOK-CRAFT.md, as executable checks.

`hook_faults` is the only thing standing between "the prompt says don't open on
an imperative" and "the prompt says it and nobody ever looks". The prompt is a
request; this is the audit. Every rule asserted here is one measured against the
2,669-video cohort, so a change to these expectations is a claim about the
corpus, not a change of taste.
"""

from __future__ import annotations

import pytest

from vira.director import HOOK_SHAPES, VideoPlan
from vira.remix import _hook_shape, hook_faults

# Conforms to every rule: finite clause, 10 words, "I", one CAPS word.
GOOD = "I gave up on sunscreen for two whole YEARS."


def test_a_conforming_hook_has_no_faults():
    assert hook_faults(GOOD) == []


@pytest.mark.parametrize("hook,fragment", [
    ("Stop saying mineral sunscreen isn't for dark skin.", "imperative"),
    ("Try this before you buy another SPF, seriously.", "imperative"),
    ("Never buy a mineral SPF before you READ this.", "negation"),
    ("This was my breakfast ten SECONDS ago, honestly.", "demonstrative"),
    ("Here's the thing nobody tells you about SUNSCREEN.", "demonstrative"),
    ("Hey, I want to show you something WEIRD today.", "throat-clearing"),
])
def test_banned_openings_are_caught(hook, fragment):
    """Each of these opening classes measured below the corpus median."""
    assert any(fragment in f for f in hook_faults(hook)), hook_faults(hook)


def test_the_real_hooks_this_change_was_written_against_all_fail():
    """The four samples the product owner called samey. If any of these passes,
    the rules are not actually excluding the thing they were written to exclude."""
    for hook in ["Stop saying mineral sunscreen isn't for dark skin.",
                 "I stopped wearing sunscreen. All of it.",
                 "This was breakfast ten seconds ago.",
                 "Ten seconds. That's it."]:
        assert hook_faults(hook), f"{hook!r} slipped through"


def test_a_verbless_label_is_not_a_hook():
    assert any("fragment" in f for f in hook_faults("Ten SECONDS of my morning."))


def test_an_irregular_past_tense_verb_counts_as_finite():
    """"I gave up on it" is the single most-recommended shape; a naive -ed test
    calls it a fragment and the checker would fight its own rules."""
    for hook in ["I gave up on SPF after one bad summer.",
                 "I lost two YEARS to a sunscreen that failed.",
                 "We threw out four FORMULAS before this one."]:
        assert not any("fragment" in f for f in hook_faults(hook)), hook


def test_an_impersonal_hook_is_flagged():
    assert any("I/we/you" in f for f in hook_faults("Zinc always leaves a GREY cast."))


@pytest.mark.parametrize("hook", [
    "I wore it for THIRTY days.",                    # 6 words, in range
    "We reformulated this four times before the cast finally went AWAY today.",
])
def test_hooks_inside_the_word_window_pass_the_length_rule(hook):
    assert not any("outside 4-14" in f for f in hook_faults(hook))


@pytest.mark.parametrize("hook,n", [("I QUIT.", 2), (
    "I spent two entire years reformulating this thing in my own home kitchen "
    "because nothing on the shelf WORKED", 19)])
def test_hooks_outside_the_word_window_are_flagged(hook, n):
    assert any(f"{n} words" in f for f in hook_faults(hook))


def test_an_acronym_does_not_count_as_the_stressed_word():
    """SPF is capitalised whatever the stress is. Counting it as emphasis made
    the checker reject its own best output."""
    assert hook_faults("I gave up on SPF for two whole YEARS.") == []
    assert any("no CAPS word" in f for f in hook_faults("I gave up on SPF for two whole years."))


def test_shouting_the_whole_line_is_flagged():
    assert any("CAPS words" in f for f in hook_faults("I REALLY HATED EVERY SINGLE ONE."))


def test_positive_superlatives_are_flagged():
    assert any("superlative" in f for f in
               hook_faults("I found the best mineral SPF I have EVER used."))


def test_a_trailing_ellipsis_is_flagged():
    assert any("ellipsis" in f for f in hook_faults("I gave up on SPF for two YEARS..."))


def test_an_empty_hook_is_a_fault_not_a_crash():
    assert hook_faults("") == ["empty"]
    assert hook_faults("   ") == ["empty"]
    assert hook_faults("!!! ???") == ["no words"]


# --- the director's half of the contract ------------------------------------


def test_every_hook_shape_carries_a_worked_example():
    """A shape name with no example is a label the writer cannot execute."""
    for name, rule in HOOK_SHAPES.items():
        assert "e.g." in rule, f"{name} has no example"
        assert len(rule) > 60, f"{name} is too thin to be a constraint"


def test_the_writer_is_handed_the_rule_not_just_the_key():
    plan = VideoPlan(hook_shape="first-person-admission")
    rendered = _hook_shape(plan)
    assert "first-person-admission" in rendered
    assert HOOK_SHAPES["first-person-admission"] in rendered


def test_an_unset_shape_still_binds_the_grammar_rules():
    assert "HOOK GRAMMAR" in _hook_shape(VideoPlan())


def test_an_invented_shape_is_not_passed_through_as_a_constraint():
    """A hallucinated shape would hand the writer a rule with nothing measured
    behind it, which is worse than leaving the choice open."""
    assert "HOOK GRAMMAR" in _hook_shape(VideoPlan(hook_shape="vibes-based-banger"))


def test_the_shapes_the_director_offers_avoid_the_banned_openings():
    """The examples inside HOOK_SHAPES are what the writer imitates. If one of
    them opens on an imperative, the prompt contradicts itself."""
    for name, rule in HOOK_SHAPES.items():
        example = rule.split("e.g.", 1)[1].strip().strip("'\"")
        faults = [f for f in hook_faults(example)
                  if "banned" in f or "I/we/you" in f or "fragment" in f]
        assert not faults, f"{name}: {example!r} -> {faults}"

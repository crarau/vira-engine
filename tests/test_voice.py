"""Stage 6 — performance direction and the timing spine.

`_words_from_alignment` and `_assign` are where every frame number in the final
video comes from. A one-character drift here shifts every caption in the ad, and
it does so silently: the render still succeeds, the audio is still fine, the
words just land on the wrong pictures. Hence the character-offset arithmetic is
asserted explicitly rather than approximately.
"""

from __future__ import annotations

import pytest

from vira import voice
from vira.lanes import get as get_lane
from vira.models import Beat
from tests.conftest import make_remix, word


def alignment(text: str, *, step: float = 0.1):
    """Character array in the shape ElevenLabs returns, one `step` per char."""
    chars = list(text)
    starts = [round(i * step, 3) for i in range(len(chars))]
    ends = [round((i + 1) * step, 3) for i in range(len(chars))]
    return chars, starts, ends


# --- direct() ---------------------------------------------------------------


def test_direct_opens_and_closes_with_the_default_palette():
    remix = make_remix(beats=[Beat(say="One", show=""), Beat(say="Two", show=""),
                              Beat(say="Three", show="")])
    text = voice.direct(remix)

    assert text.startswith(f"{voice.OPENING} One")
    assert text.endswith(f"{voice.CLOSING} Three")
    assert voice.MIDDLE[0] in text


def test_direct_uses_the_lane_palette_over_the_default():
    lane = get_lane("founder-story")
    remix = make_remix(beats=[Beat(say="One", show=""), Beat(say="Two", show=""),
                              Beat(say="Three", show="")])
    text = voice.direct(remix, lane)

    assert text.startswith(f"{lane.opening} One")
    assert text.endswith(f"{lane.closing} Three")
    assert lane.middle[0] in text
    # A confession must not be delivered in the hard-sell register.
    assert voice.OPENING not in text
    assert voice.CLOSING not in text


def test_direct_cycles_the_middle_palette_across_long_scripts():
    lane = get_lane("demo-first")
    beats = [Beat(say=f"line{i}", show="") for i in range(len(lane.middle) + 3)]
    text = voice.direct(make_remix(beats=beats), lane)

    for tag in lane.middle:
        assert tag in text


def test_a_beats_own_delivery_wins_over_the_lane():
    """The director authored the tag deliberately; the palette is only a default."""
    lane = get_lane("contrarian")
    remix = make_remix(beats=[
        Beat(say="One", show="", delivery="[whispers]"),
        Beat(say="Two", show=""),
        Beat(say="Three", show="", delivery="[laughs]"),
    ])
    text = voice.direct(remix, lane)

    assert text.startswith("[whispers] One")
    assert text.endswith("[laughs] Three")
    assert lane.opening not in text
    assert lane.closing not in text


def test_direct_skips_blank_beats():
    remix = make_remix(beats=[Beat(say="One", show=""), Beat(say="   ", show=""),
                              Beat(say="Two", show="")])
    text = voice.direct(remix)
    assert "  " not in text.replace("] ", "]")
    assert text.count("[") == 2


def test_direct_closes_hard_even_when_the_last_beat_is_blank():
    """The CTA is the loudest line in the ad; a trailing empty beat must not eat
    the closing tag and leave the ad ending on a mid-script register."""
    remix = make_remix(beats=[Beat(say="One", show=""), Beat(say="Two", show=""),
                              Beat(say="", show="")])
    text = voice.direct(remix)
    assert text.endswith(f"{voice.CLOSING} Two")


def test_direct_opens_hard_even_when_the_first_beat_is_blank():
    remix = make_remix(beats=[Beat(say="", show=""), Beat(say="One", show=""),
                              Beat(say="Two", show="")])
    text = voice.direct(remix)
    assert text.startswith(f"{voice.OPENING} One")


def test_a_single_beat_gets_the_opening_tag():
    text = voice.direct(make_remix(beats=[Beat(say="Only", show="")]))
    assert text == f"{voice.OPENING} Only"


# --- _words_from_alignment --------------------------------------------------


def test_bracketed_performance_tags_are_not_spoken_words():
    """The tag occupies characters in the alignment but is never voiced, so its
    characters must not become a word or shift the timing of the ones that are."""
    chars, starts, ends = alignment("[excited] Hello world")
    words = voice._words_from_alignment(chars, starts, ends)

    assert [w.w for w in words] == ["Hello", "world"]
    assert (words[0].start, words[0].end) == (1.0, 1.5)   # chars 10..14
    assert (words[1].start, words[1].end) == (1.6, 2.1)   # chars 16..20


def test_a_tag_mid_script_does_not_leak_into_the_words():
    chars, starts, ends = alignment("Go now [shouting] Buy it")
    words = voice._words_from_alignment(chars, starts, ends)

    assert [w.w for w in words] == ["Go", "now", "Buy", "it"]
    # "Buy" starts at index 18, after the tag and its trailing space.
    assert words[2].start == 1.8


def test_multi_tag_openers_are_skipped_whole():
    """`social-proof` closes on "[laughs] [excited]" — two tags, one line."""
    chars, starts, ends = alignment("[laughs] [excited] Wild")
    words = voice._words_from_alignment(chars, starts, ends)

    assert [w.w for w in words] == ["Wild"]
    assert words[0].start == 1.9


def test_timings_come_from_the_character_array_not_from_our_text():
    chars, starts, ends = alignment("Hi there")
    words = voice._words_from_alignment(chars, starts, ends)

    assert words[0].start == starts[0] and words[0].end == ends[1]
    assert words[1].start == starts[3] and words[1].end == ends[7]


def test_punctuation_stays_attached_to_its_word():
    chars, starts, ends = alignment("Wait, really?")
    words = voice._words_from_alignment(chars, starts, ends)
    assert [w.w for w in words] == ["Wait,", "really?"]


def test_runs_of_whitespace_do_not_produce_empty_words():
    chars, starts, ends = alignment("one  \n two")
    words = voice._words_from_alignment(chars, starts, ends)
    assert [w.w for w in words] == ["one", "two"]


def test_a_tag_only_utterance_yields_no_words():
    chars, starts, ends = alignment("[sighs]")
    assert voice._words_from_alignment(chars, starts, ends) == []


def test_the_last_word_runs_to_the_end_of_the_alignment():
    chars, starts, ends = alignment("done")
    words = voice._words_from_alignment(chars, starts, ends)
    assert (words[0].start, words[0].end) == (starts[0], ends[-1])


def test_a_trailing_tag_does_not_stretch_the_last_word():
    """A tag after the final word would otherwise hold the caption on screen for
    the length of a word that is never spoken."""
    chars, starts, ends = alignment("done [sighs]")
    words = voice._words_from_alignment(chars, starts, ends)

    assert [w.w for w in words] == ["done"]
    assert words[0].end == ends[3]


# --- _assign ----------------------------------------------------------------


def test_words_are_handed_back_to_beats_by_position():
    remix = make_remix(beats=[Beat(say="Hello world", show=""),
                              Beat(say="Goodbye now", show="")])
    words = [word("Hello", 0.0, 0.4), word("world", 0.5, 0.9),
             word("Goodbye", 1.0, 1.6), word("now", 1.7, 2.0)]
    voice._assign(remix, words)

    assert [w.w for w in remix.beats[0].words] == ["Hello", "world"]
    assert [w.w for w in remix.beats[1].words] == ["Goodbye", "now"]
    assert (remix.beats[0].start_s, remix.beats[0].end_s) == (0.0, 0.9)
    assert (remix.beats[1].start_s, remix.beats[1].end_s) == (1.0, 2.0)


def test_assign_overwrites_the_draft_t_with_real_timing():
    remix = make_remix(beats=[Beat(t=0.0, say="Hello world", show=""),
                              Beat(t=3.0, say="Goodbye now", show="")])
    voice._assign(remix, [word("Hello", 0.0, 0.4), word("world", 0.5, 0.9),
                          word("Goodbye", 1.0, 1.6), word("now", 1.7, 2.0)])

    assert remix.beats[1].t == 1.0
    assert remix.beats[1].t == remix.beats[1].start_s


def test_matching_is_positional_so_normalised_punctuation_cannot_shift_beats():
    """ElevenLabs returns a curly apostrophe for our straight one; a string match
    would drop the word and slide every later beat one word early."""
    remix = make_remix(beats=[Beat(say="don't stop", show=""),
                              Beat(say="keep going", show="")])
    voice._assign(remix, [word("don’t", 0.0, 0.4), word("stop", 0.5, 0.9),
                          word("keep", 1.0, 1.4), word("going", 1.5, 2.0)])

    assert [w.w for w in remix.beats[1].words] == ["keep", "going"]
    assert remix.beats[1].start_s == 1.0


def test_extra_internal_spaces_do_not_consume_extra_words():
    remix = make_remix(beats=[Beat(say="  a   b  ", show=""),
                              Beat(say="c", show="")])
    voice._assign(remix, [word("a", 0.0, 0.1), word("b", 0.2, 0.3),
                          word("c", 0.4, 0.5)])
    assert [w.w for w in remix.beats[1].words] == ["c"]


def test_a_beat_with_no_words_left_keeps_its_timing_unset():
    """Better an untimed beat than a beat holding another beat's timings."""
    remix = make_remix(beats=[Beat(say="only these two", show=""),
                              Beat(say="never spoken", show="")])
    voice._assign(remix, [word("only", 0.0, 0.2), word("these", 0.3, 0.5),
                          word("two", 0.6, 0.8)])

    assert remix.beats[0].end_s == 0.8
    assert remix.beats[1].words == []
    assert remix.beats[1].start_s is None


def test_blank_beats_do_not_consume_words():
    remix = make_remix(beats=[Beat(say="", show=""), Beat(say="hello", show="")])
    voice._assign(remix, [word("hello", 0.0, 0.5)])
    assert [w.w for w in remix.beats[1].words] == ["hello"]


def test_the_full_spine_end_to_end():
    """direct() -> alignment -> words -> beats: the tags inserted at the top must
    not move a single beat boundary at the bottom."""
    remix = make_remix(beats=[Beat(say="Everyone chases heat", show=""),
                              Beat(say="Nobody chases flavour", show="")])
    text = voice.direct(remix)
    chars, starts, ends = alignment(text)

    words = voice._words_from_alignment(chars, starts, ends)
    voice._assign(remix, words)

    assert [w.w for w in remix.beats[0].words] == ["Everyone", "chases", "heat"]
    assert [w.w for w in remix.beats[1].words] == ["Nobody", "chases", "flavour"]
    # "Everyone" begins right after "[excited] " — ten characters in.
    assert remix.beats[0].start_s == pytest.approx(len(voice.OPENING + " ") * 0.1)
    assert remix.beats[0].end_s < remix.beats[1].start_s

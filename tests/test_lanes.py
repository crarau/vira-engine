"""Creative lanes — the contract between `lanes.py` and everything that reads it.

Two lanes sharing a voice_id, or a lane renamed without updating the callers,
produces five ads that a human panel cannot meaningfully rank — and the ranking
is the entire point of generating five. These are cheap tests for an expensive,
silent failure.
"""

from __future__ import annotations

import dataclasses
import re

import pytest

import variants
from vira.agentic.crew import Production
from vira.lanes import BY_NAME, LANES, Lane, get

EXPECTED = ["problem-first", "demo-first", "founder-story", "social-proof",
            "contrarian"]

TAG = re.compile(r"^(\[[^\[\]]+\]\s*)+$")


def test_the_five_lanes_are_the_five_documented_ones():
    assert [lane.name for lane in LANES] == EXPECTED


def test_every_lane_has_its_own_voice():
    """Two lanes on one voice_id is five ads read by four people."""
    voices = [lane.voice_id for lane in LANES]
    assert len(set(voices)) == len(LANES)
    assert all(v.strip() for v in voices)


def test_every_lane_carries_a_full_creative_identity():
    for lane in LANES:
        assert lane.brief.strip(), f"{lane.name} has no copy direction"
        assert lane.look.strip(), f"{lane.name} has no visual grade"
        assert lane.voice_note.strip(), f"{lane.name} has no voice note"
        assert lane.middle, f"{lane.name} has no middle palette"


def test_briefs_and_looks_are_distinct_between_lanes():
    assert len({lane.brief for lane in LANES}) == len(LANES)
    assert len({lane.look for lane in LANES}) == len(LANES)


@pytest.mark.parametrize("lane", LANES, ids=[lane.name for lane in LANES])
def test_performance_tags_are_well_formed(lane):
    """An unbracketed tag is spoken aloud by ElevenLabs instead of performed."""
    for tag in [lane.opening, lane.closing, *lane.middle]:
        assert TAG.match(tag), f"{lane.name}: {tag!r} is not a performance tag"


def test_lanes_are_immutable():
    """Five variants build in parallel off the same Lane objects; a mutable lane
    would let one variant's steer leak into another's."""
    with pytest.raises(dataclasses.FrozenInstanceError):
        LANES[0].brief = "something else"


def test_lookup_by_name_covers_every_lane():
    assert set(BY_NAME) == set(EXPECTED)
    for name in EXPECTED:
        assert get(name) is BY_NAME[name]


def test_an_unknown_lane_fails_loudly():
    with pytest.raises(KeyError):
        get("no-such-lane")


def test_variants_default_n_selects_all_five():
    """`variants.py` slices LANES[:n] with n defaulting to 5."""
    assert LANES[:5] == LANES


def test_variants_can_resolve_every_lane_back_from_a_manifest_name():
    """`variants.produce` does `next(l for l in LANES if l.name == name)` on the
    name it wrote into the manifest; a rename here would raise StopIteration
    halfway through a render."""
    for name in EXPECTED:
        assert next(l for l in LANES if l.name == name).name == name


def test_the_agentic_default_lane_exists():
    """agentic_video.py defaults to --lane founder-story."""
    assert get("founder-story")


def test_the_crew_reads_only_fields_a_lane_actually_has():
    """`vira/agentic/crew.py` reaches into lane.name/brief/look/voice_note/voice_id
    while building prompts and calling the synthesiser."""
    fields = {f.name for f in dataclasses.fields(Lane)}
    assert {"name", "brief", "look", "voice_note", "voice_id",
            "opening", "closing", "middle"} <= fields

    annotation = Production.__dataclass_fields__["lane"].type
    assert annotation in (Lane, "Lane")


def test_variants_imports_the_same_lane_table():
    assert variants.LANES is LANES

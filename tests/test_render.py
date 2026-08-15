"""Stage 7 — the props seam between Python and Remotion.

Remotion does no timing maths of its own, so every frame number in the final
video is computed here. Two invariants matter most: seconds become frames at the
configured fps, and per-variant asset namespacing survives into the props — five
concurrent renders reading each other's audio is a bug you only notice on
playback.
"""

from __future__ import annotations

import json
from pathlib import Path

from vira.models import Beat
from vira.render import build_props, write_props
from tests.conftest import make_company, make_remix, word


def timed_remix():
    return make_remix(
        beats=[
            Beat(say="Hello world", show="a bowl", shot="close", motion="punch",
                 camera="push", start_s=0.0, end_s=1.5,
                 words=[word("Hello", 0.0, 0.7), word("world", 0.8, 1.5)]),
            Beat(say="Goodbye now", show="a spoon", shot="wide",
                 start_s=1.6, end_s=3.0,
                 words=[word("Goodbye", 1.6, 2.4), word("now", 2.5, 3.0)]),
        ]
    )


def build(**kw):
    remix = kw.pop("remix", None) or timed_remix()
    args = {
        "audio_path": Path("out/demo/narration-demo-first.mp3"),
        "duration_s": 5.0,
        "shots": None,
    }
    args.update(kw)
    return build_props(make_company(), "Cocoa oats", remix, **args)


# --- frame arithmetic -------------------------------------------------------


def test_beat_frames_come_from_start_and_end_seconds(cfg):
    props = build()
    assert [(b["startFrame"], b["endFrame"]) for b in props["beats"]] == [
        (0, 45), (48, 90)
    ]


def test_word_timings_become_frames(cfg):
    props = build()
    assert props["beats"][0]["words"] == [
        {"w": "Hello", "startFrame": 0, "endFrame": 21},
        {"w": "world", "startFrame": 24, "endFrame": 45},
    ]


def test_changing_fps_re_times_everything(cfg):
    """A copy change re-times the video for free only if nothing is hard-coded."""
    cfg.fps = 60
    props = build()

    assert props["fps"] == 60
    assert (props["beats"][1]["startFrame"], props["beats"][1]["endFrame"]) == (96, 180)
    assert props["beats"][1]["words"][0]["startFrame"] == 96
    assert props["durationInFrames"] == 300


def test_duration_is_derived_from_the_audio_length(cfg):
    assert build(duration_s=12.4)["durationInFrames"] == 372


def test_duration_never_falls_below_one_second(cfg):
    """A zero-frame composition makes Remotion fail with an opaque error."""
    assert build(duration_s=0.0)["durationInFrames"] == 30
    assert build(duration_s=0.2)["durationInFrames"] == 30


def test_untimed_beats_fall_back_to_the_draft_t(cfg):
    """Props must still build before the voice stage has run, e.g. for a preview."""
    remix = make_remix(beats=[Beat(t=2.0, say="Hello", show="x")])
    props = build_props(make_company(), "oats", remix, audio_path=None,
                        duration_s=5.0)

    assert (props["beats"][0]["startFrame"], props["beats"][0]["endFrame"]) == (60, 150)


def test_a_partially_timed_beat_uses_what_it_has(cfg):
    remix = make_remix(beats=[Beat(t=2.0, say="Hello", show="x", start_s=1.0)])
    props = build_props(make_company(), "oats", remix, audio_path=None,
                        duration_s=5.0)
    assert (props["beats"][0]["startFrame"], props["beats"][0]["endFrame"]) == (30, 150)


# --- asset namespacing ------------------------------------------------------


def test_audio_file_is_namespaced_from_the_audio_path(cfg):
    """Five concurrent renders share video/public/, so the filename is the only
    thing keeping one variant from playing another variant's narration."""
    props = build(audio_path=Path("video/public/narration-founder-story.mp3"))
    assert props["audioFile"] == "narration-founder-story.mp3"


def test_audio_src_is_absolute(cfg):
    props = build(audio_path=Path("out/demo/narration-demo-first.mp3"))
    assert Path(props["audioSrc"]).is_absolute()
    assert props["audioSrc"].endswith("narration-demo-first.mp3")


def test_no_audio_falls_back_to_the_default_filename(cfg):
    props = build(audio_path=None)
    assert props["audioSrc"] is None
    assert props["audioFile"] == "narration.mp3"


def test_per_variant_image_subdirectory_survives_into_the_beat(cfg):
    """`variants.py` rewrites shot files to "<lane>/shotNN.jpg"; flattening that
    here would make every lane render the first lane's imagery."""
    shots = [
        {"file": "demo-first/shot00.jpg", "credit": "generated"},
        {"file": "demo-first/shot01.jpg", "credit": None},
    ]
    props = build(shots=shots)

    assert [b["image"] for b in props["beats"]] == [
        "demo-first/shot00.jpg", "demo-first/shot01.jpg"
    ]
    assert props["beats"][0]["credit"] == "generated"


def test_beats_without_a_shot_get_a_null_image(cfg):
    props = build(shots=[{"file": "x/shot00.jpg"}])
    assert props["beats"][1]["image"] is None
    assert props["beats"][1]["credit"] is None


def test_shots_are_matched_to_beats_by_index(cfg):
    props = build(shots=[{"file": "a.jpg"}, {"file": "b.jpg"}, {"file": "c.jpg"}])
    assert [b["image"] for b in props["beats"]] == ["a.jpg", "b.jpg"]


# --- payload shape ----------------------------------------------------------


def test_brand_copy_reaches_the_composition(cfg):
    remix = timed_remix()
    props = build(remix=remix)

    assert props["brand"] == "Sunday Oats"
    assert props["product"] == "Cocoa oats"
    assert props["hook"] == remix.hook
    assert props["cta"] == remix.cta
    assert props["caption"] == remix.caption
    assert props["hashtags"] == remix.hashtags


def test_beat_direction_is_passed_through_never_inferred(cfg):
    props = build()
    first, second = props["beats"]

    assert (first["motion"], first["camera"]) == ("punch", "push")
    assert (second["motion"], second["camera"]) == ("", "")
    assert (first["say"], first["show"], first["shot"]) == ("Hello world", "a bowl",
                                                            "close")


def test_props_are_json_serialisable_and_round_trip(cfg, tmp_path):
    """The props file is the seam; anything unserialisable breaks the render at
    the very end of the slowest stage."""
    props = build(shots=[{"file": "a.jpg", "credit": "cc"}])
    path = write_props(props, tmp_path / "nested" / "dir")

    assert path == tmp_path / "nested" / "dir" / "props.json"
    assert json.loads(path.read_text()) == props

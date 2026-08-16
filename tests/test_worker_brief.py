"""A brief, driven through the real worker.

`tests/test_brief.py` proves the mapping is correct in isolation. This proves it
is actually wired: that `_produce` grounds on the brief's references instead of
running selection, that the director does not get to re-decide a shape the brief
already fixed, and that `signalQuality` reaches the gate.

Every network stage is stubbed. What is under test is the four `if brief is not
None` branches in `vira/api/worker.py` and nothing else — if one of them is
deleted, the pipeline still produces a video and one of these fails.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from vira.api import store, worker
from vira.director import Critique
from vira.brief import Brief
from vira.models import CorpusAnalysis, Score
from tests.conftest import make_remix, make_score, make_trend

BRIEF = {
    "durationSeconds": 4,
    "brand": {"name": "Sunday Oats", "slug": "sunday-oats",
              "bio": "oats that set in four hours",
              "mission": "breakfast already made",
              "neverSay": ["superfood"], "palette": ["#0B0B0F"]},
    "references": [{"trendKey": "t1", "whyItWorks": "withholds the result"}],
    "narrative": {"hook": "I stopped buying breakfast for two YEARS",
                  "beats": [{"t": 0.0, "shot": "close on the jar"},
                            {"t": 2.0, "shot": "the lid coming off"}]},
    "style": {"look": "cold kitchen at 6am"},
    "constraints": {"noCompetitorMarks": True},
    "signalQuality": "high",
}


@pytest.fixture
def pipeline(monkeypatch, tmp_path):
    state: dict[str, Any] = {
        "score": make_score(relevance=5, specificity=5, actionability=5,
                            differentiation=5, evidence=4.0),
        "planned": False,
        "grounded_via": [],
        "row": {"id": "lovable-id", "name": "Sunday Oats", "slug": "sunday-oats"},
    }

    class FakeSupa:
        pass

    async def get_company(_supa, _slug):
        return dict(state["row"]) if state["row"] else None

    async def shortlist(*_a, **_kw):
        state["grounded_via"].append("shortlist")
        return [make_trend("t1")], {}

    async def resolve_refs(_supa, _brief):
        state["grounded_via"].append("brief references")
        return [make_trend("t1")], {}

    async def verify_all(trends):
        return list(trends), []

    async def analyze_corpus(*_a, **_kw):
        return CorpusAnalysis()

    async def make_plan(*_a, **_kw):
        state["planned"] = True
        from vira.director import VideoPlan
        return VideoPlan(beat_count=7, target_seconds=28)

    async def build_remix(company, product, trends, corpus, plan=None):
        state["plan"] = plan
        state["mission"] = company.mission
        return make_remix()

    async def critique(*_a, **_kw):
        return Critique(notes=[])

    async def synthesize(_remix, out_dir: Path, _lane=None):
        out_dir.mkdir(parents=True, exist_ok=True)
        mp3 = out_dir / "narration.mp3"
        mp3.write_bytes(b"id3")
        return mp3, 4.0

    async def fetch_or_generate(_c, _p, _r, dest: Path, look=""):
        state["look"] = look
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "shot00.jpg").write_bytes(b"jpeg")
        return [{"file": "shot00.jpg", "credit": "generated", "prompt": "a jar"}]

    async def score_remix(*_a, **_kw):
        return state["score"]

    def render(_props, out_file: Path, **_kw):
        out_file.parent.mkdir(parents=True, exist_ok=True)
        out_file.write_bytes(b"mp4")
        return out_file

    async def create_video(**kw):
        state["video"] = kw
        return {"id": "video-1", "hook": "h", "mp4_path": kw.get("mp4_path"),
                "score": None, "disposition": kw.get("disposition")}

    async def update_job_status(*_a, **_kw):
        return None

    (tmp_path / "video" / "public").mkdir(parents=True)
    monkeypatch.setattr(worker, "Supa", FakeSupa)
    monkeypatch.setattr(worker, "get_company", get_company)
    monkeypatch.setattr(worker, "shortlist", shortlist)
    monkeypatch.setattr(worker.briefs, "resolve_trend_refs", resolve_refs)
    monkeypatch.setattr(worker, "verify_all", verify_all)
    monkeypatch.setattr(worker, "analyze_corpus", analyze_corpus)
    monkeypatch.setattr(worker, "make_plan", make_plan)
    monkeypatch.setattr(worker, "build_remix", build_remix)
    monkeypatch.setattr(worker, "critique", critique)
    monkeypatch.setattr(worker, "synthesize", synthesize)
    monkeypatch.setattr(worker, "fetch_or_generate", fetch_or_generate)
    monkeypatch.setattr(worker, "score_remix", score_remix)
    monkeypatch.setattr(worker, "render", render)
    monkeypatch.setattr(worker, "VIDEO_DIR", tmp_path / "video")
    monkeypatch.setattr(worker, "OUT_DIR", tmp_path / "out")
    monkeypatch.setattr(store, "create_video", create_video)
    monkeypatch.setattr(store, "update_job_status", update_job_status)
    return state


async def run(brief: dict | None, **kw):
    args = {"company_slug": "sunday-oats", "product": "cocoa oats",
            "lane_name": "founder-story", "mode": "fast"}
    args.update(kw)
    if brief is not None:
        args["brief"] = Brief.model_validate(brief)
    await worker.run_job("33333333-3333-3333-3333-333333333333", **args)


async def test_without_a_brief_nothing_changes(pipeline):
    await run(None)
    assert pipeline["grounded_via"] == ["shortlist"]
    assert pipeline["planned"] is True
    assert pipeline["plan"].beat_count == 7


async def test_trend_references_replace_category_selection_entirely(pipeline):
    await run(BRIEF)
    assert pipeline["grounded_via"] == ["brief references"]
    assert "shortlist" not in pipeline["grounded_via"]


async def test_a_brief_without_trend_references_still_uses_selection(pipeline):
    await run({**BRIEF, "references": []})
    assert pipeline["grounded_via"] == ["shortlist"]


async def test_the_brief_fixes_the_shape_so_the_director_never_runs(pipeline):
    await run(BRIEF)
    assert pipeline["planned"] is False, "the brief already decided; do not re-decide"
    assert pipeline["plan"].target_seconds == 4
    assert pipeline["plan"].beat_count == 2, "two authored beats, not the 4s default"


async def test_the_constraints_reach_the_writer_through_the_lane(pipeline):
    await run(BRIEF)
    assert 'never say "superfood"' in pipeline["mission"]
    assert "no competitor brand names" in pipeline["mission"]
    assert "REQUIRED BEATS" in pipeline["mission"]


async def test_the_briefs_look_reaches_the_imagery_stage(pipeline):
    await run(BRIEF)
    assert "cold kitchen at 6am" in pipeline["look"]
    assert "#0B0B0F" in pipeline["look"]


async def test_a_brand_that_is_not_in_lovable_cloud_still_generates(pipeline):
    """The brief carries the brand, so a missing corpus row is not a failure."""
    pipeline["row"] = None
    await run(BRIEF)
    assert pipeline["video"]["disposition"] is not None


async def test_no_brief_and_no_company_row_is_still_a_failure(pipeline):
    pipeline["row"] = None
    await run(None)
    assert "video" not in pipeline, "nothing should have been produced"


async def test_low_signal_quality_reaches_the_gate_and_can_drop_the_video(pipeline, cfg):
    pipeline["score"] = Score(relevance=5, specificity=5, actionability=5,
                              differentiation=5, evidence=3.5)
    await run({**BRIEF, "signalQuality": "low"})
    assert pipeline["video"]["disposition"] == "dropped"
    assert pipeline["video"]["drop_reason"] == "not supported by the cited source videos"


async def test_the_same_score_survives_when_the_signal_is_good(pipeline, cfg):
    pipeline["score"] = Score(relevance=5, specificity=5, actionability=5,
                              differentiation=5, evidence=3.5)
    await run(BRIEF)
    assert pipeline["video"]["disposition"] != "dropped"


async def test_a_script_that_overran_the_clock_is_cut_before_anything_is_paid_for(
    pipeline, monkeypatch
):
    """Voice and imagery are the expensive stages; the cut has to land first."""
    from vira.models import Beat

    order: list[str] = []
    long = make_remix(beats=[
        Beat(say="I haven't bought breakfast in two YEARS", show="a jar"),
        Beat(say="This is why: it's already made, actually fills me up, and "
                 "tastes like dessert", show="a spoon"),
        Beat(say="Tap to try the first batch", show="the lid"),
    ])
    short = make_remix(beats=[Beat(say="I haven't bought breakfast in two YEARS",
                                   show="a jar")])

    async def build_remix(*_a, **_kw):
        order.append("write")
        return long

    async def compress(remix, brief, trends):
        order.append("compress")
        assert remix is long
        return short

    async def fetch_or_generate(_c, _p, remix, dest, look=""):
        order.append("imagery")
        assert remix is short, "the frames must be drawn for the cut script"
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "shot00.jpg").write_bytes(b"jpeg")
        return [{"file": "shot00.jpg", "credit": "generated", "prompt": "a jar"}]

    monkeypatch.setattr(worker, "build_remix", build_remix)
    monkeypatch.setattr(worker.briefs, "compress", compress)
    monkeypatch.setattr(worker, "fetch_or_generate", fetch_or_generate)

    await run({**BRIEF, "durationSeconds": 4})
    assert order == ["write", "compress", "imagery"]
    assert pipeline["video"]["recipe"]["notes"]["duration_overrun"] is None


async def test_a_script_already_inside_the_clock_costs_no_extra_call(pipeline, monkeypatch):
    def refuse(*_a, **_kw):
        raise AssertionError("nothing to cut — compression must not run")

    monkeypatch.setattr(worker.briefs, "compress", refuse)
    await run(BRIEF)
    assert pipeline["video"]["recipe"]["notes"].get("duration_overrun") is None


async def test_the_brief_is_written_into_the_recipe(pipeline):
    await run(BRIEF)
    recipe = pipeline["video"]["recipe"]
    assert recipe["notes"]["brief"]["signalQuality"] == "high"
    assert recipe["notes"]["plan"]["target_seconds"] == 4

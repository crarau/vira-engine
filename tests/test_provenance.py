"""Provenance — the recipe next to every video.

The promise is "edit a prompt in RECIPE.md, re-run, get a different ad". That
only holds if the prompts land there *verbatim*: a truncated, escaped or
re-wrapped prompt is a prompt you cannot paste back. So the assertions are for
exact substrings, not for the presence of a section heading.
"""

from __future__ import annotations

import json
import sys
import types

import pytest

from vira.llm import complete
from vira.provenance import Recorder, current
from tests.conftest import make_company, make_remix, make_score, make_trend

SYSTEM = "You are a sceptical grader.\nScore 0-5 per dimension."
PROMPT = 'Write the ad.\n\n```json\n{"weird": "characters | in <the> prompt"}\n```'


@pytest.fixture
def anthropic_stub(monkeypatch):
    def install(body: str = '{"ok": true}', stop: str = "end_turn"):
        class Block:
            type = "text"
            text = body

        class Msg:
            content = [Block()]
            stop_reason = stop

        class Messages:
            async def create(self, **kw):
                return Msg()

        class AsyncAnthropic:
            def __init__(self, **_):
                self.messages = Messages()

        module = types.ModuleType("anthropic")
        module.AsyncAnthropic = AsyncAnthropic
        monkeypatch.setitem(sys.modules, "anthropic", module)

    return install


def finish(rec, **kw):
    args = {
        "company": make_company(),
        "product": "Cocoa Hazelnut overnight oats",
        "remix": make_remix(),
        "score": make_score(),
        "sources": [make_trend("t1"), make_trend("t2")],
        "voice_id": "TX3LPaxmHKxFdv7VOQHJ",
    }
    args.update(kw)
    return rec.finish(**args)


# --- the ambient recorder ---------------------------------------------------


async def test_there_is_no_recorder_outside_a_block():
    assert current() is None


async def test_the_recorder_is_ambient_inside_its_block_and_gone_after(tmp_path):
    async with Recorder(tmp_path) as rec:
        assert current() is rec
    assert current() is None


async def test_the_recorder_is_restored_after_a_failure(tmp_path):
    with pytest.raises(RuntimeError):
        async with Recorder(tmp_path):
            raise RuntimeError("a variant blew up")
    assert current() is None


# --- capture ----------------------------------------------------------------


async def test_llm_calls_route_themselves_into_the_active_recorder(
    tmp_path, anthropic_stub
):
    anthropic_stub('{"hook": "yes"}')
    async with Recorder(tmp_path) as rec:
        await complete(PROMPT, system=SYSTEM, max_tokens=1500)

    assert len(rec.calls) == 1
    call = rec.calls[0]
    assert call["system_prompt"] == SYSTEM
    assert call["user_prompt"] == PROMPT
    assert call["response"] == '{"hook": "yes"}'
    assert call["max_tokens"] == 1500
    assert call["stop_reason"] == "end_turn"


async def test_an_llm_call_with_no_recorder_does_not_explode(anthropic_stub):
    """The pipeline has to run outside a Recorder too — e.g. from the CLI."""
    anthropic_stub()
    text, _ = await complete("p", system="s")
    assert text == '{"ok": true}'


async def test_calls_are_numbered_in_order(tmp_path):
    async with Recorder(tmp_path) as rec:
        for i in range(3):
            rec.capture(system="s", prompt=f"p{i}", model="m", max_tokens=10,
                        response="r", stop_reason=None)
    assert [c["n"] for c in rec.calls] == [1, 2, 3]
    assert [c["user_prompt"] for c in rec.calls] == ["p0", "p1", "p2"]


async def test_notes_are_kept(tmp_path):
    async with Recorder(tmp_path) as rec:
        rec.note("lane", "founder-story")
        rec.note("look", "warm golden low light")
    assert rec.notes == {"lane": "founder-story", "look": "warm golden low light"}


# --- recipe.json ------------------------------------------------------------


async def test_finish_writes_both_artifacts(tmp_path):
    async with Recorder(tmp_path / "founder-story") as rec:
        rec.note("lane", "founder-story")
        rec.capture(system=SYSTEM, prompt=PROMPT, model="claude-sonnet-5",
                    max_tokens=4000, response='{"hook": "x"}', stop_reason="end_turn")
    path = finish(rec)

    assert path.name == "recipe.json"
    assert path.exists()
    assert (tmp_path / "founder-story" / "RECIPE.md").exists()


async def test_recipe_json_holds_the_prompts_verbatim(tmp_path):
    async with Recorder(tmp_path) as rec:
        rec.capture(system=SYSTEM, prompt=PROMPT, model="claude-sonnet-5",
                    max_tokens=4000, response="{}", stop_reason="end_turn")
    finish(rec)

    data = json.loads((tmp_path / "recipe.json").read_text())
    assert data["llm_calls"][0]["system_prompt"] == SYSTEM
    assert data["llm_calls"][0]["user_prompt"] == PROMPT


async def test_recipe_json_records_the_corpus_that_was_in_scope(tmp_path):
    """"The ad was told to borrow from these and nothing else" is a claim the
    recipe has to be able to back up."""
    async with Recorder(tmp_path) as rec:
        pass
    finish(rec, sources=[make_trend("t1"), make_trend("t2"), make_trend("t3")])

    data = json.loads((tmp_path / "recipe.json").read_text())
    assert [c["trend_key"] for c in data["corpus"]] == ["t1", "t2", "t3"]
    assert all(c["source_url"] for c in data["corpus"])


async def test_recipe_json_records_the_settings_in_force(tmp_path, cfg):
    async with Recorder(tmp_path) as rec:
        pass
    finish(rec, settings_snapshot={"evidence_floor": cfg.evidence_floor,
                                   "surface_threshold": cfg.surface_threshold})

    data = json.loads((tmp_path / "recipe.json").read_text())
    assert data["settings"]["evidence_floor"] == 3.0
    assert data["voice_id"] == "TX3LPaxmHKxFdv7VOQHJ"
    assert data["score"]["evidence"] == 4.0
    assert data["git_commit"]


async def test_recipe_json_is_reloadable_as_the_models_it_came_from(tmp_path):
    """Round-tripping is what makes a recipe a starting point rather than a log."""
    from vira.models import Company, Remix

    remix = make_remix()
    async with Recorder(tmp_path) as rec:
        pass
    finish(rec, remix=remix)

    data = json.loads((tmp_path / "recipe.json").read_text())
    assert Company(**data["company"]).slug == "sunday-oats"
    rebuilt = Remix(hook=data["output"]["hook"], beats=data["output"]["beats"],
                    caption=data["output"]["caption"], cta=data["output"]["cta"])
    assert rebuilt.narration() == remix.narration()


async def test_a_recipe_without_a_score_still_writes(tmp_path):
    """The agentic path finishes before the gate has run."""
    async with Recorder(tmp_path) as rec:
        pass
    finish(rec, score=None)
    assert json.loads((tmp_path / "recipe.json").read_text())["score"] is None


# --- RECIPE.md --------------------------------------------------------------


async def test_recipe_md_holds_the_prompts_verbatim(tmp_path):
    async with Recorder(tmp_path) as rec:
        rec.note("lane", "founder-story")
        rec.capture(system=SYSTEM, prompt=PROMPT, model="claude-sonnet-5",
                    max_tokens=4000, response='{"hook": "x"}', stop_reason="end_turn")
    finish(rec)

    md = (tmp_path / "RECIPE.md").read_text()
    assert SYSTEM in md
    assert PROMPT in md
    assert '{"hook": "x"}' in md
    assert "claude-sonnet-5" in md
    assert "max_tokens=4000" in md


async def test_recipe_md_names_the_company_and_the_lane(tmp_path):
    async with Recorder(tmp_path) as rec:
        rec.note("lane", "contrarian")
    finish(rec)

    md = (tmp_path / "RECIPE.md").read_text()
    assert md.startswith("# Recipe — Sunday Oats · contrarian")


async def test_recipe_md_falls_back_when_no_lane_was_noted(tmp_path):
    async with Recorder(tmp_path) as rec:
        pass
    finish(rec)
    assert "· default" in (tmp_path / "RECIPE.md").read_text()


async def test_recipe_md_lists_the_output_and_the_sources(tmp_path):
    remix = make_remix()
    async with Recorder(tmp_path) as rec:
        pass
    finish(rec, remix=remix)

    md = (tmp_path / "RECIPE.md").read_text()
    assert remix.hook in md
    assert remix.cta in md
    for beat in remix.beats:
        assert beat.say in md
    assert "t1" in md and "t2" in md
    assert "2 verified sources" in md


async def test_recipe_md_survives_a_recipe_with_nothing_optional_set(tmp_path):
    async with Recorder(tmp_path) as rec:
        pass
    rec.finish(company=make_company(), product="oats",
               remix=make_remix(hashtags=[], grounded_in=[]))
    assert (tmp_path / "RECIPE.md").read_text()


async def test_finish_creates_the_output_directory(tmp_path):
    target = tmp_path / "v001-20260815" / "demo-first"
    async with Recorder(target) as rec:
        pass
    finish(rec)
    assert (target / "recipe.json").exists()


# --- which stage made the call ---------------------------------------------


async def test_a_captured_call_names_its_stage_when_the_api_is_driving(
    tmp_path, anthropic_stub
):
    """The recipe reads better when a prompt says where it came from.

    Only the API worker tracks a stage, so this is the path a job takes; the CLI
    keeps recording an empty one, which the recipe renders as no annotation at
    all rather than as a wrong one.
    """
    from vira.api import events

    anthropic_stub('{"hook": "yes"}')
    job = "5c4b3a29-1111-2222-3333-444455556666"
    try:
        with events.watching(job):
            events.set_stage("critique")
            async with Recorder(tmp_path) as rec:
                await complete(PROMPT, system=SYSTEM)
    finally:
        events.bus.forget(job)

    assert rec.calls[0]["stage"] == "critique"


async def test_a_cli_call_records_no_stage(tmp_path, anthropic_stub):
    anthropic_stub('{"hook": "yes"}')
    async with Recorder(tmp_path) as rec:
        await complete(PROMPT, system=SYSTEM)

    assert rec.calls[0]["stage"] == ""


async def test_the_director_records_its_own_prompts_too(tmp_path):
    """The one model in the system that does not go through `vira.llm`.

    Its instructions decide the shape of the whole film, so an agentic recipe
    that omitted them recorded the crew's homework and not the brief that set it.
    """
    from vira.agentic.crew import DIRECTOR_INSTRUCTIONS, Production, _record_turn

    seen: list[tuple] = []
    p = Production.__new__(Production)
    p.log = []
    p.on_event = lambda stage, msg, level, data: seen.append((stage, level, data))

    class Call:
        id = "call_1"
        function = type("F", (), {"name": "write_script", "arguments": '{"beat_count": 5}'})()

    reply = type("M", (), {"content": None, "tool_calls": [Call()]})()

    async with Recorder(tmp_path) as rec:
        _record_turn(
            p, turn=1,
            messages=[{"role": "system", "content": DIRECTOR_INSTRUCTIONS},
                      {"role": "user", "content": "Brand: Sunday Oats"}],
            reply=reply, model="gpt-5.4", elapsed_ms=1200, stop_reason="tool_calls",
        )

    assert rec.calls[0]["system_prompt"] == DIRECTOR_INSTRUCTIONS
    assert "Sunday Oats" in rec.calls[0]["user_prompt"]
    assert "write_script" in rec.calls[0]["response"]
    assert rec.calls[0]["stage"] == "director:turn1"

    stage, level, data = seen[0]
    assert (stage, level) == ("llm", "debug")
    assert data["system_prompt"] == DIRECTOR_INSTRUCTIONS

"""The LLM wrapper — JSON extraction and the truncation retry.

Truncation is the failure mode that actually happens in this pipeline: a model
writes a long string inside a list, runs out of budget mid-token, and returns
unparseable JSON. The retry exists for exactly that, and it has to grow the
budget rather than merely try again, so both halves are asserted.
"""

from __future__ import annotations

import json

import pytest

from vira import llm
from vira.llm import LLMError, _extract, complete, complete_json

# `azure_stub` comes from conftest — the same fake provider drives the
# provenance tests, and one stub means one place to keep honest.


# --- _extract ---------------------------------------------------------------


def test_extract_unwraps_a_json_fence():
    assert _extract('```json\n{"a": 1}\n```') == '{"a": 1}'


def test_extract_unwraps_a_bare_fence():
    assert _extract('```\n{"a": 1}\n```') == '{"a": 1}'


def test_extract_unwraps_a_fence_buried_in_prose():
    raw = 'Sure! Here you go:\n\n```json\n{"a": 1}\n```\n\nHope that helps.'
    assert _extract(raw) == '{"a": 1}'


def test_extract_drops_leading_prose_without_a_fence():
    assert _extract('Here is the JSON: {"a": 1}') == '{"a": 1}'


def test_extract_leaves_a_bare_object_alone():
    assert _extract('{"a": 1}') == '{"a": 1}'


def test_extract_tolerates_surrounding_whitespace():
    assert _extract('\n\n  {"a": 1}  \n') == '{"a": 1}'


@pytest.mark.parametrize(
    "raw",
    [
        '```json\n{"a": 1}\n```',
        '```\n{"a": 1}\n```',
        'Here is the JSON: {"a": 1}',
        '{"a": 1}',
        '  ```json\n{"a": 1}\n```  ',
    ],
)
def test_everything_extract_returns_is_loadable(raw):
    assert json.loads(_extract(raw)) == {"a": 1}


# --- complete ---------------------------------------------------------------


async def test_complete_returns_the_message_content(azure_stub):
    azure_stub("the whole answer")
    text, stop = await complete("p", system="s")

    assert text == "the whole answer"
    assert stop == "stop"


async def test_a_reply_with_no_content_becomes_an_empty_string(azure_stub):
    """A filtered reply carries `content: null`, and every caller downstream of
    here does string work on what comes back."""
    azure_stub((None, "content_filter"))
    text, stop = await complete("p", system="s")

    assert text == ""
    assert stop == "content_filter"


async def test_complete_refuses_to_run_without_a_key(cfg, azure_stub):
    cfg.azure_openai_api_key = None
    azure_stub("{}")
    with pytest.raises(LLMError, match="AZURE_OPENAI_API_KEY"):
        await complete("p", system="s")


async def test_complete_refuses_to_run_without_an_endpoint(cfg, azure_stub):
    cfg.azure_openai_endpoint = None
    azure_stub("{}")
    with pytest.raises(LLMError, match="AZURE_OPENAI_ENDPOINT"):
        await complete("p", system="s")


async def test_complete_sends_the_configured_model_and_the_given_budget(
    cfg, azure_stub
):
    cfg.agent_model = "gpt-test-model"
    sent = azure_stub("{}")
    await complete("the prompt", system="the system", max_tokens=123)

    assert sent[0]["model"] == "gpt-test-model"
    assert sent[0]["max_completion_tokens"] == 123
    assert sent[0]["messages"] == [
        {"role": "system", "content": "the system"},
        {"role": "user", "content": "the prompt"},
    ]


async def test_an_empty_system_prompt_sends_no_system_message(azure_stub):
    sent = azure_stub("{}")
    await complete("the prompt", system="")

    assert sent[0]["messages"] == [{"role": "user", "content": "the prompt"}]


async def test_the_providers_length_is_reported_as_max_tokens(azure_stub):
    """The provider says "length" where the rest of this codebase says
    "max_tokens". `complete_json`'s truncation retry keys on the literal string,
    so without this mapping every truncated reply reads as a complete one."""
    azure_stub(("half a th", "length"))
    _, stop = await complete("p", system="s")

    assert stop == "max_tokens"


async def test_an_ordinary_stop_reason_is_passed_through(azure_stub):
    azure_stub(("the whole answer", "stop"))
    _, stop = await complete("p", system="s")

    assert stop == "stop"


# --- complete_json ----------------------------------------------------------


async def test_json_returns_on_the_first_clean_attempt(azure_stub):
    sent = azure_stub('{"hook": "yes"}')
    assert await complete_json("p", system="s") == {"hook": "yes"}
    assert len(sent) == 1


async def test_truncation_retries_with_a_bigger_budget_and_succeeds(azure_stub):
    """The tail of a truncated response is simply gone, so the retry has to buy
    more room and ask for brevity — repairing the fragment is not an option.

    The first reply finishes on the provider's "length", which is the only way
    this path is reachable in production."""
    sent = azure_stub(
        ('{"hook": "a very long string that ran out of bud', "length"),
        '{"hook": "short"}',
    )
    got = await complete_json("write me an ad", system="s", max_tokens=1000)

    assert got == {"hook": "short"}
    assert len(sent) == 2
    assert sent[0]["max_completion_tokens"] == 1000
    assert sent[1]["max_completion_tokens"] == 2000
    assert sent[1]["messages"][-1]["content"].startswith("write me an ad")
    assert llm.TERSE in sent[1]["messages"][-1]["content"]
    assert sent[1]["messages"][0] == sent[0]["messages"][0]


async def test_the_first_attempt_carries_no_brevity_hint(azure_stub):
    sent = azure_stub("{}")
    await complete_json("write me an ad", system="s")
    assert sent[0]["messages"][-1]["content"] == "write me an ad"


async def test_unparseable_json_is_retried_then_returned(azure_stub):
    sent = azure_stub("I'd rather not.", '{"ok": true}')
    assert await complete_json("p", system="s") == {"ok": True}
    assert len(sent) == 2


async def test_a_fenced_reply_needs_no_retry(azure_stub):
    sent = azure_stub('```json\n{"ok": true}\n```')
    assert await complete_json("p", system="s") == {"ok": True}
    assert len(sent) == 1


async def test_two_truncations_raise_rather_than_return_a_fragment(azure_stub):
    azure_stub(('{"a": 1', "length"), ('{"a": 1', "length"))
    with pytest.raises(LLMError, match="truncated"):
        await complete_json("p", system="s")


async def test_a_json_array_is_not_accepted_as_an_object(azure_stub):
    """Callers index the result by key; a list would blow up far from here."""
    azure_stub("[1, 2, 3]", "[1, 2, 3]")
    with pytest.raises(LLMError, match="JSON object"):
        await complete_json("p", system="s")


async def test_a_persistently_broken_model_raises_with_the_last_error(azure_stub):
    azure_stub("nope", "still nope")
    with pytest.raises(LLMError, match="unparseable"):
        await complete_json("p", system="s")


async def test_it_gives_up_after_two_attempts_not_forever(azure_stub):
    sent = azure_stub("nope", "nope")
    with pytest.raises(LLMError):
        await complete_json("p", system="s")
    assert len(sent) == 2


# --- the live feed ----------------------------------------------------------
#
# Verbose mode hangs off a context variable the API worker sets. The CLI never
# sets it, and these two tests are the boundary: nothing published, nothing
# imported, nothing changed for `variants.py` and `agentic_video.py`.


VERBOSE_JOB = "9d8c7b6a-5555-4444-3333-222211110000"


async def test_a_call_outside_a_job_publishes_nothing(azure_stub):
    from vira.api import events

    azure_stub("the answer")
    assert events.current_job() is None

    text, _ = await complete("the prompt", system="the system")

    assert text == "the answer"
    assert events.bus.known(VERBOSE_JOB) is False


async def test_a_call_inside_a_job_puts_the_whole_prompt_on_the_feed(azure_stub):
    from vira.api import events

    prompt = "Write the ad.\n" + ("y" * 9_000)
    azure_stub("the answer")
    try:
        with events.watching(VERBOSE_JOB):
            events.set_stage("write")
            await complete(prompt, system="the system", max_tokens=321)

        # Not on the ordinary feed, and whole on the verbose one.
        assert events.bus.history(VERBOSE_JOB) == []
        (event,) = events.bus.history(VERBOSE_JOB, level="debug")
        assert event.stage == "llm"
        assert event.data["pipeline_stage"] == "write"
        assert event.data["model"] == "gpt-5.4"
        assert event.data["max_tokens"] == 321
        assert event.data["stop_reason"] == "stop"
        assert event.data["system_prompt"] == "the system"
        assert event.data["user_prompt"] == prompt
        assert event.data["response"] == "the answer"
    finally:
        events.bus.forget(VERBOSE_JOB)


async def test_the_job_binding_does_not_outlive_its_block(azure_stub):
    from vira.api import events

    with events.watching(VERBOSE_JOB):
        assert events.current_job() == VERBOSE_JOB
    assert events.current_job() is None
    assert events.current_stage() == ""

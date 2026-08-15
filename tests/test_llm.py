"""The LLM wrapper — JSON extraction and the truncation retry.

Truncation is the failure mode that actually happens in this pipeline: a model
writes a long string inside a list, runs out of budget mid-token, and returns
unparseable JSON. The retry exists for exactly that, and it has to grow the
budget rather than merely try again, so both halves are asserted.
"""

from __future__ import annotations

import json
import sys
import types

import pytest

from vira import llm
from vira.llm import LLMError, _extract, complete, complete_json


class Block:
    def __init__(self, text: str, kind: str = "text"):
        self.text = text
        self.type = kind


class Reply:
    def __init__(self, blocks, stop_reason):
        self.content = blocks
        self.stop_reason = stop_reason


def text_reply(body: str, stop: str = "end_turn") -> Reply:
    return Reply([Block(body)], stop)


@pytest.fixture
def anthropic_stub(monkeypatch):
    """`vira.llm` imports the SDK inside the call, so the stub goes in sys.modules.

    Returns the list of requests the fake client received, which is how the
    retry's larger budget and brevity hint get inspected.
    """
    sent: list[dict] = []

    def install(*replies):
        queue = list(replies)

        class Messages:
            async def create(self, **kw):
                sent.append(kw)
                assert queue, "the model was called more times than expected"
                return queue.pop(0)

        class AsyncAnthropic:
            def __init__(self, api_key=None, **_):
                self.api_key = api_key
                self.messages = Messages()

        module = types.ModuleType("anthropic")
        module.AsyncAnthropic = AsyncAnthropic
        monkeypatch.setitem(sys.modules, "anthropic", module)
        return sent

    return install


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


async def test_complete_joins_text_blocks_and_ignores_the_rest(anthropic_stub):
    anthropic_stub(
        Reply([Block("part one "), Block("{}", "tool_use"), Block("part two")],
              "end_turn")
    )
    text, stop = await complete("p", system="s")

    assert text == "part one part two"
    assert stop == "end_turn"


async def test_complete_refuses_to_run_without_a_key(cfg, anthropic_stub):
    cfg.anthropic_api_key = None
    anthropic_stub(text_reply("{}"))
    with pytest.raises(LLMError, match="ANTHROPIC_API_KEY"):
        await complete("p", system="s")


async def test_complete_sends_the_configured_model_and_the_given_budget(
    cfg, anthropic_stub
):
    cfg.llm_model = "claude-test-model"
    sent = anthropic_stub(text_reply("{}"))
    await complete("the prompt", system="the system", max_tokens=123)

    assert sent[0]["model"] == "claude-test-model"
    assert sent[0]["max_tokens"] == 123
    assert sent[0]["system"] == "the system"
    assert sent[0]["messages"] == [{"role": "user", "content": "the prompt"}]


async def test_complete_surfaces_the_stop_reason(anthropic_stub):
    """Truncation is only detectable through this value, so it must not be lost."""
    anthropic_stub(text_reply("half a th", stop="max_tokens"))
    _, stop = await complete("p", system="s")
    assert stop == "max_tokens"


# --- complete_json ----------------------------------------------------------


async def test_json_returns_on_the_first_clean_attempt(anthropic_stub):
    sent = anthropic_stub(text_reply('{"hook": "yes"}'))
    assert await complete_json("p", system="s") == {"hook": "yes"}
    assert len(sent) == 1


async def test_truncation_retries_with_a_bigger_budget_and_succeeds(anthropic_stub):
    """The tail of a truncated response is simply gone, so the retry has to buy
    more room and ask for brevity — repairing the fragment is not an option."""
    sent = anthropic_stub(
        text_reply('{"hook": "a very long string that ran out of bud',
                   stop="max_tokens"),
        text_reply('{"hook": "short"}'),
    )
    got = await complete_json("write me an ad", system="s", max_tokens=1000)

    assert got == {"hook": "short"}
    assert len(sent) == 2
    assert sent[0]["max_tokens"] == 1000
    assert sent[1]["max_tokens"] == 2000
    assert sent[1]["messages"][0]["content"].startswith("write me an ad")
    assert llm.TERSE in sent[1]["messages"][0]["content"]
    assert sent[1]["system"] == sent[0]["system"]


async def test_the_first_attempt_carries_no_brevity_hint(anthropic_stub):
    sent = anthropic_stub(text_reply("{}"))
    await complete_json("write me an ad", system="s")
    assert sent[0]["messages"][0]["content"] == "write me an ad"


async def test_unparseable_json_is_retried_then_returned(anthropic_stub):
    sent = anthropic_stub(text_reply("I'd rather not."), text_reply('{"ok": true}'))
    assert await complete_json("p", system="s") == {"ok": True}
    assert len(sent) == 2


async def test_a_fenced_reply_needs_no_retry(anthropic_stub):
    sent = anthropic_stub(text_reply('```json\n{"ok": true}\n```'))
    assert await complete_json("p", system="s") == {"ok": True}
    assert len(sent) == 1


async def test_two_truncations_raise_rather_than_return_a_fragment(anthropic_stub):
    anthropic_stub(text_reply('{"a": 1', stop="max_tokens"),
                   text_reply('{"a": 1', stop="max_tokens"))
    with pytest.raises(LLMError, match="truncated"):
        await complete_json("p", system="s")


async def test_a_json_array_is_not_accepted_as_an_object(anthropic_stub):
    """Callers index the result by key; a list would blow up far from here."""
    anthropic_stub(text_reply("[1, 2, 3]"), text_reply("[1, 2, 3]"))
    with pytest.raises(LLMError, match="JSON object"):
        await complete_json("p", system="s")


async def test_a_persistently_broken_model_raises_with_the_last_error(anthropic_stub):
    anthropic_stub(text_reply("nope"), text_reply("still nope"))
    with pytest.raises(LLMError, match="unparseable"):
        await complete_json("p", system="s")


async def test_it_gives_up_after_two_attempts_not_forever(anthropic_stub):
    sent = anthropic_stub(text_reply("nope"), text_reply("nope"))
    with pytest.raises(LLMError):
        await complete_json("p", system="s")
    assert len(sent) == 2

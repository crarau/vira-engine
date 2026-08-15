"""Terac client tests.

The thing worth testing here is not the wrappers — they are one dict each. It
is the transport, because the transport is where this integration is unusual:
a JSON-RPC reply arrives inside an SSE frame, and every mistake in unwrapping
it produces a confident wrong answer rather than an exception. So the fakes
below serve genuine `text/event-stream` bodies, including the shapes the spec
allows but Terac does not happen to send today.

The second thing worth testing is that money is hard to spend by accident.
"""

from __future__ import annotations

import json

import httpx
import pytest

from vira import terac


def transport(body: str, status: int = 200, capture: dict | None = None) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if capture is not None:
            capture["headers"] = dict(request.headers)
            capture["json"] = json.loads(request.content)
        return httpx.Response(
            status, text=body, headers={"content-type": "text/event-stream"}
        )

    return httpx.MockTransport(handler)


@pytest.fixture
def mcp(monkeypatch):
    """Swap httpx.AsyncClient for one wired to a MockTransport of our choosing."""
    state: dict = {}

    def install(body: str, status: int = 200) -> dict:
        real = httpx.AsyncClient

        def factory(*_a, **kwargs):
            return real(transport=transport(body, status, state), **kwargs)

        monkeypatch.setattr(terac.httpx, "AsyncClient", factory)
        return state

    monkeypatch.setattr(terac.settings(), "terac_api_key", "tk_test_key")
    return install


def sse(result: object) -> str:
    return f'event: message\ndata: {json.dumps({"jsonrpc": "2.0", "id": 1, "result": result})}\n\n'


def tool_result(payload: object) -> str:
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return sse({"content": [{"type": "text", "text": text}]})


# -- SSE framing -----------------------------------------------------------


def test_frames_reads_a_single_data_line():
    assert terac._frames('event: message\ndata: {"a": 1}\n\n') == [{"a": 1}]


def test_frames_reads_data_with_no_space_after_the_colon():
    """The SSE spec makes the space optional. A `[6:]` slice eats the `{`."""
    assert terac._frames('data:{"a": 1}\n\n') == [{"a": 1}]


def test_frames_joins_a_message_split_over_several_data_lines():
    assert terac._frames('data: {"a":\ndata:  1}\n\n') == [{"a": 1}]


def test_frames_ignores_event_and_id_lines_and_reads_every_message():
    body = 'event: message\nid: 1\ndata: {"a": 1}\n\nevent: message\ndata: {"b": 2}\n\n'
    assert terac._frames(body) == [{"a": 1}, {"b": 2}]


# -- transport -------------------------------------------------------------


async def test_call_tool_unwraps_the_sse_frame_and_the_content_block(mcp):
    mcp(tool_result("hello"))
    assert await terac.call_tool("terac_get_context") == "hello"


async def test_call_json_parses_a_json_document_tool_result(mcp):
    mcp(tool_result({"data": [{"id": "abc"}]}))
    assert await terac.call_json("terac_list_opportunities") == {"data": [{"id": "abc"}]}


async def test_call_json_keeps_markdown_tools_usable(mcp):
    """terac_get_context returns prose. It must not blow up a JSON parse."""
    mcp(tool_result("## Vira\n- **Balance:** $25.00"))
    payload = await terac.call_json("terac_get_context")
    assert payload["text"].startswith("## Vira")


async def test_the_request_carries_bearer_auth_and_both_accept_types(mcp):
    """Send only application/json and Terac refuses before reading the body."""
    state = mcp(tool_result("ok"))
    await terac.call_tool("terac_get_context")
    assert state["headers"]["authorization"] == "Bearer tk_test_key"
    assert "text/event-stream" in state["headers"]["accept"]
    assert "application/json" in state["headers"]["accept"]


async def test_it_is_stateless_no_initialize_and_no_session_header(mcp):
    state = mcp(tool_result("ok"))
    await terac.call_tool("terac_get_context")
    assert state["json"]["method"] == "tools/call"
    assert "mcp-session-id" not in state["headers"]


async def test_a_jsonrpc_error_frame_raises(mcp):
    mcp(sse(None).replace('"result": null', '"error": {"code": -32602}'))
    with pytest.raises(terac.TeracError):
        await terac.call_tool("terac_nope")


async def test_an_is_error_tool_result_raises_rather_than_returning_the_message(mcp):
    """`isError` lives inside a 200 with a perfectly valid frame."""
    mcp(sse({"isError": True, "content": [{"type": "text", "text": "bad project_id"}]}))
    with pytest.raises(terac.TeracError, match="bad project_id"):
        await terac.call_tool("terac_create_opportunity", {})


async def test_an_http_error_surfaces_the_body_not_just_the_status(mcp):
    mcp("Not Acceptable", status=406)
    with pytest.raises(terac.TeracError, match="406"):
        await terac.call_tool("terac_get_context")


async def test_a_body_with_no_data_frame_raises_instead_of_returning_none(mcp):
    mcp("event: ping\n\n")
    with pytest.raises(terac.TeracError, match="no data frame"):
        await terac.call_tool("terac_get_context")


async def test_no_key_raises_before_any_request_is_made(monkeypatch):
    monkeypatch.setattr(terac.settings(), "terac_api_key", None)
    with pytest.raises(terac.TeracNotConfigured):
        await terac.call_tool("terac_get_context")


async def test_the_key_never_appears_in_an_error_message(mcp):
    mcp("Unauthorized", status=401)
    with pytest.raises(terac.TeracError) as caught:
        await terac.call_tool("terac_get_context")
    assert "tk_test_key" not in str(caught.value)


# -- the payload that becomes a panel --------------------------------------


def test_the_judge_url_becomes_the_task_url():
    """This one line is the entire Terac integration."""
    payload = terac.judge_opportunity_payload(
        batch_id="b1", judge_url="https://vira.example/review/tok", title="Rate",
        num_participants=5,
    )
    assert payload["tasks"][0]["task_url"] == "https://vira.example/review/tok"
    assert payload["tasks"][0]["task_type"] == "activity"


def test_the_payload_has_no_screener_because_a_screener_costs_hours():
    payload = terac.judge_opportunity_payload(
        batch_id="b1", judge_url="https://x.test/t", title="Rate", num_participants=5
    )
    assert "screening_questions" not in payload


def test_general_population_is_unrestricted_audience_with_no_filters():
    """Terac rejects a create carrying neither, so the flag is not optional."""
    payload = terac.judge_opportunity_payload(
        batch_id="b1", judge_url="https://x.test/t", title="Rate", num_participants=5
    )
    assert payload["unrestricted_audience"] is True
    assert "filters" not in payload


def test_the_batch_id_travels_in_the_internal_title_for_a_human_reading_terac():
    payload = terac.judge_opportunity_payload(
        batch_id="b-42", judge_url="https://x.test/t", title="Rate", num_participants=5
    )
    assert "b-42" in payload["internal_title"]


# -- spending is deliberate ------------------------------------------------


async def test_launch_refuses_without_the_explicit_flag(mcp):
    mcp(tool_result({"status": "active"}))
    with pytest.raises(terac.TeracError, match="refusing to launch"):
        await terac.launch_draft("opp1")


async def test_launch_sends_nothing_when_it_refuses(mcp):
    state = mcp(tool_result({"status": "active"}))
    with pytest.raises(terac.TeracError):
        await terac.launch_draft("opp1")
    assert state == {}


async def test_launch_goes_through_with_the_flag(mcp):
    state = mcp(tool_result({"status": "active"}))
    await terac.launch_draft("opp1", i_understand_this_spends_real_money=True)
    assert state["json"]["params"]["name"] == "terac_launch_draft_opportunity"


# -- reading a panel back --------------------------------------------------


def test_batch_token_is_recovered_from_the_task_url():
    """The URL is the batch<->opportunity link; there is no column for it."""
    opportunity = {"tasks": [{"task_url": "https://vira.example/v1/review-batches/tok-9"}]}
    assert terac.batch_token_of(opportunity) == "tok-9"


def test_batch_token_is_none_when_terac_holds_no_url():
    assert terac.batch_token_of({"tasks": [{"task_url": None}]}) is None


def test_submission_ref_is_namespaced_so_a_colleague_is_not_a_panellist():
    assert terac.submission_ref({"id": "sub1"}) == "terac:sub1"


def test_submission_text_walks_a_shape_the_tool_schema_does_not_pin():
    submission = {
        "tasks": [{"response": "the second one, the hook lands"}],
        "answers": [{"text": "would share it"}],
    }
    text = terac.submission_text(submission)
    assert "the hook lands" in text and "would share it" in text


def test_submission_text_is_empty_rather_than_raising_on_an_unknown_shape():
    assert terac.submission_text({"weird": 1}) == ""

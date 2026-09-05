from unittest.mock import Mock

from pydantic import BaseModel

import specialist_workflow.llm as llm
from specialist_workflow.llm import _can_fallback_to_chat, parse_structured_output


class Example(BaseModel):
    passed: bool
    summary: str


def test_parse_plain_json() -> None:
    result = parse_structured_output('{"passed": true, "summary": "ok"}', Example)
    assert result.passed is True


def test_parse_fenced_json() -> None:
    result = parse_structured_output(
        '```json\n{"passed": false, "summary": "needs work"}\n```', Example
    )
    assert result.summary == "needs work"


def test_parse_json_surrounded_by_text() -> None:
    result = parse_structured_output(
        'Result follows: {"passed": true, "summary": "ok"} done.', Example
    )
    assert result.passed is True


def test_protocol_fallback_for_not_implemented() -> None:
    error = Mock(status_code=500)
    error.__str__ = Mock(return_value="500 not implemented convert_request_failed")
    assert _can_fallback_to_chat(error) is True


def test_protocol_does_not_fallback_for_auth_error() -> None:
    error = Mock(status_code=401)
    error.__str__ = Mock(return_value="401 invalid api key")
    assert _can_fallback_to_chat(error) is False


def test_extract_streamed_text_ignores_relay_metadata_events() -> None:
    lines = [
        "event: codex.rate_limits",
        'data: {"type":"codex.rate_limits","remaining":99}',
        "",
        "event: response.created",
        'data: {"type":"response.created"}',
        "",
        "event: response.output_text.delta",
        'data: {"type":"response.output_text.delta","delta":"{\\"passed\\": "}',
        "",
        "event: response.output_text.delta",
        'data: {"type":"response.output_text.delta","delta":"true}"}',
        "",
        "data: [DONE]",
        "",
    ]

    assert llm._response_text_from_sse(lines) == '{"passed": true}'


def test_response_uses_raw_stream_to_tolerate_relay_events() -> None:
    raw_response = Mock()
    raw_response.iter_lines.return_value = [
        "event: codex.rate_limits",
        'data: {"type":"codex.rate_limits"}',
        "",
        "event: response.output_text.delta",
        'data: {"type":"response.output_text.delta","delta":"{\\"passed\\":true}"}',
        "",
    ]
    stream_context = Mock()
    stream_context.__enter__ = Mock(return_value=raw_response)
    stream_context.__exit__ = Mock(return_value=False)
    client = llm.StructuredModelClient.__new__(llm.StructuredModelClient)
    client.client = Mock()
    create = client.client.responses.with_streaming_response.create
    create.return_value = stream_context

    result = client._response("planner-model", "system prompt", "user prompt")

    assert result == '{"passed":true}'
    create.assert_called_once_with(
        model="planner-model",
        instructions="system prompt",
        input="user prompt",
        stream=True,
    )

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from typing import TypeVar

from openai import APIStatusError, OpenAI
from pydantic import BaseModel

from .config import Settings

SchemaT = TypeVar("SchemaT", bound=BaseModel)


def _response_text_from_sse(lines: Iterable[str]) -> str:
    """Collect visible text deltas while ignoring relay-specific SSE events."""
    deltas: list[str] = []
    data_lines: list[str] = []

    def consume_event() -> None:
        if not data_lines:
            return
        raw = "\n".join(data_lines)
        if raw == "[DONE]":
            return
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            return
        if event.get("type") != "response.output_text.delta":
            return
        delta = event.get("delta")
        if isinstance(delta, str):
            deltas.append(delta)

    for line in lines:
        if not line:
            consume_event()
            data_lines = []
            continue
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    consume_event()
    return "".join(deltas)


class StructuredModelClient:
    """Small OpenAI-compatible client with strict local validation."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = OpenAI(
            api_key=settings.resolved_api_key(),
            base_url=settings.resolved_base_url(),
            timeout=180.0,
            max_retries=2,
        )

    def generate(
        self,
        model: str,
        system: str,
        prompt: str,
        schema: type[SchemaT],
        *,
        mode: str | None = None,
    ) -> SchemaT:
        schema_json = json.dumps(schema.model_json_schema(), ensure_ascii=False)
        full_prompt = (
            f"{prompt}\n\nReturn exactly one JSON object. Do not use Markdown fences. "
            f"The JSON must satisfy this schema:\n{schema_json}"
        )
        selected_mode = (mode or self.settings.ai_api_mode).strip().lower()
        if selected_mode not in {"auto", "responses", "chat_completions"}:
            raise ValueError("AI_API_MODE must be auto, responses, or chat_completions")

        if selected_mode == "chat_completions":
            text = self._chat_completion(model, system, full_prompt)
        elif selected_mode == "responses":
            text = self._response(model, system, full_prompt)
        else:
            try:
                text = self._response(model, system, full_prompt)
            except APIStatusError as error:
                if not _can_fallback_to_chat(error):
                    raise
                text = self._chat_completion(model, system, full_prompt)
        return parse_structured_output(text, schema)

    def _response(self, model: str, system: str, prompt: str) -> str:
        with self.client.responses.with_streaming_response.create(
            model=model,
            instructions=system,
            input=prompt,
            stream=True,
        ) as response:
            return _response_text_from_sse(response.iter_lines())

    def _chat_completion(self, model: str, system: str, prompt: str) -> str:
        response = self.client.chat.completions.create(
            model=model,
            temperature=0,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
        )
        return response.choices[0].message.content or ""


def parse_structured_output(text: str, schema: type[SchemaT]) -> SchemaT:
    candidate = text.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", candidate, flags=re.DOTALL)
    if fenced:
        candidate = fenced.group(1)
    try:
        return schema.model_validate_json(candidate)
    except Exception as first_error:
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start >= 0 and end > start:
            try:
                return schema.model_validate_json(candidate[start : end + 1])
            except Exception:
                pass
        raise ValueError(f"Model did not return valid {schema.__name__} JSON") from first_error


def _can_fallback_to_chat(error: APIStatusError) -> bool:
    if error.status_code in {404, 405, 501}:
        return True
    if error.status_code not in {400, 422, 500}:
        return False
    detail = str(error).lower()
    markers = (
        "not implemented",
        "unsupported",
        "convert_request_failed",
        "unknown endpoint",
        "responses api",
    )
    return any(marker in detail for marker in markers)

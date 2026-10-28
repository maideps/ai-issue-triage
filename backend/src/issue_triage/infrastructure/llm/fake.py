"""Deterministic fake provider: no network, no key, no cost, same answer every time.

Model names look like ``fake/<handler>``. A handler turns the messages (and the requested
JSON schema) into reply text; unknown handlers fall back to the smallest JSON object that
satisfies the schema, so any structured call works out of the box.
"""

import json
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from issue_triage.domain.llm import Completion, Message, Usage

FakeHandler = Callable[[Sequence[Message], dict[str, Any] | None], str]


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def minimal_instance(schema: dict[str, Any], defs: dict[str, Any] | None = None) -> Any:
    """The simplest value valid for a (Pydantic-generated) JSON schema."""
    defs = defs if defs is not None else schema.get("$defs", {})
    if "$ref" in schema:
        return minimal_instance(defs[schema["$ref"].rsplit("/", 1)[-1]], defs)
    if "const" in schema:
        return schema["const"]
    if "enum" in schema:
        return schema["enum"][0]
    if "default" in schema:
        return schema["default"]
    for key in ("anyOf", "oneOf", "allOf"):
        if key in schema:
            return minimal_instance(schema[key][0], defs)
    kind = schema.get("type")
    if kind == "object":
        return {
            name: minimal_instance(sub, defs)
            for name, sub in schema.get("properties", {}).items()
            if name in schema.get("required", [])
        }
    if kind == "array":
        return []
    if kind == "string":
        return ""
    if kind in ("integer", "number"):
        return schema.get("minimum", 0)
    if kind == "boolean":
        return False
    return None


def schema_default_handler(messages: Sequence[Message], schema: dict[str, Any] | None) -> str:
    if schema is None:
        return "fake response"
    return json.dumps(minimal_instance(schema))


class FakeLLMProvider:
    def __init__(self, handlers: Mapping[str, FakeHandler] | None = None) -> None:
        self._handlers = dict(handlers or {})

    def register(self, name: str, handler: FakeHandler) -> None:
        self._handlers[name] = handler

    def complete(
        self,
        messages: Sequence[Message],
        *,
        model: str,
        response_schema: dict[str, Any] | None = None,
        temperature: float = 0.0,
    ) -> Completion:
        start = time.perf_counter()
        name = model.split("/", 1)[1] if "/" in model else model
        handler = self._handlers.get(name, schema_default_handler)
        text = handler(messages, response_schema)
        usage = Usage(
            input_tokens=sum(estimate_tokens(m.content) for m in messages),
            output_tokens=estimate_tokens(text),
            cost_usd=0.0,
            latency_ms=round((time.perf_counter() - start) * 1000, 3),
        )
        return Completion(text=text, model=model, usage=usage)

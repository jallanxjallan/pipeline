from __future__ import annotations

from openai import OpenAI

from asc.config.secrets import openai_api_key
from asc.models.process.result import ExternalFailure, Response
from asc.worker.runtime_io import EngineInput


ENGINE = "chatgpt"

DEFAULT_MODEL_LABEL = "terra"

MODEL_LABELS: dict[str, str] = {
    "sol": "gpt-5.6-sol",
    "terra": "gpt-5.6-terra",
    "luna": "gpt-5.6-luna",
}

# Existing published plans may still contain the old workload labels. Keep them
# executable without advertising them as choices in new plans.
LEGACY_MODEL_LABELS: dict[str, str] = {
    "best": "gpt-5.6-sol",
    "frontier": "gpt-5.6-sol",
    "pro": "gpt-5.6-sol",
    "standard": "gpt-5.6-terra",
    "cheap": "gpt-5.6-terra",
    "mini": "gpt-5.6-terra",
    "nano": "gpt-5.6-luna",
}

ENGINE_COMPONENT = {
    "label": "ChatGPT",
    "kind": "llm",
    "step_fields": [
        "model",
        "instruction_keys",
        "temperature",
        "max_output_tokens",
    ],
    "models": MODEL_LABELS,
    "default_model": DEFAULT_MODEL_LABEL,
}


def make_call(data: EngineInput) -> Response | ExternalFailure:
    """Run one ChatGPT Responses API call from a hydrated EngineInput."""

    runtime = data.runtime
    if runtime.engine_kind != "llm":
        raise TypeError(
            f"{ENGINE} requires an llm runtime, got {runtime.engine_kind!r}"
        )
    if not runtime.model:
        raise ValueError(f"{ENGINE} runtime requires model")

    try:
        model = {**LEGACY_MODEL_LABELS, **MODEL_LABELS}[runtime.model]
    except KeyError as exc:
        raise ValueError(
            f"unknown ChatGPT model label: {runtime.model!r}"
        ) from exc

    request: dict[str, object] = {
        "model": model,
        "input": data.content,
    }

    instructions = _instructions_text(data)
    if instructions:
        request["instructions"] = instructions

    if runtime.temperature is not None:
        request["temperature"] = runtime.temperature

    if runtime.max_output_tokens is not None:
        request["max_output_tokens"] = runtime.max_output_tokens

    try:
        result = OpenAI(api_key=openai_api_key()).responses.create(**request)
    except Exception as exc:
        return ExternalFailure(
            identity=data.call.identity,
            ordinal=runtime.ordinal,
            content=str(exc),
            failure_reason=type(exc).__name__,
            raw_json={
                "engine": ENGINE,
                "model_label": runtime.model,
                "model": model,
                "error": str(exc),
                "error_type": type(exc).__name__,
            },
            boundary=ENGINE,
        )

    return Response(
        identity=data.call.identity,
        ordinal=runtime.ordinal,
        content=result.output_text,
        raw_json={
            "engine": ENGINE,
            "model_label": runtime.model,
            "model": model,
            "provider": result.model_dump(mode="json"),
        },
    )


def _instructions_text(data: EngineInput) -> str:
    return "\n\n".join(
        instruction.content for instruction in data.instructions
    ).strip()


__all__ = [
    "DEFAULT_MODEL_LABEL",
    "ENGINE",
    "ENGINE_COMPONENT",
    "LEGACY_MODEL_LABELS",
    "MODEL_LABELS",
    "make_call",
]

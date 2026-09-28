from __future__ import annotations

from asc.models.process.result import Transform
from asc.registries.extensions import load_transform
from asc.worker.runtime_io import EngineInput


ENGINE = "scripts"
ENGINE_COMPONENT = {
    "label": "Local script",
    "kind": "script",
    "step_fields": ["script", "instruction_keys"],
}


def make_call(data: EngineInput) -> Transform:
    """Run one validated local script transform."""

    runtime = data.runtime
    if runtime.engine_kind != "script":
        raise TypeError(
            f"{ENGINE} requires a script runtime, got {runtime.engine_kind!r}"
        )
    if not runtime.script:
        raise ValueError(f"{ENGINE} runtime requires script")

    transform = load_transform(runtime.script)
    output = transform(data.content)

    return Transform(
        identity=data.call.identity,
        ordinal=runtime.ordinal,
        content=output,
        raw_json={
            "engine": ENGINE,
            "script": runtime.script,
        },
    )


__all__ = ["ENGINE", "ENGINE_COMPONENT", "make_call"]

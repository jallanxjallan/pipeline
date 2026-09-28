from __future__ import annotations

from asc.models.process.result import Retrieval
from asc.worker.runtime_io import EngineInput


ENGINE = "rag"
ENGINE_COMPONENT = {
    "label": "RAG",
    "kind": "rag",
    "step_fields": ["rag_profile", "instruction_keys"],
}


def make_call(data: EngineInput) -> Retrieval:
    """Run one validated retrieval step.

    This remains a pass-through placeholder until a concrete RAG provider is
    registered, but it obeys the worker's EngineInput/Result contract.
    """

    runtime = data.runtime
    if runtime.engine_kind != "rag":
        raise TypeError(
            f"{ENGINE} requires a rag runtime, got {runtime.engine_kind!r}"
        )
    if not runtime.rag_profile:
        raise ValueError(f"{ENGINE} runtime requires rag_profile")

    return Retrieval(
        identity=data.call.identity,
        ordinal=runtime.ordinal,
        content=data.content,
        raw_json={
            "engine": ENGINE,
            "rag_profile": runtime.rag_profile,
        },
    )


__all__ = ["ENGINE", "ENGINE_COMPONENT", "make_call"]

from __future__ import annotations

from pathlib import Path


def default_git_output(repo: str, source: dict) -> dict:
    identity = source.get("identity")
    path = source.get("path")
    if not isinstance(identity, str) or not identity:
        raise ValueError("source identity is required for Git output")
    if not isinstance(path, str) or not path:
        raise ValueError("source path is required for Git output")
    return {
        "adapter": "git",
        "repo": Path(repo).name,
        "identity": identity,
        "path_hint": path,
        "commit_message": f"AutoScribe response: {identity}",
    }


def build_call_parts(
    *,
    repo: str,
    commit: str,
    ref: str | None,
    plan: dict,
    source: dict,
    output: dict | None = None,
) -> tuple[dict, dict]:
    """Split one canonical call into model-facing content and routing baggage.

    Runtime/model content stays separate from destination metadata. The source identity
    is retained only in baggage because Responses needs a stable file identity after a
    path move; it is never exposed to the model-facing content record.
    """
    content = {
        "schema": "autoscribe.call-content.v1",
        "input": {
            "content": source["content"],
            "directive": source.get("directive"),
        },
        "plan": plan,
    }
    baggage = {
        "schema": "autoscribe.call-baggage.v2",
        "source": {
            "repo": repo,
            "commit": commit,
            "ref": ref,
            "path": source["path"],
            "blob": source["blob"],
        },
        "output": output or default_git_output(repo, source),
    }
    return content, baggage

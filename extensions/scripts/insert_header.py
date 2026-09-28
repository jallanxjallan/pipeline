from __future__ import annotations


SCRIPT_COMPONENT = {
    "label": "Insert Header",
    "callable": "transform",
}


_HEADER = "<<<local-transform:insert-header>>>"


def transform(content: str) -> str:
    """Simple local smoke-test transform that marks content as preprocessed."""
    text = content.strip()
    if text.startswith(_HEADER):
        return text + "\n"
    return f"{_HEADER}\n\n{text}\n"

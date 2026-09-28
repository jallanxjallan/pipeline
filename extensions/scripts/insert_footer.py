from __future__ import annotations


SCRIPT_COMPONENT = {
    "label": "Insert Footer",
    "callable": "transform",
}


_FOOTER = "<<<local-transform:insert-footer>>>"


def transform(content: str) -> str:
    """Simple local smoke-test transform that marks content as preprocessed."""
    text = content.strip()
    if text.endswith(_FOOTER):
        return text + "\n"
    return f"{text}\n\n{_FOOTER}\n"

from __future__ import annotations


SCRIPT_COMPONENT = {
    "label": "Inject Simulated RAG Guidelines",
    "callable": "transform",
}


_START = "<<<simulated-rag-guidelines>>>"
_END = "<<<end-simulated-rag-guidelines>>>"

_GUIDELINES = """Preserve the first-person reflective voice.
Keep Nyepi, pecalang, and jam karet visible.
Do not flatten the cultural tension into a generic work-life-balance lesson.
Do not moralize.
Mention that “emergency” is being redefined by the narrator.
Preserve the expatriate/local contrast without caricature.
End with an unresolved reflective question, not a neat conclusion.
Do not use bullet points in the final rewritten article."""


def transform(content: str) -> str:
    """Inject a fixed simulated-RAG guideline block for LLM chain testing.

    This intentionally behaves like a local transform step rather than a real
    retriever. It gives the following LLM step a deterministic retrieved-context
    block to parse and obey.
    """
    text = content.strip()

    if text.startswith(_START):
        return text + "\n"

    block = f"{_START}\n{_GUIDELINES}\n{_END}"
    return f"{block}\n\n{text}\n"

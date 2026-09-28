#!/usr/bin/env python3
"""
deconstruct_topics_fire.py

Deconstruct Obsidian topic notes into append-safe finding notes.

Principles:
- Obsidian frontmatter is the database.
- Pandoc parses source frontmatter and Markdown into an AST.
- H1 blocks are the atomic split boundary.
- Pandoc applies the finding-note template.
- Pipeline writeback appends drafts; it never overwrites original notes.

Requires:
    pip install fire pyyaml
    pandoc on PATH

Typical use:
    python deconstruct_topics_fire.py deconstruct Research/ ImportedFindings/

Dry run:
    python deconstruct_topics_fire.py deconstruct Research/ ImportedFindings/ --dry_run=True

With a custom template:
    python deconstruct_topics_fire.py deconstruct Research/ ImportedFindings/ --template=fnd_template.pandoc.md
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import yaml

try:
    import fire
except ModuleNotFoundError as exc:  # fail loud, but with the one useful instruction
    raise SystemExit("fatal: Fire is not installed. Install with: pip install fire") from exc


FINDING_PREFIX = "fnd__"
TOPIC_PREFIX = "tpc__"
DEFAULT_FROM_FORMAT = (
    "markdown"
    "+yaml_metadata_block"
    "+wikilinks_title_after_pipe"
    "+bracketed_spans"
    "+fenced_divs"
)
DEFAULT_TO_FORMAT = DEFAULT_FROM_FORMAT

DEFAULT_TEMPLATE = r'''---
id: "$id$"
kind: finding
topic: "$topic$"
status: "$status$"
verification_status: "$verification_status$"
source_status: "$source_status$"
source_topic_file: "$source_topic_file$"
source_heading: "$source_heading$"
source_index: $source_index$
input_sha256: "$input_sha256$"
last_pipeline_run: "$last_pipeline_run$"
updated: "$updated$"
tags:
$for(tags)$
  - "$tags$"
$endfor$
---

# $title$

## Original note

$body$

## Pipeline drafts

<!-- Append generated drafts here. Do not overwrite Original note. -->

## Accepted version

<!-- Human-owned final/current version for topic transclusion. -->
'''


@dataclass(frozen=True)
class ParsedDocument:
    api_version: list[int]
    meta: dict[str, Any]
    blocks: list[dict[str, Any]]


@dataclass(frozen=True)
class Section:
    index: int
    title: str
    blocks: list[dict[str, Any]]


@dataclass(frozen=True)
class RenderedFinding:
    path: Path
    source: Path
    title: str
    note: str
    status: str


def fail(message: str) -> None:
    raise SystemExit(f"fatal: {message}")


def require_pandoc() -> None:
    if shutil.which("pandoc") is None:
        fail("pandoc is not available on PATH")


def run_pandoc(args: list[str], *, stdin: str) -> str:
    proc = subprocess.run(
        ["pandoc", *args],
        input=stdin,
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        fail("pandoc failed:\n" + proc.stderr.strip())
    return proc.stdout


def pandoc_json(markdown: str, *, from_format: str) -> dict[str, Any]:
    return json.loads(run_pandoc(["--from", from_format, "--to", "json"], stdin=markdown))


def parse_markdown(path: Path, *, from_format: str) -> ParsedDocument:
    raw = path.read_text(encoding="utf-8")
    ast = pandoc_json(raw, from_format=from_format)
    return ParsedDocument(
        api_version=ast.get("pandoc-api-version", [1, 23]),
        meta=ast.get("meta", {}),
        blocks=ast.get("blocks", []),
    )


def ast_to_markdown(
    *,
    blocks: list[dict[str, Any]],
    api_version: list[int],
    to_format: str,
    wrap: str,
) -> str:
    doc = {
        "pandoc-api-version": api_version,
        "meta": {},
        "blocks": blocks,
    }
    return run_pandoc(
        ["--from", "json", "--to", to_format, "--wrap", wrap],
        stdin=json.dumps(doc),
    ).strip()


def render_template(
    *,
    body_markdown: str,
    metadata: dict[str, Any],
    template_path: Path,
    from_format: str,
    to_format: str,
    wrap: str,
) -> str:
    with tempfile.TemporaryDirectory() as td:
        meta_path = Path(td) / "metadata.yml"
        meta_path.write_text(
            yaml.safe_dump(metadata, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        rendered = run_pandoc(
            [
                "--from", from_format,
                "--to", to_format,
                "--standalone",
                "--template", str(template_path),
                "--metadata-file", str(meta_path),
                "--wrap", wrap,
            ],
            stdin=body_markdown,
        )
    return rendered.strip() + "\n"


def meta_scalar(meta_value: Any) -> str:
    """Small Pandoc Meta -> string converter for source frontmatter values."""
    if not isinstance(meta_value, dict):
        return ""
    tag = meta_value.get("t")
    value = meta_value.get("c")
    if tag == "MetaString":
        return str(value)
    if tag == "MetaBool":
        return "true" if value else "false"
    if tag == "MetaInlines":
        return stringify_inlines(value)
    if tag == "MetaBlocks":
        return stringify_blocks(value)
    if tag == "MetaList":
        return ", ".join(meta_scalar(item) for item in value)
    return ""


def source_frontmatter(parsed: ParsedDocument) -> dict[str, str]:
    return {key: meta_scalar(value) for key, value in parsed.meta.items()}


def stringify_blocks(blocks: list[dict[str, Any]]) -> str:
    return " ".join(
        stringify_inlines(block.get("c", []))
        for block in blocks
        if block.get("t") in {"Plain", "Para"}
    ).strip()


def stringify_inlines(inlines: Any) -> str:
    parts: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, list):
            for item in node:
                walk(item)
            return
        if not isinstance(node, dict):
            return

        tag = node.get("t")
        value = node.get("c")

        if tag in {"Str", "Code", "Math"}:
            if isinstance(value, list):
                parts.append(str(value[-1]))
            else:
                parts.append(str(value))
        elif tag in {"Space", "SoftBreak", "LineBreak"}:
            parts.append(" ")
        elif tag in {"Emph", "Strong", "Strikeout", "SmallCaps", "Quoted", "Span", "Link"}:
            walk(value)
        elif tag == "Cite" and isinstance(value, list) and len(value) > 1:
            walk(value[1])
        else:
            walk(value)

    walk(inlines)
    return re.sub(r"\s+", " ", "".join(parts)).strip()


def slugify(text: str, *, fallback: str = "untitled") -> str:
    normalized = unicodedata.normalize("NFKD", text)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    ascii_text = ascii_text.lower()
    ascii_text = re.sub(r"['’]", "", ascii_text)
    ascii_text = re.sub(r"[^a-z0-9]+", "-", ascii_text).strip("-")
    return ascii_text or fallback


def strip_known_prefix(stem: str) -> str:
    for prefix in (FINDING_PREFIX, TOPIC_PREFIX):
        if stem.startswith(prefix):
            return stem[len(prefix):]
    return stem


def first_words_from_blocks(blocks: list[dict[str, Any]], *, limit: int = 8) -> str:
    words: list[str] = []
    for block in blocks:
        if block.get("t") == "Header":
            words.extend(stringify_inlines(block["c"][2]).split())
        elif block.get("t") in {"Plain", "Para"}:
            words.extend(stringify_inlines(block.get("c", [])).split())
        if len(words) >= limit:
            break
    return " ".join(words[:limit])


def is_substantive(blocks: list[dict[str, Any]]) -> bool:
    return any(block.get("t") != "HorizontalRule" for block in blocks)


def demote_headings(blocks: list[dict[str, Any]], *, by: int = 1) -> list[dict[str, Any]]:
    demoted: list[dict[str, Any]] = []
    for block in blocks:
        if block.get("t") != "Header":
            demoted.append(block)
            continue
        level, attrs, inlines = block["c"]
        demoted.append({"t": "Header", "c": [level + by, attrs, inlines]})
    return demoted


def split_at_h1(blocks: list[dict[str, Any]], *, include_preamble: bool) -> list[Section]:
    sections: list[Section] = []
    current_title = ""
    current_blocks: list[dict[str, Any]] = []
    seen_h1 = False

    def flush() -> None:
        nonlocal current_title, current_blocks
        if not is_substantive(current_blocks):
            current_title = ""
            current_blocks = []
            return
        if current_title or include_preamble:
            title = current_title or "Preamble"
            sections.append(Section(len(sections) + 1, title, current_blocks))
        current_title = ""
        current_blocks = []

    for block in blocks:
        if block.get("t") == "Header" and block["c"][0] == 1:
            flush()
            seen_h1 = True
            current_title = stringify_inlines(block["c"][2]) or "Untitled"
            current_blocks = []
            continue
        current_blocks.append(block)

    flush()

    if not sections and is_substantive(blocks):
        # Fail useful: h1 is the contract, but a one-note fallback avoids empty runs
        # when testing older topic files.
        title = first_words_from_blocks(blocks) or "Untitled"
        sections.append(Section(1, title, blocks))

    return sections


def markdown_files(input_path: Path, *, recursive: bool) -> Iterable[Path]:
    if input_path.is_file():
        if input_path.suffix.lower() == ".md":
            yield input_path
        return

    pattern = "**/*.md" if recursive else "*.md"
    for path in sorted(input_path.glob(pattern)):
        if path.name.startswith("."):
            continue
        if any(part.startswith("_") for part in path.relative_to(input_path).parts[:-1]):
            continue
        if path.suffix.lower() == ".md":
            yield path


def write_default_template(path: Path, *, overwrite: bool = False) -> Path:
    target = path.expanduser().resolve()
    if target.exists() and not overwrite:
        fail(f"template already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(DEFAULT_TEMPLATE, encoding="utf-8")
    return target


def ensure_template(template: str | Path | None) -> tuple[Path, tempfile.TemporaryDirectory[str] | None]:
    if template:
        path = Path(template).expanduser().resolve()
        if not path.exists():
            fail(f"template not found: {path}")
        return path, None

    td = tempfile.TemporaryDirectory()
    path = Path(td.name) / "fnd_template.pandoc.md"
    path.write_text(DEFAULT_TEMPLATE, encoding="utf-8")
    return path, td


def output_metadata(
    *,
    finding_id: str,
    topic_id: str,
    source_path: Path,
    source_heading: str,
    source_index: int,
    content_sha256: str,
    title: str,
    today: str,
    source_meta: dict[str, str],
) -> dict[str, Any]:
    # Keep the durable Obsidian record in frontmatter. Pull topic from source
    # frontmatter if present; otherwise infer from filename.
    topic = source_meta.get("topic") or topic_id
    return {
        "id": finding_id,
        "title": title,
        "topic": topic,
        "status": "imported",
        "verification_status": source_meta.get("verification_status") or "pending",
        "source_status": source_meta.get("source_status") or "unchecked",
        "source_topic_file": str(source_path),
        "source_heading": source_heading,
        "source_index": source_index,
        "input_sha256": content_sha256,
        "last_pipeline_run": "",
        "updated": today,
        "tags": ["finding", "imported_topic"],
    }


def index_markdown(rows: list[dict[str, str]]) -> str:
    lines = [
        "# Deconstructed Topic Findings",
        "",
        "| Topic | Finding | Source heading | Status |",
        "|---|---|---|---|",
    ]
    for row in rows:
        lines.append(
            f"| [[{row['topic']}]] | [[{row['finding']}]] | {row['heading']} | {row['status']} |"
        )
    lines.append("")
    return "\n".join(lines)


class TopicDeconstructor:
    """Fire CLI for deconstructing topic notes into finding notes."""

    def template(self, path: str = "fnd_template.pandoc.md", overwrite: bool = False) -> str:
        """Write the default Pandoc finding-note template."""
        written = write_default_template(Path(path), overwrite=overwrite)
        return f"wrote {written}"

    def deconstruct(
        self,
        input_path: str,
        output_path: str,
        recursive: bool = False,
        template: str | None = None,
        dry_run: bool = False,
        overwrite: bool = False,
        include_preamble: bool = False,
        write_index: bool = True,
        from_format: str = DEFAULT_FROM_FORMAT,
        to_format: str = DEFAULT_TO_FORMAT,
        wrap: str = "none",
    ) -> str:
        """Split topic files at H1 and write append-safe finding notes."""
        require_pandoc()

        src = Path(input_path).expanduser().resolve()
        out = Path(output_path).expanduser().resolve()
        if not src.exists():
            fail(f"input path does not exist: {src}")
        if wrap not in {"auto", "none", "preserve"}:
            fail("wrap must be one of: auto, none, preserve")

        template_path, template_tmp = ensure_template(template)
        today = dt.date.today().isoformat()
        rows: list[dict[str, str]] = []
        written_count = 0
        skipped_count = 0
        planned_count = 0

        if not dry_run:
            out.mkdir(parents=True, exist_ok=True)

        files = list(markdown_files(src, recursive=recursive))
        if not files:
            fail("no markdown files found")

        try:
            for source_path in files:
                parsed = parse_markdown(source_path, from_format=from_format)
                source_meta = source_frontmatter(parsed)

                topic_slug = slugify(strip_known_prefix(source_path.stem))
                inferred_topic_id = f"{TOPIC_PREFIX}{topic_slug}"
                topic_id = source_meta.get("id") or source_meta.get("topic") or inferred_topic_id

                sections = split_at_h1(parsed.blocks, include_preamble=include_preamble)
                print(f"{source_path.name}: {len(sections)} finding(s)")

                seen: dict[str, int] = {}
                for section in sections:
                    title_slug = slugify(section.title, fallback=f"finding-{section.index:03d}")
                    seen[title_slug] = seen.get(title_slug, 0) + 1
                    if seen[title_slug] > 1:
                        title_slug = f"{title_slug}-{seen[title_slug]}"

                    finding_slug = f"{topic_slug}__{section.index:03d}_{title_slug}"
                    filename = f"{FINDING_PREFIX}{finding_slug}.md"
                    out_file = out / filename
                    finding_id = out_file.stem

                    body_blocks = demote_headings(section.blocks, by=1)
                    body = ast_to_markdown(
                        blocks=body_blocks,
                        api_version=parsed.api_version,
                        to_format=to_format,
                        wrap=wrap,
                    )
                    content_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
                    metadata = output_metadata(
                        finding_id=finding_id,
                        topic_id=topic_id,
                        source_path=source_path,
                        source_heading=section.title,
                        source_index=section.index,
                        content_sha256=content_hash,
                        title=section.title,
                        today=today,
                        source_meta=source_meta,
                    )
                    note = render_template(
                        body_markdown=body,
                        metadata=metadata,
                        template_path=template_path,
                        from_format=from_format,
                        to_format=to_format,
                        wrap=wrap,
                    )

                    status = "planned" if dry_run else "written"
                    if dry_run:
                        print(f"  would write {out_file.name}")
                        planned_count += 1
                    elif out_file.exists() and not overwrite:
                        print(f"  skip existing {out_file.name}")
                        status = "skipped"
                        skipped_count += 1
                    else:
                        out_file.write_text(note, encoding="utf-8")
                        print(f"  wrote {out_file.name}")
                        written_count += 1

                    rows.append(
                        {
                            "topic": topic_id,
                            "finding": finding_id,
                            "heading": section.title.replace("|", "\\|") or "—",
                            "status": status,
                        }
                    )

            if write_index and not dry_run:
                index_path = out / "_deconstruction_index.md"
                index_path.write_text(index_markdown(rows), encoding="utf-8")
                print(f"wrote {index_path.name}")
        finally:
            if template_tmp is not None:
                template_tmp.cleanup()

        return (
            f"done: written={written_count}, skipped={skipped_count}, "
            f"planned={planned_count}, sources={len(files)}"
        )


if __name__ == "__main__":
    fire.Fire(TopicDeconstructor)

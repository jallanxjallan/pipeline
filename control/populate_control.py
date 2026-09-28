#!/usr/bin/env python3
"""Seed the new AutoScribe relational Control database from retained legacy Control.

Only the dependency closure of the three retained plans is inserted:
- 3 plans
- 3 steps
- 5 instructions (shared role + shared context + 3 tasks)

Unreferenced legacy instructions are deliberately omitted.

Default database: ~/Data/control.sql

The legacy LLM step fields are carried literally into the new generic step fields:
    legacy engine -> steps.executor
    legacy model  -> steps.entrypoint
No other legacy capability/registry data is migrated.
"""

from __future__ import annotations

import argparse
import sqlite3
import uuid
from pathlib import Path

NAMESPACE = uuid.UUID("6f8d9cf0-b3c5-4d42-9c48-6ed0a2b87d55")


def stable_id(kind: str, ref: str) -> str:
    return str(uuid.uuid5(NAMESPACE, f"autoscribe-control:{kind}:{ref}"))

INSTRUCTIONS = [
    ('rol_SYMR6P5K1VNX68EZ', 'rol.redrafting-editor-role.5fpy6y', 'Redrafting Editor Role', 'role', '# Redrafting Editor Role\n\nYou are a substantive editor responsible for turning incomplete, disorderly, or heavily annotated prose into a coherent working draft.\n\nWork at the level of content, structure, sequence, emphasis, and continuity. Treat the supplied material as evidence to be organized rather than wording to be preserved mechanically.\n\nDo not perform a cleanup pass. Do not spend effort on house style, spelling normalization, punctuation consistency, typographic consistency, or mechanical proofreading. Correct wording only where necessary to make the redraft intelligible or to prevent a substantive error.\n\nDo not invent facts, examples, quotations, motives, causal relationships, or conclusions. Where the material does not support a responsible resolution, retain a concise bracketed editorial query.\n'),
    ('ctx_6FR6QK42XASKBYJW', 'ctx.hhp-client-case-review.8r2m6q', 'HHP Client Case Review', 'context', "# HHP Client Case Review\n\nThis is a client-review drafting round for a long-form history of HHP Law Firm. The immediate purpose is not to produce finished manuscript prose. It is to place a compact, readable account of each candidate matter into the manuscript so the client can decide whether the matter belongs in the book, correct factual or interpretive errors, identify HHP's actual role and the lawyers involved, and add recollections or anecdotes that are not available in public-source notes.\n\nTreat every supplied case file as provisional source material. Existing prose, research notes, source summaries, annotations, directives, and editorial queries may all contain errors. Do not treat confident wording as evidence merely because it is already drafted.\n\n## What the briefing must establish\n\nWhere the supplied material supports it, give the client enough information to recognize:\n\n- what transaction, dispute, restructuring, financing, regulatory matter, institutional event, or public-interest legal intervention is being described;\n- when it occurred and who the principal parties were;\n- what HHP is documented or said to have done;\n- why the matter may be worth including at this point in the firm's history.\n\nThe HHP role is the central editorial question. Distinguish transaction or controversy background from evidence of HHP's involvement. If the supplied material does not establish HHP's role, do not infer one from the file name, its inclusion in the manuscript, a client relationship, or general knowledge. Draft the supported background and ask the client to supply or confirm the firm's role.\n\nA public-interest position paper, commentary, or other pro bono legal intervention is a valid HHP role in its own right. Do not recast such work as representation of a party merely to make it resemble a conventional case matter.\n\n## Level of detail\n\nUse only the detail needed to let the client identify and assess the matter. Prefer concrete facts over exhaustive transaction mechanics, deal values, percentages, subsidiary names, ratings, dates, or regulatory detail unless a detail is central to understanding why the matter mattered.\n\nDo not research beyond the supplied material in this pass. Do not repair gaps from general knowledge. Preserve material uncertainty as a client-review question.\n\nDo not turn public-source praise, law-firm marketing language, retrospective significance claims, allegations, or controversy into unattributed fact. Attribute or qualify such material where it is necessary to the briefing; otherwise omit it.\n\n## Manuscript placement\n\nWrite each passage to make sense in its present manuscript neighborhood, but do not force the evidence to support the chapter's apparent thesis. If the supplied case does not fit its current section, flag the placement rather than silently changing the facts or inventing a thematic connection.\n\nCurrent case placements supplied for this review include:\n\n- **Looking Outward / Long Distance IPO:** Indosat; Telkom.\n- **Asian Financial Crisis / Saving the Banks:** Merging into Mandiri.\n- **Post-Crisis Restructuring / Bank Restructures:** Bank Mandiri Private Placement; Bank Mandiri IPO; Bank Permata; BCA IPO.\n- **Post-Crisis Restructuring / Debt Workouts:** Chandra Asri; Pelindo; Tuban Petrochemical; Sierad Produce; Phillip Morris.\n- **Post-Crisis Restructuring / Sidebar:** JSX-SSX Merger.\n- **Change of Perspective:** Rising Sun Buys Matahari.\n- **Change of Perspective / Saving the Big Boys:** Berlian Laju; Tri Polyta-Trypolita.\n- **Change of Perspective / Major League Law:** Toll Roads; IndoFood; Aerowisata; Medco IPO.\n- **An Indonesian Firm / Delicate Matters:** Freeport Divestment; Cargill.\n- **Digital Revolution / Wiring a Nation:** Indihome Spinoff; Indosiar Restructuring.\n- **New Legal Frontiers / New Horizons:** BNP Green Bond; Merging The Muslims; Green Sukuk.\n- **New Legal Frontiers / Pro Bono Outreach:** ADB Water Controversy as a standalone boxout about HHP's position paper concerning the case.\n\nThe supplied Phillip Morris note describes a foreign strategic acquisition of HM Sampoerna rather than a debt workout. Treat its current placement under **Debt Workouts** as a client/editorial question rather than rationalizing the mismatch.\n\n## Client-review marker\n\nAfter the briefing paragraphs, add one compact line in this form:\n\n`[Client review: ...]`\n\nUse it to ask only the questions that materially affect inclusion or accuracy. Prioritize, as applicable: retain or omit; confirm HHP's role; identify lead lawyers or authors; correct a disputed or uncertain fact; explain why the matter mattered to the firm; add a specific anecdote or recollection. Do not ask generic questions whose answers are already supported by the supplied material.\n"),
    ('tsk_MV3N3P6W6CCBSEQ1', 'tsk.prepare-client-case-briefing-from-notes.6p4v1n', 'Prepare Client Case Briefing from Notes', 'task', "# Prepare a Client Case Briefing from Notes\n\n## Objective\n\nTurn raw research notes, fact summaries, source extracts, bullet points, and other provisional material about one case or transaction into short sample manuscript text for client review.\n\nThe passage must help the client decide whether the matter belongs in the book, recognize and correct the factual account, confirm HHP's actual involvement, and add firsthand detail.\n\n## Method\n\nRead all supplied material before drafting. Identify the smallest supported narrative sufficient to explain the matter: the event or transaction, its immediate context, the principal parties, HHP's documented role if any, the outcome if material, and the reason the matter may belong at the supplied manuscript location.\n\nBuild fresh prose from the evidence rather than reproducing the order or wording of the notes. Remove research-management language, source URLs, citation clutter, headings such as “why it mattered,” and lists that exist only to organize research.\n\nBe conservative about significance. Prefer a plain description of what made the matter unusual or representative over claims that it was landmark, pioneering, decisive, transformative, or a reference point unless the supplied material directly supports that characterization.\n\nDo not infer HHP's role. Where transaction facts are reasonably clear but the firm's involvement is missing or uncertain, draft only the supported transaction background and make HHP's role an explicit client-review question.\n\nIf the material contains allegations, controversy, criticism, or a disputed interpretation, do not flatten it into narrative fact. Attribute it where it is necessary to understanding the matter, or ask the client whether it belongs.\n\nUse approximately one to three compact paragraphs. This is briefing prose, not final literary prose: readable, concrete, and neutral, with enough context for recognition but without decorative scene-setting or rhetorical flourish.\n\n## Output\n\nReturn only:\n\n1. the complete briefing passage; and\n2. one final `[Client review: ...]` line containing only material questions raised by the supplied evidence and the project context.\n\nDo not add a title, research summary, explanation of method, or source list.\n"),
    ('tsk_SBM7YWDZ6V0TZGS6', 'tsk.recast-existing-case-draft-for-client-review.2c7w5k', 'Recast Existing Case Draft for Client Review', 'task', "# Recast an Existing Case Draft for Client Review\n\n## Objective\n\nTurn an existing rough, revised, or apparently finished case passage into short sample manuscript text whose purpose is client verification rather than publication.\n\nThe existing passage is provisional source material. Preserve supported substance, not its claims to completeness or its rhetorical confidence.\n\n## Method\n\nRead the entire passage, including annotations, editorial queries, directives, and linked-note summaries that are present in the supplied material.\n\nRebuild the passage around the minimum facts the client needs to recognize and assess the matter. Retain supported chronology, parties, transaction structure, HHP involvement, and outcome where they matter. Compress or remove background that overwhelms the case itself.\n\nDowngrade unsupported interpretation. Remove or query claims about motives, causal significance, investor reaction, trust, prestige, national importance, institutional impact, or the firm's special contribution when the supplied material does not establish them. Do not preserve a claim merely because the passage is marked revised or finished.\n\nTreat existing editorial queries as unresolved unless the supplied material itself answers them. Convert material unresolved points into concise client-review questions rather than trying to adjudicate them from general knowledge.\n\nDo not perform new research. Do not use outside knowledge to correct the draft. Do not polish toward final-book rhetoric; the goal is a clear and economical briefing that invites useful client intervention.\n\nUse approximately one to three compact paragraphs. Preserve a natural manuscript voice, but favor factual clarity over narrative flourish.\n\n## Output\n\nReturn only:\n\n1. the complete recast briefing passage; and\n2. one final `[Client review: ...]` line containing only material questions raised by the supplied evidence and the project context.\n\nDo not add a title, change log, research summary, explanation of method, or source list.\n"),
    ('tsk_CVVNKBJK4ZRWK11P', 'tsk.prepare-pro-bono-position-paper-boxout.4h8n3d', 'Prepare Pro Bono Position Paper Boxout for Client Review', 'task', "# Prepare a Pro Bono Position-Paper Boxout for Client Review\n\n## Objective\n\nTurn provisional notes about a dispute or public controversy on which HHP produced a position paper, legal commentary, or comparable pro bono intervention into a short standalone manuscript boxout for client review.\n\nThe purpose is to let the client verify what prompted HHP to intervene, what legal position the firm took, who was involved in preparing or presenting that position, and whether the episode belongs in the book's account of pro bono work.\n\n## Method\n\nStart with the controversy only to the extent needed to make HHP's intervention intelligible. Identify the decision, legal issue, institutional action, or public consequence that prompted the position paper, using neutral language and preserving any uncertainty or dispute in the supplied material.\n\nThen make HHP's intervention the center of the boxout. Explain what the supplied sources establish about the position paper or commentary: what question it addressed, what position or concern it articulated, and what form the intervention took.\n\nDo not imply that HHP represented ADB, a water operator, a government body, a litigant, or any other party unless the supplied material expressly establishes that representation. A position paper or public-interest legal intervention is itself the relevant HHP activity.\n\nDo not turn the boxout into a general history of the underlying dispute. Do not research beyond the supplied material. Do not resolve contested factual or legal claims from outside knowledge.\n\nUse approximately one to three compact paragraphs suitable for a standalone boxout. Keep the tone factual and readable rather than argumentative. This is sample content for verification, not final advocacy or final manuscript prose.\n\n## Output\n\nReturn only:\n\n1. the complete boxout passage; and\n2. one final `[Client review: ...]` line asking only material questions, especially confirmation of the circumstances of the position paper, its authors or participating HHP lawyers, the firm's intended purpose, any response it received, and any anecdote that would make the intervention memorable.\n\nDo not add a title, source list, legal analysis section, or explanation of method.\n"),
]

PLANS = [
    {'ref': 'plan.hhp-client-case-briefing-from-notes.6p4v1n', 'label': 'HHP Client Case Briefing from Notes', 'description': 'Turn raw HHP case research and notes into compact sample manuscript briefing paragraphs for client inclusion, correction, role confirmation, and anecdotal supplementation.', 'step_label': 'Prepare client case briefing', 'executor': 'chatgpt', 'entrypoint': 'sol', 'instruction_ids': ['rol_SYMR6P5K1VNX68EZ', 'ctx_6FR6QK42XASKBYJW', 'tsk_MV3N3P6W6CCBSEQ1']},
    {'ref': 'plan.hhp-recast-case-draft-for-client-review.2c7w5k', 'label': 'HHP Recast Case Draft for Client Review', 'description': 'Recast an existing HHP case narrative as neutral briefing-quality sample manuscript prose for client verification, inclusion decisions, and anecdotal supplementation.', 'step_label': 'Recast existing case draft', 'executor': 'chatgpt', 'entrypoint': 'sol', 'instruction_ids': ['rol_SYMR6P5K1VNX68EZ', 'ctx_6FR6QK42XASKBYJW', 'tsk_SBM7YWDZ6V0TZGS6']},
    {'ref': 'plan.hhp-pro-bono-position-paper-boxout.4h8n3d', 'label': 'HHP Pro Bono Position Paper Boxout', 'description': 'Prepare a standalone client-review boxout about an HHP pro bono position paper or comparable public-interest legal intervention without implying representation of a party.', 'step_label': 'Prepare pro bono position paper boxout', 'executor': 'chatgpt', 'entrypoint': 'sol', 'instruction_ids': ['rol_SYMR6P5K1VNX68EZ', 'ctx_6FR6QK42XASKBYJW', 'tsk_CVVNKBJK4ZRWK11P']},
]


EXPECTED_COLUMNS = {
    "plans": {"id", "ref", "label", "description"},
    "steps": {"id", "ref", "label", "executor", "entrypoint"},
    "instructions": {"id", "ref", "label", "kind", "body"},
    "plan_steps": {"plan_id", "step_id", "position"},
    "step_instructions": {"step_id", "instruction_id", "position"},
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Populate the new AutoScribe Control database from the retained plan dependency graph"
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=Path.home() / "Data" / "control.sql",
        help="Control SQLite database (default: ~/Data/control.sql)",
    )
    return parser.parse_args()


def require_schema(conn: sqlite3.Connection) -> None:
    for table, expected in EXPECTED_COLUMNS.items():
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        if not rows:
            raise RuntimeError(f"missing required table: {table}")
        actual = {row[1] for row in rows}
        missing = expected - actual
        if missing:
            raise RuntimeError(
                f"table {table} is missing required columns: {', '.join(sorted(missing))}"
            )


def insert_exact_ref(
    conn: sqlite3.Connection,
    table: str,
    row_id: str,
    ref: str,
    fields: dict[str, object],
) -> None:
    existing = conn.execute(
        f"SELECT id FROM {table} WHERE ref = ?", (ref,)
    ).fetchone()
    if existing is not None and existing[0] != row_id:
        raise RuntimeError(
            f"{table} ref already belongs to a different id: {ref} -> {existing[0]}"
        )

    columns = ["id", "ref", *fields.keys()]
    values = [row_id, ref, *fields.values()]
    placeholders = ", ".join("?" for _ in columns)
    assignments = ", ".join(f"{name}=excluded.{name}" for name in ["ref", *fields.keys()])

    conn.execute(
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders}) "
        f"ON CONFLICT(id) DO UPDATE SET {assignments}",
        values,
    )


def seed(conn: sqlite3.Connection) -> None:
    instruction_ids = {row[0] for row in INSTRUCTIONS}

    for instruction_id, ref, label, kind, body in INSTRUCTIONS:
        insert_exact_ref(
            conn,
            "instructions",
            instruction_id,
            ref,
            {"label": label, "kind": kind, "body": body},
        )

    for plan in PLANS:
        plan_ref = str(plan["ref"])
        plan_id = stable_id("plan", plan_ref)
        step_ref = f"{plan_ref}#1"
        step_id = stable_id("step", step_ref)

        refs = list(plan["instruction_ids"])
        unknown = set(refs) - instruction_ids
        if unknown:
            raise RuntimeError(
                f"plan {plan_ref} references instructions absent from seed data: {sorted(unknown)}"
            )

        insert_exact_ref(
            conn,
            "plans",
            plan_id,
            plan_ref,
            {
                "label": plan["label"],
                "description": plan["description"],
            },
        )
        insert_exact_ref(
            conn,
            "steps",
            step_id,
            step_ref,
            {
                "label": plan["step_label"],
                "executor": plan["executor"],
                "entrypoint": plan["entrypoint"],
            },
        )

        # Rebuild only relationships owned by this seeded plan/step.
        conn.execute("DELETE FROM plan_steps WHERE plan_id = ?", (plan_id,))
        conn.execute("DELETE FROM step_instructions WHERE step_id = ?", (step_id,))

        conn.execute(
            "INSERT INTO plan_steps(plan_id, step_id, position) VALUES (?, ?, 1)",
            (plan_id, step_id),
        )
        for position, instruction_id in enumerate(refs, start=1):
            conn.execute(
                "INSERT INTO step_instructions(step_id, instruction_id, position) "
                "VALUES (?, ?, ?)",
                (step_id, instruction_id, position),
            )


def report(conn: sqlite3.Connection) -> None:
    counts = {}
    for table in ("plans", "steps", "instructions", "plan_steps", "step_instructions"):
        counts[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    print("Control population complete")
    for table, count in counts.items():
        print(f"  {table}: {count}")

    print("\nSeeded plans:")
    for ref, label in conn.execute("SELECT ref, label FROM plans ORDER BY ref"):
        print(f"  {ref}  [{label}]")


def main() -> None:
    args = parse_args()
    db_path = args.db.expanduser().resolve()
    if not db_path.exists():
        raise SystemExit(
            f"database does not exist: {db_path}\n"
            "Initialize it from server/control/schema.sql first."
        )

    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        require_schema(conn)
        with conn:
            seed(conn)
        errors = conn.execute("PRAGMA foreign_key_check").fetchall()
        if errors:
            raise RuntimeError(f"foreign key check failed: {errors}")
        report(conn)
    finally:
        conn.close()


if __name__ == "__main__":
    main()

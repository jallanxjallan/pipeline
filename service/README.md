# AutoScribe Service

Rust reads and writes vault Markdown directly. Format conversion and publishing are
external to the AutoScribe service.

Normal document flow:

**vault Markdown → Rust dispatch → server → model pipeline → Rust writeback → vault Markdown**

## Dispatch

```sh
dispatch
dispatch --repo /absolute/path/to/repository --commit <full-sha> --plan <plan-identity>
```

Dispatch is independent of `$PWD`. Selection proceeds through repository, unqueued
commit, plan type, and plan; see [Dispatch selector](#dispatch-selector-server) below.
With no commit argument, embedded skim displays the 100 most recent eligible
commits reachable from the selected repository's current branch, ordered by committer
timestamp. Each row displays short SHA, ISO timestamp, and unmodified subject;
the selector returns the full SHA. Subjects have no processing semantics.
Explicit options skip their selectors. Esc/Ctrl-C cancels without enqueue or
persistent mutation; terminal errors fail explicitly. Both forms use `run_commit`.

Dispatch has no `--vault` option and performs no vault initialization or Obsidian
checks. `writeback` requires the exact `$PWD` repository root; `inflight` retains
its `--vault` option.
The existing NDJSON source kind `vault` remains a protocol label only.

A selected commit dispatches added/modified Markdown files from its first-parent
delta (the complete Markdown tree for a root commit). Deleted files are omitted;
renames are treated as delete/add. Optional source arguments narrow that set.
Every selected file must have a unique, non-empty frontmatter slug and nonblank
body. Source content comes from Git blobs, never the working tree. No status field,
slug prefix, filename convention, or subject convention selects processing.

Commit/source state comes from `asc storage dispatch-state --repository <root>`.
It reads `~/Data/ledger.sql` without creating a database; live queued calls cover
the period before scrivener persists them. Calls without terminal results are in
flight. Successful calls requiring delivery keep their commits inflight and sources
busy until receipted. Successful `none` calls and terminal failures release that state. State is re-read before
enqueue, but this is not an atomic reservation across concurrent dispatch clients.
The existing server enqueue boundary and NDJSON field layout are unchanged.
Repository, full commit SHA and blob ID travel in existing `extra.context`.
`inflight` reports this state as JSON. New dispatches create no Git state refs.

Markdown body bytes are preserved, except for an optional leading directive block:

```markdown
::: directive
Correct spelling while preserving the author's voice.
:::

The body starts here.
```

`::: {.directive}` is also accepted. Only this leading standalone block is extracted;
blank or unterminated directives and blank dispatched bodies fail. The extracted text
is sent as the optional top-level `directive`. General fenced-div parsing and asset
extraction are outside this service. Unterminated leading frontmatter fails safely.
The validated batch is serialized once at the `asc enqueue` NDJSON boundary.

The default response action is `writeback`. `--response-action` accepts `writeback`,
`writenew`, `export`, or `none`; `export` is retained as a serialized server-routing
compatibility value, not a local document command.

## Write responses

```sh
writeback
```

Run manually from the repository root. The command refuses a nested `$PWD`, so it
can only select and write files in the repository rooted at that exact current
directory. It selects inflight commits through `asc storage dispatch-state`, then retrieves complete
successful, unreceipted responses through `asc export list-pending --repository
<root> --source-commit <sha> ... --ndjson`. Retrieval records no receipts.

Each response must match its stored source commit, blob and slug. Writeback
preserves raw source frontmatter and replaces only the body. Mismatching response
slugs, unsafe paths, symlinks and files differing from both source and intended
response are blocked; independent valid responses still proceed. Duplicate target
paths are blocked. The index must contain no staged changes before materialization;
unrelated unstaged changes remain untouched.

Only accepted response paths are staged and committed, with subject
`autoscribe: write responses` and one `Autoscribe-Call:` trailer per response.
Export receipts follow commit verification and include repository, source SHA,
writeback SHA and target path. An identical response may produce an empty content
commit so its acknowledgement is durable. Legacy manifest writeback is removed.

If a receipt fails, retrying finds an exact call trailer in reachable Git history,
verifies the expected target bytes and records the missing receipt against that
commit. It does not overwrite later local edits or create a duplicate response
commit. Receipt failures are identified in JSON and produce a nonzero CLI exit.
The report includes `inflight_commits`, `available_results`, `written`,
`already_committed`, `blocked`, `writeback_commit`, `receipted` and
`receipt_failures`. Empty selections are successful no-ops. `written` identifies
accepted materializations, including files already equal to the target;
`writeback_commit` is null when no new commit was needed.

Do not run concurrent write-response invocations or edit target files during the
operation. A failure before commit leaves files/index available for inspection;
no receipts are issued for those rows. Resolve staged changes before retrying.

## Writenew

```sh
writenew [--target-dir /absolute/path/to/folder] < records.ndjson
```

The target directory must already exist; it defaults to `_ingest` in the current
directory. Records require non-empty `content` and `input_record.slug` strings.
Optional `input_record.filename_hint` supplies the basename; otherwise the second
slug segment becomes a title (`pss.my-document` becomes `My Document.md`).

Rust writes a deterministic YAML envelope from scalar `input_record` fields in
sorted key order, using JSON-compatible YAML quoting. No fields are synthesized.
Nested objects/arrays are rejected. Leading response frontmatter is stripped so
`input_record` remains authoritative. Records are written sequentially; existing
entries, including dangling symlinks, are refused with exclusive file creation.
The command emits no records to stdout.

Local document import, export, and external-file ingestion are outside this service.

## Configuration and validation

`AUTOSCRIBE_ASC` overrides the server executable. `AUTOSCRIBE_GIT_PY` overrides
the Git entry point (default: `/home/jeremy/Loom/server/service/git.py`).
Git subprocesses use `/usr/bin/python3` and that wrapper. The service embeds skim
5.7.0 and never invokes the `sk` executable. fzf is unaffected.

The server runtime ledger path is `/home/jeremy/Data/ledger.sql`. The control
catalogue remains Git-authoritative; this change neither creates nor changes
`~/Data/control.sql`. No database schemas are changed.

Validation:

```sh
cargo fmt --check
cargo clippy --all-targets --all-features -- -D warnings
cargo test
cargo build --release --bins
cargo test --test dispatch --test dispatch_integration
```

## Dispatch selector (server)

`dispatch` may be run from any directory. With no selector overrides it performs:

1. **Repository** — recursively discovers Git working trees below `/home/jeremy/Repos`.
   `AUTOSCRIBE_REPOS` overrides this root. Directory symlinks are followed and canonical
   paths are deduplicated, so `~/Repos` may itself be a symlink to `~/Studio`.
2. **Unqueued commit** — asks `asc storage dispatch-state --repository <repo>` and excludes
   both `queued_commits` and `inflight_commits` from the Git history presented by skim.
   The Rust client accepts older `asc` responses which omit `queued_commits`.
3. **Plan type** — reads distinct plan types directly from the authoritative control SQLite DB.
4. **Plan** — shows only plans belonging to the selected type.

The control DB defaults to `/home/jeremy/Data/control.sql` and may be overridden with
`AUTOSCRIBE_CONTROL_DB`. The `plans` table must contain an identity (`identity`, `slug`, or
`id`), display label (`label`, `title`, or `name`), and plan type (`plan_type`, `type`, or
`kind`).

For scripted/worker use each selector can be bypassed explicitly:

```sh
dispatch --repo /home/jeremy/Repos/Book \
  --commit <full-sha> --plan-type editing --plan plan.copyedit
```

Supplying `--plan` makes `--plan-type` optional because no filtering UI is needed.

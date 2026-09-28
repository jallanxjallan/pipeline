# Current Rust Implementation Contract

Rust reads and writes vault Markdown directly. Format conversion and publishing are
external to the AutoScribe service. There is no resident daemon or SQLite client ledger.

## Dispatch and tracking

- Select repository, unqueued commit, plan type, then plan, using the
  domain-independent embedded skim selector. Human display text is not parsed.
- Explicit `--commit` / `--plan` and interactive selection reach `run_commit`.
- Dispatch repository discovery is independent of `$PWD`; `--repo` selects an
  explicit repository. Writeback requires `$PWD` itself to be the Git working-tree
  root and never widens a nested current directory to its containing repository.
- Dispatch changed Markdown blobs from the selected commit's first-parent delta;
  a root commit uses its full tree. Optional paths narrow the source set.
- Validate frontmatter slugs, uniqueness, nonblank bodies and published plan identity.
  No ai-process scan, prefix convention, plan inference, or Pandoc is involved.
- Preserve body bytes apart from the existing leading directive extraction.
- Preserve PipelineCall fields and the `asc enqueue` boundary. Store repository,
  source commit and source blob in the existing `extra.context` object.
- Query existing calls/results/export custody via `asc storage dispatch-state`;
  account for queued Redis calls not yet persisted. Do not create state in Git.
- Do not create a missing ledger during read-only selection. Cancellation performs
  no enqueue, ledger write, or repository mutation.

`ResponseAction::Export` retains its serialized `export` spelling for server routing
compatibility. There is no local document-export command.

## Writers

Existing-note writeback preserves the original frontmatter bytes and replaces only
body content. Leading response frontmatter is discarded, never merged; a response slug differing from the source is rejected. Body-only
notes receive no synthesized frontmatter. Malformed leading envelopes fail before
writing. `writeback` requires `$PWD` itself to be the Git working-tree root; it
refuses nested directories instead of broadening write authority to a parent repository.
Dispatch state contains only inflight commits and busy sources. Required deliveries
remain inflight until receipted; successful action `none` does not require delivery.

Repository/commit-scoped `asc export list-pending --ndjson` returns call identity,
source identity, content and dispatch extra metadata without receipts. Snapshot
blob/slug checks, safe paths, symlink refusal and source-or-target equality guard
filesystem writes. A clean index is required; only accepted paths are committed.
One response commit carries exact `Autoscribe-Call:` trailers. Export receipts are
appended only after verifying the committed response, including repository, source
commit, writeback commit and target path in existing receipt columns. Missing
receipts recover from reachable matching trailers and target blobs. Blocked rows
remain pending and do not block independent responses. No legacy manifest writeback
path, new database schema, daemon invocation or automatic synchronization exists.

`writenew` accepts NDJSON with object `input_record`, a non-empty slug, optional
non-empty filename hint, and non-empty content. Its existing destination rules apply.
The native writer emits the supplied scalar metadata in sorted key order, with
JSON-compatible YAML quoting, followed by the response body and a final newline.
Nested metadata is rejected. Response frontmatter is discarded. Exclusive creation
refuses existing files and symlinks; no conversion job or temporary defaults exist.

`AscClient` owns the `asc export list-pending` and `update-exports` subprocesses.
Writeback requires no separate result extraction.

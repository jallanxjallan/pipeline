# AutoScribe Rust Service Cleanup Work Order

## Objective

Clean up the Rust service without changing its current external behavior or architecture.

The current commit-based dispatcher tests are authoritative. Do not restore obsolete behavior merely to satisfy old tests or dead compatibility code.

## Architectural invariants

Preserve all of the following:

- `dispatch`, `writeback`, `writenew`, and `inflight` are independent, single-purpose binaries.
- Shared implementation may remain in the Rust library.
- Dispatch is Git-commit based.
- Repository discovery is independent of the current working directory (`$PWD`).
- There is no `status: ai-process` scan or status-based dispatch eligibility.
- Dispatch selects, in order:
  1. repository
  2. unqueued commit
  3. plan type
  4. plan
- The selected Git commit/blob identity is authoritative for dispatched source.
- Dispatch hands off work and exits.
- Dispatch does not execute workers.
- Dispatch does not perform writeback.
- Writeback remains a separate user-initiated operation.
- Current dispatcher tests must continue to pass.

## Cleanup tasks

1. Remove dead code left over from the former monolithic `svc` CLI.
2. Remove obsolete status-based and vault-dispatch machinery where no current code depends on it.
3. Remove stale tests that encode superseded dispatcher behavior.
4. Remove compatibility code that is demonstrably unreachable or contradicts the current contract.
5. Remove unused imports and dead modules where safe.
6. Eliminate compiler and Clippy warnings.
7. Simplify module boundaries and imports where this does not alter behavior.
8. Keep each executable narrow and single-purpose.
9. Prefer deletion of clearly dead machinery over retaining speculative compatibility layers.

## Do not change

Do not change any of the following merely as part of cleanup:

- database schemas
- NDJSON contracts
- enqueue semantics
- writeback semantics
- Git identity rules
- dispatcher selection order
- response-action semantics
- worker execution architecture
- externally visible behavior that is covered by current tests

If a proposed cleanup appears to require any of these changes, stop and flag it as an architectural issue rather than guessing.

## Test authority

The passing commit-based dispatcher tests define the current dispatcher contract.

In particular:

- Do not restore `status: ai-process` requirements.
- Do not make dispatch depend on `$PWD`.
- Do not merge the single-purpose binaries back into one command router.
- Do not treat the appearance of words such as `writeback` in option values or help text as evidence that dispatch owns writeback behavior.
- Do not restore obsolete tests simply because they previously existed.

## Working method

- Work on a dedicated cleanup branch.
- Make small, logical commits.
- Use compiler and test feedback to guide deletion.
- Avoid broad rewrites.
- Prefer the smallest change that removes dead or obsolete machinery.
- If uncertain whether code is obsolete, trace references and report the ambiguity instead of deleting speculatively.

Suggested branch:

```bash
git switch -c cleanup/rust-service
```

## Required validation

Before declaring the cleanup complete, run:

```bash
cargo fmt --check
cargo clippy --all-targets --all-features -- -D warnings
cargo test
cargo build --release --bins
```

All four commands must pass.

Also confirm that the dispatcher-specific suite still passes:

```bash
cargo test --test dispatch --test dispatch_integration
```

## Deliverables

Provide:

1. The cleaned Rust service source.
2. A short summary of deleted or simplified components.
3. A list of commits made during cleanup.
4. The final output/status of:
   - `cargo fmt --check`
   - `cargo clippy --all-targets --all-features -- -D warnings`
   - `cargo test`
   - `cargo build --release --bins`
5. Any architectural questions or code that was deliberately left untouched because its status was ambiguous.

## Acceptance criteria

The cleanup is accepted when:

- all current tests pass;
- all release binaries build;
- Clippy passes with warnings denied;
- no obsolete status-based dispatcher behavior has been reintroduced;
- the four single-purpose binaries remain separate;
- no database or NDJSON contract has been changed without explicit instruction;
- dead code and obsolete tests have been removed where confidently identified.


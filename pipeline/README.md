# AutoScribe server alpha 0.9.0

This release adds the first Git response writer while keeping export orchestration deliberately small.

## Durable facts

SQLite remains a relational index, not a payload store:

- `calls(call_id, created_at)`
- `call_keys(call_id, role, redis_key, created_at)`
- `responses(call_id, redis_key, created_at)`
- `exports(call_id, redis_key, created_at)`

`responses MINUS exports` is the Responses queue. There is no second durable queue and no mutable export-state flag.

## Export / Responses contract

When a worker records a response it runs `asc export`. That command does only two things:

1. count currently unexported response rows;
2. if the count is non-zero, send that integer to the Responses Unix datagram socket.

It does not send calls, baggage, content, paths, or work instructions.

Responses then queries the exporter itself:

```bash
asc export pending
```

The command emits one NDJSON row per pending export containing only the call ID and baggage:

```json
{"schema":"autoscribe.export-pending.v1","call_id":"...","baggage":{"schema":"autoscribe.call-baggage.v2","source":{"repo":"...","commit":"...","ref":"...","path":"...","blob":"..."},"output":{"adapter":"git","repo":"Project.git","identity":"psg.example","path_hint":"Contents/Example.md","commit_message":"AutoScribe response: psg.example"}}}
```

No response content is included. After Responses has resolved the destination repo and stable frontmatter identity, and any source-blob guard has passed, it requests only that record's response text:

```bash
asc export content <call-id>
```

## Git writer

For each pending Git output record, Responses:

1. resolves `output.repo` beneath `AUTOSCRIBE_REPO_ROOT`;
2. searches current `master` Markdown for exactly one file whose frontmatter `slug` equals `output.identity`;
3. if the call originated from Git and baggage contains `source.blob`, refuses to overwrite a target whose blob has changed since dispatch;
4. requests response content only after those checks;
5. preserves the target frontmatter and replaces only its body;
6. makes one atomic Git commit for that one response using the baggage-supplied `commit_message`;
7. appends an `AutoScribe-Call: <call-id>` commit trailer for idempotent recovery;
8. stores a small Redis export receipt and records the SQLite export fact only after the Git commit succeeds.

Records are independent. One failure does not block other records in the same repo, and later/straggling records remain pending until a later poke. A commit that succeeded before a ledger failure is recognized by its `AutoScribe-Call` trailer and reconciled without a duplicate write.

The first writer supports Git destinations only. Calls may eventually originate outside Git; destination routing therefore lives in baggage rather than being inferred from source provenance.

## Runtime services

- `autoscribe-dispatch.service`
- `autoscribe-executor.service`
- `autoscribe-worker.service`
- `autoscribe-responses.service`

## Smoke test

```bash
./reset-test-fixture.sh
./run-smoke-dispatch.sh
asc export pending
```

For v0.9, the production smoke test should additionally verify that one response becomes one Git commit and then disappears from `asc export pending`.

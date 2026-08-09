# Complete provider artifacts

Complete provider artifacts are the transport-neutral boundary between
Documentation Engine and an external traceability consumer. They remove the
need for an adopter to invoke paged commands repeatedly, validate cursors or
understand projection files. Documentation Engine owns that mechanical work;
the consumer receives one complete, body-free JSON artifact.

The capability name is `traceability-provider-v1`. It is generic: the
artifact describes documentation entities and changes only. Product logic,
code/test relations, reconciliation policy, attention queues and impact
semantics remain consumer responsibilities.

## Export complete evidence

Build and retain the required projection generations first, then export them
explicitly:

```bash
docsystem provider export snapshot GENERATION PROJECT \
  --output snapshot.json

docsystem provider export compare BEFORE AFTER PROJECT \
  --output comparison.json
```

Both commands fully verify the selected retained generation or generations,
walk the existing bounded provider pages internally and require the pages to
reconstruct one complete ordered result. The output is written through a
temporary file and atomically selected only after assembly and self-
verification. An existing output path is never replaced.

Export remains subject to provider retention. An evicted generation fails as
unknown or unavailable and requires an explicit rebaseline. The command never
reconstructs an operand from current Markdown and never interprets provider
unavailability as entity absence.

## Verify without project state

Any process with the artifact file can validate its integrity and supported
contract without a Documentation Engine project, provider cache or Python API:

```bash
docsystem provider artifact verify snapshot.json --json
docsystem provider artifact verify comparison.json --json
```

A valid artifact returns deterministic JSON with `valid: true`, its kind,
provider ID, pinned generation IDs, digest and item counts. Failure exits `1`,
writes no stdout and emits a stable code on stderr.

Important failure classes include:

- `artifact-corrupt`: malformed content, invalid entity evidence or digest
  mismatch;
- `artifact-incomplete`: missing observations, changes, coverage or count
  evidence;
- `artifact-mixed-provider`: inconsistent provider identities;
- `artifact-mixed-query`: incompatible scopes inside one comparison;
- `artifact-schema-unsupported`: unsupported artifact schema;
- `artifact-incompatible`: unsupported protocol, capability, kind or assembly
  policy;
- `artifact-output-exists`: export refused to replace existing evidence;
- `artifact-write-failed`: the destination filesystem could not provide the
  required atomic create operation.

The SHA-256 digest detects accidental or unauthorized content changes when the
expected digest is retained separately. It is not a digital signature and
does not establish who created the artifact. Transport authentication and
artifact provenance remain deployment responsibilities.

## Artifact contract

Every artifact contains:

- `schema_version`: version of the JSON artifact layout;
- `artifact_kind`: snapshot or comparison artifact;
- `protocol`: `traceability-provider`, version `1`, with capability
  `traceability-provider-v1`;
- `provider`: stable provider ID, provider contract version, visibility and
  generation-advertised capabilities;
- `query`: exact pinned generation and scope identity;
- `completeness`: `complete: true`, coverage, boundaries and exact counts;
- `policy`: bounded-page assembly, complete-or-fail behavior, ordering and
  body-omission policy;
- complete ordered `observations` or `changes`;
- `content_digest`: SHA-256 over canonical content excluding the digest field.

Snapshot observations contain stable kind/document/anchor identity, content
hash, provider-relative POSIX path, inclusive line hints, anchor kind and
visibility. Comparison changes contain `relocated`, `changed`, `missing` or
`added` plus exact before/after observations. Markdown bodies, headings,
metadata values, relations, absolute paths and projection locations are never
included.

The entity identity and change semantics are identical to the paged
[pinned provider contract](provider-snapshots.md). A consumer should store the
complete artifact as immutable evidence and import only fields it understands.

## Compatibility policy

Consumers must negotiate the artifact, not the installed package release:

1. Require protocol name `traceability-provider` and version `1`.
2. Require capability `traceability-provider-v1`.
3. Require a supported artifact `schema_version` and `artifact_kind`.
4. Run `provider artifact verify` before import.
5. Bind imported state to `provider.id`, exact generation IDs, query scope and
   `content_digest`.

Within protocol version 1, Documentation Engine may add optional fields or
additional capabilities. Consumers must ignore unknown optional fields while
continuing to validate the required v1 fields. Existing field meanings,
identity rules, classifications and completeness semantics do not change
within v1. A breaking change requires a new protocol/schema version or a new
capability, so an adopter does not need to track Documentation Engine patch or
minor releases merely to decide whether an artifact is importable.

The paged `provider snapshot` and `provider compare` commands remain supported
for streaming clients. New adopters that need a durable process boundary
should prefer complete artifacts and should not read `.docsystem/cache`, shard
files or internal Python modules.

## Deliberate boundary

Artifact verification does not enable Documentation Engine to compare two
previously exported files. `provider export compare` still selects two retained
provider generations and exports their verified comparison. Portable
comparison after provider retention expiry remains a separate future
capability; consumers must not infer it from `traceability-provider-v1`.

See [`examples/provider-snapshots/`](../examples/provider-snapshots/) for a
public synthetic provider fixture suitable for black-box integration tests.

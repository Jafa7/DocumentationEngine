# Document profile registry

The optional `[profiles]` registry is project-authored validation policy for
document types. It turns observed metadata inventory into an explicit contract
without inferring schema, rewriting Markdown or making generated data
authoritative.

## Configuration

Each named profile owns one or more document types. A document type can belong
to only one profile.

```toml
[profiles.roadmap]
document_types = ["roadmap"]
history_mode = "immutable-after-state"
required_metadata = ["status", "owner"]
required_roles = ["outcome", "acceptance"]
allowed_relations = ["depends_on", "derived_from", "validated_against"]
allowed_statuses = ["active", "completed"]

[profiles.roadmap.roles]
outcome = ["outcome", "product-outcome"]
acceptance = ["acceptance"]
```

`history_mode` is one of `living`, `append-only`, or
`immutable-after-state`. It is reported as policy evidence; this milestone
does not use it to authorize or perform writes.

`required_metadata` accepts core or additional field names. For semantic
relation names, presence means that the document has at least one valid value
for that relation; an authored empty relation list does not satisfy the
requirement. Omitting `allowed_relations` or `allowed_statuses` leaves that
dimension unrestricted. An explicit empty list allows no values.

Semantic roles are not heading titles. The project maps each role to one or
more canonical section anchors. A document satisfies the role when any listed
anchor exists. Documentation Engine does not infer aliases from titles or
similarity, and several roles may intentionally share one anchor.

## Validation

```bash
docsystem profile-check .
docsystem profile-check . --json
```

The report includes profile summaries, body-free document assignments,
history mode, unprofiled document IDs and deterministic violations:

- `missing-metadata`;
- `missing-role`;
- `relation-not-allowed`;
- `status-not-allowed`.

Profile violations produce the report and exit `1`; they are validation
evidence, not a transport error. Malformed configuration or an invalid
catalog/graph exits `1`, writes diagnostics to stderr and emits no partial
stdout. Markdown bodies and additional metadata values are never returned.
The command does not modify Markdown, configuration, projection or local
state.

Configured profile violations also participate in ordinary `validate` and
`doctor`, where they are emitted as concise errors on stderr. This makes
authored profile policy part of the normal quality gate without changing any
project that has no configured profiles.

An absent or empty registry is backward compatible. Documents whose type has
no configured profile remain visible under `unprofiled_documents` but are not
violations, allowing gradual adoption. A future strict-coverage policy should
be added only after real projects have stable profile evidence.

Use [`metadata-inventory`](metadata-inventory.md) to discover observed fields
and types, then author or revise profile policy deliberately. Inventory facts
never update this registry automatically.
